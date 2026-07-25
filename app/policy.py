"""Deterministic policy evaluator.

Loads ``policy/rules.yaml`` and turns a rights event plus a discovered descendant
into an :class:`~app.rights.ImpactDecision`. No language model participates in
this path: the same inputs always yield the same verdict, and every verdict names
the rule ids that produced it.

An LLM may narrate a plan produced here, or propose a rights event for a human to
record, but it cannot decide enforcement.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.rights import (
    Action,
    Criticality,
    Descendant,
    Exposure,
    ImpactDecision,
    RightsEvent,
)

DEFAULT_RULES_PATH = Path(__file__).resolve().parent.parent / "policy" / "rules.yaml"


class PolicyError(Exception):
    """Raised when the rule table is malformed or cannot produce a verdict."""


@dataclass(frozen=True)
class Rule:
    """One entry from the policy table."""

    id: str
    description: str
    precedence: int
    when: Mapping[str, Any]
    actions: tuple[Action, ...]
    missing_evidence: tuple[str, ...] = ()
    requires_approval: bool = True


@dataclass(frozen=True)
class PriorityModel:
    """Additive, deterministic priority scoring."""

    exposure: Mapping[str, int]
    criticality: Mapping[str, int]
    escalation_bonus: int
    max: int

    def score(self, exposure: Exposure, criticality: Criticality, escalated: bool) -> int:
        total = self.exposure.get(exposure.value, 0) + self.criticality.get(criticality.value, 0)
        if escalated:
            total += self.escalation_bonus
        return min(total, self.max)


@dataclass(frozen=True)
class PolicyTable:
    """The loaded, ordered rule set."""

    version: int
    rules: tuple[Rule, ...]
    priority: PriorityModel

    def rule_ids(self) -> tuple[str, ...]:
        return tuple(r.id for r in self.rules)


def load_policy(path: Path | str = DEFAULT_RULES_PATH) -> PolicyTable:
    """Parse and validate the rule table.

    Raises:
        PolicyError: if the file is malformed, has duplicate ids or precedences,
            or names an unknown action.
    """
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or "rules" not in raw:
        raise PolicyError(f"Rule table at {path} is missing a top-level 'rules' key")

    rules: list[Rule] = []
    for entry in raw["rules"]:
        try:
            actions = tuple(Action(a) for a in entry["then"]["actions"])
        except ValueError as exc:
            raise PolicyError(f"Rule {entry.get('id')!r} names an unknown action: {exc}") from exc
        except KeyError as exc:
            raise PolicyError(f"Rule {entry.get('id')!r} is missing {exc}") from exc

        rules.append(
            Rule(
                id=entry["id"],
                description=entry.get("description", "").strip(),
                precedence=entry["precedence"],
                when=entry.get("when") or {},
                actions=actions,
                missing_evidence=tuple(entry["then"].get("missing_evidence", ())),
                requires_approval=entry["then"].get("requires_approval", True),
            )
        )

    ids = [r.id for r in rules]
    if len(set(ids)) != len(ids):
        raise PolicyError("Duplicate rule ids in the policy table")

    precedences = [r.precedence for r in rules]
    if len(set(precedences)) != len(precedences):
        # Ties would make evaluation order depend on file ordering, which is a
        # silent source of non-determinism.
        raise PolicyError("Duplicate precedence values make rule evaluation ambiguous")

    priority_raw = raw.get("priority", {})
    priority = PriorityModel(
        exposure=priority_raw.get("exposure", {}),
        criticality=priority_raw.get("criticality", {}),
        escalation_bonus=priority_raw.get("escalation_bonus", 0),
        max=priority_raw.get("max", 100),
    )

    return PolicyTable(
        version=raw.get("version", 1),
        rules=tuple(sorted(rules, key=lambda r: r.precedence)),
        priority=priority,
    )


@lru_cache(maxsize=1)
def get_policy() -> PolicyTable:
    """Return the cached default policy table."""
    return load_policy()


def is_affected(event: RightsEvent, descendant: Descendant) -> bool:
    """Whether this descendant is in scope for containment.

    In scope when either holds:

    - the artifact itself uses a purpose the event removed; or
    - an ancestor on a lineage path does, so this artifact's content is *derived*
      from revoked data even though its own declared purpose was never revoked.

    The second clause matters more than it looks. A prediction API declares
    ``serving`` and a CSV export declares ``export`` -- neither purpose is revoked
    when a feed loses its training right, yet both carry content learned from
    revoked data. Without propagation they would report no-action, which is the
    exact false all-clear this product exists to prevent.

    Propagation deliberately starts at the *descendants*, not the source. An
    analytics table hanging directly off a feed that lost only its training right
    keeps operating, and proving that precision is what separates this from a
    blunt subtree delete.
    """
    lost = event.lost_purposes
    if not lost:
        return False
    if descendant.current_purposes & lost:
        return True
    return descendant.contaminated_upstream


def _matches(rule: Rule, facts: Mapping[str, Any]) -> bool:
    """Whether every condition in the rule's ``when`` block holds."""
    for key, expected in rule.when.items():
        actual = facts.get(key)
        if key == "artifact_class":
            if actual not in expected:
                return False
        elif actual != expected:
            return False
    return True


def evaluate(
    event: RightsEvent,
    descendant: Descendant,
    table: PolicyTable | None = None,
) -> ImpactDecision:
    """Produce the deterministic verdict for one descendant.

    The first rule matching in precedence order wins. Because the table ends with
    an unconditional escalation backstop, this always returns a decision rather
    than falling through.
    """
    table = table or get_policy()

    affected = is_affected(event, descendant)
    facts: dict[str, Any] = {
        "lineage_complete": descendant.lineage_complete,
        "has_paths": bool(descendant.paths),
        "affected": affected,
        "has_replacement": event.has_replacement,
        "rebuildable": descendant.rebuildable_from_replacement,
        "artifact_class": descendant.artifact_class.value,
    }

    for rule in table.rules:
        if not _matches(rule, facts):
            continue

        escalated = Action.ESCALATE in rule.actions
        priority = table.priority.score(descendant.exposure, descendant.criticality, escalated)

        return ImpactDecision(
            descendant_urn=descendant.urn,
            artifact_class=descendant.artifact_class,
            actions=rule.actions,
            rule_ids=(rule.id,),
            rationale=rule.description or rule.id,
            paths=descendant.paths,
            priority=priority,
            missing_evidence=rule.missing_evidence,
            requires_approval=rule.requires_approval,
        )

    # Unreachable while the table keeps its unconditional backstop, but a policy
    # engine that silently returns nothing would be worse than one that complains.
    raise PolicyError(
        f"No rule matched {descendant.urn!r}. The policy table has lost its backstop rule."
    )


def evaluate_all(
    event: RightsEvent,
    descendants: list[Descendant],
    table: PolicyTable | None = None,
) -> list[ImpactDecision]:
    """Evaluate every descendant, highest priority first.

    Ordering is total and deterministic: priority descending, then URN ascending,
    so the same graph always produces the same plan.
    """
    table = table or get_policy()
    decisions = [evaluate(event, d, table) for d in descendants]
    return sorted(decisions, key=lambda d: (-d.priority, d.descendant_urn))
