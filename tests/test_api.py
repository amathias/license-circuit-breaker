"""API contract tests for the shared coordinator endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import INTERNAL_PORT, get_settings, reset_settings_cache
from app.main import app


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    """Point state at a temp dir so readiness probes never touch real fixtures."""
    monkeypatch.setenv("APP_STATE_DIR", str(tmp_path / "state"))
    reset_settings_cache()
    yield
    reset_settings_cache()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


class TestHealth:
    def test_returns_ok(self, client):
        response = client.get("/api/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["project_slug"] == "license-circuit-breaker"

    def test_does_not_require_datahub(self, client, monkeypatch):
        # Liveness must stay green when the shared instance is down, or the
        # proxy will restart a perfectly healthy process.
        monkeypatch.setenv("DATAHUB_GMS_URL", "")
        reset_settings_cache()
        assert client.get("/api/health").status_code == 200


class TestReadiness:
    def test_reports_every_required_check(self, client):
        body = client.get("/api/readiness").json()
        names = {c["name"] for c in body["checks"]}
        assert {
            "state_dir",
            "policy_table",
            "namespace_guard",
            "datahub_token",
            "datahub_endpoints",
            "mcp_capabilities",
            "project_entities",
        } <= names

    def test_degraded_when_token_missing(self, client, monkeypatch):
        monkeypatch.setenv("DATAHUB_TOKEN", "")
        reset_settings_cache()
        body = client.get("/api/readiness").json()
        assert body["status"] == "degraded"
        token = next(c for c in body["checks"] if c["name"] == "datahub_token")
        assert not token["passed"]

    def test_degraded_when_endpoints_unconfigured(self, client, monkeypatch):
        monkeypatch.setenv("DATAHUB_GMS_URL", "")
        monkeypatch.setenv("DATAHUB_MCP_URL", "")
        reset_settings_cache()
        body = client.get("/api/readiness").json()
        assert body["status"] == "degraded"
        endpoints = next(c for c in body["checks"] if c["name"] == "datahub_endpoints")
        assert not endpoints["passed"]

    def test_unreachable_datahub_is_never_ready(self, client, monkeypatch):
        # A configured but unreachable instance must fail closed rather than
        # falling back to a locally-green result.
        monkeypatch.setenv("APP_ENV", "live")
        monkeypatch.setenv("DATAHUB_GMS_URL", "http://127.0.0.1:1")
        monkeypatch.setenv("DATAHUB_MCP_URL", "http://127.0.0.1:1/mcp")
        monkeypatch.setenv("DATAHUB_TOKEN", "fixture-token")
        reset_settings_cache()
        body = client.get("/api/readiness").json()
        assert body["status"] == "degraded"

    def test_offline_mode_is_flagged_as_simulated(self, client, monkeypatch):
        monkeypatch.setenv("APP_ENV", "offline")
        reset_settings_cache()
        body = client.get("/api/readiness").json()
        assert body["simulated"] is True

    def test_reports_the_enforced_namespace(self, client):
        body = client.get("/api/readiness").json()
        assert body["namespace"]["urn_prefix"] == "license."
        assert body["namespace"]["project_tag"] == "project-license-circuit-breaker"

    def test_does_not_mutate_shared_state(self, client, monkeypatch):
        # A readiness probe that wrote to DataHub would corrupt four other
        # submissions every time the proxy polled it.
        def _fail(*args, **kwargs):
            raise AssertionError("readiness must not issue write requests")

        monkeypatch.setattr("httpx.post", _fail)
        monkeypatch.setattr("httpx.put", _fail)
        monkeypatch.setattr("httpx.delete", _fail)
        assert client.get("/api/readiness").status_code == 200


class TestPolicyEndpoint:
    def test_exposes_the_rule_table(self, client):
        body = client.get("/api/policy/rules").json()
        ids = [r["id"] for r in body["rules"]]
        assert "LCB-R001" in ids
        assert len(ids) == len(set(ids))

    def test_rules_are_precedence_ordered(self, client):
        body = client.get("/api/policy/rules").json()
        precedences = [r["precedence"] for r in body["rules"]]
        assert precedences == sorted(precedences)


class TestAllocation:
    def test_default_port_matches_coordinator_assignment(self):
        assert INTERNAL_PORT == 8102
        assert get_settings().app_port == 8102

    def test_default_namespace_matches_coordinator_registry(self):
        settings = get_settings()
        assert settings.datahub_urn_prefix == "license."
        assert settings.datahub_project_tag == "project-license-circuit-breaker"
        assert settings.datahub_domain == "Demo / License Circuit Breaker"
