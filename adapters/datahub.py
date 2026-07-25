"""DataHub integration client.

Reads license and lineage context through the coordinator-hosted MCP endpoint and
performs reversible, ``license.``-scoped writeback through GMS.

Two rules shape everything here:

1. **Every write is namespace-guarded.** Five submissions share one DataHub
   instance. A write that cannot be proven to target a ``license.`` entity is
   refused before any request leaves the process.
2. **Every write is reversible.** :meth:`DataHubClient.reversible_tag_writeback`
   captures the prior aspect, writes, immediately re-reads to prove the write
   landed, then restores the prior state. The demo proves writeback capability
   without leaving the shared instance mutated.

The MCP endpoint comes from ``DATAHUB_MCP_URL``. Never hardcode a deployment
port -- the coordinator runs two private pinned MCP workers and may move them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx

from app.namespace import Namespace, NamespaceViolation, require_in_namespace


class DataHubError(Exception):
    """Raised when DataHub is unreachable or returns an unusable response."""


class DataHubUnavailable(DataHubError):
    """Raised when the endpoint cannot be reached at all."""


#: MCP tools this project depends on. Readiness fails closed unless the endpoint
#: advertises all of them, so a stripped-down or misconfigured worker is caught
#: before it produces a misleading empty impact analysis.
REQUIRED_MCP_TOOLS = frozenset({"search", "get_entities", "get_lineage"})


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

    def has_tag(self, tag: str) -> bool:
        return tag in self.tags


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

    ``restored`` matters as much as ``verified``: a writeback that landed but did
    not restore leaves the shared instance dirty, and the receipt has to say so.
    """

    urn: str
    aspect: str
    applied_value: str
    prior_value: list[str]
    written_at: datetime
    verified: bool
    restored: bool
    detail: str = ""

    @property
    def clean(self) -> bool:
        """Whether the write was proven *and* the instance was left as found."""
        return self.verified and self.restored


class DataHubClient(Protocol):
    """The surface the application depends on.

    Kept narrow so the deterministic fake in :class:`FakeDataHubClient` is a real
    substitute rather than a partial mock.
    """

    def list_mcp_tools(self) -> frozenset[str]: ...

    def get_entity(self, urn: str) -> EntityContext | None: ...

    def get_downstream_lineage(self, urn: str, max_depth: int = 5) -> list[LineageEdge]: ...

    def get_tags(self, urn: str) -> list[str]: ...

    def set_tags(self, urn: str, tags: list[str]) -> None: ...


