"""MCP client contract regressions.

Two contract breaks are pinned here, both found by a live gate rather than by
this suite.

The first: the implementation hand-rolled JSON-RPC over httpx and skipped the
``initialize`` handshake entirely, which a conforming server may reject.

The second: ``mcp`` 1.28 changed ``streamable_http_client`` to take an
``httpx.AsyncClient`` instead of ``headers=``/``timeout=``. The call site was not
updated, so every session raised ``TypeError`` before issuing a request. The
suite could not catch it because it never touched the transport -- it asserted on
*source text*. The tests below bind against ``inspect.signature`` of the
installed ``mcp``, and one drives the genuine transport with no patching at all.
"""

from __future__ import annotations

import contextlib
import inspect
from types import SimpleNamespace

import pytest

from adapters.datahub import LiveDataHubClient
from adapters.mcp_client import (
    DEFAULT_MAX_HOPS,
    DEFAULT_MAX_RESULTS,
    DEFAULT_SSE_READ_TIMEOUT,
    DEFAULT_TIMEOUT,
    McpError,
    McpTransport,
    McpUnavailable,
    ToolSchema,
    extract_payload,
)
from app.namespace import Namespace

NS = Namespace(
    project_slug="license-circuit-breaker",
    urn_prefix="license.",
    project_tag="project-license-circuit-breaker",
    domain="Demo / License Circuit Breaker",
)
SOURCE = "urn:li:dataset:(urn:li:dataPlatform:duckdb,license.reviews.partner_feed,PROD)"


class TestUsesTheOfficialSdk:
    def test_session_uses_client_session_and_streamable_http(self):
        # Pins the two symbols the coordinator-verified contract names. A
        # hand-rolled transport would not import these at all.
        import inspect

        import adapters.mcp_client as module

        source = inspect.getsource(module.McpTransport._session.__wrapped__)
        assert "ClientSession" in source
        assert "streamable_http_client" in source

    def test_session_performs_initialization(self):
        import inspect

        import adapters.mcp_client as module

        source = inspect.getsource(module.McpTransport._session.__wrapped__)
        assert "session.initialize()" in source

    def test_missing_url_is_refused(self):
        with pytest.raises(McpError, match="DATAHUB_MCP_URL"):
            McpTransport("", "token")


class TestSchemaIntrospection:
    def test_reports_declared_arguments(self):
        schema = ToolSchema(
            name="get_lineage",
            input_schema={"properties": {"urn": {}, "max_hops": {}, "max_results": {}}},
        )
        assert schema.accepts("max_hops")
        assert not schema.accepts("nonexistent")

    def test_reads_the_advertised_maximum(self):
        schema = ToolSchema(
            name="get_lineage",
            input_schema={"properties": {"max_results": {"type": "integer", "maximum": 50}}},
        )
        assert schema.maximum_for("max_results") == 50

    def test_clamps_to_a_smaller_advertised_maximum(self):
        # A worker with a smaller cap must be respected, not overridden with a
        # hardcoded 100 that the server would reject.
        schema = ToolSchema(
            name="get_lineage",
            input_schema={"properties": {"max_results": {"maximum": 25}}},
        )
        assert schema.clamp("max_results", 100) == 25

    def test_does_not_raise_a_request_above_the_default(self):
        schema = ToolSchema(
            name="get_lineage",
            input_schema={"properties": {"max_results": {"maximum": 500}}},
        )
        assert schema.clamp("max_results", 100) == 100

    def test_absent_maximum_leaves_the_request_unchanged(self):
        schema = ToolSchema(name="get_lineage", input_schema={"properties": {"max_results": {}}})
        assert schema.clamp("max_results", 100) == 100

    def test_exclusive_maximum_is_respected(self):
        schema = ToolSchema(
            name="get_lineage",
            input_schema={"properties": {"max_results": {"exclusiveMaximum": 51}}},
        )
        assert schema.clamp("max_results", 100) == 50


class _Block:
    """Stands in for an MCP text content block."""

    def __init__(self, text: str):
        self.type = "text"
        self.text = text


