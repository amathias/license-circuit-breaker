"""Execution tests: ordering, failure tolerance, resume, and residual exposure.

The behaviours under test are the ones that separate a governance tool from a
dashboard: a failed step does not abandon the rest of the plan, a killed run
resumes without redoing completed work, and nothing that was not demonstrably
contained is allowed to disappear from the report.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from adapters.containment import AdapterContext, ContainmentError
from adapters.fake_datahub import FakeDataHubClient
from app.approvals import REJECTED, ApprovalStore
from app.execution import (
    ACTION_FAILED,
    COMPLETED,
    ESCALATED,
    FAILED,
    NOT_APPROVED,
    NOT_APPROVED_REASON,
    ExecutionError,
    ExecutionJournal,
    execute_plan,
    plan_steps,
)
from app.namespace import Namespace
from app.receipts import ReceiptLedger
from app.rights import Action, License, Purpose, RightsEvent, RightsState
from app.store import GovernanceStore
from app.workflow import build_impact_plan
from demo import graph
from demo.corpus import PARTNER_PREFIX
from demo.estate import (
    EstatePaths,
    ServingControl,
    active_version,
    build_estate,
    export_path,
    index_manifest,
    training_manifest,
)
from demo.seed import seed

NS = Namespace(
    project_slug="license-circuit-breaker",
    urn_prefix="license.",
    project_tag="project-license-circuit-breaker",
    domain="Demo / License Circuit Breaker",
)


def _event() -> RightsEvent:
    return RightsEvent(
        event_id="evt-execution-001",
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
        reason="partner revoked training and retrieval",
        replacement_source_urn=graph.REPLACEMENT_SOURCE,
        requester="governance@example.com",
    )


@pytest.fixture
def paths(tmp_path) -> EstatePaths:
    built = EstatePaths.under(tmp_path)
    build_estate(built)
    return built


@pytest.fixture
def store(tmp_path) -> GovernanceStore:
    return GovernanceStore(tmp_path)


@pytest.fixture
def plan():
    client = FakeDataHubClient(namespace=NS)
    seed(client, NS)
    return build_impact_plan(client, _event(), NS)


@pytest.fixture
def context(paths) -> AdapterContext:
    return AdapterContext(
        paths=paths,
        namespace=NS,
        replacement_source_urn=graph.REPLACEMENT_SOURCE,
        actor="approver@example.com",
    )


@pytest.fixture
def approval(store, plan):
    return ApprovalStore(store).record(plan, approver="approver@example.com")


class TestStepPlanning:
    def test_only_destructive_actions_become_steps(self, plan):
        steps = plan_steps(plan)
        assert steps
        assert all(step.action in {a for a in Action if a.value in
                                   {"freeze", "quarantine", "purge", "rebuild",
                                    "retrain", "replace"}} for step in steps)

    def test_freeze_is_sequenced_first(self, plan):
        steps = plan_steps(plan)
        assert steps[0].action is Action.FREEZE
        assert steps[0].urn == graph.PREDICT_API

    def test_replace_is_sequenced_last(self, plan):
        assert plan_steps(plan)[-1].action is Action.REPLACE

    def test_sequence_numbers_are_dense_and_ordered(self, plan):
        steps = plan_steps(plan)
        assert [step.seq for step in steps] == list(range(len(steps)))

    def test_planning_is_deterministic(self, plan):
        assert [s.to_dict() for s in plan_steps(plan)] == [
            s.to_dict() for s in plan_steps(plan)
        ]


class TestGateEnforcement:
    def test_an_approval_for_a_different_plan_is_refused(self, plan, context, store):
        # Approve a genuinely different plan, then try to execute this one with
        # it. The executor re-checks the binding rather than trusting the caller
        # to have gone through the gate.
        client = FakeDataHubClient(namespace=NS)
        seed(client, NS)
        other_event = _event().model_copy(update={"reason": "an unrelated revocation"})
        other_plan = build_impact_plan(client, other_event, NS)
        stale = ApprovalStore(store).record(other_plan, approver="approver@example.com")

        assert stale.plan_hash != plan.plan_hash()
        with pytest.raises(ExecutionError, match="authorizes plan"):
            execute_plan(plan, stale, context, store)

    def test_a_rejection_cannot_be_executed(self, plan, context, store):
        rejected = ApprovalStore(store).record(
            plan, approver="approver@example.com", decision=REJECTED
        )
        with pytest.raises(ExecutionError, match="is a rejected"):
            execute_plan(plan, rejected, context, store)

    def test_nothing_is_touched_when_the_gate_refuses(self, plan, context, store, paths):
        rejected = ApprovalStore(store).record(
            plan, approver="approver@example.com", decision=REJECTED
        )
        with pytest.raises(ExecutionError):
            execute_plan(plan, rejected, context, store)
        assert not ServingControl.load(paths.serving_path).is_blocked(graph.PREDICT_API)
        assert export_path(paths).exists()


class TestHappyPath:
    def test_every_step_completes(self, plan, approval, context, store):
        report = execute_plan(plan, approval, context, store)
        assert len(report.completed) == len(report.outcomes)
        assert not report.failed

    def test_the_estate_genuinely_changes(self, plan, approval, context, store, paths):
        execute_plan(plan, approval, context, store)

        assert ServingControl.load(paths.serving_path).is_blocked(graph.PREDICT_API)
        assert not export_path(paths).exists()
        assert not any(
            rid.startswith(PARTNER_PREFIX) for rid in index_manifest(paths)["row_ids"]
        )
        assert active_version(paths, "review_sentiment") == "v2-approved"
        assert not any(
            rid.startswith(PARTNER_PREFIX)
            for rid in training_manifest(paths, "review_sentiment")["row_ids"]
        )

    def test_the_run_is_not_fully_executed_while_an_escalation_stands(
        self, plan, approval, context, store
    ):
        # Every step succeeded, yet the orphan's lineage gap is unresolved. This
        # is the exact case where a lesser tool reports an all-clear.
        report = execute_plan(plan, approval, context, store)
        assert not report.failed
        assert report.fully_executed is False
        assert any(r.reason == ESCALATED for r in report.residual)

    def test_the_run_is_recorded(self, plan, approval, context, store):
        report = execute_plan(plan, approval, context, store)
        recorded = ExecutionJournal(store).run(report.run_id)
        assert recorded["status"] == "finished"
        assert recorded["plan_hash"] == plan.plan_hash()
        assert recorded["approval_id"] == approval.approval_id

    def test_running_twice_converges(self, plan, approval, context, store, paths):
        execute_plan(plan, approval, context, store)
        second = execute_plan(plan, approval, context, store)
        assert not second.failed
        assert active_version(paths, "review_sentiment") == "v2-approved"


class TestScopeSkipping:
    def test_an_unapproved_action_is_skipped_not_silently_dropped(
        self, plan, context, store, paths
    ):
        narrow = ApprovalStore(store).record(
            plan,
            approver="approver@example.com",
            scope={graph.PREDICT_API: ["freeze"]},
            note="freeze only for now",
        )
        report = execute_plan(plan, narrow, context, store)

        statuses = {o.step.action.value: o.status for o in report.outcomes}
        assert statuses["freeze"] == COMPLETED
        assert statuses["quarantine"] == NOT_APPROVED

        # Skipped means still exposed, and the report has to say so.
        assert any(
            r.reason == NOT_APPROVED_REASON and r.urn == graph.EXPORT for r in report.residual
        )
        assert export_path(paths).exists()


class TestFailureTolerance:
    def test_a_failed_step_does_not_abandon_the_rest_of_the_plan(
        self, plan, approval, context, store, paths
    ):
        def fail_quarantine(adapter: str, urn: str, action: Action) -> None:
            if adapter == "export-quarantine":
                raise ContainmentError("simulated filesystem failure")

        context.fault_injector = fail_quarantine
        report = execute_plan(plan, approval, context, store)

        assert len(report.failed) == 1
        assert report.failed[0].step.action is Action.QUARANTINE
        # Stopping here would have left the API serving and the index populated,
        # which is strictly worse than continuing.
        assert ServingControl.load(paths.serving_path).is_blocked(graph.PREDICT_API)
        assert active_version(paths, "review_sentiment") == "v2-approved"

    def test_a_failed_step_becomes_residual_exposure(self, plan, approval, context, store):
        context.fault_injector = lambda adapter, *_: (
            (_ for _ in ()).throw(ContainmentError("boom"))
            if adapter == "export-quarantine"
            else None
        )
        report = execute_plan(plan, approval, context, store)

        residual = [r for r in report.residual if r.reason == ACTION_FAILED]
        assert len(residual) == 1
        assert residual[0].urn == graph.EXPORT
        assert residual[0].action == "quarantine"
        assert "boom" in residual[0].detail

    def test_a_failed_run_is_never_fully_executed(self, plan, approval, context, store):
        context.fault_injector = lambda adapter, *_: (
            (_ for _ in ()).throw(ContainmentError("boom"))
            if adapter == "export-quarantine"
            else None
        )
        assert execute_plan(plan, approval, context, store).fully_executed is False

    def test_the_failed_artifact_really_is_still_exposed(
        self, plan, approval, context, store, paths
    ):
        context.fault_injector = lambda adapter, *_: (
            (_ for _ in ()).throw(ContainmentError("boom"))
            if adapter == "export-quarantine"
            else None
        )
        execute_plan(plan, approval, context, store)
        assert export_path(paths).exists(), "residual exposure must be real, not cosmetic"


class TestResume:
    def test_resume_reruns_only_unfinished_steps(self, plan, approval, context, store):
        attempts: list[tuple[str, str]] = []

        def record_and_fail(adapter: str, urn: str, action: Action) -> None:
            attempts.append((adapter, action.value))
            if adapter == "vector-index" and action is Action.PURGE:
                raise ContainmentError("simulated crash during purge")

        context.fault_injector = record_and_fail
        first = execute_plan(plan, approval, context, store)
        assert len(first.failed) == 1

        attempts.clear()
        context.fault_injector = lambda adapter, urn, action: attempts.append(
            (adapter, action.value)
        )
        second = execute_plan(plan, approval, context, store, run_id=first.run_id)

        # Only the purge should have been attempted again; everything else was
        # already journalled as complete.
        assert attempts == [("vector-index", "purge")]
        assert len(second.resumed) == len(second.outcomes) - 1
        assert not second.failed

    def test_resumed_steps_are_labelled_as_such(self, plan, approval, context, store):
        first = execute_plan(plan, approval, context, store)
        second = execute_plan(plan, approval, context, store, run_id=first.run_id)
        assert all(o.resumed for o in second.outcomes)
        assert all(o.status == COMPLETED for o in second.outcomes)

    def test_a_fresh_run_id_does_not_resume(self, plan, approval, context, store):
        execute_plan(plan, approval, context, store)
        second = execute_plan(plan, approval, context, store)
        assert not second.resumed

    def test_the_journal_survives_a_new_process(self, tmp_path, plan, context):
        first = execute_plan(
            plan,
            ApprovalStore(GovernanceStore(tmp_path)).record(plan, approver="a@example.com"),
            context,
            GovernanceStore(tmp_path),
        )
        # A completely fresh store handle over the same directory, as a resumed
        # CLI invocation would have.
        reopened = GovernanceStore(tmp_path)
        completed = ExecutionJournal(reopened).completed_steps(first.run_id)
        assert len(completed) == len(first.outcomes)

    def test_resume_after_a_scope_widening_approval_reruns_the_skipped_step(
        self, plan, context, store, paths
    ):
        approvals = ApprovalStore(store)
        narrow = approvals.record(
            plan, approver="a@example.com", scope={graph.PREDICT_API: ["freeze"]}
        )
        first = execute_plan(plan, narrow, context, store)
        assert export_path(paths).exists()

        full = approvals.record(plan, approver="b@example.com")
        second = execute_plan(plan, full, context, store, run_id=first.run_id)

        assert not second.failed
        assert not export_path(paths).exists()


class TestResidualExposureModel:
    def test_escalations_are_always_residual(self, plan, approval, context, store):
        report = execute_plan(plan, approval, context, store)
        escalated = [r for r in report.residual if r.reason == ESCALATED]
        assert [r.urn for r in escalated] == [graph.ORPHAN]

    def test_escalation_residual_carries_the_missing_evidence(
        self, plan, approval, context, store
    ):
        report = execute_plan(plan, approval, context, store)
        escalated = next(r for r in report.residual if r.reason == ESCALATED)
        assert "lineage has a gap" in escalated.detail

    def test_residual_entries_are_deterministically_ordered(
        self, plan, approval, context, store
    ):
        report = execute_plan(plan, approval, context, store)
        keys = [(r.urn, r.reason, r.action or "") for r in report.residual]
        assert keys == sorted(keys)

    def test_the_report_serializes_completely(self, plan, approval, context, store):
        payload = execute_plan(plan, approval, context, store).to_dict()
        assert payload["plan_hash"] == plan.plan_hash()
        assert len(payload["steps"]) == len(plan_steps(plan))
        assert payload["fully_executed"] is False
        assert payload["residual_exposure"]


class TestLedger:
    def test_every_step_is_appended_to_the_receipt_ledger(
        self, plan, approval, context, store, tmp_path
    ):
        ledger = ReceiptLedger(tmp_path / "ledger")
        report = execute_plan(plan, approval, context, store, ledger=ledger)

        entries = [e for e in ledger.entries() if e["operation"].startswith("containment.")]
        assert len(entries) == len(report.outcomes)
        assert ledger.verify_chain()[0] is True

    def test_containment_receipts_are_never_marked_simulated(
        self, plan, approval, context, store, tmp_path
    ):
        # These adapters act on real local artifacts. Marking them simulated
        # would understate what actually happened on disk.
        ledger = ReceiptLedger(tmp_path / "ledger")
        execute_plan(plan, approval, context, store, ledger=ledger)
        assert all(
            entry["simulated"] is False
            for entry in ledger.entries()
            if entry["operation"].startswith("containment.")
        )

    def test_a_failed_step_is_recorded_as_a_failure(
        self, plan, approval, context, store, tmp_path
    ):
        ledger = ReceiptLedger(tmp_path / "ledger")
        context.fault_injector = lambda adapter, *_: (
            (_ for _ in ()).throw(ContainmentError("boom"))
            if adapter == "export-quarantine"
            else None
        )
        execute_plan(plan, approval, context, store, ledger=ledger)

        quarantine = next(
            e for e in ledger.entries() if e["operation"] == "containment.quarantine"
        )
        assert quarantine["succeeded"] is False
        assert quarantine["payload"]["status"] == FAILED
