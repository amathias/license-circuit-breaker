"""Structured rights model.

Licenses and rights are modelled as typed data, never as prose handed to a
language model. The policy evaluator in :mod:`app.policy` consumes these types
and nothing else, which is what makes enforcement decisions reproducible.

This module encodes *what the operator asserted*, not a legal interpretation.
License Circuit Breaker supports compliance operations; it does not provide legal
advice and does not attempt to interpret arbitrary contract text.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Purpose(StrEnum):
    """A use to which data may be put. Rights are granted per purpose."""

    TRAINING = "training"
    RETRIEVAL = "retrieval"
    ANALYTICS = "analytics"
    SERVING = "serving"
    EXPORT = "export"


class RightsState(StrEnum):
    """The operator-asserted state of a right."""

    APPROVED = "approved"
    RESTRICTED = "restricted"
    REVOKED = "revoked"


class ArtifactClass(StrEnum):
    """What a descendant *is*, which determines how it can be contained."""

    DATASET = "dataset"
    TRANSFORMATION = "transformation"
    FEATURE = "feature"
    TRAINING_SNAPSHOT = "training_snapshot"
    MODEL = "model"
    DEPLOYMENT = "deployment"
    VECTOR_INDEX = "vector_index"
    API = "api"
    CACHE = "cache"
    EXPORT = "export"
    UNKNOWN = "unknown"


class Action(StrEnum):
    """A typed containment action."""

    FREEZE = "freeze"
    QUARANTINE = "quarantine"
    PURGE = "purge"
    REBUILD = "rebuild"
    RETRAIN = "retrain"
    REPLACE = "replace"
    NO_ACTION = "no_action"
    ESCALATE = "escalate"


#: Actions that change or remove a real artifact. These require approval and are
#: refused in dry-run mode.
DESTRUCTIVE_ACTIONS = frozenset(
    {Action.FREEZE, Action.QUARANTINE, Action.PURGE, Action.REBUILD, Action.RETRAIN, Action.REPLACE}
)


class Exposure(StrEnum):
    """How reachable the artifact is by a consumer."""

    PUBLIC = "public"
    INTERNAL = "internal"
    OFFLINE = "offline"


class Criticality(StrEnum):
    """Business criticality, supplied by the fixture or operator."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class License(BaseModel):
    """A structured license grant.

    ``permitted_purposes`` is the authoritative field. A purpose absent from the
    set is not granted; there is no implicit permission.
    """

    model_config = ConfigDict(frozen=True)

    license_id: str = Field(description="Identifier, e.g. a vendor agreement ref or SPDX id")
    name: str
    permitted_purposes: frozenset[Purpose] = Field(default_factory=frozenset)
    state: RightsState = RightsState.APPROVED
    environments: frozenset[str] = Field(
        default_factory=lambda: frozenset({"PROD"}),
        description="Environments the grant covers",
    )
    evidence_ref: str | None = Field(
        default=None, description="Operator-supplied pointer to the source document"
    )

    def permits(self, purpose: Purpose) -> bool:
        """Whether this license currently permits ``purpose``."""
        if self.state is RightsState.REVOKED:
            return False
        return purpose in self.permitted_purposes