class _Result:
    """Stands in for an MCP tool result."""

    def __init__(self, structured=None, blocks=()):
        self.structuredContent = structured
        self.content = list(blocks)


class TestPayloadExtraction:
    def test_prefers_structured_content(self):
        result = _Result(structured={"entities": [{"urn": "a"}]})
        assert extract_payload(result) == {"entities": [{"urn": "a"}]}

    def test_falls_back_to_json_in_text_blocks(self):
        result = _Result(blocks=[_Block('{"entities": [{"urn": "b"}]}')])
        assert extract_payload(result) == {"entities": [{"urn": "b"}]}

    def test_unparseable_payload_returns_none_so_callers_fail_closed(self):
        # None lets callers fail closed rather than mistaking an unreadable
        # response for an empty result set.
        assert extract_payload(_Result(blocks=[_Block("not json")])) is None


class _RecordingTransport:
    """Captures the request shape without opening a session."""

    def __init__(self, lineage_schema: ToolSchema | None = None):
        self.calls: list[tuple[str, dict]] = []
        self._schemas = {
            "get_lineage": lineage_schema
            or ToolSchema(
                name="get_lineage",
                input_schema={
                    "properties": {
                        "urn": {},
                        "upstream": {},
                        "max_hops": {},
                        "max_results": {"maximum": 100},
                    }
                },
            ),
            "get_entities": ToolSchema(
                name="get_entities", input_schema={"properties": {"urns": {}}}
            ),
            "search": ToolSchema(name="search", input_schema={"properties": {"query": {}}}),
        }

    def tool_names(self):
        return frozenset(self._schemas)

    def schema_for(self, name):
        return self._schemas[name]

    def call(self, name, arguments):
        schema = self._schemas[name]
        filtered = {k: v for k, v in arguments.items() if schema.accepts(k)}
        self.calls.append((name, filtered))
        return {"entities": [], "relationships": []}


@pytest.fixture
def client() -> LiveDataHubClient:
    live = LiveDataHubClient(
        gms_url="http://localhost:8080",
        mcp_url="http://localhost:8000/mcp",
        token="fixture-token",
        namespace=NS,
    )
    live._transport = _RecordingTransport()
    return live


class TestLineageRequestShape:
    def test_requests_downstream_explicitly(self, client):
        # Some builds default to upstream. Relying on the default would silently
        # return ancestors and produce an empty impact set.
        client.get_downstream_lineage(SOURCE)
        _, args = client._transport.calls[0]
        assert args["upstream"] is False

    def test_bounds_max_hops(self, client):
        client.get_downstream_lineage(SOURCE, max_depth=999)
        _, args = client._transport.calls[0]
        assert args["max_hops"] <= DEFAULT_MAX_HOPS

    def test_requests_the_documented_page_size(self, client):
        client.get_downstream_lineage(SOURCE)
        _, args = client._transport.calls[0]
        assert args["max_results"] == DEFAULT_MAX_RESULTS

    def test_clamps_page_size_to_a_smaller_advertised_maximum(self):
        live = LiveDataHubClient(
            gms_url="http://localhost:8080",
            mcp_url="http://localhost:8000/mcp",
            token="fixture-token",
            namespace=NS,
        )
        live._transport = _RecordingTransport(
            ToolSchema(
                name="get_lineage",
                input_schema={
                    "properties": {
                        "urn": {},
                        "upstream": {},
                        "max_hops": {},
                        "max_results": {"maximum": 20},
                    }
                },
            )
        )
        live.get_downstream_lineage(SOURCE)
        _, args = live._transport.calls[0]
        assert args["max_results"] == 20

    def test_unadvertised_arguments_are_dropped(self):
        live = LiveDataHubClient(
            gms_url="http://localhost:8080",
            mcp_url="http://localhost:8000/mcp",
            token="fixture-token",
            namespace=NS,
        )
        live._transport = _RecordingTransport(
            ToolSchema(name="get_lineage", input_schema={"properties": {"urn": {}}})
        )
        live.get_downstream_lineage(SOURCE)
        _, args = live._transport.calls[0]
        assert set(args) == {"urn"}


