"""MCP client contract regressions.

The earlier implementation hand-rolled JSON-RPC over httpx and skipped the
``initialize`` handshake entirely, which a conforming server may reject. These
tests pin the parts of the contract that were wrong.
"""

from __future__ import annotations

import pytest

from adapters.datahub import LiveDataHubClient
from adapters.mcp_client import (
    DEFAULT_MAX_HOPS,
    DEFAULT_MAX_RESULTS,
    McpError,
    McpTransport,
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