class RightsEvent(BaseModel):
    """A versioned, append-only record of a rights change.

    Events are never mutated. A correction is a new event with a higher
    ``version`` referencing the same ``source_urn``.
    """

    model_config = ConfigDict(frozen=True)

    event_id: str
    version: int = Field(default=1, ge=1)
    effective_at: datetime
    source_urn: str = Field(description="DataHub URN of the asset whose rights changed")

    prior: License
    new: License

    reason: str
    evidence_ref: str | None = None
    replacement_source_urn: str | None = Field(
        default=None, description="Approved source that rebuild/retrain may draw from"
    )

    requester: str
    approver: str | None = None
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("effective_at", "recorded_at")
    @classmethod
    def _must_be_timezone_aware(cls, value: datetime) -> datetime:
        # Naive timestamps produce ambiguous audit records.
        if value.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware")
        return value

    @property
    def revokes(self) -> bool:
        """Whether this event removes rights rather than granting them."""
        return self.new.state is RightsState.REVOKED or bool(
            self.prior.permitted_purposes - self.new.permitted_purposes
        )

    @property
    def lost_purposes(self) -> frozenset[Purpose]:
        """Purposes permitted before this event but not after."""
        if self.new.state is RightsState.REVOKED:
            return self.prior.permitted_purposes
        return self.prior.permitted_purposes - self.new.permitted_purposes

    @property
    def has_replacement(self) -> bool:
        return self.replacement_source_urn is not None

    def content_hash(self) -> str:
        """Stable hash of the event's substantive content, for the evidence ledger."""
        payload = self.model_dump(mode="json", exclude={"recorded_at"})
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class LineagePath(BaseModel):
    """One concrete path from the revoked source to a descendant.

    Every decision cites at least one of these. A decision without a path is not
    evidence-backed and must escalate rather than assert impact.
    """

    model_config = ConfigDict(frozen=True)

    hops: tuple[str, ...] = Field(description="URNs from source to descendant, inclusive")
    complete: bool = Field(
        default=True,
        description="False when DataHub lineage had a gap somewhere along this path",
    )

    @field_validator("hops")
    @classmethod
    def _needs_at_least_source_and_target(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) < 2:
            raise ValueError("a lineage path needs at least a source and a descendant")
        return value

    @property
    def depth(self) -> int:
        """Number of hops between source and descendant."""
        return len(self.hops) - 1


class Descendant(BaseModel):
    """A downstream artifact discovered through DataHub lineage."""

    model_config = ConfigDict(frozen=True)

    urn: str
    artifact_class: ArtifactClass
    exposure: Exposure = Exposure.INTERNAL
    criticality: Criticality = Criticality.MEDIUM
    owners: tuple[str, ...] = ()
    paths: tuple[LineagePath, ...] = ()
    current_purposes: frozenset[Purpose] = Field(
        default_factory=frozenset, description="What this artifact currently does with the data"
    )
    rebuildable_from_replacement: bool = False

    @property
    def lineage_complete(self) -> bool:
        """Whether every known path to this artifact is complete."""
        return bool(self.paths) and all(p.complete for p in self.paths)


class ImpactDecision(BaseModel):
    """The deterministic verdict for one descendant."""

    model_config = ConfigDict(frozen=True)

    descendant_urn: str
    artifact_class: ArtifactClass
    actions: tuple[Action, ...]
    rule_ids: tuple[str, ...] = Field(description="Deterministic rules that produced this verdict")
    rationale: str
    paths: tuple[LineagePath, ...] = ()
    priority: int = Field(ge=0, le=100)
    missing_evidence: tuple[str, ...] = ()
    requires_approval: bool = True

    @property
    def is_escalation(self) -> bool:
        return Action.ESCALATE in self.actions

    @property
    def is_destructive(self) -> bool:
        return any(a in DESTRUCTIVE_ACTIONS for a in self.actions)


class ContainmentEvidence(BaseModel):
    """Proof of what an adapter did and whether it worked."""

    model_config = ConfigDict(frozen=True)

    descendant_urn: str
    action: Action
    attempted_at: datetime
    actor: str
    succeeded: bool
    adapter_receipt: dict[str, object] = Field(default_factory=dict)
    verification_method: str | None = None
    verification_passed: bool | None = None
    residual_exposure: str | None = Field(
        default=None, description="Set when containment did not fully succeed"
    )
    datahub_writeback_receipt: dict[str, object] | None = None

    @property
    def contained(self) -> bool:
        """Containment is only claimed when the action ran *and* verification passed.

        A successful adapter call with no passing verification is not containment;
        that distinction is what prevents a false all-clear.
        """
        return self.succeeded and self.verification_passed is True


Verdict = Literal["contained", "residual", "escalated", "not_started"]
