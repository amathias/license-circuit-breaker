"""The evidence bundle: one durable, self-describing account of a containment.

Assembles the rights event, the plan and the rules that produced it, the
approval, what actually ran, what the probes observed, and what was written back
to DataHub -- as JSON for machines and Markdown for humans.

The bundle is written to state, never to the repository. Runtime evidence
belongs to a run, not to source control, and a committed receipt is a claim
nobody can reproduce.

Two rules govern what it may say:

**The verdict is derived, never asserted.** ``contained`` requires every step to
have completed, every probe to have passed, and the residual list to be empty.
Anything less is ``residual`` or ``escalated``, and the bundle names each
remaining exposure with the reason it survived.

**Simulated runs are labelled.** A run against the in-memory DataHub fake is
marked at the top of both formats. It is legitimate evidence of the local
containment -- the artifacts really did change -- and it is not evidence of
DataHub integration.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.approvals import Approval
from app.execution import ESCALATED, ExecutionReport, ResidualExposure
from app.rights import Verdict
from app.verification import VerificationReport
from app.workflow import ImpactPlan

#: Stated wherever the bundle could be mistaken for a legal conclusion.
LEGAL_DISCLAIMER = (
    "License Circuit Breaker supports compliance operations. It does not provide "
    "legal advice, does not interpret contract text, and makes no determination "
    "about whether any obligation has been met. The rights asserted here are the "
    "ones an operator recorded."
)

#: Stated wherever the bundle could be mistaken for a completeness guarantee.
SCOPE_DISCLAIMER = (
    "Containment covers descendants represented in the demonstrated DataHub graph. "
    "Untracked copies, offline extracts, and systems outside the graph are not "
    "addressed. Stopping a model serving is not proof that it has unlearned its "
    "training data."
)


@dataclass(frozen=True)
class EvidenceBundle:
    """Everything a reviewer needs to check the claim, in one object."""

    generated_at: datetime
    plan: ImpactPlan
    approval: Approval | None = None
    execution: ExecutionReport | None = None
    verification: VerificationReport | None = None
    writeback: dict[str, Any] | None = None
    estate: dict[str, Any] | None = None
    #: True when the DataHub side ran against the in-memory fake. Local artifact
    #: changes are real either way; this flags only the catalog integration.
    simulated: bool = True

    # -- derived judgements ---------------------------------------------

    def residual(self) -> tuple[ResidualExposure, ...]:
        """Every unresolved exposure, from execution and verification together.

        Deduplicated on (urn, reason, action): a failed quarantine produces both
        an execution failure and a failed probe, and counting it twice would
        overstate the number of distinct problems while telling an operator
        nothing new.
        """
        collected: list[ResidualExposure] = []
        if self.execution is not None:
            collected.extend(self.execution.residual)
        if self.verification is not None:
            collected.extend(self.verification.residual())

        seen: set[tuple[str, str, str | None]] = set()
        unique: list[ResidualExposure] = []
        for entry in collected:
            key = (entry.urn, entry.reason, entry.action)
            if key in seen:
                continue
            seen.add(key)
            unique.append(entry)

        unique.sort(key=lambda r: (r.urn, r.reason, r.action or ""))
        return tuple(unique)

    def verdict(self) -> Verdict:
        """The overall outcome, derived from evidence rather than asserted."""
        if self.execution is None:
            return "not_started"

        residual = self.residual()
        probes_passed = self.verification is not None and self.verification.contained

        if not residual and not self.execution.failed and probes_passed:
            return "contained"
        if residual and all(entry.reason == ESCALATED for entry in residual):
            return "escalated"
        return "residual"

    @property
    def contained_urns(self) -> tuple[str, ...]:
        """Artifacts a passing containment probe confirmed."""
        if self.verification is None:
            return ()
        return tuple(
            sorted(p.urn for p in self.verification.containment_probes if p.passed)
        )

    # -- serialization ---------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at.isoformat(),
            "simulated": self.simulated,
            "verdict": self.verdict(),
            "disclaimers": {"legal": LEGAL_DISCLAIMER, "scope": SCOPE_DISCLAIMER},
            "rights_event": json.loads(self.plan.event.model_dump_json()),
            "plan": self.plan.to_dict(),
            "approval": self.approval.to_dict() if self.approval else None,
            "execution": self.execution.to_dict() if self.execution else None,
            "verification": self.verification.to_dict() if self.verification else None,
            "datahub_writeback": self.writeback,
            "estate": self.estate,
            "contained": list(self.contained_urns),
            "residual_exposure": [r.to_dict() for r in self.residual()],
        }

    def to_markdown(self) -> str:
        event = self.plan.event
        verdict = self.verdict()
        lines: list[str] = [
            "# Containment report",
            "",
            f"**Verdict:** `{verdict}`  ",
            f"**Generated:** {self.generated_at.isoformat()}  ",
            f"**Plan:** `{self.plan.plan_hash()}`",
            "",
        ]

        if self.simulated:
            lines += [
                "> **SIMULATED DATAHUB RUN.** The catalog reads and the writeback in this "
                "report ran against the deterministic in-memory DataHub substitute, not a "
                "live instance. The local artifact changes below are real and were probed "
                "directly.",
                "",
            ]

        lines += [
            "## Rights event",
            "",
            f"- **Event:** `{event.event_id}` v{event.version}",
            f"- **Source:** `{event.source_urn}`",
            f"- **Effective:** {event.effective_at.isoformat()}",
            f"- **Reason:** {event.reason}",
            f"- **Purposes lost:** {', '.join(sorted(p.value for p in event.lost_purposes))}",
            f"- **Replacement source:** `{event.replacement_source_urn or 'none'}`",
            f"- **Requested by:** {event.requester}",
            "",
            "## Decisions",
            "",
            "| Priority | Artifact | Class | Actions | Rules |",
            "|---:|---|---|---|---|",
        ]
        for decision in self.plan.decisions:
            lines.append(
                f"| {decision.priority} | `{_short(decision.descendant_urn)}` "
                f"| {decision.artifact_class.value} "
                f"| {', '.join(a.value for a in decision.actions)} "
                f"| {', '.join(decision.rule_ids)} |"
            )

        lines += ["", "## Approval", ""]
        if self.approval is None:
            lines.append("_No approval was recorded. Nothing was enforced._")
        else:
            lines += [
                f"- **Decision:** {self.approval.decision}",
                f"- **Approver:** {self.approval.approver}",
                f"- **At:** {self.approval.decided_at.isoformat()}",
                f"- **Approval:** `{self.approval.approval_id}`",
                f"- **Bound to plan:** `{self.approval.plan_hash}`",
            ]
            if self.approval.note:
                lines.append(f"- **Note:** {self.approval.note}")

        lines += ["", "## Execution", ""]
        if self.execution is None:
            lines.append("_Not executed._")
        else:
            lines += [
                f"{self.execution.describe()}",
                "",
                "| # | Artifact | Action | Status | Changed | Detail |",
                "|---:|---|---|---|---|---|",
            ]
            for outcome in self.execution.outcomes:
                lines.append(
                    f"| {outcome.step.seq} | `{_short(outcome.step.urn)}` "
                    f"| {outcome.step.action.value} | {outcome.status} "
                    f"| {'yes' if outcome.changed else 'no'} "
                    f"| {outcome.error or outcome.detail} |"
                )

        lines += ["", "## Verification", ""]
        if self.verification is None:
            lines.append("_Not verified._")
        else:
            lines += [
                f"{self.verification.describe()}",
                "",
                "| Artifact | Probe | Result | Observed |",
                "|---|---|---|---|",
            ]
            for probe in self.verification.probes:
                lines.append(
                    f"| `{_short(probe.urn)}` | {probe.method} "
                    f"| {'PASS' if probe.passed else 'FAIL'} | {probe.observed} |"
                )

        lines += ["", "## Residual exposure", ""]
        residual = self.residual()
        if not residual:
            lines.append("None. Every probed artifact was confirmed contained.")
        else:
            lines += ["| Artifact | Reason | Action | Detail |", "|---|---|---|---|"]
            for entry in residual:
                lines.append(
                    f"| `{_short(entry.urn)}` | {entry.reason} "
                    f"| {entry.action or '-'} | {entry.detail} |"
                )

        lines += ["", "## DataHub writeback", ""]
        if self.writeback is None:
            lines.append("_No writeback was attempted._")
        else:
            # Receipts are rendered as a table rather than through the scalar
            # branch below. Interpolating the list into an f-string emits a
            # Python dict repr -- one unreadable line per run, in the section a
            # reviewer opens the report to read.
            receipts = self.writeback.get("receipts")
            for key, value in sorted(self.writeback.items()):
                if key == "receipts":
                    continue
                lines.append(f"- **{key}:** `{value}`")

            if isinstance(receipts, list) and receipts:
                lines += [
                    "",
                    "| Artifact | Status | Tag | Aspects | Verified |",
                    "|---|---|---|---|---|",
                ]
                for receipt in receipts:
                    aspects = receipt.get("aspects") or []
                    lines.append(
                        f"| `{_short(str(receipt.get('urn', '')))}` "
                        f"| {receipt.get('status', '-')} "
                        f"| `{receipt.get('tag', '-')}` "
                        f"| {', '.join(str(a) for a in aspects) or '-'} "
                        f"| {'yes' if receipt.get('verified') else 'NO'} |"
                    )

        lines += [
            "",
            "## Limitations",
            "",
            f"- {LEGAL_DISCLAIMER}",
            f"- {SCOPE_DISCLAIMER}",
            "",
        ]
        return "\n".join(lines)

    def write(self, directory: Path | str, stem: str = "containment-report") -> tuple[Path, Path]:
        """Write both formats. Returns ``(json_path, markdown_path)``."""
        target = Path(directory)
        target.mkdir(parents=True, exist_ok=True)

        json_path = target / f"{stem}.json"
        markdown_path = target / f"{stem}.md"
        json_path.write_text(
            json.dumps(self.to_dict(), indent=2, sort_keys=True, default=str), encoding="utf-8"
        )
        markdown_path.write_text(self.to_markdown(), encoding="utf-8")
        return json_path, markdown_path


def build_bundle(
    plan: ImpactPlan,
    *,
    approval: Approval | None = None,
    execution: ExecutionReport | None = None,
    verification: VerificationReport | None = None,
    writeback: dict[str, Any] | None = None,
    estate: dict[str, Any] | None = None,
    simulated: bool = True,
) -> EvidenceBundle:
    """Assemble a bundle from whatever stages have run so far."""
    return EvidenceBundle(
        generated_at=datetime.now(UTC),
        plan=plan,
        approval=approval,
        execution=execution,
        verification=verification,
        writeback=writeback,
        estate=estate,
        simulated=simulated,
    )


def _short(urn: str) -> str:
    """The readable middle of a tuple URN, for table cells."""
    return urn.rsplit(",", 2)[-2] if urn.count(",") >= 2 else urn


__all__ = [
    "LEGAL_DISCLAIMER",
    "SCOPE_DISCLAIMER",
    "EvidenceBundle",
    "build_bundle",
]
