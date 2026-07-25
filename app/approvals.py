"""Persistent, exact-plan approval.

Nothing destructive runs without a recorded human decision, and the decision
binds to *one exact plan*. An approval carries the plan hash and the enforcement
scope it authorized; if either has moved on, the approval no longer applies.

That strictness is the point. The failure mode this guards against is mundane
and serious: a reviewer approves a plan, the graph changes, the plan is
regenerated with a wider scope, and the old sign-off silently authorizes actions
nobody looked at. Here that produces :class:`PlanDrift` and stops.

Approvals are never deleted or amended. A change of mind is a new decision with
a later timestamp, so the audit trail shows what was decided and when.

Persistence matters as much as strictness. An approval held in memory or in a
UI's state disappears with the process; enforcement would then be gated by
whichever tab happened to be open. This lives in SQLite under ``APP_STATE_DIR``
and is re-read on every enforcement attempt.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.rights import Action
from app.store import GovernanceStore
from app.workflow import ImpactPlan

APPROVED = "approved"
REJECTED = "rejected"


class ApprovalError(Exception):
    """Base class for every refusal to enforce."""


class ApprovalRequired(ApprovalError):
    """Raised when no approval exists for the plan being enforced."""


class ApprovalRefused(ApprovalError):
    """Raised when the recorded decision was an explicit rejection."""


class PlanDrift(ApprovalError):
    """Raised when an approval exists for this event but not for *this* plan.

    Its own type because the operator response differs from a missing approval:
    the fix is to re-review the changed plan, not to go and find an approver for
    the first time.
    """


class ScopeViolation(ApprovalError):
    """Raised when enforcement attempts an action the approval did not cover."""


@dataclass(frozen=True)
class Approval:
    """One recorded human decision about one exact plan."""

    approval_id: str
    plan_hash: str
    event_id: str
    event_hash: str
    decision: str
    approver: str
    note: str
    scope: dict[str, tuple[str, ...]]
    decided_at: datetime

    @property
    def approved(self) -> bool:
        return self.decision == APPROVED

    def covers(self, urn: str, action: Action) -> bool:
        """Whether this approval authorizes one specific action on one target."""
        return action.value in self.scope.get(urn, ())

    def to_dict(self) -> dict[str, Any]:
        return {
            "approval_id": self.approval_id,
            "plan_hash": self.plan_hash,
            "event_id": self.event_id,
            "event_hash": self.event_hash,
            "decision": self.decision,
            "approver": self.approver,
            "note": self.note,
            "scope": {urn: list(actions) for urn, actions in self.scope.items()},
            "decided_at": self.decided_at.isoformat(),
        }


class ApprovalStore:
    """Records and retrieves approval decisions."""

    def __init__(self, store: GovernanceStore) -> None:
        self._store = store

    # -- recording ------------------------------------------------------

    def record(
        self,
        plan: ImpactPlan,
        *,
        approver: str,
        decision: str = APPROVED,
        note: str = "",
        scope: dict[str, list[str]] | None = None,
    ) -> Approval:
        """Record one decision against ``plan``.

        ``scope`` defaults to the plan's full enforcement scope. Supplying a
        narrower one is how a reviewer approves part of a plan: anything left out
        simply never gets an adapter run against it, and the executor reports it
        as un-actioned residual exposure rather than skipping it silently.

        Raises:
            ApprovalError: if ``decision`` is not a known value, or if ``scope``
                names an action the plan does not contain. An approval must never
                authorize more than the plan it is attached to.
        """
        if decision not in (APPROVED, REJECTED):
            raise ApprovalError(f"unknown approval decision {decision!r}")

        plan_scope = plan.enforcement_scope()
        chosen = plan_scope if scope is None else scope

        for urn, actions in chosen.items():
            permitted = set(plan_scope.get(urn, ()))
            extra = sorted(set(actions) - permitted)
            if extra:
                raise ApprovalError(
                    f"approval scope for {urn} names {extra}, which the plan does not "
                    "propose. An approval cannot authorize more than the plan it "
                    "is attached to."
                )

        approval = Approval(
            approval_id=f"apr-{uuid.uuid4().hex[:12]}",
            plan_hash=plan.plan_hash(),
            event_id=plan.event.event_id,
            event_hash=plan.event.content_hash(),
            decision=decision,
            approver=approver,
            note=note,
            scope={urn: tuple(actions) for urn, actions in chosen.items()},
            decided_at=datetime.now(UTC),
        )

        self.remember_plan(plan)
        with self._store.connect() as connection:
            connection.execute(
                "INSERT INTO approvals (approval_id, plan_hash, event_id, event_hash, "
                "decision, approver, note, scope, decided_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    approval.approval_id,
                    approval.plan_hash,
                    approval.event_id,
                    approval.event_hash,
                    approval.decision,
                    approval.approver,
                    approval.note,
                    json.dumps(
                        {urn: list(actions) for urn, actions in approval.scope.items()},
                        sort_keys=True,
                    ),
                    approval.decided_at.isoformat(),
                ),
            )
        return approval

    def remember_plan(self, plan: ImpactPlan) -> None:
        """Persist the plan a decision refers to, so drift can be explained."""
        with self._store.connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO plans (plan_hash, event_id, event_hash, payload, "
                "generated_at) VALUES (?, ?, ?, ?, ?)",
                (
                    plan.plan_hash(),
                    plan.event.event_id,
                    plan.event.content_hash(),
                    json.dumps(plan.to_dict(), sort_keys=True),
                    plan.generated_at.isoformat(),
                ),
            )

    # -- retrieval ------------------------------------------------------

    def latest_for_plan(self, plan_hash: str) -> Approval | None:
        """The most recent decision for one exact plan."""
        with self._store.connect() as connection:
            row = connection.execute(
                "SELECT * FROM approvals WHERE plan_hash = ? "
                "ORDER BY decided_at DESC, rowid DESC LIMIT 1",
                (plan_hash,),
            ).fetchone()
        return _to_approval(row) if row else None

    def for_event(self, event_id: str) -> list[Approval]:
        """Every decision recorded for a rights event, newest first."""
        with self._store.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM approvals WHERE event_id = ? ORDER BY decided_at DESC, rowid DESC",
                (event_id,),
            ).fetchall()
        return [_to_approval(row) for row in rows]

    def get(self, approval_id: str) -> Approval | None:
        with self._store.connect() as connection:
            row = connection.execute(
                "SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)
            ).fetchone()
        return _to_approval(row) if row else None


def require_approval(approvals: ApprovalStore, plan: ImpactPlan) -> Approval:
    """Return the approval authorizing this exact plan, or refuse.

    Raises:
        PlanDrift: an approval exists for this rights event, but for a different
            plan. The scope changed after review.
        ApprovalRefused: the recorded decision was a rejection.
        ApprovalRequired: no decision has been recorded at all.
    """
    plan_hash = plan.plan_hash()
    approval = approvals.latest_for_plan(plan_hash)

    if approval is None:
        prior = approvals.for_event(plan.event.event_id)
        if prior:
            approved_hashes = sorted({a.plan_hash for a in prior})
            raise PlanDrift(
                f"Enforcement refused: {len(prior)} decision(s) exist for rights event "
                f"{plan.event.event_id!r}, but none of them approved this plan. "
                f"Approved plan(s): {approved_hashes}; this plan: {plan_hash}. "
                "The plan changed after review and must be re-approved."
            )
        raise ApprovalRequired(
            f"Enforcement refused: no approval has been recorded for plan {plan_hash}. "
            "Destructive containment actions require a recorded human decision."
        )

    if not approval.approved:
        raise ApprovalRefused(
            f"Enforcement refused: plan {plan_hash} was rejected by "
            f"{approval.approver!r} at {approval.decided_at.isoformat()}"
            + (f" -- {approval.note}" if approval.note else "")
        )

    return approval


def require_scope(approval: Approval, urn: str, action: Action) -> None:
    """Assert that ``approval`` authorizes this exact action on this exact target.

    Checked immediately before every adapter call, not once at the start. A
    plan-level check alone would let a bug in step selection enforce something
    outside the approved scope.

    Raises:
        ScopeViolation: if the action was not approved for that target.
    """
    if not approval.covers(urn, action):
        approved = sorted(approval.scope.get(urn, ()))
        raise ScopeViolation(
            f"Enforcement refused: approval {approval.approval_id} does not cover "
            f"{action.value!r} on {urn}. Approved for that target: {approved or 'nothing'}."
        )


def _to_approval(row) -> Approval:
    raw_scope = json.loads(row["scope"])
    return Approval(
        approval_id=row["approval_id"],
        plan_hash=row["plan_hash"],
        event_id=row["event_id"],
        event_hash=row["event_hash"],
        decision=row["decision"],
        approver=row["approver"],
        note=row["note"],
        scope={urn: tuple(actions) for urn, actions in raw_scope.items()},
        decided_at=datetime.fromisoformat(row["decided_at"]),
    )


__all__ = [
    "APPROVED",
    "REJECTED",
    "Approval",
    "ApprovalError",
    "ApprovalRefused",
    "ApprovalRequired",
    "ApprovalStore",
    "PlanDrift",
    "ScopeViolation",
    "require_approval",
    "require_scope",
]
