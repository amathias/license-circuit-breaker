"""Approved containment execution: ordered, journalled, resumable.

The executor turns an approved plan into adapter calls, writes every outcome to
a durable journal as it happens, and reports what is still exposed at the end.

Three behaviours matter more than the mechanics:

**A failure does not abort the run.** If quarantine fails, the freeze and the
purge still need to happen -- stopping would leave *more* exposed, not less. The
failure is recorded and surfaces as residual exposure.

**Resume replays only unfinished work.** Each step is journalled the moment it
completes, so a run killed halfway can be resumed and will not re-purge an index
it already rebuilt. The adapters are idempotent as a second line of defence, but
correctness does not depend on that.

**Nothing is quietly dropped.** Every decision in the plan appears in the report
with a status. An action nobody approved, an artifact class no adapter supports,
and an escalation that could not be acted on are all residual exposure with
distinct reasons, because an operator responds to each of them differently.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from adapters.containment import (
    NON_EXECUTABLE_ACTIONS,
    AdapterContext,
    AdapterReceipt,
    AdapterRegistry,
    NoAdapterError,
    execution_stage,
)
from app.approvals import Approval, ScopeViolation, require_scope
from app.receipts import ReceiptLedger
from app.rights import DESTRUCTIVE_ACTIONS, Action, ArtifactClass
from app.store import GovernanceStore
from app.workflow import ImpactPlan

#: Step statuses. ``completed`` is the only one a resume skips.
COMPLETED = "completed"
FAILED = "failed"
UNSUPPORTED = "unsupported"
NOT_APPROVED = "not_approved"

RUN_RUNNING = "running"
RUN_FINISHED = "finished"


class ExecutionError(Exception):
    """Raised when a run cannot start or be resumed."""


# --- residual exposure -------------------------------------------------

#: Why an artifact is still exposed. Distinct reasons because the operator
#: response differs: retry, escalate to a human, or accept and document.
ACTION_FAILED = "action_failed"
NO_ADAPTER = "no_adapter"
NOT_APPROVED_REASON = "not_approved"
ESCALATED = "escalated"
VERIFICATION_FAILED = "verification_failed"


@dataclass(frozen=True)
class ResidualExposure:
    """One artifact that is not demonstrably contained, and why."""

    urn: str
    reason: str
    detail: str
    action: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "urn": self.urn,
            "reason": self.reason,
            "detail": self.detail,
            "action": self.action,
        }


# --- steps -------------------------------------------------------------


@dataclass(frozen=True)
class PlannedStep:
    """One adapter call the plan implies."""

    seq: int
    urn: str
    action: Action
    artifact_class: ArtifactClass

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "urn": self.urn,
            "action": self.action.value,
            "artifact_class": self.artifact_class.value,
        }


@dataclass(frozen=True)
class StepOutcome:
    """What happened to one step."""

    step: PlannedStep
    status: str
    changed: bool
    detail: str
    error: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    #: True when this step was already complete in the journal and was not re-run.
    resumed: bool = False

    @property
    def succeeded(self) -> bool:
        return self.status == COMPLETED

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.step.to_dict(),
            "status": self.status,
            "changed": self.changed,
            "detail": self.detail,
            "error": self.error,
            "evidence": self.evidence,
            "resumed": self.resumed,
        }


def plan_steps(plan: ImpactPlan) -> tuple[PlannedStep, ...]:
    """Expand a plan into ordered adapter calls.

    Ordering comes from :func:`~adapters.containment.execution_stage` and is
    total, so the same plan always produces the same sequence and a resumed run
    lines up with the journal it is resuming. Non-executable outcomes
    (``no_action``, ``escalate``) produce no step; they are reported directly.
    """
    candidates: list[tuple[int, str, str, Action, ArtifactClass]] = []
    for decision in plan.decisions:
        for action in decision.actions:
            if action in NON_EXECUTABLE_ACTIONS or action not in DESTRUCTIVE_ACTIONS:
                continue
            candidates.append(
                (
                    execution_stage(action, decision.artifact_class),
                    decision.descendant_urn,
                    action.value,
                    action,
                    decision.artifact_class,
                )
            )

    candidates.sort(key=lambda entry: (entry[0], entry[1], entry[2]))
    return tuple(
        PlannedStep(seq=index, urn=urn, action=action, artifact_class=artifact_class)
        for index, (_stage, urn, _name, action, artifact_class) in enumerate(candidates)
    )


# --- journal -----------------------------------------------------------


class ExecutionJournal:
    """Durable per-step record of one run."""

    def __init__(self, store: GovernanceStore) -> None:
        self._store = store

    def start(self, run_id: str, plan_hash: str, approval_id: str) -> None:
        with self._store.connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO runs (run_id, plan_hash, approval_id, status, started_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (run_id, plan_hash, approval_id, RUN_RUNNING, datetime.now(UTC).isoformat()),
            )

    def finish(self, run_id: str) -> None:
        with self._store.connect() as connection:
            connection.execute(
                "UPDATE runs SET status = ?, finished_at = ? WHERE run_id = ?",
                (RUN_FINISHED, datetime.now(UTC).isoformat(), run_id),
            )

    def record(self, run_id: str, outcome: StepOutcome) -> None:
        """Persist one step immediately. This is what makes resume possible."""
        with self._store.connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO steps (run_id, seq, urn, action, status, changed, "
                "detail, error, evidence, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    outcome.step.seq,
                    outcome.step.urn,
                    outcome.step.action.value,
                    outcome.status,
                    int(outcome.changed),
                    outcome.detail,
                    outcome.error,
                    json.dumps(outcome.evidence, sort_keys=True, default=str),
                    datetime.now(UTC).isoformat(),
                ),
            )

    def completed_steps(self, run_id: str) -> dict[int, dict[str, Any]]:
        """Steps already finished successfully, keyed by sequence number."""
        with self._store.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM steps WHERE run_id = ? AND status = ?", (run_id, COMPLETED)
            ).fetchall()
        return {int(row["seq"]): dict(row) for row in rows}

    def run(self, run_id: str) -> dict[str, Any] | None:
        with self._store.connect() as connection:
            row = connection.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return dict(row) if row else None

    def runs_for_plan(self, plan_hash: str) -> list[dict[str, Any]]:
        with self._store.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM runs WHERE plan_hash = ? ORDER BY started_at DESC", (plan_hash,)
            ).fetchall()
        return [dict(row) for row in rows]


# --- report ------------------------------------------------------------


@dataclass(frozen=True)
class ExecutionReport:
    """Everything one run did, and everything it left exposed."""

    run_id: str
    plan_hash: str
    approval_id: str
    started_at: datetime
    finished_at: datetime
    outcomes: tuple[StepOutcome, ...]
    residual: tuple[ResidualExposure, ...]

    @property
    def completed(self) -> tuple[StepOutcome, ...]:
        return tuple(o for o in self.outcomes if o.status == COMPLETED)

    @property
    def failed(self) -> tuple[StepOutcome, ...]:
        return tuple(o for o in self.outcomes if o.status == FAILED)

    @property
    def resumed(self) -> tuple[StepOutcome, ...]:
        return tuple(o for o in self.outcomes if o.resumed)

    @property
    def fully_executed(self) -> bool:
        """Whether every planned step completed and nothing is left exposed.

        Deliberately *not* named ``contained``. Execution succeeding means the
        adapters ran; only verification can say containment held. Conflating the
        two is precisely how a false all-clear gets issued.
        """
        return not self.failed and not self.residual

    def describe(self) -> str:
        parts = [f"{len(self.completed)}/{len(self.outcomes)} steps completed"]
        if self.failed:
            parts.append(f"{len(self.failed)} FAILED")
        if self.resumed:
            parts.append(f"{len(self.resumed)} resumed from journal")
        if self.residual:
            parts.append(f"{len(self.residual)} residual exposure(s)")
        return ", ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "plan_hash": self.plan_hash,
            "approval_id": self.approval_id,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "fully_executed": self.fully_executed,
            "summary": self.describe(),
            "steps": [o.to_dict() for o in self.outcomes],
            "residual_exposure": [r.to_dict() for r in self.residual],
        }


# --- execution ---------------------------------------------------------


def execute_plan(
    plan: ImpactPlan,
    approval: Approval,
    context: AdapterContext,
    store: GovernanceStore,
    *,
    registry: AdapterRegistry | None = None,
    run_id: str | None = None,
    ledger: ReceiptLedger | None = None,
) -> ExecutionReport:
    """Execute an approved plan, journalling as it goes.

    Passing an existing ``run_id`` resumes that run: steps already recorded as
    completed are reported as ``resumed`` and are not re-run.

    Raises:
        ExecutionError: if the approval does not authorize this exact plan. This
            is checked here as well as at the gate, because the executor is the
            last thing between an approval and a destructive action.
    """
    registry = registry or AdapterRegistry()
    journal = ExecutionJournal(store)

    if approval.plan_hash != plan.plan_hash():
        raise ExecutionError(
            f"Execution refused: approval {approval.approval_id} authorizes plan "
            f"{approval.plan_hash}, not {plan.plan_hash()}."
        )
    if not approval.approved:
        raise ExecutionError(
            f"Execution refused: approval {approval.approval_id} is a {approval.decision}."
        )

    run_id = run_id or f"run-{uuid.uuid4().hex[:12]}"
    started_at = datetime.now(UTC)
    journal.start(run_id, plan.plan_hash(), approval.approval_id)

    already_done = journal.completed_steps(run_id)
    steps = plan_steps(plan)
    outcomes: list[StepOutcome] = []

    for step in steps:
        if step.seq in already_done:
            row = already_done[step.seq]
            outcomes.append(
                StepOutcome(
                    step=step,
                    status=COMPLETED,
                    changed=bool(row["changed"]),
                    detail=row["detail"],
                    evidence=json.loads(row["evidence"] or "{}"),
                    resumed=True,
                )
            )
            continue

        outcome = _run_step(registry, context, approval, step)
        journal.record(run_id, outcome)
        _log(ledger, run_id, outcome)
        outcomes.append(outcome)

    journal.finish(run_id)
    finished_at = datetime.now(UTC)

    return ExecutionReport(
        run_id=run_id,
        plan_hash=plan.plan_hash(),
        approval_id=approval.approval_id,
        started_at=started_at,
        finished_at=finished_at,
        outcomes=tuple(outcomes),
        residual=residual_exposure(plan, tuple(outcomes)),
    )


def _run_step(
    registry: AdapterRegistry,
    context: AdapterContext,
    approval: Approval,
    step: PlannedStep,
) -> StepOutcome:
    """Run one step, converting every failure mode into a recorded outcome.

    An exception escaping here would abort the run and leave the remaining
    artifacts exposed, so each failure mode becomes a status instead.
    """
    try:
        require_scope(approval, step.urn, step.action)
    except ScopeViolation as exc:
        return StepOutcome(
            step=step,
            status=NOT_APPROVED,
            changed=False,
            detail="skipped: outside the approved scope",
            error=str(exc),
        )

    try:
        receipt: AdapterReceipt = registry.execute(context, step.urn, step.action)
    except NoAdapterError as exc:
        return StepOutcome(
            step=step,
            status=UNSUPPORTED,
            changed=False,
            detail="no adapter implements this action for this artifact class",
            error=str(exc),
        )
    except Exception as exc:  # namespace violations, unresolvable artifacts
        return StepOutcome(
            step=step,
            status=FAILED,
            changed=False,
            detail="refused before the adapter ran",
            error=f"{type(exc).__name__}: {exc}",
        )

    return StepOutcome(
        step=step,
        status=COMPLETED if receipt.succeeded else FAILED,
        changed=receipt.changed,
        detail=receipt.detail,
        error=receipt.error,
        evidence={**receipt.evidence, "adapter": receipt.adapter},
    )


def load_report(
    store: GovernanceStore, plan: ImpactPlan, run_id: str, approval_id: str
) -> ExecutionReport | None:
    """Reconstruct a finished run's report from the journal.

    Reading evidence must never have side effects, so this replays what was
    recorded rather than re-running anything. Returns None when the run is
    unknown.
    """
    journal = ExecutionJournal(store)
    run_row = journal.run(run_id)
    if run_row is None:
        return None

    recorded = journal.completed_steps(run_id)
    with store.connect() as connection:
        rows = connection.execute(
            "SELECT * FROM steps WHERE run_id = ?", (run_id,)
        ).fetchall()
    by_seq = {int(row["seq"]): dict(row) for row in rows}
    by_seq.update(recorded)

    outcomes = [
        StepOutcome(
            step=step,
            status=by_seq[step.seq]["status"],
            changed=bool(by_seq[step.seq]["changed"]),
            detail=by_seq[step.seq]["detail"],
            error=by_seq[step.seq]["error"],
            evidence=json.loads(by_seq[step.seq]["evidence"] or "{}"),
            resumed=True,
        )
        for step in plan_steps(plan)
        if step.seq in by_seq
    ]

    started = datetime.fromisoformat(run_row["started_at"])
    finished = datetime.fromisoformat(run_row["finished_at"] or run_row["started_at"])

    return ExecutionReport(
        run_id=run_id,
        plan_hash=plan.plan_hash(),
        approval_id=approval_id,
        started_at=started,
        finished_at=finished,
        outcomes=tuple(outcomes),
        residual=residual_exposure(plan, tuple(outcomes)),
    )


def residual_exposure(
    plan: ImpactPlan, outcomes: tuple[StepOutcome, ...]
) -> tuple[ResidualExposure, ...]:
    """Everything the run did not demonstrably contain.

    Four distinct sources, kept distinct because the operator response differs:

    - a step failed -- retry, or contain by hand;
    - no adapter supports the artifact class -- contain outside this tool;
    - the action was outside the approved scope -- go back to the approver;
    - the decision escalated -- a human must establish the missing evidence.

    Collapsing these into one count would tell an operator that something is
    wrong without telling them what to do about it.
    """
    residual: list[ResidualExposure] = []

    for outcome in outcomes:
        if outcome.status == FAILED:
            residual.append(
                ResidualExposure(
                    urn=outcome.step.urn,
                    reason=ACTION_FAILED,
                    detail=outcome.error or outcome.detail,
                    action=outcome.step.action.value,
                )
            )
        elif outcome.status == UNSUPPORTED:
            residual.append(
                ResidualExposure(
                    urn=outcome.step.urn,
                    reason=NO_ADAPTER,
                    detail=outcome.error or outcome.detail,
                    action=outcome.step.action.value,
                )
            )
        elif outcome.status == NOT_APPROVED:
            residual.append(
                ResidualExposure(
                    urn=outcome.step.urn,
                    reason=NOT_APPROVED_REASON,
                    detail=(
                        "the plan proposed this action but the approval did not "
                        "authorize it"
                    ),
                    action=outcome.step.action.value,
                )
            )

    for decision in plan.escalations:
        residual.append(
            ResidualExposure(
                urn=decision.descendant_urn,
                reason=ESCALATED,
                detail="; ".join(decision.missing_evidence) or decision.rationale,
                action=Action.ESCALATE.value,
            )
        )

    residual.sort(key=lambda r: (r.urn, r.reason, r.action or ""))
    return tuple(residual)


def _log(ledger: ReceiptLedger | None, run_id: str, outcome: StepOutcome) -> None:
    if ledger is None:
        return
    ledger.append(
        operation=f"containment.{outcome.step.action.value}",
        urn=outcome.step.urn,
        succeeded=outcome.succeeded,
        # Adapters act on real local artifacts, never on a simulation.
        simulated=False,
        detail=outcome.detail,
        payload={
            "run_id": run_id,
            "seq": outcome.step.seq,
            "status": outcome.status,
            "changed": outcome.changed,
            "error": outcome.error,
            "evidence": outcome.evidence,
        },
    )


__all__ = [
    "ACTION_FAILED",
    "COMPLETED",
    "ESCALATED",
    "FAILED",
    "NOT_APPROVED",
    "NOT_APPROVED_REASON",
    "NO_ADAPTER",
    "RUN_FINISHED",
    "RUN_RUNNING",
    "UNSUPPORTED",
    "VERIFICATION_FAILED",
    "ExecutionError",
    "ExecutionJournal",
    "ExecutionReport",
    "PlannedStep",
    "ResidualExposure",
    "StepOutcome",
    "execute_plan",
    "load_report",
    "plan_steps",
    "residual_exposure",
]
