"""MCP client following the coordinator-verified contract.

Uses ``mcp.ClientSession`` over ``streamablehttp_client`` with a real
``initialize()`` handshake, rather than hand-rolled JSON-RPC over ``httpx``. The
hand-rolled version skipped initialization entirely, which a conforming server is
entitled to reject.

Advertised tool schemas are introspected rather than assumed. Request shapes are
built from what the server actually declares -- notably ``max_results``, which is
clamped to the advertised maximum instead of a hardcoded constant, so a worker
with a smaller cap is respected instead of erroring.

Nothing here handles or logs the bearer token beyond passing it to the transport.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

#: Default page size for lineage requests. Clamped down when the server
#: advertises a smaller maximum.
DEFAULT_MAX_RESULTS = 100

#: Bound on lineage traversal depth. Unbounded traversal on a shared instance
#: can walk into another project's subgraph before the namespace filter runs.
DEFAULT_MAX_HOPS = 6


class McpError(Exception):
    """Raised when the MCP endpoint is unusable or returns an unusable response."""


class McpUnavailable(McpError):
    """Raised when the endpoint cannot be reached or the handshake fails."""


@dataclass(frozen=True)
class ToolSchema:
    """One tool as advertised by the server."""

    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)

    @property
    def properties(self) -> dict[str, Any]:
        return self.input_schema.get("properties", {}) or {}

    def accepts(self, argument: str) -> bool:
        """Whether the server declares this argument.

        Used to avoid sending arguments a given worker build does not understand.
        """
        return argument in self.properties

    def maximum_for(self, argument: str) -> int | None:
        """The advertised maximum for a numeric argument, if declared."""
        spec = self.properties.get(argument) or {}
        for key in ("maximum", "exclusiveMaximum"):
            value = spec.get(key)
            if isinstance(value, (int, float)):
                return int(value) - (1 if key == "exclusiveMaximum" else 0)
        return None

    def clamp(self, argument: str, requested: int) -> int:
        """Clamp ``requested`` to the advertised maximum for ``argument``."""
        advertised = self.maximum_for(argument)
        if advertised is None:
            return requested
        return min(requested, advertised)


def extract_payload(result: Any) -> Any:
    """Pull the useful payload out of an MCP tool result.

    Prefers ``structuredContent`` when the server provides it, then falls back to
    parsing JSON out of text content blocks. Returns ``None`` when neither yields
    anything parseable, so callers can fail closed rather than treat an
    unparseable response as an empty result.
    """
    structured = getattr(result, "structuredContent", None)
    if structured:
        return structured

    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if not text:
            continue
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            continue
    return None


class McpTransport:
    """Synchronous facade over an async MCP session.

    The application is synchronous; each call opens a session, performs the
    handshake, issues the request, and closes. Sessions are cheap relative to the
    demo's request volume, and holding one open across a long-lived FastAPI
    process would need reconnection handling this milestone does not require.
    """

    def __init__(self, url: str, token: str, timeout: float = 30.0) -> None:
        if not url:
            raise McpError("DATAHUB_MCP_URL is not configured")
        self._url = url
        self._token = token
        self._timeout = timeout
        self._schema_cache: dict[str, ToolSchema] | None = None

    @property
    def _headers(self) -> dict[str, str]:
        # The token is passed to the transport and never logged, stored, or echoed.
        return {"Authorization": f"Bearer {self._token}"} if self._token else {}

    @asynccontextmanager
    async def _session(self):
        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamable_http_client
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise McpError(
                "The 'mcp' package is required for live DataHub reads. "
                "Install with: pip install 'mcp>=1.13'"
            ) from exc

        try:
            async with streamable_http_client(self._url, headers=self._headers) as (
                read_stream,
                write_stream,
                _,
            ):
                async with ClientSession(read_stream, write_stream) as session:
                    # The handshake is mandatory. A conforming server may reject
                    # any request that arrives before it.
                    await session.initialize()
                    yield session
        except McpError:
            raise
        except Exception as exc:
            raise McpUnavailable(f"MCP session failed: {exc}") from exc

    async def _alist_tools(self) -> dict[str, ToolSchema]:
        async with self._session() as session:
            result = await session.list_tools()
            schemas: dict[str, ToolSchema] = {}
            for tool in result.tools:
                schemas[tool.name] = ToolSchema(
                    name=tool.name,
                    description=getattr(tool, "description", "") or "",
                    input_schema=dict(getattr(tool, "inputSchema", {}) or {}),
                )
            return schemas

    async def _acall(self, name: str, arguments: dict[str, Any]) -> Any:
        async with self._session() as session:
            result = await session.call_tool(name, arguments)
            if getattr(result, "isError", False):
                raise McpError(f"MCP tool {name!r} returned an error: {extract_payload(result)}")
            return extract_payload(result)

    # -- synchronous surface --------------------------------------------

    def list_tool_schemas(self, refresh: bool = False) -> dict[str, ToolSchema]:
        """Introspect advertised tools and their input schemas."""
        if self._schema_cache is None or refresh:
            self._schema_cache = _run(self._alist_tools())
        return self._schema_cache

    def tool_names(self) -> frozenset[str]:
        return frozenset(self.list_tool_schemas().keys())

    def schema_for(self, name: str) -> ToolSchema:
        schemas = self.list_tool_schemas()
        if name not in schemas:
            raise McpError(f"MCP endpoint does not advertise required tool {name!r}")
        return schemas[name]

    def call(self, name: str, arguments: dict[str, Any]) -> Any:
        """Call a tool, dropping arguments the server does not advertise."""
        schema = self.schema_for(name)
        filtered = {k: v for k, v in arguments.items() if schema.accepts(k)}
        return _run(self._acall(name, filtered))


def _run(coro):
    """Run a coroutine from synchronous code.

    Refuses to run inside an existing loop rather than silently deadlocking.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise McpError(
        "McpTransport cannot be called from inside a running event loop; "
        "use the async surface directly."
    )
