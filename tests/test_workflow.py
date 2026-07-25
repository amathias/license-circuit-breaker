"""Vertical slice tests: context validation, decision, reversible writeback, receipts."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from adapters.datahub import DataHubError, reversible_tag_writeback
from adapters.fake_datahub import FakeDataHubClient
from app.context import build_paths, discover_descendants, validate_entity
from app.namespace import Namespace, NamespaceViolation
from app.receipts import ReceiptLedger
from app.rights import Action, License, Purpose, RightsEvent, RightsState
from app.workflow import (
    REVOCATION_TAG,
    WorkflowError,
    build_impact_plan,
    perform_reversible_writeback,
)
from demo.graph import (
    ANALYTICS,
    EXPORT,
    MODEL,
    ORPHAN,
    PREDICT_API,
    REPLACEMENT_SOURCE,
    SOURCE,
    VECTOR_INDEX,
)
from demo.seed import seed

NS = Namespace(
    project_slug="license-circuit-breaker",
    urn_prefix="license.",
    project_tag="project-license-circuit-breaker",
    domain="Demo / License Circuit Breaker",
)
FOREIGN = "urn:li:dataset:(urn:li:dataPlatform:duckdb,lifeboat.reviews.feed,PROD)"


@pytest.fixture
def client() -> FakeDataHubClient:
    fake = FakeDataHubClient(namespace=NS)
    seed(fake, NS)
    return fake


@pytest.fixture
def ledger(tmp_path) -> ReceiptLedger:
    return ReceiptLedger(tmp_path)


def _event(replacement: str | None = REPLACEMENT_SOURCE) -> RightsEvent:
    prior = License(
        license_id="PARTNER-2026-01",
        name="Partner review feed",
        permitted_purposes=frozenset(
            {
                Purpose.TRAINING,
                Purpose.RETRIEVAL,
                Purpose.ANALYTICS,
                Purpose.SERVING,
                Purpose.EXPORT,
            }
        ),
    )
    return RightsEvent(
        event_id="evt-lcb-001",
        effective_at=datetime(2026, 7, 25, tzinfo=UTC),
        source_urn=SOURCE,
        prior=prior,
        new=License(
            license_id="PARTNER-2026-01",
            name="Partner review feed",
            permitted_purposes=frozenset({Purpose.ANALYTICS}),
            state=RightsState.RESTRICTED,
        ),
        reason="Partner revoked training and retrieval rights",
        requester="governance@example.com",
        replacement_source_urn=replacement,
    )


class TestContextValidation:
    def test_seeded_entity_validates(self, client):
        entity = client.get_entity(SOURCE)
        result = validate_entity(entity, SOURCE, NS)
        assert result.usable
        assert result.has_project_tag

    def test_missing_entity_is_unusable(self):
        result = validate_entity(None, SOURCE, NS)
        assert not result.usable
        assert any("not found" in i for i in result.issues)

    def test_foreign_urn_is_unusable(self, client):
        client.add_entity(FOREIGN, tags=(NS.project_tag,))
        result = validate_entity(client.get_entity(FOREIGN), FOREIGN, NS)
        assert not result.in_namespace
        assert not result.usable

    def test_untagged_entity_is_unusable(self, client):
        urn = "urn:li:dataset:(urn:li:dataPlatform:duckdb,license.untagged,PROD)"
        client.add_entity(urn, tags=())
        result = validate_entity(client.get_entity(urn), urn, NS)
        assert not result.has_project_tag
        assert not result.usable


class TestLineagePaths:
    def test_builds_a_path_to_a_direct_descendant(self, client):
        edges = client.get_downstream_lineage(SOURCE)
        paths = build_paths(SOURCE, ANALYTICS, edges)
        assert paths
        assert paths[0].hops[0] == SOURCE
        assert paths[0].hops[-1] == ANALYTICS

    def test_builds_a_multi_hop_path(self, client):
        edges = client.get_downstream_lineage(SOURCE)
        paths = build_paths(SOURCE, PREDICT_API, edges)
        assert paths
        assert paths[0].depth >= 3

    def test_unresolved_edge_marks_the_path_incomplete(self, client):
        edges = client.get_downstream_lineage(SOURCE)
        paths = build_paths(SOURCE, ORPHAN, edges)
        assert paths
        assert not paths[0].complete


class TestImpactPlan:
    def test_produces_decisions_for_every_in_namespace_descendant(self, client):
        plan = build_impact_plan(client, _event(), NS)
        assert len(plan.decisions) >= 6

    def test_every_decision_cites_a_rule(self, client):
        plan = build_impact_plan(client, _event(), NS)
        for decision in plan.decisions:
            assert decision.rule_ids

    def test_analytics_branch_is_unaffected(self, client):
        plan = build_impact_plan(client, _event(), NS)
        analytics = next(d for d in plan.decisions if d.descendant_urn == ANALYTICS)
        assert analytics.actions == (Action.NO_ACTION,)

    def test_broken_lineage_escalates(self, client):
        plan = build_impact_plan(client, _event(), NS)
        orphan = next(d for d in plan.decisions if d.descendant_urn == ORPHAN)
        assert orphan.is_escalation

    def test_api_is_frozen(self, client):
        # Regression: the API declares only `serving`, which this event does not
        # revoke. It must still be frozen because it serves model output learned
        # from revoked training data. Reporting no_action here would be the exact
        # false all-clear the product exists to prevent.
        plan = build_impact_plan(client, _event(), NS)
        api = next(d for d in plan.decisions if d.descendant_urn == PREDICT_API)
        assert Action.FREEZE in api.actions
        assert Action.NO_ACTION not in api.actions

    def test_export_is_quarantined(self, client):
        # Regression: same shape as the API. `export` is not a revoked purpose,
        # but the extract contains revoked-derived rows.
        plan = build_impact_plan(client, _event(), NS)
        export = next(d for d in plan.decisions if d.descendant_urn == EXPORT)
        assert Action.QUARANTINE in export.actions
        assert Action.NO_ACTION not in export.actions

    def test_contamination_propagates_downstream(self, client):
        plan = build_impact_plan(client, _event(), NS)
        contaminated = {PREDICT_API, EXPORT, VECTOR_INDEX, MODEL}
        for urn in contaminated:
            decision = next(d for d in plan.decisions if d.descendant_urn == urn)
            assert decision.actions != (Action.NO_ACTION,), f"{urn} should be in scope"

    def test_contamination_does_not_reach_the_retained_purpose_branch(self, client):
        # Precision check: the analytics report hangs directly off the source and
        # uses only a retained purpose, so propagation must not sweep it in.
        plan = build_impact_plan(client, _event(), NS)
        analytics = next(d for d in plan.decisions if d.descendant_urn == ANALYTICS)
        assert analytics.actions == (Action.NO_ACTION,)

    def test_model_retrains_when_a_replacement_exists(self, client):
        plan = build_impact_plan(client, _event(), NS)
        model = next(d for d in plan.decisions if d.descendant_urn == MODEL)
        assert Action.RETRAIN in model.actions

    def test_vector_index_purges_and_rebuilds(self, client):
        plan = build_impact_plan(client, _event(), NS)
        index = next(d for d in plan.decisions if d.descendant_urn == VECTOR_INDEX)
        assert index.actions == (Action.PURGE, Action.REBUILD)

    def test_escalation_prevents_an_all_clear(self, client):
        plan = build_impact_plan(client, _event(), NS)
        assert plan.escalations
        assert not plan.all_clear

    def test_plan_requires_approval(self, client):
        plan = build_impact_plan(client, _event(), NS)
        assert plan.requires_approval

    def test_foreign_source_is_refused(self, client):
        event = _event().model_copy(update={"source_urn": FOREIGN})
        with pytest.raises(NamespaceViolation):
            build_impact_plan(client, event, NS)

    def test_unreadable_source_is_refused(self):
        empty = FakeDataHubClient(namespace=NS)
        with pytest.raises(WorkflowError, match="not usable"):
            build_impact_plan(empty, _event(), NS)

    def test_plan_is_deterministic(self, client):
        first = build_impact_plan(client, _event(), NS)
        second = build_impact_plan(client, _event(), NS)
        assert [d.descendant_urn for d in first.decisions] == [
            d.descendant_urn for d in second.decisions
        ]
        assert [d.actions for d in first.decisions] == [d.actions for d in second.decisions]

    def test_plan_records_a_receipt(self, client, ledger):
        build_impact_plan(client, _event(), NS, ledger=ledger, simulated=True)
        entries = list(ledger.entries())
        assert entries[0]["operation"] == "impact_plan"
        assert entries[0]["simulated"] is True


class TestReversibleWriteback:
    def test_applies_verifies_and_restores(self, client):
        before = list(client.get_tags(SOURCE))
        receipt = reversible_tag_writeback(client, SOURCE, REVOCATION_TAG, NS)

        assert receipt.verified
        assert receipt.restored
        assert receipt.clean
        assert sorted(client.get_tags(SOURCE)) == sorted(before)

    def test_leaves_no_residue_after_repeated_runs(self, client):
        before = sorted(client.get_tags(SOURCE))
        for _ in range(3):
            reversible_tag_writeback(client, SOURCE, REVOCATION_TAG, NS)
        assert sorted(client.get_tags(SOURCE)) == before

    def test_records_prior_value(self, client):
        prior = list(client.get_tags(SOURCE))
        receipt = reversible_tag_writeback(client, SOURCE, REVOCATION_TAG, NS)
        assert sorted(receipt.prior_value) == sorted(prior)

    def test_foreign_target_is_refused(self, client):
        client.add_entity(FOREIGN)
        with pytest.raises(NamespaceViolation):
            reversible_tag_writeback(client, FOREIGN, REVOCATION_TAG, NS)

    def test_failed_restore_is_reported_not_swallowed(self, client):
        client.swallow_restore = True
        receipt = reversible_tag_writeback(client, SOURCE, REVOCATION_TAG, NS)
        # The write landed and was verified, but the rollback did not apply.
        assert receipt.verified
        assert not receipt.restored
        assert not receipt.clean
        assert "restore" in receipt.detail

    def test_write_failure_propagates(self, client):
        client.fail_next_write = True
        with pytest.raises(DataHubError):
            reversible_tag_writeback(client, SOURCE, REVOCATION_TAG, NS)


class TestWritebackReceipts:
    def test_successful_writeback_is_recorded(self, client, ledger):
        perform_reversible_writeback(client, SOURCE, NS, ledger=ledger, simulated=True)
        entry = next(e for e in ledger.entries() if e["operation"] == "writeback")
        assert entry["succeeded"] is True
        assert entry["payload"]["verified"] is True
        assert entry["payload"]["restored"] is True

    def test_failed_writeback_is_still_recorded(self, client, ledger):
        client.fail_next_write = True
        with pytest.raises(DataHubError):
            perform_reversible_writeback(client, SOURCE, NS, ledger=ledger, simulated=True)
        entry = next(e for e in ledger.entries() if e["operation"] == "writeback")
        assert entry["succeeded"] is False

    def test_unrestored_writeback_is_not_marked_successful(self, client, ledger):
        client.swallow_restore = True
        perform_reversible_writeback(client, SOURCE, NS, ledger=ledger, simulated=True)
        entry = next(e for e in ledger.entries() if e["operation"] == "writeback")
        assert entry["succeeded"] is False
        assert entry["payload"]["restored"] is False

    def test_foreign_writeback_never_reaches_datahub(self, client, ledger):
        client.add_entity(FOREIGN)
        before = len(client.write_log)
        with pytest.raises(NamespaceViolation):
            perform_reversible_writeback(client, FOREIGN, NS, ledger=ledger)
        assert len(client.write_log) == before


class TestDescendantDiscovery:
    def test_foreign_descendants_are_excluded_but_reported(self, client):
        client.add_entity(FOREIGN, tags=(NS.project_tag,))
        client.add_edge(SOURCE, FOREIGN)

        descendants, validations = discover_descendants(client, SOURCE, NS)

        assert all(d.urn != FOREIGN for d in descendants)
        # Reported, not silently dropped -- a cross-project lineage link is
        # something an operator needs to see.
        assert any(v.urn == FOREIGN and not v.in_namespace for v in validations)
