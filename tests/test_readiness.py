"""Fail-closed readiness tests.

The property under test is that readiness reports ready *only* with positive proof
of every required condition. A reachable GMS, or an unrunnable check, must never
produce a green result.
"""

from __future__ import annotations

import pytest

from adapters.datahub import DataHubError
from adapters.fake_datahub import FakeDataHubClient
from app.config import Settings
from app.namespace import Namespace
from app.policy import get_policy
from app.readiness import evaluate_readiness
from demo.graph import SENTINEL_URN
from demo.seed import seed

NS = Namespace(
    project_slug="license-circuit-breaker",
    urn_prefix="license.",
    project_tag="project-license-circuit-breaker",
    domain="Demo / License Circuit Breaker",
)


@pytest.fixture
def settings(tmp_path) -> Settings:
    # Readiness no longer creates the directory -- it only checks it -- so the
    # fixture must create it the way application startup does.
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    return Settings(
        APP_STATE_DIR=str(state),
        DATAHUB_GMS_URL="http://localhost:8080",
        DATAHUB_MCP_URL="http://localhost:8000/mcp",
        DATAHUB_TOKEN="fixture-token-not-a-real-secret",
    )


@pytest.fixture
def seeded_client() -> FakeDataHubClient:
    client = FakeDataHubClient(namespace=NS)
    seed(client, NS)
    return client


def _evaluate(settings, client):
    return evaluate_readiness(settings, load_policy=get_policy, client_factory=lambda: client)


class TestReadyPath:
    def test_fully_configured_and_seeded_is_ready(self, settings, seeded_client):
        report = _evaluate(settings, seeded_client)
        assert report.ready, [c.detail for c in report.failures()]

    def test_reports_every_required_check(self, settings, seeded_client):
        report = _evaluate(settings, seeded_client)
        names = {c.name for c in report.checks}
        assert {
            "state_dir",
            "policy_table",
            "namespace_guard",
            "datahub_token",
            "datahub_endpoints",
            "mcp_capabilities",
            "project_controls",
            "entity_coverage",
            "fixture_lineage",
        } <= names


class TestFailClosed:
    def test_missing_token_is_not_ready(self, tmp_path, seeded_client):
        settings = Settings(
            APP_STATE_DIR=str(tmp_path),
            DATAHUB_GMS_URL="http://localhost:8080",
            DATAHUB_MCP_URL="http://localhost:8000/mcp",
            DATAHUB_TOKEN="",
        )
        report = _evaluate(settings, seeded_client)
        assert not report.ready
        assert any(c.name == "datahub_token" for c in report.failures())

    def test_missing_mcp_url_is_not_ready(self, tmp_path, seeded_client):
        settings = Settings(
            APP_STATE_DIR=str(tmp_path),
            DATAHUB_GMS_URL="http://localhost:8080",
            DATAHUB_MCP_URL="",
            DATAHUB_TOKEN="fixture-token",
        )
        report = _evaluate(settings, seeded_client)
        assert not report.ready
        assert any(c.name == "datahub_endpoints" for c in report.failures())

    def test_missing_mcp_tool_is_not_ready(self, settings, seeded_client):
        # A worker without get_lineage would silently produce an empty impact
        # analysis, which is indistinguishable from an all-clear.
        seeded_client.tools = frozenset({"search", "get_entities"})
        report = _evaluate(settings, seeded_client)
        assert not report.ready
        failure = next(c for c in report.failures() if c.name == "mcp_capabilities")
        assert "get_lineage" in failure.detail

    def test_unseeded_instance_is_not_ready(self, settings):
        # Reachable and authenticated, but our entities are absent.
        empty = FakeDataHubClient(namespace=NS)
        report = _evaluate(settings, empty)
        assert not report.ready
        assert any(c.name in ("project_controls", "entity_coverage") for c in report.failures())

    def test_sentinel_without_project_tag_is_not_ready(self, settings):
        client = FakeDataHubClient(namespace=NS)
        client.add_entity(SENTINEL_URN, tags=("some-other-tag",))
        report = _evaluate(settings, client)
        assert not report.ready
        assert any(c.name in ("project_controls", "entity_coverage") for c in report.failures())

    def test_sentinel_in_a_foreign_domain_is_not_ready(self, settings):
        client = FakeDataHubClient(namespace=NS)
        client.add_entity(
            SENTINEL_URN, tags=(NS.project_tag,), domain="Demo / Lineage Lifeboat"
        )
        report = _evaluate(settings, client)
        assert not report.ready

    def test_unconstructable_client_is_not_ready(self, settings):
        def _boom():
            raise DataHubError("no route to host")

        report = evaluate_readiness(settings, load_policy=get_policy, client_factory=_boom)
        assert not report.ready
        # Unrunnable checks must be reported failed, never skipped.
        assert any(c.name == "mcp_capabilities" and not c.passed for c in report.checks)
        assert any(c.name == "project_controls" and not c.passed for c in report.checks)

    def test_absent_client_factory_is_not_ready(self, settings):
        report = evaluate_readiness(settings, load_policy=get_policy, client_factory=None)
        assert not report.ready

    def test_read_failure_is_not_ready(self, settings, seeded_client):
        seeded_client.fail_next_read = True
        report = _evaluate(settings, seeded_client)
        assert not report.ready

    def test_broken_policy_table_is_not_ready(self, settings, seeded_client):
        def _boom():
            raise RuntimeError("rule table missing")

        report = evaluate_readiness(
            settings, load_policy=_boom, client_factory=lambda: seeded_client
        )
        assert not report.ready


class TestNonMutating:
    def test_readiness_issues_no_writes(self, settings, seeded_client):
        before = len(seeded_client.write_log)
        snapshot = {k: v.tags for k, v in seeded_client.entities.items()}

        _evaluate(settings, seeded_client)

        assert len(seeded_client.write_log) == before
        assert {k: v.tags for k, v in seeded_client.entities.items()} == snapshot

    def test_repeated_probes_do_not_accumulate_state(self, settings, seeded_client):
        for _ in range(10):
            _evaluate(settings, seeded_client)
        assert seeded_client.write_log == []
