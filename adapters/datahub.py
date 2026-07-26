"""DataHub integration client.

Reads license and lineage context through the coordinator-hosted MCP endpoint
(see :mod:`adapters.mcp_client`) and performs reversible, ``license.``-scoped
writeback through the DataHub SDK (see :mod:`adapters.catalog`).

Two rules shape everything here:

1. **Every write is namespace-guarded.** Five submissions share one DataHub
   instance. A write that cannot be proven to target a ``license.`` entity is
   refused before any request leaves the process.
2. **Every write is reversible, and restoration is attempted whenever the write
   may have landed** -- including when the verifying re-read itself raises. That
   is the difference between leaving the shared instance clean and leaving a
   stray tag behind because an exception took the short path out.

The MCP endpoint comes from ``DATAHUB_MCP_URL``. Never hardcode a deployment
port -- the coordinator runs private pinned MCP workers and may move them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from adapters.mcp_client import (
    DEFAULT_MAX_HOPS,
    DEFAULT_MAX_RESULTS,
    McpError,
    McpTransport,
)
from app.namespace import Namespace, NamespaceViolation, require_in_namespace


class DataHubError(Exception):
    """Raised when DataHub is unreachable or returns an unusable response."""


class DataHubUnavailable(DataHubError):
    """Raised when the endpoint cannot be reached at all."""


#: MCP tools this project depends on. Readiness fails closed unless the endpoint
#: advertises all of them, so a stripped-down or misconfigured worker is caught
#: before it produces a misleading empty impact analysis.
REQUIRED_MCP_TOOLS = frozenset({"search", "get_entities", "get_lineage"})

#: Custom properties every seeded entity must carry. Readiness verifies these:
#: an entity missing ``artifact_class`` would classify as UNKNOWN and escalate,
#: which is safe but indistinguishable from a genuinely unclassifiable artifact.
REQUIRED_CUSTOM_PROPERTIES = frozenset({"artifact_class", "purposes"})


@dataclass(frozen=True)
class EntityContext:
    """Governance context for one DataHub entity."""

    urn: str
    entity_type: str
    name: str
    tags: tuple[str, ...] = ()
    domain: str | None = None
    owners: tuple[str, ...] = ()
    description: str | None = None
    custom_properties: dict[str, str] = field(default_factory=dict)
    #: False when the entity carries ``Status(removed=True)``. A soft-deleted
    #: entity is not a usable catalog entry.
    active: bool = True

    def has_tag(self, tag: str) -> bool:
        return tag in self.tags

    def missing_properties(self) -> frozenset[str]:
        return frozenset(REQUIRED_CUSTOM_PROPERTIES - set(self.custom_properties))


@dataclass(frozen=True)
class LineageEdge:
    """One downstream hop reported by DataHub."""

    upstream_urn: str
    downstream_urn: str
    #: False when DataHub reported the edge but could not resolve the entity,
    #: which is what drives LCB-R001 escalation instead of a false all-clear.
    resolved: bool = True


@dataclass(frozen=True)
class WritebackReceipt:
    """Evidence that a reversible writeback ran end to end.

    The four flags are deliberately independent so the receipt can describe every
    real outcome, including the awkward ones:

    - ``started`` -- the write was attempted, so state may have changed.
    - ``write_failed`` -- the write call itself raised.
    - ``verified`` -- an immediate re-read observed the applied value.
    - ``restored`` -- prior state was put back and confirmed.

    A write that landed but whose verifying re-read raised is ``started=True,
    verified=False, restored=True``. Collapsing that into a single success flag
    would hide the fact that the instance was touched.
    """

    urn: str
    aspect: str
    applied_value: str
    prior_value: list[str]
    written_at: datetime
    started: bool
    verified: bool
    restored: bool
    write_failed: bool = False
    detail: str = ""

    @property
    def clean(self) -> bool:
        """Whether the write was proven *and* the instance was left as found."""
        return self.verified and self.restored

    @property
    def residual_risk(self) -> bool:
        """Whether state may have been left behind on the shared instance."""
        return self.started and not self.restored


class DataHubClient(Protocol):
    """The surface the application depends on.

    Kept narrow so the deterministic fake in :class:`adapters.fake_datahub.FakeDataHubClient`
    is a real substitute rather than a partial mock.
    """

    def list_mcp_tools(self) -> frozenset[str]: ...

    def get_entity(self, urn: str) -> EntityContext | None: ...

    def get_entities(self, urns: list[str]) -> dict[str, EntityContext]: ...

    def get_downstream_lineage(self, urn: str, max_depth: int = 5) -> list[LineageEdge]: ...

    def get_tags(self, urn: str) -> list[str]: ...

    def set_tags(self, urn: str, tags: list[str]) -> None: ...

    def set_properties(self, urn: str, properties: dict[str, str]) -> None: ...


class LiveDataHubClient:
    """Live client: MCP for reads, DataHub SDK for writeback.

    Constructed from settings so endpoints are always configuration-driven.
    """

    def __init__(
        self,
        *,
        gms_url: str,
        mcp_url: str,
        token: str,
        namespace: Namespace,
        max_hops: int = DEFAULT_MAX_HOPS,
        max_results: int = DEFAULT_MAX_RESULTS,
    ) -> None:
        if not gms_url:
            raise DataHubError("DATAHUB_GMS_URL is not configured")
        if not token:
            # Failing here rather than sending an unauthenticated request keeps a
            # missing token from surfacing later as a confusing empty result set.
            raise DataHubError("DATAHUB_TOKEN is not configured")

        self._namespace = namespace
        self._max_hops = max_hops
        self._max_results = max_results
        self._transport = McpTransport(mcp_url, token)
        self._catalog: Any = None
        self._gms_url = gms_url
        self._token = token

    def _get_catalog(self):
        from adapters.catalog import LiveCatalog

        if self._catalog is None:
            self._catalog = LiveCatalog(self._gms_url, self._token, self._namespace)
        return self._catalog

    # -- reads ----------------------------------------------------------

    def list_mcp_tools(self) -> frozenset[str]:
        try:
            return self._transport.tool_names()
        except McpError as exc:
            raise DataHubUnavailable(str(exc)) from exc

    def get_entities(self, urns: list[str]) -> dict[str, EntityContext]:
        """Batch entity fetch.

        ``get_entities`` takes a list, so one call replaces N. On a demo graph
        this is the difference between 1 round trip and 12.
        """
        if not urns:
            return {}
        try:
            payload = self._transport.call("get_entities", {"urns": list(urns)})
        except McpError as exc:
            raise DataHubUnavailable(str(exc)) from exc

        contexts: dict[str, EntityContext] = {}
        for raw in _iter_entities(payload):
            urn = raw.get("urn")
            if urn:
                contexts[urn] = _to_entity_context(urn, raw)
        return contexts

    def get_entity(self, urn: str) -> EntityContext | None:
        return self.get_entities([urn]).get(urn)

    def get_downstream_lineage(self, urn: str, max_depth: int | None = None) -> list[LineageEdge]:
        """Downstream lineage, with request shape taken from the advertised schema.

        ``upstream=false`` is explicit: the default on some builds is upstream,
        which would silently return ancestors and produce an empty impact set.
        """
        hops = min(max_depth or self._max_hops, self._max_hops)
        try:
            schema = self._transport.schema_for("get_lineage")
            results = schema.clamp("max_results", self._max_results)
            payload = self._transport.call(
                "get_lineage",
                {
                    "urn": urn,
                    "upstream": False,
                    "max_hops": hops,
                    "max_results": results,
                },
            )
        except McpError as exc:
            raise DataHubUnavailable(str(exc)) from exc

        return _to_lineage_edges(urn, payload)

    def get_tags(self, urn: str) -> list[str]:
        entity = self.get_entity(urn)
        return list(entity.tags) if entity else []

    # -- writes ---------------------------------------------------------

    def upsert_spec(self, spec) -> None:
        """Materialize one complete catalog entry through the SDK.

        Properties, active status, tags, domain, and lineage -- not just tags.
        """
        from adapters.catalog import CatalogError

        try:
            self._get_catalog().upsert(spec)
        except CatalogError as exc:
            raise DataHubError(str(exc)) from exc
        except NamespaceViolation:
            raise
        except Exception as exc:
            raise DataHubUnavailable(f"catalog upsert failed: {exc}") from exc

    def set_status(self, urn: str, removed: bool) -> None:
        """Soft-delete or restore. Namespace-guarded inside the catalog."""
        from adapters.catalog import CatalogError

        try:
            self._get_catalog().set_status(urn, removed)
        except CatalogError as exc:
            raise DataHubError(str(exc)) from exc
        except NamespaceViolation:
            raise
        except Exception as exc:
            raise DataHubUnavailable(f"status change failed: {exc}") from exc

    def set_tags(self, urn: str, tags: list[str]) -> None:
        """Replace the globalTags aspect. Namespace-guarded."""
        require_in_namespace(urn, self._namespace, operation="set_tags")

        from datahub.emitter.mcp import MetadataChangeProposalWrapper
        from datahub.metadata.schema_classes import GlobalTagsClass, TagAssociationClass

        from adapters.catalog import CatalogError, tag_urn

        try:
            self._get_catalog().emit(
                [
                    MetadataChangeProposalWrapper(
                        entityUrn=urn,
                        aspect=GlobalTagsClass(
                            tags=[TagAssociationClass(tag=tag_urn(t)) for t in tags]
                        ),
                    )
                ]
            )
        except CatalogError as exc:
            raise DataHubError(str(exc)) from exc
        except Exception as exc:
            raise DataHubUnavailable(f"tag writeback failed: {exc}") from exc

    def set_properties(self, urn: str, properties: dict[str, str]) -> None:
        """Merge custom properties into the entity's ``datasetProperties``.

        Merged rather than replaced: this project seeds ``artifact_class``,
        ``purposes``, and the fixture marker, and a governance status write must
        not silently drop the metadata the policy engine reads.
        """
        require_in_namespace(urn, self._namespace, operation="set_properties")

        from adapters.catalog import CatalogError

        existing = self.get_entity(urn)
        merged = dict(existing.custom_properties) if existing else {}
        merged.update({str(k): str(v) for k, v in properties.items()})

        try:
            self._get_catalog().set_custom_properties(
                urn,
                name=existing.name if existing else urn,
                description=existing.description if existing else "",
                properties=merged,
            )
        except CatalogError as exc:
            raise DataHubError(str(exc)) from exc
        except NamespaceViolation:
            raise
        except Exception as exc:
            raise DataHubUnavailable(f"property writeback failed: {exc}") from exc


# --- durable revocation writeback --------------------------------------

#: Custom-property keys this project writes back. Prefixed so they are
#: unmistakably ours on an instance shared with four other submissions.
REVOCATION_STATUS_KEY = "lcb_revocation_status"
REVOCATION_EVENT_KEY = "lcb_revocation_event"
REVOCATION_EVIDENCE_KEY = "lcb_evidence_ref"
REVOCATION_PLAN_KEY = "lcb_plan_hash"
REVOCATION_VERIFIED_KEY = "lcb_verified_at"

#: Status values, and the tag that carries each one into the DataHub UI.
STATUS_CONTAINED = "contained"
STATUS_RESIDUAL = "residual"
STATUS_ESCALATED = "escalated"

_STATUS_TAGS = {
    STATUS_CONTAINED: "license-revocation-contained",
    STATUS_RESIDUAL: "license-revocation-residual",
    STATUS_ESCALATED: "license-revocation-escalated",
}


@dataclass(frozen=True)
class RevocationWriteback:
    """Evidence that a durable governance status reached DataHub."""

    urn: str
    status: str
    tag: str
    aspects: tuple[str, ...]
    properties: dict[str, str]
    written_at: datetime
    verified: bool
    detail: str
    #: True when this ran against the in-memory fake rather than a live instance.
    simulated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "urn": self.urn,
            "status": self.status,
            "tag": self.tag,
            "aspects": list(self.aspects),
            "properties": dict(self.properties),
            "written_at": self.written_at.isoformat(),
            "verified": self.verified,
            "detail": self.detail,
            "simulated": self.simulated,
        }


def record_revocation(
    client: DataHubClient,
    urn: str,
    namespace: Namespace,
    *,
    status: str,
    event_id: str,
    plan_hash: str,
    evidence_ref: str,
    simulated: bool = False,
) -> RevocationWriteback:
    """Write the durable revocation outcome back to DataHub and verify it.

    Unlike :func:`reversible_tag_writeback`, this is *meant* to persist. The
    reversible write proves the integration works without leaving state; this
    one records the governance result that the next person to open the catalog
    entry needs to see. Both exist because they answer different questions.

    Writes a status tag plus prefixed custom properties, then re-reads to
    confirm. An unverified write is reported as unverified, never as done.

    Raises:
        NamespaceViolation: if the target is outside this project's allocation.
            Raised before anything is attempted.
        DataHubError: if the status is unknown, or the write itself fails.
    """
    require_in_namespace(urn, namespace, operation="record_revocation")

    tag = _STATUS_TAGS.get(status)
    if tag is None:
        raise DataHubError(
            f"unknown revocation status {status!r}; expected one of {sorted(_STATUS_TAGS)}"
        )

    properties = {
        REVOCATION_STATUS_KEY: status,
        REVOCATION_EVENT_KEY: event_id,
        REVOCATION_PLAN_KEY: plan_hash,
        REVOCATION_EVIDENCE_KEY: evidence_ref,
        REVOCATION_VERIFIED_KEY: datetime.now(UTC).isoformat(),
    }

    prior_tags = list(client.get_tags(urn))
    # Only one status tag may apply at a time, or an entity contained after an
    # earlier residual run would carry both and read as ambiguous.
    kept = [t for t in prior_tags if t not in _STATUS_TAGS.values()]
    client.set_tags(urn, sorted({*kept, tag}))
    client.set_properties(urn, properties)

    observed = client.get_entity(urn)
    notes: list[str] = []
    verified = observed is not None

    if observed is None:
        notes.append("entity could not be re-read after the write")
    else:
        if tag not in observed.tags:
            verified = False
            notes.append(f"status tag {tag!r} not observed; tags are {sorted(observed.tags)}")
        for key, value in properties.items():
            if observed.custom_properties.get(key) != value:
                verified = False
                notes.append(f"property {key!r} did not land")

    if verified:
        notes.append(f"status {status!r} written and confirmed by re-read")

    return RevocationWriteback(
        urn=urn,
        status=status,
        tag=tag,
        aspects=("globalTags", "datasetProperties"),
        properties=properties,
        written_at=datetime.now(UTC),
        verified=verified,
        detail="; ".join(notes),
        simulated=simulated,
    )


def reversible_tag_writeback(
    client: DataHubClient,
    urn: str,
    tag: str,
    namespace: Namespace,
) -> WritebackReceipt:
    """Apply a tag, prove it landed, then restore the prior state.

    Restoration runs in ``finally`` from the moment the write is attempted. If the
    verifying re-read raises, the write may still have landed, so rolling back is
    exactly as necessary as it is on the happy path -- and skipping it would leave
    a stray tag on an instance shared with four other submissions.

    The receipt records ``started``, ``write_failed``, ``verified``, and
    ``restored`` independently, so an unrestored write can never be presented as a
    clean one.

    Raises:
        NamespaceViolation: if ``urn`` is outside this project's allocation. Raised
            before anything is attempted, so no state changes.
    """
    require_in_namespace(urn, namespace, operation="reversible_tag_writeback")

    prior = list(client.get_tags(urn))
    applied = sorted(set(prior) | {tag})

    started = False
    write_failed = False
    verified = False
    restored = False
    notes: list[str] = []

    try:
        started = True
        client.set_tags(urn, applied)

        observed = list(client.get_tags(urn))
        verified = tag in observed
        if not verified:
            notes.append(f"re-read did not observe {tag!r}; tags were {sorted(observed)}")
    except (DataHubError, NamespaceViolation) as exc:
        # The write or the verifying re-read failed. Either way state may have
        # changed, so restoration below still runs.
        write_failed = True
        notes.append(f"write or verification failed: {exc}")
    finally:
        try:
            client.set_tags(urn, prior)
            after = list(client.get_tags(urn))
            restored = sorted(after) == sorted(prior)
            if not restored:
                notes.append(f"restore left tags as {sorted(after)}, expected {sorted(prior)}")
        except (DataHubError, NamespaceViolation) as exc:
            notes.append(f"restore failed: {exc}")

    if verified and restored and not notes:
        notes.append("tag applied, re-read confirmed, prior state restored")
    if started and not restored:
        notes.append("RESIDUAL: shared instance may retain this write")

    return WritebackReceipt(
        urn=urn,
        aspect="globalTags",
        applied_value=tag,
        prior_value=prior,
        written_at=datetime.now(UTC),
        started=started,
        verified=verified,
        restored=restored,
        write_failed=write_failed,
        detail="; ".join(notes),
    )


# -- parsing helpers ----------------------------------------------------


def _iter_entities(payload: Any) -> list[dict[str, Any]]:
    """Normalize the several shapes an entity payload arrives in."""
    if payload is None:
        return []
    if isinstance(payload, dict):
        for key in ("entities", "results", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [e for e in value if isinstance(e, dict)]
        if "urn" in payload:
            return [payload]
        return []
    if isinstance(payload, list):
        return [e for e in payload if isinstance(e, dict)]
    return []


def _to_entity_context(urn: str, raw: dict[str, Any]) -> EntityContext:
    tags = raw.get("tags") or []
    properties = raw.get("customProperties") or raw.get("custom_properties") or {}
    status = raw.get("status") or {}
    removed = status.get("removed") if isinstance(status, dict) else raw.get("removed")

    return EntityContext(
        urn=raw.get("urn", urn),
        entity_type=raw.get("entityType", raw.get("type", "unknown")),
        name=raw.get("name", ""),
        tags=tuple(_tag_name(t) for t in tags),
        domain=_domain_name(raw.get("domain")),
        owners=tuple(str(o) for o in (raw.get("owners") or ())),
        description=raw.get("description"),
        custom_properties={str(k): str(v) for k, v in dict(properties).items()},
        active=not bool(removed),
    )


def _to_lineage_edges(source_urn: str, payload: Any) -> list[LineageEdge]:
    entries: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        for key in ("relationships", "entities", "results", "lineage"):
            value = payload.get(key)
            if isinstance(value, list):
                entries = [e for e in value if isinstance(e, dict)]
                break
    elif isinstance(payload, list):
        entries = [e for e in payload if isinstance(e, dict)]

    edges: list[LineageEdge] = []
    for entry in entries:
        downstream = entry.get("urn") or entry.get("entity") or entry.get("downstream")
        if not downstream:
            continue
        edges.append(
            LineageEdge(
                upstream_urn=entry.get("via") or entry.get("upstream") or source_urn,
                downstream_urn=str(downstream),
                # DataHub can report a relationship whose entity it cannot resolve.
                # Treating that as complete would manufacture a false all-clear.
                resolved=bool(entry.get("resolved", True)),
            )
        )
    return edges


def _tag_name(raw: Any) -> str:
    """Normalize a tag to its bare name."""
    if isinstance(raw, dict):
        raw = raw.get("tag") or raw.get("name") or ""
    text = str(raw)
    return text.rsplit(":", 1)[-1] if text.startswith("urn:li:tag:") else text


def _domain_name(raw: Any) -> str | None:
    """Normalize a domain reference to a comparable name."""
    if raw is None:
        return None
    if isinstance(raw, dict):
        for key in ("name", "urn", "id"):
            value = raw.get(key)
            if value:
                return str(value)
        return None
    if isinstance(raw, list):
        return _domain_name(raw[0]) if raw else None
    return str(raw)


def _encode(urn: str) -> str:
    from urllib.parse import quote

    return quote(urn, safe="")


__all__ = [
    "REQUIRED_CUSTOM_PROPERTIES",
    "REQUIRED_MCP_TOOLS",
    "REVOCATION_EVENT_KEY",
    "REVOCATION_EVIDENCE_KEY",
    "REVOCATION_PLAN_KEY",
    "REVOCATION_STATUS_KEY",
    "REVOCATION_VERIFIED_KEY",
    "STATUS_CONTAINED",
    "STATUS_ESCALATED",
    "STATUS_RESIDUAL",
    "DataHubClient",
    "DataHubError",
    "DataHubUnavailable",
    "EntityContext",
    "LineageEdge",
    "LiveDataHubClient",
    "RevocationWriteback",
    "WritebackReceipt",
    "record_revocation",
    "reversible_tag_writeback",
]
