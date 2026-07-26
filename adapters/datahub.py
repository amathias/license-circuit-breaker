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

    def has_edge(self, upstream: str, downstream: str) -> bool:
        """Whether DataHub reports ``downstream`` as a *direct* descendant.

        Readiness verifies each declared fixture edge, and cannot do that from a
        single walk out of the source: ``get_lineage`` returns descendants with a
        ``degree``, never the parent they arrived through, so a walk from the
        source can only ever prove the edges that leave the source. Asking about
        one specific upstream is the only question this envelope answers exactly.

        Costs one MCP call per edge. Readiness is a probe, not a hot path, and an
        edge check that cannot fail is worth less than nothing.
        """
        edges = self.get_downstream_lineage(upstream, max_depth=1)
        return any(e.downstream_urn == downstream and e.resolved for e in edges)

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


class PayloadError(DataHubError):
    """Raised when an MCP payload does not match the DataHub envelope.

    Deliberately an error rather than an empty result. The previous normalizers
    returned ``[]`` for any shape they did not recognize, so when the envelope
    turned out to be ``{"result": [...]}`` rather than ``{"entities": [...]}``,
    readiness reported *"12/12 entities unusable"* against an instance holding
    all 12 entities, correctly seeded. "I cannot read the response" and "the data
    is not there" are opposite diagnoses and must never render the same.
    """


