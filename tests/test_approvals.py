"""Approval gate tests.

The gate has one job: no destructive action runs without a recorded human
decision about *this exact plan*. These tests attack that from every direction --
no approval, a rejection, an approval for a different plan, an approval for a
narrower scope, and an approval that tries to authorize more than the plan asks.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from adapters.fake_datahub import FakeDataHubClient
from app.approvals import (
    APPROVED,
    REJECTED,
    ApprovalError,
    ApprovalRefused,
    ApprovalRequired,
    ApprovalStore,
    PlanDrift,
    ScopeViolation,
    require_approval,
    require_scope,
)
from app.namespace import Namespace
from app.rights import Action, License, Purpose, RightsEvent, RightsState
from app.store import GovernanceStore
from app.workflow import build_impact_plan
from demo import graph
from demo.seed import seed

NS = Namespace(
    project_slug="license-circuit-breaker",
    urn_prefix="license.",
    project_tag="project-license-circuit-breaker",
    domain="Demo / License Circuit Breaker",
)


def _event(event_id: str = "evt-approval-001", reason: str = "partner revoked") -> RightsEvent:
    return RightsEvent(
        event_id=event_id,
        effective_at=datetime(2026, 7, 25, tzinfo=UTC),
        source_urn=graph.SOURCE,
        prior=License(
            license_id="PARTNER-2026-01",
            name="Partner review feed",
            permitted_purposes=frozenset(
                {Purpose.TRAINING, Purpose.RETRIEVAL, Purpose.ANALYTICS}
            ),
        ),
        new=License(
            license_id="PARTNER-2026-01",
            name="Partner review feed",
            permitted_purposes=frozenset({Purpose.ANALYTICS}),
            state=RightsState.RESTRICTED,
        ),
        reason=reason,
        replacement_source_urn=graph.REPLACEMENT_SOURCE,
        requester="governance@example.com",
    )


@pytest.fixture
def client() -> FakeDataHubClient:
    fake = FakeDataHubClient(namespace=NS)
    seed(fake, NS)
    return fake


@pytest.fixture
def store(tmp_path) -> GovernanceStore:
    return GovernanceStore(tmp_path)


@pytest.fixture
def approvals(store) -> ApprovalStore:
    return ApprovalStore(store)


@pytest.fixture
def plan(client):
    return build_impact_plan(client, _event(), NS)


class TestPlanHash:
    def test_regenerating_the_same_plan_yields_the_same_hash(self, client):
        # Otherwise every re-plan would invalidate a perfectly good approval.
        first = build_impact_plan(client, _event(), NS)
        second = build_impact_plan(client, _event(), NS)
        assert first.plan_hash() == second.plan_hash()
        assert first.generated_at != second.generated_at

    def test_a_different_rights_event_yields_a_different_hash(self, client, plan):
        other = build_impact_plan(client, _event(reason="a different reason entirely"), NS)
        assert other.plan_hash() != plan.plan_hash()

    def test_a_changed_graph_yields_a_different_hash(self, client, plan):
        # Add a descendant, and the approved scope is no longer the real scope.
        client.add_entity(
            "urn:li:dataset:(urn:li:dataPlatform:duckdb,license.reviews.extra,PROD)",
            tags=("project-license-circuit-breaker", "lcb-demo-fixture"),
            domain="urn:li:domain:demo-license-circuit-breaker",
            custom_properties={"artifact_class": "dataset", "purposes": "training"},
        )
        client.add_edge(
            graph.NORMALIZED,
            "urn:li:dataset:(urn:li:dataPlatform:duckdb,license.reviews.extra,PROD)",
        )
        assert build_impact_plan(client, _event(), NS).plan_hash() != plan.plan_hash()


class TestEnforcementScope:
    def test_scope_contains_only_destructive_actions(self, plan):
        scope = plan.enforcement_scope()
        for actions in scope.values():
            assert "no_action" not in actions
            assert "escalate" not in actions

    def test_unaffected_and_escalated_artifacts_are_not_in_scope(self, plan):
        scope = plan.enforcement_scope()
        assert graph.ANALYTICS not in scope
        assert graph.ORPHAN not in scope

    def test_the_serving_api_and_the_model_are_in_scope(self, plan):
        scope = plan.enforcement_scope()
        assert scope[graph.PREDICT_API] == ["freeze"]
        assert scope[graph.MODEL] == ["retrain", "replace"]


class TestRequireApproval:
    def test_an_unapproved_plan_is_refused(self, approvals, plan):
        with pytest.raises(ApprovalRequired, match="no approval has been recorded"):
            require_approval(approvals, plan)

    def test_an_approved_plan_returns_its_approval(self, approvals, plan):
        recorded = approvals.record(plan, approver="reviewer@example.com")
        found = require_approval(approvals, plan)
        assert found.approval_id == recorded.approval_id
        assert found.approved is True

    def test_a_rejected_plan_is_refused(self, approvals, plan):
        approvals.record(
            plan, approver="reviewer@example.com", decision=REJECTED, note="scope too wide"
        )
        with pytest.raises(ApprovalRefused, match="scope too wide"):
            require_approval(approvals, plan)

    def test_the_latest_decision_wins(self, approvals, plan):
        approvals.record(plan, approver="first@example.com", decision=REJECTED)
        approvals.record(plan, approver="second@example.com", decision=APPROVED)
        assert require_approval(approvals, plan).approver == "second@example.com"

    def test_earlier_decisions_are_retained(self, approvals, plan):
        approvals.record(plan, approver="first@example.com", decision=REJECTED)
        approvals.record(plan, approver="second@example.com", decision=APPROVED)
        # Append-only: a change of mind must not erase what was decided before.
        assert len(approvals.for_event(plan.event.event_id)) == 2


class TestPlanDriftDetection:
    def test_an_approval_for_an_earlier_plan_does_not_authorize_a_changed_one(
        self, approvals, client, plan
    ):
        approvals.record(plan, approver="reviewer@example.com")

        # The graph grows after review; the regenerated plan is wider.
        extra = "urn:li:dataset:(urn:li:dataPlatform:duckdb,license.reviews.extra,PROD)"
        client.add_entity(
            extra,
            tags=("project-license-circuit-breaker", "lcb-demo-fixture"),
            domain="urn:li:domain:demo-license-circuit-breaker",
            custom_properties={"artifact_class": "dataset", "purposes": "training"},
        )
        client.add_edge(graph.NORMALIZED, extra)
        widened = build_impact_plan(client, _event(), NS)

        with pytest.raises(PlanDrift, match="changed after review"):
            require_approval(approvals, widened)

    def test_drift_names_both_hashes(self, approvals, client, plan):
        approvals.record(plan, approver="reviewer@example.com")
        other = build_impact_plan(client, _event(reason="revised reason"), NS)
        with pytest.raises(PlanDrift) as excinfo:
            require_approval(approvals, other)
        assert plan.plan_hash() in str(excinfo.value)
        assert other.plan_hash() in str(excinfo.value)


class TestScope:
    def test_a_narrower_scope_is_accepted(self, approvals, plan):
        approval = approvals.record(
            plan, approver="reviewer@example.com", scope={graph.PREDICT_API: ["freeze"]}
        )
        assert approval.covers(graph.PREDICT_API, Action.FREEZE)
        assert not approval.covers(graph.MODEL, Action.RETRAIN)

    def test_a_scope_wider_than_the_plan_is_refused_at_recording_time(self, approvals, plan):
        # An approval must never authorize more than the plan it is attached to.
        with pytest.raises(ApprovalError, match="which the plan does not propose"):
            approvals.record(
                plan,
                approver="reviewer@example.com",
                scope={graph.PREDICT_API: ["freeze", "purge"]},
            )

    def test_a_scope_naming_an_out_of_plan_target_is_refused(self, approvals, plan):
        with pytest.raises(ApprovalError, match="which the plan does not propose"):
            approvals.record(
                plan, approver="reviewer@example.com", scope={graph.ANALYTICS: ["purge"]}
            )

    def test_require_scope_permits_an_approved_action(self, approvals, plan):
        approval = approvals.record(plan, approver="reviewer@example.com")
        require_scope(approval, graph.PREDICT_API, Action.FREEZE)

    def test_require_scope_refuses_an_unapproved_action(self, approvals, plan):
        approval = approvals.record(
            plan, approver="reviewer@example.com", scope={graph.PREDICT_API: ["freeze"]}
        )
        with pytest.raises(ScopeViolation, match="does not cover"):
            require_scope(approval, graph.MODEL, Action.RETRAIN)

    def test_require_scope_reports_what_was_approved_instead(self, approvals, plan):
        approval = approvals.record(
            plan, approver="reviewer@example.com", scope={graph.MODEL: ["retrain"]}
        )
        with pytest.raises(ScopeViolation, match="Approved for that target: \\['retrain'\\]"):
            require_scope(approval, graph.MODEL, Action.REPLACE)


class TestPersistence:
    def test_an_approval_survives_a_new_process(self, tmp_path, plan):
        ApprovalStore(GovernanceStore(tmp_path)).record(plan, approver="reviewer@example.com")

        # A fresh store over the same directory: this is what the CLI and the API
        # each do. An approval that lived only in memory would be no gate at all.
        reopened = ApprovalStore(GovernanceStore(tmp_path))
        assert require_approval(reopened, plan).approver == "reviewer@example.com"

    def test_the_plan_a_decision_refers_to_is_persisted(self, store, approvals, plan):
        approvals.record(plan, approver="reviewer@example.com")
        with store.connect() as connection:
            row = connection.execute(
                "SELECT * FROM plans WHERE plan_hash = ?", (plan.plan_hash(),)
            ).fetchone()
        assert row is not None
        assert row["event_id"] == plan.event.event_id

    def test_approval_can_be_fetched_by_id(self, approvals, plan):
        recorded = approvals.record(plan, approver="reviewer@example.com", note="looks right")
        found = approvals.get(recorded.approval_id)
        assert found is not None
        assert found.note == "looks right"

    def test_an_unknown_approval_id_returns_none(self, approvals):
        assert approvals.get("apr-does-not-exist") is None

    def test_serialization_round_trips_the_scope(self, approvals, plan):
        recorded = approvals.record(plan, approver="reviewer@example.com")
        assert approvals.get(recorded.approval_id).scope == recorded.scope

    def test_an_unknown_decision_value_is_refused(self, approvals, plan):
        with pytest.raises(ApprovalError, match="unknown approval decision"):
            approvals.record(plan, approver="reviewer@example.com", decision="maybe")