class HttpDataHubClient:
    """Live client: MCP over Streamable HTTP for reads, GMS for writeback.

    Constructed from settings so the endpoint is always configuration-driven.
    """

    def __init__(
        self,
        *,
        gms_url: str,
        mcp_url: str,
        token: str,
        namespace: Namespace,
        timeout: float = 15.0,
    ) -> None:
        if not gms_url:
            raise DataHubError("DATAHUB_GMS_URL is not configured")
        if not mcp_url:
            raise DataHubError("DATAHUB_MCP_URL is not configured")
        if not token:
            # Failing here rather than sending an unauthenticated request keeps a
            # missing token from surfacing later as a confusing empty result set.
            raise DataHubError("DATAHUB_TOKEN is not configured")

        self._gms_url = gms_url.rstrip("/")
        self._mcp_url = mcp_url
        self._token = token
        self._namespace = namespace
        self._timeout = timeout

    # -- transport ------------------------------------------------------

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }

    def _mcp_call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Issue one JSON-RPC call against the MCP endpoint."""
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
        try:
            response = httpx.post(
                self._mcp_url, json=payload, headers=self._headers, timeout=self._timeout
            )
        except httpx.HTTPError as exc:
            raise DataHubUnavailable(f"MCP endpoint unreachable: {exc}") from exc

        if response.status_code != 200:
            raise DataHubError(f"MCP returned HTTP {response.status_code}")

        body = _parse_possibly_streamed(response.text)
        if "error" in body:
            raise DataHubError(f"MCP error: {body['error']}")
        return body.get("result", {})

    def _gms_get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            response = httpx.get(
                f"{self._gms_url}{path}",
                params=params,
                headers=self._headers,
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise DataHubUnavailable(f"GMS unreachable: {exc}") from exc
        if response.status_code == 404:
            return {}
        if response.status_code >= 400:
            raise DataHubError(f"GMS returned HTTP {response.status_code}")
        return response.json()

    # -- reads ----------------------------------------------------------

    def list_mcp_tools(self) -> frozenset[str]:
        """Names of tools the MCP endpoint advertises."""
        result = self._mcp_call("tools/list")
        return frozenset(t.get("name", "") for t in result.get("tools", []))

    def get_entity(self, urn: str) -> EntityContext | None:
        result = self._mcp_call(
            "tools/call", {"name": "get_entities", "arguments": {"urns": [urn]}}
        )
        entities = _extract_entities(result)
        if not entities:
            return None
        return _to_entity_context(urn, entities[0])

    def get_downstream_lineage(self, urn: str, max_depth: int = 5) -> list[LineageEdge]:
        result = self._mcp_call(
            "tools/call",
            {
                "name": "get_lineage",
                "arguments": {"urn": urn, "direction": "DOWNSTREAM", "max_hops": max_depth},
            },
        )
        return _to_lineage_edges(urn, result)

    def get_tags(self, urn: str) -> list[str]:
        """Read the globalTags aspect. Used to capture prior state before a write."""
        body = self._gms_get(f"/aspects/{_encode(urn)}", {"aspect": "globalTags", "version": 0})
        return _extract_tag_names(body)

    # -- writes ---------------------------------------------------------

    def set_tags(self, urn: str, tags: list[str]) -> None:
        """Replace the globalTags aspect. Namespace-guarded."""
        require_in_namespace(urn, self._namespace, operation="set_tags")

        aspect = {"tags": [{"tag": f"urn:li:tag:{t}"} for t in tags]}
        proposal = {
            "proposal": {
                "entityUrn": urn,
                "aspectName": "globalTags",
                "changeType": "UPSERT",
                "aspect": {"value": json.dumps(aspect), "contentType": "application/json"},
            }
        }
        try:
            response = httpx.post(
                f"{self._gms_url}/aspects?action=ingestProposal",
                json=proposal,
                headers=self._headers,
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise DataHubUnavailable(f"GMS unreachable during write: {exc}") from exc
        if response.status_code >= 400:
            raise DataHubError(f"Writeback failed with HTTP {response.status_code}")


def reversible_tag_writeback(
    client: DataHubClient,
    urn: str,
    tag: str,
    namespace: Namespace,
) -> WritebackReceipt:
    """Apply a tag, prove it landed, then restore the prior state.

    This is the supported-writeback demonstration. It is deliberately reversible:
    the shared instance is left exactly as found, so the capability can be proven
    repeatedly without accumulating demo residue.

    Restoration runs even when verification fails -- a write that landed but could
    not be confirmed still has to be rolled back, and the receipt records both
    outcomes separately.

    Raises:
        NamespaceViolation: if ``urn`` is outside this project's allocation.
    """
    require_in_namespace(urn, namespace, operation="reversible_tag_writeback")

    prior = list(client.get_tags(urn))
    applied = sorted(set(prior) | {tag})

    client.set_tags(urn, applied)

    # Immediate re-read: the write is not evidence until DataHub reports it back.
    observed = list(client.get_tags(urn))
    verified = tag in observed

    restored = False
    detail = ""
    try:
        client.set_tags(urn, prior)
        after_restore = list(client.get_tags(urn))
        restored = sorted(after_restore) == sorted(prior)
        if not restored:
            detail = f"restore left tags as {sorted(after_restore)}, expected {sorted(prior)}"
    except (DataHubError, NamespaceViolation) as exc:
        # Never swallow this. An unrestored write is residual state on a shared
        # instance and the receipt must carry it.
        detail = f"restore failed: {exc}"

    if verified and not detail:
        detail = "tag applied, re-read confirmed, prior state restored"
    elif not verified and not detail:
        detail = f"re-read did not observe {tag!r}; tags were {sorted(observed)}"

    return WritebackReceipt(
        urn=urn,
        aspect="globalTags",
        applied_value=tag,
        prior_value=prior,
        written_at=datetime.now(UTC),
        verified=verified,
        restored=restored,
        detail=detail,
    )


# -- parsing helpers ----------------------------------------------------


def _parse_possibly_streamed(text: str) -> dict[str, Any]:
    """Parse a JSON-RPC body that may arrive as an SSE stream.

    Streamable HTTP may return ``text/event-stream``; the useful payload is the
    last ``data:`` frame.
    """
    stripped = text.strip()
    if not stripped:
        raise DataHubError("MCP returned an empty body")

    if stripped.startswith("{"):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise DataHubError(f"MCP returned unparseable JSON: {exc}") from exc

    frames = [
        line[len("data:") :].strip()
        for line in stripped.splitlines()
        if line.startswith("data:")
    ]
    for frame in reversed(frames):
        try:
            return json.loads(frame)
        except json.JSONDecodeError:
            continue
    raise DataHubError("MCP stream contained no parseable data frame")


def _extract_entities(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Pull entity dicts out of an MCP tool result.

    MCP wraps tool output in a content envelope; the payload may be a JSON string
    inside a text block or already structured.
    """
    if "entities" in result:
        return list(result["entities"])

    for block in result.get("content", []):
        if block.get("type") != "text":
            continue
        try:
            parsed = json.loads(block.get("text", ""))
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and "entities" in parsed:
            return list(parsed["entities"])
        if isinstance(parsed, list):
            return parsed
    return []


