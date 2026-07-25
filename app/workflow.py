"""The Milestone B vertical slice.

Wires one rights event end to end: read real license and lineage context, validate
it, produce a deterministic containment decision, perform one reversible
``license.``-scoped writeback, immediately re-read and restore it, and persist
sanitized receipts.

Enforcement of local artifacts is deliberately **not** here. This milestone proves
the governed path from a rights event to an approved plan and a reversible
writeback; the containment adapters that freeze APIs and purge indexes arrive with
their own approval gate in the next milestone.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime

from adapters.datahub import (
    DataHubClient,
    DataHubError,
    WritebackReceipt,
    reversible_tag_writeback,
)
from app.context import ContextValidation, discover_descendants, validate_entity
from app.namespace import Namespace, NamespaceViolation, require_in_namespace
from app.policy import PolicyTable, evaluate_all
from app.receipts import ReceiptLedger
from app.rights import DESTRUCTIVE_ACTIONS, ImpactDecision, RightsEvent

#: Tag applied during the reversible writeback demonstration.
REVOCATION_TAG = "license-revocation-under-review"


class WorkflowError(Exception):
    """Raised when the slice cannot proceed safely."""


@dataclass(frozen=True)
class ImpactPlan:
    """A complete, evidence-backed containment plan awaiting approval."""

    event: RightsEvent
    decisions: tuple[ImpactDecision, ...]
    validations: tuple[ContextValidation, ...]
    generated_at: datetime

    @property
    def escalations(self) -> tuple[ImpactDecision, ...]:
        return tuple(d for d in self.decisions if d.is_escalation)

    @property
    def destructive(self) -> tuple[ImpactDecision, ...]:
        return tuple(d for d in self.decisions if d.is_destructive)

    @property
    def requires_approval(self) -> bool:
        return any(d.requires_approval for d in self.decisions)

    @property
    def all_clear(self) -> bool:
        """True only when nothing needs containment *and* nothing escalated.

        An escalation must never be reported as an all-clear -- that is the
        specific false negative this product exists to prevent.
        """
        return not self.destructive and not self.escalations

    def plan_hash(self) -> str:
        """Stable fingerprint of exactly what this plan would enforce.

        Covers the rights event's content plus every decision's target, actions,
        rules, and priority -- and nothing else. ``generated_at`` is deliberately
        excluded, so regenerating the same plan from the same graph produces the
        same hash and an existing approval still applies.

        This is the value an approval binds to. Any change to what would be
        enforced changes the hash, which invalidates the approval rather than
        letting a re-planned scope ride on an old sign-off.
        """
        payload = {
            "event": self.event.content_hash(),
            "decisions": [
                {
                    "urn": decision.descendant_urn,
                    "actions": [a.value for a in decision.actions],
                    "rule_ids": list(decision.rule_ids),
                    "priority": decision.priority,
                }
                for decision in self.decisions
            ],
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def enforcement_scope(self) -> dict[str, list[str]]:
        """The destructive actions this plan would perform, per descendant.

        Only these need approval and only these reach an adapter. ``no_action``
        and ``escalate`` are outcomes, not operations.
        """
        scope: dict[str, list[str]] = {}
        for decision in self.decisions:
            actions = [a.value for a in decision.actions if a in DESTRUCTIVE_ACTIONS]
            if actions:
                scope[decision.descendant_urn] = actions
        return scope

    def to_dict(self) -> dict:
        """Serializable form, for persistence and the judge console."""
        return {
            "plan_hash": self.plan_hash(),
            "generated_at": self.generated_at.isoformat(),
            "event": json.loads(self.event.model_dump_json()),
            "event_hash": self.event.content_hash(),
            "all_clear": self.all_clear,
            "requires_approval": self.requires_approval,
            "enforcement_scope": self.enforcement_scope(),
            "decisions": [json.loads(d.model_dump_json()) for d in self.decisions],
            "validations": [
                {
                    "urn": v.urn,
                    "present": v.present,
                    "in_namespace": v.in_namespace,
                    "has_project_tag": v.has_project_tag,
                    "in_project_domain": v.in_project_domain,
                    "usable": v.usable,
                    "issues": list(v.issues),
                }
                for v in self.validations
            ],
        }

    def decision_for(self, urn: str) -> ImpactDecision | None:
        for decision in self.decisions:
            if decision.descendant_urn == urn:
                return decision
        return None

    @property
    def escalated_urns(self) -> tuple[str, ...]:
        """Descendants whose impact could not be resolved.

        These carry residual exposure by construction: an escalation means the
        evidence was insufficient to act, not that there was nothing to do.
        """
        return tuple(d.descendant_urn for d in self.escalations)


def build_impact_plan(
    client: DataHubClient,
    event: RightsEvent,
    namespace: Namespace,
    table: PolicyTable | None = None,
    ledger: ReceiptLedger | None = None,
    simulated: bool = False,
) -> ImpactPlan:
    """Read context, validate it, and produce the deterministic plan.

    Raises:
        NamespaceViolation: if the event's source is outside the allocation.
        WorkflowError: if the source entity cannot be read or is not ours.
    """
    require_in_namespace(event.source_urn, namespace, operation="build_impact_plan")

    try:
        source = client.get_entity(event.source_urn)
    except DataHubError as exc:
        raise WorkflowError(f"Could not read source entity: {exc}") from exc

    source_validation = validate_entity(source, event.source_urn, namespace)
    if not source_validation.usable:
        raise WorkflowError(
            f"Source entity is not usable: {'; '.join(source_validation.issues)}"
        )

    descendants, validations = discover_descendants(
        client, event.source_urn, namespace, lost_purposes=event.lost_purposes
    )
    decisions = evaluate_all(event, descendants, table)

    if ledger is not None:
        ledger.append(
            operation="impact_plan",
            urn=event.source_urn,
            succeeded=True,
            simulated=simulated,
            detail=(
                f"{len(decisions)} decisions, {sum(1 for d in decisions if d.is_escalation)} "
                f"escalations"
            ),
            payload={
                "event_id": event.event_id,
                "event_hash": event.content_hash(),
                "rule_ids": sorted({rid for d in decisions for rid in d.rule_ids}),
                "descendant_count": len(descendants),
            },
        )

    return ImpactPlan(
        event=event,
        decisions=tuple(decisions),
        validations=(source_validation, *validations),
        generated_at=datetime.now(UTC),
    )


def perform_reversible_writeback(
    client: DataHubClient,
    urn: str,
    namespace: Namespace,
    ledger: ReceiptLedger | None = None,
    tag: str = REVOCATION_TAG,
    simulated: bool = False,
) -> WritebackReceipt:
    """Apply a tag, re-read to prove it landed, restore, and record the receipt.

    The receipt is persisted whether or not the write succeeded. A failed or
    unrestored writeback is exactly the thing an evidence log must not omit.
    """
    require_in_namespace(urn, namespace, operation="perform_reversible_writeback")

    try:
        receipt = reversible_tag_writeback(client, urn, tag, namespace)
    except (DataHubError, NamespaceViolation) as exc:
        if ledger is not None:
            ledger.append(
                operation="writeback",
                urn=urn,
                succeeded=False,
                simulated=simulated,
                detail=f"writeback failed: {exc}",
                payload={"tag": tag},
            )
        raise

    if ledger is not None:
        ledger.append(
            operation="writeback",
            urn=urn,
            # Only a verified *and* restored write counts as success. A write that
            # landed but was not rolled back left residue on a shared instance.
            succeeded=receipt.clean,
            simulated=simulated,
            detail=receipt.detail,
            payload={
                "aspect": receipt.aspect,
                "applied_value": receipt.applied_value,
                "prior_value": receipt.prior_value,
                "started": receipt.started,
                "write_failed": receipt.write_failed,
                "verified": receipt.verified,
                "restored": receipt.restored,
                "residual_risk": receipt.residual_risk,
            },
        )

    return receipt
