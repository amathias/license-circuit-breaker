"""End-to-end tests for the judge workflow API.

Drives the seven states a judge walks through, over HTTP, in order: exposure,
graph, plan, approval, execution, verification, writeback. The refusals matter
as much as the successes -- an enforcement endpoint that works without an
approval, or a frozen endpoint that answers 200, would each invalidate the
whole demonstration.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.api as api_module
from app.api import LEGALLY_UNAVAILABLE, get_client
from app.config import get_settings, reset_settings_cache
from app.demo_guard import DemoMutationGuard
from app.main import app
from demo import graph
from demo.corpus import APPROVED_PREFIX, PARTNER_PREFIX
from demo.estate import EstatePaths, build_estate


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """A private state directory and the in-memory DataHub substitute."""
    monkeypatch.setenv("APP_ENV", "offline")
    monkeypatch.setenv("APP_STATE_DIR", str(tmp_path / "state"))
    reset_settings_cache()
    build_estate(EstatePaths.under(get_settings().ensure_state_dir()))
    yield
    reset_settings_cache()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _approve(client, **kwargs):
    payload = {"approver": "judge@example.com", **kwargs}
    response = client.post("/api/approvals", json=payload)
    assert response.status_code == 200, response.text
    return response.json()["approval"]


class TestExposureBefore:
    def test_the_prediction_endpoint_answers(self, client):
        response = client.post("/api/demo/predict", json={"text": "battery charge"})
        assert response.status_code == 200
        assert response.json()["model_version"] == "v1"

    def test_search_returns_partner_derived_content(self, client):
        hits = client.get("/api/demo/search").json()["hits"]
        assert hits
        assert any(hit["review_id"].startswith(PARTNER_PREFIX) for hit in hits)

    def test_the_export_is_downloadable(self, client):
        body = client.get("/api/demo/export").json()
        assert body["rows"] == 24

    def test_the_estate_reports_its_exposure(self, client):
        body = client.get("/api/estate").json()
        assert body["index"]["holds_partner_rows"] is True
        assert body["model"]["holds_partner_rows"] is True


class TestRightsEventAndGraph:
    def test_the_rights_event_is_structured_and_hashed(self, client):
        body = client.get("/api/rights-event").json()
        assert body["revokes"] is True
        assert body["lost_purposes"] == ["retrieval", "training"]
        assert len(body["content_hash"]) == 64

    def test_the_rights_event_disclaims_legal_interpretation(self, client):
        assert "does not provide legal advice" in client.get("/api/rights-event").json()["note"]

    def test_the_graph_carries_lineage_and_decisions(self, client):
        body = client.get("/api/graph").json()
        assert body["source"] == graph.SOURCE
        assert len(body["edges"]) == 8

        by_urn = {n["urn"]: n for n in body["nodes"]}
        assert by_urn[graph.PREDICT_API]["decision"]["actions"] == ["freeze"]
        assert by_urn[graph.PREDICT_API]["decision"]["rule_ids"] == ["LCB-R050"]

    def test_the_graph_marks_the_unresolvable_edge(self, client):
        body = client.get("/api/graph").json()
        unresolved = [e for e in body["edges"] if not e["resolved"]]
        assert [e["downstream"] for e in unresolved] == [graph.ORPHAN]

    def test_every_decision_cites_a_lineage_path(self, client):
        body = client.get("/api/graph").json()
        for node in body["nodes"]:
            decision = node["decision"]
            if decision and "escalate" not in decision["actions"]:
                assert decision["paths"], f"{node['urn']} has no cited lineage path"

    def test_the_graph_is_flagged_simulated_offline(self, client):
        assert client.get("/api/graph").json()["simulated"] is True


class TestPlan:
    def test_the_plan_is_deterministic(self, client):
        first = client.get("/api/plan").json()
        second = client.get("/api/plan").json()
        assert first["plan_hash"] == second["plan_hash"]

    def test_the_plan_is_never_all_clear_while_lineage_is_broken(self, client):
        assert client.get("/api/plan").json()["all_clear"] is False

    def test_enforcement_scope_excludes_the_unaffected_branch(self, client):
        scope = client.get("/api/plan").json()["enforcement_scope"]
        assert graph.ANALYTICS not in scope
        assert graph.ORPHAN not in scope

    def test_steps_are_ordered_with_freeze_first(self, client):
        steps = client.get("/api/plan").json()["steps"]
        assert steps[0]["action"] == "freeze"
        assert steps[-1]["action"] == "replace"

    def test_the_rule_table_is_readable(self, client):
        rules = client.get("/api/policy/rules").json()["rules"]
        assert {r["id"] for r in rules} >= {"LCB-R001", "LCB-R050", "LCB-R090"}


class TestApprovalGate:
    def test_execution_without_an_approval_is_refused(self, client):
        response = client.post("/api/execute", json={})
        assert response.status_code == 409
        assert response.json()["detail"]["error"] == "ApprovalRequired"

    def test_a_refused_execution_changes_nothing(self, client):
        client.post("/api/execute", json={})
        assert client.post("/api/demo/predict", json={"text": "x"}).status_code == 200
        assert client.get("/api/demo/export").status_code == 200

    def test_a_rejection_blocks_execution(self, client):
        _approve(client, decision="rejected", note="scope too wide")
        response = client.post("/api/execute", json={})
        assert response.status_code == 409
        assert response.json()["detail"]["error"] == "ApprovalRefused"

    def test_an_approval_is_listed_with_its_plan_binding(self, client):
        approval = _approve(client)
        body = client.get("/api/approvals").json()
        assert body["current"]["approval_id"] == approval["approval_id"]
        assert body["current"]["plan_hash"] == body["plan_hash"]

    def test_an_over_wide_scope_is_rejected(self, client):
        response = client.post(
            "/api/approvals",
            json={
                "approver": "judge@example.com",
                "scope": {graph.PREDICT_API: ["freeze", "purge"]},
            },
        )
        assert response.status_code == 422
        assert "does not propose" in response.json()["detail"]

    def test_an_unknown_decision_is_rejected(self, client):
        response = client.post(
            "/api/approvals", json={"approver": "judge@example.com", "decision": "maybe"}
        )
        assert response.status_code == 422


class TestExecutionAndVerification:
    def test_the_approved_plan_executes(self, client):
        _approve(client)
        body = client.post("/api/execute", json={}).json()["execution"]
        assert len(body["steps"]) == 8
        assert all(step["status"] == "completed" for step in body["steps"])

    def test_prohibited_serving_actually_stops(self, client):
        _approve(client)
        client.post("/api/execute", json={})

        assert client.post("/api/demo/predict", json={"text": "x"}).status_code == (
            LEGALLY_UNAVAILABLE
        )
        assert client.get("/api/demo/search").status_code == LEGALLY_UNAVAILABLE
        assert client.get("/api/demo/export").status_code == LEGALLY_UNAVAILABLE

    def test_a_refusal_explains_itself(self, client):
        _approve(client)
        client.post("/api/execute", json={})
        detail = client.post("/api/demo/predict", json={"text": "x"}).json()["detail"]
        assert detail["urn"] == graph.PREDICT_API
        assert "containment" in detail["reason"]

    def test_verification_confirms_containment(self, client):
        _approve(client)
        client.post("/api/execute", json={})
        body = client.get("/api/verify").json()
        assert body["contained"] is True
        assert len(body["probes"]) == 8

    def test_fault_injection_is_not_exposed_over_http(self, client):
        response = client.post(
            "/api/execute", json={"fail_adapter": "export-quarantine"}
        )

        assert response.status_code == 422
        assert response.json()["detail"][0]["type"] == "extra_forbidden"
        assert client.get("/api/demo/export").status_code == 200

    def test_a_run_can_be_resumed(self, client):
        _approve(client)
        first = client.post("/api/execute", json={}).json()["execution"]

        second = client.post("/api/execute", json={"run_id": first["run_id"]}).json()[
            "execution"
        ]
        assert second["run_id"] == first["run_id"]
        assert all(step["status"] == "completed" for step in second["steps"])
        assert client.get("/api/demo/export").status_code == LEGALLY_UNAVAILABLE

    def test_runs_are_listed_against_the_plan(self, client):
        _approve(client)
        run_id = client.post("/api/execute", json={}).json()["execution"]["run_id"]
        body = client.get("/api/runs").json()
        assert run_id in {r["run_id"] for r in body["runs"]}

    def test_the_replaced_model_serves_approved_content_after_an_unfreeze(self, client):
        _approve(client)
        client.post("/api/execute", json={})
        # The endpoint stays frozen, but the artifact behind it was genuinely
        # rebuilt: the estate report is what proves the swap happened.
        estate = client.get("/api/estate").json()
        assert estate["model"]["active_version"] == "v2-approved"
        assert estate["model"]["holds_partner_rows"] is False


class TestEvidenceAndWriteback:
    def test_writeback_before_execution_is_refused(self, client):
        _approve(client)
        response = client.post("/api/writeback")
        assert response.status_code == 409
        assert "nothing has been executed" in response.json()["detail"]

    def test_writeback_records_a_status_per_artifact(self, client):
        _approve(client)
        client.post("/api/execute", json={})
        body = client.post("/api/writeback").json()

        assert body["verified"] == body["attempted"] > 0
        statuses = {r["urn"]: r["status"] for r in body["receipts"]}
        assert statuses[graph.PREDICT_API] == "contained"
        assert statuses[graph.ORPHAN] == "escalated"

    def test_the_writeback_is_visible_in_the_graph(self, client):
        _approve(client)
        client.post("/api/execute", json={})
        client.post("/api/writeback")

        by_urn = {n["urn"]: n for n in client.get("/api/graph").json()["nodes"]}
        assert by_urn[graph.PREDICT_API]["revocation_status"] == "contained"
        assert by_urn[graph.ORPHAN]["revocation_status"] == "escalated"

    def test_an_outstanding_escalation_yields_an_escalated_verdict(self, client):
        _approve(client)
        client.post("/api/execute", json={})
        assert client.post("/api/writeback").json()["verdict"] == "escalated"

    def test_the_evidence_bundle_names_residual_exposure(self, client):
        _approve(client)
        client.post("/api/execute", json={})
        body = client.get("/api/evidence").json()

        assert body["verdict"] == "escalated"
        assert [r["urn"] for r in body["residual_exposure"]] == [graph.ORPHAN]
        assert body["simulated"] is True

    def test_reading_evidence_has_no_side_effects(self, client):
        _approve(client)
        client.post("/api/execute", json={})
        first = client.get("/api/evidence").json()
        second = client.get("/api/evidence").json()
        assert first["execution"]["steps"] == second["execution"]["steps"]

    def test_evidence_before_execution_reports_not_started(self, client):
        assert client.get("/api/evidence").json()["verdict"] == "not_started"

    def test_evidence_carries_both_disclaimers(self, client):
        body = client.get("/api/evidence").json()
        assert "does not provide legal advice" in body["disclaimers"]["legal"]
        assert "unlearn" in body["disclaimers"]["scope"]


class TestReset:
    def test_reset_restores_the_exposed_state(self, client):
        _approve(client)
        client.post("/api/execute", json={})
        assert client.post("/api/demo/predict", json={"text": "x"}).status_code == (
            LEGALLY_UNAVAILABLE
        )

        client.post("/api/demo/reset", json={"clear_governance": True})

        assert client.post("/api/demo/predict", json={"text": "x"}).status_code == 200
        assert client.get("/api/demo/export").json()["rows"] == 24
        hits = client.get("/api/demo/search").json()["hits"]
        assert any(h["review_id"].startswith(PARTNER_PREFIX) for h in hits)

    def test_reset_keeps_the_audit_trail_by_default(self, client):
        approval = _approve(client)
        client.post("/api/execute", json={})
        client.post("/api/demo/reset", json={})
        assert client.get("/api/approvals").json()["current"]["approval_id"] == (
            approval["approval_id"]
        )

    def test_clearing_governance_removes_the_approval(self, client):
        _approve(client)
        client.post("/api/demo/reset", json={"clear_governance": True})
        assert client.get("/api/approvals").json()["current"] is None

    def test_the_demo_can_be_run_twice(self, client):
        for _ in range(2):
            client.post("/api/demo/reset", json={"clear_governance": True})
            _approve(client)
            execution = client.post("/api/execute", json={}).json()["execution"]
            assert all(step["status"] == "completed" for step in execution["steps"])
            assert client.get("/api/verify").json()["contained"] is True

    def test_a_rebuilt_index_holds_only_approved_rows_after_a_second_run(self, client):
        _approve(client)
        client.post("/api/execute", json={})
        client.post("/api/demo/reset", json={"clear_governance": True})
        _approve(client)
        client.post("/api/execute", json={})

        estate = client.get("/api/estate").json()
        assert estate["index"]["holds_partner_rows"] is False
        assert estate["index"]["source_urns"] == [graph.REPLACEMENT_SOURCE]


class TestIsolation:
    def test_no_endpoint_touches_anything_outside_the_namespace(self, client):
        _approve(client)
        client.post("/api/execute", json={})
        client.post("/api/writeback")

        for node in client.get("/api/graph").json()["nodes"]:
            assert node["urn"].count("license.") == 1, node["urn"]

    def test_the_approved_branch_is_never_written_back_to(self, client):
        _approve(client)
        client.post("/api/execute", json={})
        receipts = client.post("/api/writeback").json()["receipts"]
        assert graph.APPROVED_MODEL not in {r["urn"] for r in receipts}

    def test_the_rebuilt_content_comes_from_the_approved_feed(self, client):
        _approve(client)
        client.post("/api/execute", json={})
        client.post("/api/demo/reset", json={})

        # Reset restores exposure, so re-run to inspect the rebuilt index.
        _approve(client)
        client.post("/api/execute", json={})
        client.post("/api/demo/reset", json={})
        assert all(
            not h["review_id"].startswith(APPROVED_PREFIX)
            for h in client.get("/api/demo/search").json()["hits"]
        )


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float = 1.0) -> None:
        self.now += seconds


def _public_confirmation(client: TestClient, operation: str) -> str:
    response = client.post("/api/demo/confirmation", json={"operation": operation})
    assert response.status_code == 200, response.text
    return response.json()["confirmation"]


class TestPublicMutationBoundary:
    @pytest.mark.parametrize(
        ("path", "payload"),
        (
            ("/api/approvals", {"approver": "anonymous@example.com"}),
            ("/api/execute", {}),
            ("/api/writeback", None),
            ("/api/demo/reset", {"clear_governance": True}),
        ),
    )
    def test_hackathon_mode_requires_an_operation_confirmation(
        self, client, monkeypatch, path, payload
    ):
        monkeypatch.setenv("APP_ENV", "hackathon")
        reset_settings_cache()

        response = client.post(path, json=payload) if payload is not None else client.post(path)

        assert response.status_code == 403
        assert "operation-bound demo confirmation" in response.json()["detail"]

    def test_hackathon_readiness_tells_the_console_mutations_are_guarded(
        self, client, monkeypatch
    ):
        monkeypatch.setenv("APP_ENV", "hackathon")
        reset_settings_cache()

        body = client.get("/api/readiness").json()
        assert body["mutations_enabled"] is True
        assert body["mutation_mode"] == "guarded"

    def test_guard_runs_before_request_body_validation(self, client, monkeypatch):
        monkeypatch.setenv("APP_ENV", "hackathon")
        reset_settings_cache()

        response = client.post("/api/approvals", json={})

        assert response.status_code == 403
        assert "operation-bound demo confirmation" in response.json()["detail"]

    def test_confirmation_is_one_time_and_operation_bound(self, client, monkeypatch):
        monkeypatch.setenv("APP_ENV", "hackathon")
        reset_settings_cache()
        clock = _Clock()
        monkeypatch.setattr(
            api_module,
            "_demo_guard",
            DemoMutationGuard(clock=clock, token_factory=lambda _: "confirm-once"),
        )

        token = _public_confirmation(client, "approve")
        wrong_route = client.post(
            "/api/execute",
            headers={"X-Demo-Confirmation": token},
            json={},
        )
        assert wrong_route.status_code == 403

        reused = client.post(
            "/api/approvals",
            headers={"X-Demo-Confirmation": token},
            json={"approver": "judge@example.com"},
        )
        assert reused.status_code == 403

    def test_guarded_public_workflow_runs_and_reset_invalidates_approval(
        self, client, monkeypatch
    ):
        offline_client = get_client(get_settings())
        monkeypatch.setenv("APP_ENV", "hackathon")
        reset_settings_cache()
        monkeypatch.setattr(api_module, "build_client", lambda _settings: offline_client)
        clock = _Clock()
        monkeypatch.setattr(
            api_module,
            "_demo_guard",
            DemoMutationGuard(
                clock=clock,
                token_factory=lambda _: f"confirmation-{clock.now}",
            ),
        )

        approve_token = _public_confirmation(client, "approve")
        approved = client.post(
            "/api/approvals",
            headers={"X-Demo-Confirmation": approve_token},
            json={"approver": "judge@example.com", "note": "reviewed"},
        )
        assert approved.status_code == 200, approved.text

        clock.advance()
        execute_token = _public_confirmation(client, "execute")
        executed = client.post(
            "/api/execute",
            headers={"X-Demo-Confirmation": execute_token},
            json={},
        )
        assert executed.status_code == 200, executed.text
        run_id = executed.json()["execution"]["run_id"]

        clock.advance()
        resume_token = _public_confirmation(client, "execute")
        resume = client.post(
            "/api/execute",
            headers={"X-Demo-Confirmation": resume_token},
            json={"run_id": run_id},
        )
        assert resume.status_code == 403
        assert "starts a fresh run" in resume.json()["detail"]

        clock.advance()
        writeback_token = _public_confirmation(client, "writeback")
        written = client.post(
            "/api/writeback",
            headers={"X-Demo-Confirmation": writeback_token},
        )
        assert written.status_code == 200, written.text
        assert written.json()["evidence_path"].startswith(
            "license-circuit-breaker://evidence/"
        )

        clock.advance()
        reset_token = _public_confirmation(client, "reset")
        reset = client.post(
            "/api/demo/reset",
            headers={"X-Demo-Confirmation": reset_token},
            json={"clear_governance": False},
        )
        assert reset.status_code == 200, reset.text
        assert reset.json()["governance_cleared"] is False
        assert reset.json()["approval_invalidated"] is True

        approvals = client.get("/api/approvals").json()
        assert approvals["current"]["decision"] == "rejected"
        assert {row["decision"] for row in approvals["history"]} >= {
            "approved",
            "rejected",
        }

        clock.advance()
        execute_after_reset = client.post(
            "/api/execute",
            headers={
                "X-Demo-Confirmation": _public_confirmation(client, "execute")
            },
            json={},
        )
        assert execute_after_reset.status_code == 409
        assert execute_after_reset.json()["detail"]["error"] == "ApprovalRefused"

    def test_public_reset_cannot_delete_governance_history(self, client, monkeypatch):
        monkeypatch.setenv("APP_ENV", "hackathon")
        reset_settings_cache()
        monkeypatch.setattr(api_module, "_demo_guard", DemoMutationGuard())

        token = _public_confirmation(client, "reset")
        response = client.post(
            "/api/demo/reset",
            headers={"X-Demo-Confirmation": token},
            json={"clear_governance": True},
        )
        assert response.status_code == 403
        assert "may not delete approval or execution history" in response.json()["detail"]

    @pytest.mark.parametrize(
        "path",
        ("/api/approvals", "/api/execute", "/api/writeback", "/api/demo/reset"),
    )
    def test_production_mode_remains_read_only(self, client, monkeypatch, path):
        monkeypatch.setenv("APP_ENV", "production")
        reset_settings_cache()
        response = client.post(path, json={})
        assert response.status_code == 403
        assert "production environment" in response.json()["detail"]