def _to_entity_context(urn: str, raw: dict[str, Any]) -> EntityContext:
    tags = raw.get("tags") or []
    return EntityContext(
        urn=raw.get("urn", urn),
        entity_type=raw.get("entityType", raw.get("type", "unknown")),
        name=raw.get("name", ""),
        tags=tuple(_tag_name(t) for t in tags),
        domain=raw.get("domain"),
        owners=tuple(raw.get("owners") or ()),
        description=raw.get("description"),
        custom_properties=dict(raw.get("customProperties") or {}),
    )


def _to_lineage_edges(source_urn: str, result: dict[str, Any]) -> list[LineageEdge]:
    entries = result.get("relationships")
    if entries is None:
        for block in result.get("content", []):
            if block.get("type") != "text":
                continue
            try:
                parsed = json.loads(block.get("text", ""))
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict) and "relationships" in parsed:
                entries = parsed["relationships"]
                break
    entries = entries or []

    edges: list[LineageEdge] = []
    for entry in entries:
        downstream = entry.get("urn") or entry.get("entity")
        if not downstream:
            continue
        edges.append(
            LineageEdge(
                upstream_urn=entry.get("via", source_urn),
                downstream_urn=downstream,
                # DataHub can report a relationship whose entity it cannot resolve.
                # Treating that as complete would manufacture a false all-clear.
                resolved=entry.get("resolved", True),
            )
        )
    return edges


def _tag_name(raw: Any) -> str:
    """Normalize a tag to its bare name."""
    if isinstance(raw, dict):
        raw = raw.get("tag") or raw.get("name") or ""
    text = str(raw)
    return text.rsplit(":", 1)[-1] if text.startswith("urn:li:tag:") else text


def _extract_tag_names(body: dict[str, Any]) -> list[str]:
    aspect = body.get("aspect") or {}
    value = aspect.get("value")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    if not isinstance(value, dict):
        value = aspect
    tags = value.get("tags") or []
    return [_tag_name(t) for t in tags]


def _encode(urn: str) -> str:
    from urllib.parse import quote

    return quote(urn, safe="")