class TestBatching:
    def test_get_entities_issues_one_call_for_many_urns(self, client):
        urns = [f"urn:li:dataset:(urn:li:dataPlatform:duckdb,license.d{i},PROD)" for i in range(12)]
        client.get_entities(urns)
        assert len(client._transport.calls) == 1
        _, args = client._transport.calls[0]
        assert len(args["urns"]) == 12

    def test_empty_batch_issues_no_call(self, client):
        assert client.get_entities([]) == {}
        assert client._transport.calls == []


class TestConfigurationGuards:
    def test_missing_token_is_refused(self):
        with pytest.raises(Exception, match="DATAHUB_TOKEN"):
            LiveDataHubClient(
                gms_url="http://localhost:8080",
                mcp_url="http://localhost:8000/mcp",
                token="",
                namespace=NS,
            )

    def test_missing_gms_url_is_refused(self):
        with pytest.raises(Exception, match="DATAHUB_GMS_URL"):
            LiveDataHubClient(
                gms_url="",
                mcp_url="http://localhost:8000/mcp",
                token="fixture-token",
                namespace=NS,
            )


class _FakeSession:
    """Minimal stand-in for ``mcp.ClientSession``."""

    def __init__(self, read_stream=None, write_stream=None):
        self.initialized = False
        self.tools_listed = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def initialize(self):
        self.initialized = True

    async def list_tools(self):
        self.tools_listed += 1
        return SimpleNamespace(tools=[])


def _install_fake_transport(monkeypatch, recorder: dict, *, boom: Exception | None = None):
    """Patch the transport with a spy that enforces the *real* signature.

    The spy binds whatever it is handed against
    ``inspect.signature(streamable_http_client)`` taken from the installed
    ``mcp``. A mock that accepted anything would have let the ``headers=``
    regression through exactly as the old suite did.
    """
    import mcp.client.streamable_http as transport_module

    real_signature = inspect.signature(transport_module.streamable_http_client)

    @contextlib.asynccontextmanager
    async def spy(url, **kwargs):
        real_signature.bind(url, **kwargs)  # TypeError on any argument mcp dropped
        recorder["url"] = url
        recorder["kwargs"] = kwargs
        recorder["http_client"] = kwargs.get("http_client")
        if boom is not None:
            raise boom
        yield (None, None, None)

    monkeypatch.setattr(transport_module, "streamable_http_client", spy)
    monkeypatch.setattr("mcp.ClientSession", _FakeSession)


class TestTransportSignatureMatchesInstalledMcp:
    """The defect: ``streamable_http_client`` dropped ``headers=`` in mcp 1.28.

    ``adapters/mcp_client.py`` kept passing it, so every live session raised
    ``TypeError: streamable_http_client() got an unexpected keyword argument
    'headers'`` before a request was made. MCP verification and readiness failed
    against a correctly seeded instance -- all 12 fixtures were active.

    These bind against the installed signature rather than describing it.
    """

    def test_installed_signature_rejects_headers(self):
        from mcp.client.streamable_http import streamable_http_client

        signature = inspect.signature(streamable_http_client)
        with pytest.raises(TypeError, match="headers"):
            signature.bind("http://mcp.invalid/mcp", headers={"Authorization": "Bearer x"})

    def test_installed_signature_accepts_http_client(self):
        import httpx
        from mcp.client.streamable_http import streamable_http_client

        signature = inspect.signature(streamable_http_client)
        bound = signature.bind("http://mcp.invalid/mcp", http_client=httpx.AsyncClient())
        assert "http_client" in bound.arguments

    def test_the_session_binds_against_the_real_signature(self, monkeypatch):
        recorder: dict = {}
        _install_fake_transport(monkeypatch, recorder)

        McpTransport("http://mcp.invalid/mcp", "fixture-token").list_tool_schemas()

        assert recorder["kwargs"].keys() == {"http_client"}
        assert "headers" not in recorder["kwargs"]

    def test_the_url_is_passed_positionally(self, monkeypatch):
        recorder: dict = {}
        _install_fake_transport(monkeypatch, recorder)

        McpTransport("http://mcp.invalid/mcp", "fixture-token").list_tool_schemas()

        assert recorder["url"] == "http://mcp.invalid/mcp"

    def test_the_session_reaches_the_real_transport_without_a_type_error(self):
        """Calls the genuine mcp function, with no patching at all.

        Port 1 has nothing on it, so this fails -- the point is *how*. A
        signature mismatch surfaces here as an unexpected-keyword ``TypeError``
        rather than a connection failure, which is precisely the defect.
        """
        transport = McpTransport(
            "http://127.0.0.1:1/mcp", "fixture-token", timeout=1.0, sse_read_timeout=2.0
        )

        with pytest.raises(McpUnavailable) as excinfo:
            transport.list_tool_schemas()

        message = str(excinfo.value)
        assert "unexpected keyword argument" not in message, (
            f"the transport call does not match the installed mcp signature: {message}"
        )


