"""MCP client following the coordinator-verified contract.

Uses ``mcp.ClientSession`` over ``streamable_http_client`` with a real
``initialize()`` handshake, rather than hand-rolled JSON-RPC over ``httpx``. The
hand-rolled version skipped initialization entirely, which a conforming server is
entitled to reject.

Advertised tool schemas are introspected rather than assumed. Request shapes are
built from what the server actually declares -- notably ``max_results``, which is
clamped to the advertised maximum instead of a hardcoded constant, so a worker
with a smaller cap is respected instead of erroring.

**Transport configuration.** ``streamable_http_client`` in ``mcp`` 1.28 takes
``(url, *, http_client=None, terminate_on_close=True)``. It no longer accepts
``headers=``, ``timeout=``, or ``sse_read_timeout=``: everything HTTP-shaped now
comes from an ``httpx.AsyncClient`` the caller supplies. Passing ``headers=``
raises ``TypeError`` before a single request is made, which is what a live
readiness probe hit.

The client is ours to close. The transport manages the lifecycle *only* of a
client it created itself -- pass one in and it will not close it -- so it is
opened in an ``async with`` that unwinds on success, on handshake failure, and on
an exception thrown back into the session by the caller.

Nothing here handles or logs the bearer token beyond passing it to the transport.
Anything raised out of a session is scrubbed of it first: httpx exceptions can
carry request context, and this text reaches receipts and readiness output.
"""

from __future__ import annotations

import asyncio
import json
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from app.receipts import REDACTED

#: Default page size for lineage requests. Clamped down when the server
#: advertises a smaller maximum.
DEFAULT_MAX_RESULTS = 100

#: Bound on lineage traversal depth. Unbounded traversal on a shared instance
#: can walk into another project's subgraph before the namespace filter runs.
DEFAULT_MAX_HOPS = 6

#: Per-request budget for ordinary operations, in seconds.
DEFAULT_TIMEOUT = 30.0

#: Read budget for the long-lived SSE stream, in seconds. Deliberately far
#: larger than :data:`DEFAULT_TIMEOUT`: the GET stream stays open between
#: messages, and applying the request timeout to it would tear down a healthy
#: session on an idle server. Matches ``mcp``'s own default.
DEFAULT_SSE_READ_TIMEOUT = 300.0

#: Any ``Authorization`` header value, wherever it appears in a message.
_BEARER = re.compile(r"(Bearer\s+)\S+", re.IGNORECASE)


def _leaf_causes(exc: BaseException) -> list[str]:
    """Flatten an exception into the leaf failures an operator can act on.

    The transport runs inside an ``anyio`` task group, so a refused connection
    surfaces as ``ExceptionGroup: unhandled errors in a TaskGroup (1
    sub-exception)`` -- true, and useless in a readiness report. The leaves carry
    the actual cause.
    """
    if isinstance(exc, BaseExceptionGroup):
        causes: list[str] = []
        for sub in exc.exceptions:
            causes.extend(_leaf_causes(sub))
        return causes
    return [f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__]


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

    def __init__(
        self,
        url: str,
        token: str,
        timeout: float = DEFAULT_TIMEOUT,
        sse_read_timeout: float = DEFAULT_SSE_READ_TIMEOUT,
    ) -> None:
        if not url:
            raise McpError("DATAHUB_MCP_URL is not configured")
        self._url = url
        self._token = token
        self._timeout = timeout
        self._sse_read_timeout = sse_read_timeout
        self._schema_cache: dict[str, ToolSchema] | None = None

    @property
    def _headers(self) -> dict[str, str]:
        # The token is passed to the transport and never logged, stored, or echoed.
        return {"Authorization": f"Bearer {self._token}"} if self._token else {}

    def _redact(self, value: object) -> str:
        """Render ``value`` as text with the bearer token removed.

        Two passes, because either alone leaves a hole. The literal token is
        replaced wherever it appears, which covers a message that quotes the
        credential without the ``Bearer`` prefix; and any ``Authorization``-style
        value is replaced, which covers a token this transport never saw.
        """
        text = str(value)
        if self._token:
            text = text.replace(self._token, REDACTED)
        return _BEARER.sub(rf"\1{REDACTED}", text)

    def _build_http_client(self, httpx_module: Any) -> Any:
        """Build the client the transport will use.

        ``follow_redirects`` is not a preference: ``mcp`` sets it on every client
        it builds, and a transport that stops following redirects fails against a
        server that issues one. The split timeout is likewise deliberate -- see
        :data:`DEFAULT_SSE_READ_TIMEOUT`.
        """
        return httpx_module.AsyncClient(
            headers=self._headers,
            timeout=httpx_module.Timeout(self._timeout, read=self._sse_read_timeout),
            follow_redirects=True,
        )

    @asynccontextmanager
    async def _session(self):
        try:
            import httpx
            from mcp import ClientSession
            from mcp.client.streamable_http import streamable_http_client
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise McpError(
                "The 'mcp' package is required for live DataHub reads. "
                "Install with: pip install 'mcp>=1.13'"
            ) from exc

        try:
            # We construct the client, so we close it. `streamable_http_client`
            # enters the client's context only when it built the client itself,
            # so a passed-in one leaks its connection pool unless closed here.
            async with self._build_http_client(httpx) as http_client:
                async with streamable_http_client(self._url, http_client=http_client) as (
                    read_stream,
                    write_stream,
                    _,
                ):
                    async with ClientSession(read_stream, write_stream) as session:
                        # The handshake is mandatory. A conforming server may
                        # reject any request that arrives before it.
                        await session.initialize()
                        yield session
        except McpError:
            raise
        except Exception as exc:
            detail = self._redact("; ".join(_leaf_causes(exc)))
            raise McpUnavailable(f"MCP session failed: {detail}") from exc

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
                # Server-supplied text, scrubbed on the same terms as any other
                # message that leaves this module.
                detail = self._redact(extract_payload(result))
                raise McpError(f"MCP tool {name!r} returned an error: {detail}")
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