def _require_mapping(value: Any, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PayloadError(f"expected {what} to be an object, got {type(value).__name__}")
    return value


def _iter_entities(payload: Any) -> list[dict[str, Any]]:
    """Unwrap the ``get_entities`` envelope.

    The exact observed shape is ``{"result": [entity, ...]}``. Nothing else is
    accepted: guessing at alternatives is what hid the mismatch, and an envelope
    this project has not seen is one it cannot claim to understand.

    Raises:
        PayloadError: on any other shape, or a non-object entity.
    """
    envelope = _require_mapping(payload, "the get_entities payload")

    if "result" not in envelope:
        raise PayloadError(
            "get_entities payload has no 'result' key; "
            f"got keys {sorted(envelope)}. Expected {{'result': [entity, ...]}}."
        )

    result = envelope["result"]
    if not isinstance(result, list):
        raise PayloadError(f"get_entities 'result' must be a list, got {type(result).__name__}")

    for index, entity in enumerate(result):
        if not isinstance(entity, dict):
            raise PayloadError(
                f"get_entities result[{index}] must be an object, got {type(entity).__name__}"
            )
    return result


def _custom_properties(raw: Any) -> dict[str, str]:
    """Normalize ``customProperties`` from its observed list-of-pairs form.

    DataHub returns ``[{"key": ..., "value": ...}, ...]``, not a mapping. The
    previous code called ``dict()`` on whatever it found, which silently produced
    ``{}`` here -- and an entity with no custom properties fails the
    ``artifact_class``/``purposes`` coverage check, so every entity looked
    unusable.
    """
    if raw is None:
        return {}
    if not isinstance(raw, list):
        raise PayloadError(
            f"customProperties must be a list of {{key, value}}, got {type(raw).__name__}"
        )

    properties: dict[str, str] = {}
    for index, pair in enumerate(raw):
        if not isinstance(pair, dict) or "key" not in pair:
            raise PayloadError(f"customProperties[{index}] is not a {{key, value}} object")
        properties[str(pair["key"])] = str(pair.get("value", ""))
    return properties


def _tag_names(raw: Any) -> tuple[str, ...]:
    """Normalize the nested ``tags`` aspect to bare tag names.

    Observed shape is ``{"tags": [{"tag": {"urn": "urn:li:tag:NAME"}}]}``. The
    previous code iterated the outer object directly, which yielded the single
    string ``"tags"`` as a tag name, so every project-tag check failed.

    An absent aspect means an untagged entity, which is a legitimate state and is
    reported as such rather than as a parse failure.
    """
    if raw is None:
        return ()

    aspect = _require_mapping(raw, "the tags aspect")
    entries = aspect.get("tags")
    if entries is None:
        return ()
    if not isinstance(entries, list):
        raise PayloadError(f"tags.tags must be a list, got {type(entries).__name__}")

    names: list[str] = []
    for index, entry in enumerate(entries):
        association = _require_mapping(entry, f"tags.tags[{index}]")
        tag = _require_mapping(association.get("tag"), f"tags.tags[{index}].tag")
        urn = tag.get("urn")
        if not urn:
            raise PayloadError(f"tags.tags[{index}].tag has no urn")
        names.append(_tag_name(str(urn)))
    return tuple(names)


def _domain_urn(raw: Any) -> str | None:
    """Normalize the nested ``domain`` aspect to a domain URN.

    Observed shape is ``{"domain": {"urn": "urn:li:domain:...", ...}}``. The
    previous code looked for ``name``/``urn``/``id`` on the *outer* object and so
    returned ``None`` for every entity, which readiness reports as "no domain".

    Readiness compares this against :func:`adapters.catalog.domain_urn`, so the
    URN is what must come back -- not a display name.
    """
    if raw is None:
        return None

    aspect = _require_mapping(raw, "the domain aspect")
    domain = aspect.get("domain")
    if domain is None:
        return None

    urn = _require_mapping(domain, "domain.domain").get("urn")
    if not urn:
        raise PayloadError("domain.domain has no urn")
    return str(urn)


def _is_active(raw: dict[str, Any]) -> bool:
    """Whether the entity is not soft-deleted.

    The observed envelope carries no ``status`` for entities known to be active,
    so an absent status reads as active. When a status *is* present, ``removed``
    governs, and a non-boolean is a parse failure rather than something to
    coerce: ``bool("false")`` is ``True``, which would report a soft-deleted
    entity as live.
    """
    status = raw.get("status")
    if status is None:
        return True

    removed = _require_mapping(status, "the status aspect").get("removed")
    if removed is None:
        return True
    if not isinstance(removed, bool):
        raise PayloadError(f"status.removed must be a boolean, got {removed!r}")
    return not removed


def _to_entity_context(urn: str, raw: dict[str, Any]) -> EntityContext:
    """Build an :class:`EntityContext` from one entity of the observed envelope.

    Raises:
        PayloadError: if any aspect present is not the shape DataHub returns.
    """
    entity_urn = raw.get("urn") or urn
    if not entity_urn:
        raise PayloadError("entity has no urn")

    properties = raw.get("properties")
    properties = {} if properties is None else _require_mapping(properties, "the properties aspect")

    return EntityContext(
        urn=str(entity_urn),
        entity_type=str(raw.get("type") or "unknown"),
        # The top-level name is the entity's; properties.name is the aspect's.
        # They agree on seeded entities; prefer the top level, always present.
        name=str(raw.get("name") or properties.get("name") or ""),
        tags=_tag_names(raw.get("tags")),
        domain=_domain_urn(raw.get("domain")),
        owners=(),
        description=properties.get("description"),
        custom_properties=_custom_properties(properties.get("customProperties")),
        active=_is_active(raw),
    )


def _to_lineage_edges(source_urn: str, payload: Any) -> list[LineageEdge]:
    """Unwrap the ``get_lineage`` envelope into edges from ``source_urn``.

    The exact observed shape is::

        {"downstreams": {"total": 1,
                         "searchResults": [{"entity": {"urn": ...}, "degree": 1}]}}

    It is a *descendant* list, not an edge list: ``degree`` says how far away a
    node is, never through which parent. So only ``degree == 1`` yields a
    provable edge. A deeper descendant is real -- DataHub found it downstream --
    but this envelope cannot say by what route, and emitting it as a one-hop edge
    would let the report cite a lineage path that does not exist. It is therefore
    emitted with ``resolved=False``, which marks reconstructed paths incomplete
    and escalates under LCB-R001 rather than claiming evidence it does not have.

    Raises:
        PayloadError: on any other shape, or on a truncated result -- a lineage
            read that silently dropped descendants is a false all-clear.
    """
    envelope = _require_mapping(payload, "the get_lineage payload")

    if "downstreams" not in envelope:
        raise PayloadError(
            "get_lineage payload has no 'downstreams' key; "
            f"got keys {sorted(envelope)}. Expected {{'downstreams': {{...}}}}."
        )

    downstreams = _require_mapping(envelope["downstreams"], "get_lineage 'downstreams'")
    total = downstreams.get("total")
    if total is not None and not isinstance(total, int):
        raise PayloadError(f"downstreams.total must be an integer, got {total!r}")

    results = downstreams.get("searchResults")
    if results is None:
        # A node with no descendants. Accepted only when the server agrees there
        # are none, so a dropped key can never read as an empty graph.
        if total in (0, None):
            return []
        raise PayloadError(f"downstreams reports total={total} but carries no 'searchResults'")
    if not isinstance(results, list):
        raise PayloadError(
            f"downstreams.searchResults must be a list, got {type(results).__name__}"
        )
    if total is not None and total > len(results):
        raise PayloadError(
            f"downstreams truncated: total={total} but only {len(results)} results "
            "returned. Refusing a partial descendant set, which would read as a "
            "smaller blast radius than the real one."
        )

    edges: list[LineageEdge] = []
    for index, entry in enumerate(results):
        result = _require_mapping(entry, f"downstreams.searchResults[{index}]")
        entity = _require_mapping(
            result.get("entity"), f"downstreams.searchResults[{index}].entity"
        )
        downstream = entity.get("urn")
        if not downstream:
            raise PayloadError(f"downstreams.searchResults[{index}].entity has no urn")

        degree = result.get("degree")
        if not isinstance(degree, int):
            raise PayloadError(
                f"downstreams.searchResults[{index}].degree must be an integer, got {degree!r}"
            )

        edges.append(
            LineageEdge(
                upstream_urn=source_urn,
                downstream_urn=str(downstream),
                resolved=degree == 1,
            )
        )
    return edges


def _tag_name(raw: str) -> str:
    """Strip a tag URN down to its bare name."""
    return raw.rsplit(":", 1)[-1] if raw.startswith("urn:li:tag:") else raw


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