class TestHttpClientConfiguration:
    def test_the_token_travels_as_a_bearer_header(self):
        import httpx

        client = McpTransport("http://mcp.invalid/mcp", "fixture-token")._build_http_client(httpx)

        assert client.headers["authorization"] == "Bearer fixture-token"

    def test_no_authorization_header_without_a_token(self):
        import httpx

        client = McpTransport("http://mcp.invalid/mcp", "")._build_http_client(httpx)

        assert "authorization" not in client.headers

    def test_the_request_timeout_is_applied(self):
        # The old code stored a timeout and passed it nowhere.
        import httpx

        client = McpTransport(
            "http://mcp.invalid/mcp", "fixture-token", timeout=12.0
        )._build_http_client(httpx)

        assert client.timeout.connect == 12.0

    def test_the_sse_read_timeout_is_separate_and_longer(self):
        """A GET stream idles between messages; the request timeout would kill it."""
        import httpx

        client = McpTransport(
            "http://mcp.invalid/mcp", "fixture-token", timeout=12.0, sse_read_timeout=250.0
        )._build_http_client(httpx)

        assert client.timeout.read == 250.0
        assert client.timeout.read > client.timeout.connect

    def test_redirects_are_followed(self):
        # mcp sets this on every client it builds; one that does not follow
        # redirects fails against a server that issues one.
        import httpx

        client = McpTransport("http://mcp.invalid/mcp", "fixture-token")._build_http_client(httpx)

        assert client.follow_redirects is True

    def test_defaults_match_mcps_own(self):
        from mcp.shared._httpx_utils import MCP_DEFAULT_SSE_READ_TIMEOUT, MCP_DEFAULT_TIMEOUT

        assert DEFAULT_TIMEOUT == MCP_DEFAULT_TIMEOUT
        assert DEFAULT_SSE_READ_TIMEOUT == MCP_DEFAULT_SSE_READ_TIMEOUT


