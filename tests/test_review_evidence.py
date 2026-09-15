"""Adversarial artifact and evidence regressions from the independent review."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from adapters.containment import AdapterContext, ContainmentError
from adapters.fake_datahub import FakeDataHubClient
from app.approvals import ApprovalStore
from app.config import Settings
from app.evidence import build_bundle
from app.execution import execute_plan
from app.receipts import ReceiptLedger
from app.rights import Action
from app.store import GovernanceStore
from app.verification import probe_index, probe_model, verify_plan
from app.workflow import build_impact_plan
from demo import graph
from demo.estate import EstatePaths, build_estate, purge_table
from demo.events import demo_rights_event
from demo.seed import seed


@pytest.fixture
def scenario(tmp_path):
    ns = Settings(_env_file=None).namespace
    client = FakeDataHubClient(namespace=ns)
    seed(client, ns)
    paths = EstatePaths.under(tmp_path)
    build_estate(paths)
    plan = build_impact_plan(client, demo_rights_event(), ns)
    store = GovernanceStore(tmp_path)
    approval = ApprovalStore(store).record(plan, approver="reviewer")
    context = AdapterContext(paths, ns, graph.REPLACEMENT_SOURCE)
    return plan, approval, context, store


def test_concurrent_receipts_have_one_intact_chain(tmp_path):
    def append_batch(_):
        ledger = ReceiptLedger(tmp_path)
        for _index in range(20):
            ledger.append(operation="test", succeeded=True, simulated=True)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(append_batch, range(8)))
    ledger = ReceiptLedger(tmp_path)
    assert len(list(ledger.entries())) == 160
    assert ledger.verify_chain()[0]


def test_precision_failure_is_residual_not_escalated(scenario):
    plan, approval, context, store = scenario
    run = execute_plan(plan, approval, context, store)
    purge_table(context.paths, "review_volume")
    report = verify_plan(plan, context.paths)
    bundle = build_bundle(plan, approval=approval, execution=run, verification=report)
    assert bundle.verdict() == "residual"
    assert graph.ANALYTICS in {item.urn for item in bundle.residual()}


def test_old_vectors_cannot_hide_under_approved_manifest(scenario):
    plan, approval, context, store = scenario
    original = context.paths.index_vectors.read_bytes()
    execute_plan(plan, approval, context, store)
    assert probe_index(context.paths, graph.VECTOR_INDEX).passed
    context.paths.index_vectors.write_bytes(original)
    assert not probe_index(context.paths, graph.VECTOR_INDEX).passed


def test_old_model_cannot_hide_under_approved_manifest(scenario):
    plan, approval, context, store = scenario
    root = context.paths.model_root("review_sentiment")
    original = (root / "v1" / "model.json").read_bytes()
    execute_plan(plan, approval, context, store)
    assert probe_model(context.paths, graph.MODEL).passed
    (root / "v2-approved" / "model.json").write_bytes(original)
    assert not probe_model(context.paths, graph.MODEL).passed


def test_failed_warehouse_blocks_dependent_rebuild_but_not_quarantine(scenario):
    plan, approval, context, store = scenario

    def fail_warehouse(adapter, urn, action):
        if urn == graph.NORMALIZED and action is Action.REBUILD:
            raise ContainmentError("injected warehouse failure")

    context.fault_injector = fail_warehouse
    run = execute_plan(plan, approval, context, store)
    index = next(o for o in run.outcomes
                 if o.step.urn == graph.VECTOR_INDEX and o.step.action is Action.REBUILD)
    assert not index.succeeded
    assert not index.changed
    assert index.evidence["attempted"] is False
    assert next(o for o in run.outcomes if o.step.urn == graph.EXPORT).succeeded


def test_passing_checks_do_not_clear_unresolved_lineage(scenario):
    plan, approval, context, store = scenario
    execute_plan(plan, approval, context, store)
    report = verify_plan(plan, context.paths)
    assert report.checks_passed
    assert not report.contained
    assert not report.coverage_complete
    escalation_only = replace(plan, decisions=plan.escalations)
    assert not verify_plan(escalation_only, context.paths).contained


def test_broken_unaffected_model_produces_precision_residual(scenario):
    plan, _approval, context, _store = scenario
    model = context.paths.model_root("approved_sentiment") / "v1" / "model.json"
    model.unlink()
    report = verify_plan(plan, context.paths)
    assert graph.APPROVED_MODEL in {item.urn for item in report.residual()}


def test_malformed_unaffected_manifest_is_failed_probe(scenario):
    plan, _approval, context, _store = scenario
    manifest = context.paths.model_root("approved_sentiment") / "v1" / "training_manifest.json"
    assert manifest.is_file()
    manifest.write_text("{broken", encoding="utf-8")
    report = verify_plan(plan, context.paths)
    assert graph.APPROVED_MODEL in {item.urn for item in report.residual()}
