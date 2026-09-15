"""Regressions for review LCB-02, LCB-03, and LCB-04."""

from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from adapters.containment import AdapterContext
from app.api import demo_rights_event, get_client
from app.approvals import ApprovalStore
from app.config import Settings, get_settings, reset_settings_cache
from app.execution import ExecutionError, ExecutionJournal, execute_plan
from app.main import app
from app.store import GovernanceStore
from app.workflow import build_impact_plan
from demo import graph
from demo.estate import EstatePaths, build_estate, export_path


@pytest.fixture
def sandbox(monkeypatch):
    monkeypatch.setenv("APP_ENV", "offline")
    reset_settings_cache()
    settings = get_settings()
    return settings, get_client(settings, refresh=True)


def test_approval_rejects_a_plan_changed_since_display(sandbox):
    settings, catalog = sandbox
    client = TestClient(app)
    displayed = client.get("/api/plan").json()["plan_hash"]
    catalog.lineage[graph.SOURCE] = [
        replace(edge, resolved=True) if edge.downstream_urn == graph.ORPHAN else edge
        for edge in catalog.lineage[graph.SOURCE]
    ]
    assert client.get("/api/plan").json()["plan_hash"] != displayed
    response = client.post("/api/approvals", json={
        "approver": "reviewer", "plan_hash": displayed,
    })
    assert response.status_code == 409
    assert ApprovalStore(GovernanceStore(settings.app_state_dir)).for_event(
        demo_rights_event().event_id
    ) == []


def test_approval_requires_displayed_plan_hash(sandbox):
    response = TestClient(app).post("/api/approvals", json={"approver": "reviewer"})
    assert response.status_code == 422


def test_resume_rejects_a_different_plan_without_changing_journal(sandbox):
    settings, catalog = sandbox
    paths = EstatePaths.under(settings.app_state_dir)
    build_estate(paths)
    store = GovernanceStore(settings.app_state_dir)
    first_plan = build_impact_plan(catalog, demo_rights_event(), settings.namespace)
    approvals = ApprovalStore(store)
    first_approval = approvals.record(first_plan, approver="reviewer")
    context = AdapterContext(paths, settings.namespace, graph.REPLACEMENT_SOURCE)
    run = execute_plan(first_plan, first_approval, context, store)
    changed = replace(first_plan, event=first_plan.event.model_copy(update={"version": 2}))
    second_approval = approvals.record(changed, approver="reviewer")
    original = ExecutionJournal(store).run(run.run_id)
    with pytest.raises(ExecutionError, match="plan"):
        execute_plan(changed, second_approval, context, store, run_id=run.run_id)
    assert ExecutionJournal(store).run(run.run_id) == original


def test_resume_rejects_rebuilt_estate(sandbox):
    settings, catalog = sandbox
    paths = EstatePaths.under(settings.app_state_dir)
    build_estate(paths)
    store = GovernanceStore(settings.app_state_dir)
    plan = build_impact_plan(catalog, demo_rights_event(), settings.namespace)
    approval = ApprovalStore(store).record(plan, approver="reviewer")
    context = AdapterContext(paths, settings.namespace, graph.REPLACEMENT_SOURCE)
    run = execute_plan(plan, approval, context, store)
    build_estate(paths)
    assert export_path(paths).exists()
    with pytest.raises(ExecutionError, match="estate"):
        execute_plan(plan, approval, context, store, run_id=run.run_id)


@pytest.mark.parametrize("environment", ["prod", "hackaton", "", "unknown"])
def test_unknown_environment_is_rejected(environment):
    with pytest.raises(ValidationError):
        Settings(APP_ENV=environment, _env_file=None)


def test_environment_whitespace_cannot_bypass_production_guard(monkeypatch):
    monkeypatch.setenv("APP_ENV", " production ")
    reset_settings_cache()
    response = TestClient(app).post("/api/demo/reset", json={"clear_governance": True})
    assert response.status_code == 403


def test_unset_environment_defaults_to_read_only(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    assert Settings(_env_file=None).app_env == "production"


def test_cli_and_api_use_same_stable_event():
    from demo.cli import demo_rights_event as cli_event

    assert cli_event().content_hash() == demo_rights_event().content_hash()
    assert cli_event().content_hash() == cli_event().content_hash()