class TestHttpClientLifecycle:
    """We pass the client in, so mcp will not close it -- we must.

    ``streamable_http_client`` enters the client's context only when it built the
    client itself. A passed-in client that is never closed leaks its connection
    pool once per session, and this transport opens one session per call.
    """

    def test_the_client_is_closed_after_a_successful_session(self, monkeypatch):
        recorder: dict = {}
        _install_fake_transport(monkeypatch, recorder)

        McpTransport("http://mcp.invalid/mcp", "fixture-token").list_tool_schemas()

        assert recorder["http_client"].is_closed, "the http client leaked"

    def test_the_client_is_closed_when_the_handshake_fails(self, monkeypatch):
        recorder: dict = {}
        _install_fake_transport(monkeypatch, recorder, boom=RuntimeError("handshake refused"))

        with pytest.raises(McpUnavailable):
            McpTransport("http://mcp.invalid/mcp", "fixture-token").list_tool_schemas()

        assert recorder["http_client"].is_closed, "the http client leaked on the failure path"

    def test_each_session_gets_its_own_client(self, monkeypatch):
        seen: list = []

        import mcp.client.streamable_http as transport_module

        real_signature = inspect.signature(transport_module.streamable_http_client)

        @contextlib.asynccontextmanager
        async def spy(url, **kwargs):
            real_signature.bind(url, **kwargs)
            seen.append(kwargs["http_client"])
            yield (None, None, None)

        monkeypatch.setattr(transport_module, "streamable_http_client", spy)
        monkeypatch.setattr("mcp.ClientSession", _FakeSession)

        transport = McpTransport("http://mcp.invalid/mcp", "fixture-token")
        transport.list_tool_schemas()
        transport.list_tool_schemas(refresh=True)

        assert len(seen) == 2
        assert seen[0] is not seen[1]
        assert all(client.is_closed for client in seen)

    def test_the_handshake_still_runs(self, monkeypatch):
        recorder: dict = {}
        _install_fake_transport(monkeypatch, recorder)
        created: list = []

        class _Recording(_FakeSession):
            def __init__(self, *args):
                super().__init__(*args)
                created.append(self)

        monkeypatch.setattr("mcp.ClientSession", _Recording)

        McpTransport("http://mcp.invalid/mcp", "fixture-token").list_tool_schemas()

        assert created and created[0].initialized, "the handshake was skipped"


class TestSecretRedaction:
    """The bearer token must not reach a message, a receipt, or a readiness report."""

    def test_the_token_is_scrubbed_from_a_session_failure(self, monkeypatch):
        recorder: dict = {}
        secret = "fixture-token-abcdef123456"
        _install_fake_transport(
            monkeypatch, recorder, boom=RuntimeError(f"refused for token {secret}")
        )

        with pytest.raises(McpUnavailable) as excinfo:
            McpTransport("http://mcp.invalid/mcp", secret).list_tool_schemas()

        assert secret not in str(excinfo.value)
        assert "[REDACTED]" in str(excinfo.value)

    def test_an_authorization_value_is_scrubbed_even_when_it_is_not_ours(self, monkeypatch):
        recorder: dict = {}
        _install_fake_transport(
            monkeypatch, recorder, boom=RuntimeError("sent Bearer someone-elses-token")
        )

        with pytest.raises(McpUnavailable) as excinfo:
            McpTransport("http://mcp.invalid/mcp", "fixture-token").list_tool_schemas()

        assert "someone-elses-token" not in str(excinfo.value)

    def test_redaction_leaves_clean_text_alone(self):
        transport = McpTransport("http://mcp.invalid/mcp", "fixture-token")

        assert transport._redact("connection refused") == "connection refused"


class TestFailureReporting:
    def test_a_task_group_failure_names_its_leaf_cause(self, monkeypatch):
        """``unhandled errors in a TaskGroup`` is true and useless in a report."""
        recorder: dict = {}
        group = ExceptionGroup("unhandled errors in a TaskGroup", [ConnectionRefusedError("boom")])
        _install_fake_transport(monkeypatch, recorder, boom=group)

        with pytest.raises(McpUnavailable) as excinfo:
            McpTransport("http://mcp.invalid/mcp", "fixture-token").list_tool_schemas()

        message = str(excinfo.value)
        assert "ConnectionRefusedError" in message
        assert "boom" in message

    def test_nested_groups_are_flattened(self, monkeypatch):
        recorder: dict = {}
        inner = ExceptionGroup("inner", [ValueError("deep cause")])
        _install_fake_transport(monkeypatch, recorder, boom=ExceptionGroup("outer", [inner]))

        with pytest.raises(McpUnavailable) as excinfo:
            McpTransport("http://mcp.invalid/mcp", "fixture-token").list_tool_schemas()

        assert "deep cause" in str(excinfo.value)

    def test_a_transport_failure_is_never_reported_as_success(self, monkeypatch):
        """Fail closed: an unusable endpoint must raise, not return an empty tool set."""
        recorder: dict = {}
        _install_fake_transport(monkeypatch, recorder, boom=RuntimeError("down"))

        with pytest.raises(McpUnavailable):
            McpTransport("http://mcp.invalid/mcp", "fixture-token").tool_names()
