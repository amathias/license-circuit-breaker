"""Deterministic policy tests.

Covers the five cases required by BUILD_PLAN.md: allowed, revoked,
replacement-available, incomplete-evidence, and unaffected -- plus the
determinism and fail-closed properties the whole design rests on.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.policy import PolicyError, evaluate, evaluate_all, get_policy, is_affected, load_policy
from app.rights import (
    Action,
    ArtifactClass,
    Criticality,
    Descendant,
    Exposure,
    License,
    LineagePath,
    Purpose,
    RightsEvent,
    RightsState,
)

NOW = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)
SOURCE = "urn:li:dataset:(urn:li:dataPlatform:duckdb,license.reviews.partner_feed,PROD)"
REPLACEMENT = "urn:li:dataset:(urn:li:dataPlatform:duckdb,license.reviews.approved_feed,PROD)"

ALL_PURPOSES = {
    Purpose.TRAINING,
    Purpose.RETRIEVAL,
    Purpose.ANALYTICS,
    Purpose.SERVING,
    Purpose.EXPORT,
}


def _license(state: RightsState, purposes: set[Purpose]) -> License:
    return License(
        license_id="PARTNER-2026-01",
        name="Partner review feed agreement",
        permitted_purposes=frozenset(purposes),
        state=state,
    )


def _event(
    *, replacement: str | None = None, new_purposes: set[Purpose] | None = None
) -> RightsEvent:
    """A revocation event. By default it revokes everything."""
    if new_purposes is None:
        new = _license(RightsState.REVOKED, set())
    else:
        new = _license(RightsState.APPROVED, new_purposes)
    return RightsEvent(
        event_id="evt-001",
        effective_at=NOW,
        source_urn=SOURCE,
        prior=_license(
            RightsState.APPROVED,
            ALL_PURPOSES,
        ),
        new=new,
        reason="Partner revoked usage rights",
        requester="governance@example.com",
        replacement_source_urn=replacement,
    )


def _path(*, complete: bool = True, depth: int = 2) -> LineagePath:
    hops = (
        SOURCE,
        *(
            f"urn:li:dataset:(urn:li:dataPlatform:duckdb,license.hop{i},PROD)"
            for i in range(depth)
        ),
    )
    return LineagePath(hops=hops, complete=complete)


def _descendant(
    urn: str = "urn:li:dataset:(urn:li:dataPlatform:duckdb,license.reviews.normalized,PROD)",
    *,
    artifact_class: ArtifactClass = ArtifactClass.DATASET,
    purposes: set[Purpose] | None = None,
    complete: bool = True,
    has_paths: bool = True,
    rebuildable: bool = False,
    exposure: Exposure = Exposure.INTERNAL,
    criticality: Criticality = Criticality.MEDIUM,
) -> Descendant:
    return Descendant(
        urn=urn,
        artifact_class=artifact_class,
        exposure=exposure,
        criticality=criticality,
        paths=(_path(complete=complete),) if has_paths else (),
        current_purposes=frozenset(purposes if purposes is not None else {Purpose.TRAINING}),
        rebuildable_from_replacement=rebuildable,
    )


class TestTableIntegrity:
    def test_table_loads(self):
        table = get_policy()
        assert table.version >= 1
        assert len(table.rules) > 0

    def test_rules_are_precedence_ordered(self):
        precedences = [r.precedence for r in get_policy().rules]
        assert precedences == sorted(precedences)

    def test_every_rule_has_a_description(self):
        # Descriptions become the operator-facing rationale, so a blank one
        # would produce an unexplained enforcement action.
        for rule in get_policy().rules:
            assert rule.description, f"{rule.id} has no description"

    def test_duplicate_precedence_is_rejected(self, tmp_path):
        bad = tmp_path / "rules.yaml"
        bad.write_text(
            "version: 1\nrules:\n"
            "  - {id: A, description: a, precedence: 1, when: {}, then: {actions: [purge]}}\n"
            "  - {id: B, description: b, precedence: 1, when: {}, then: {actions: [purge]}}\n",
            encoding="utf-8",
        )
        with pytest.raises(PolicyError, match="precedence"):
            load_policy(bad)

    def test_unknown_action_is_rejected(self, tmp_path):
        bad = tmp_path / "rules.yaml"
        bad.write_text(
            "version: 1\nrules:\n"
            "  - {id: A, description: a, precedence: 1, when: {}, then: {actions: [detonate]}}\n",
            encoding="utf-8",
        )
        with pytest.raises(PolicyError, match="unknown action"):
            load_policy(bad)


class TestUnaffected:
    def test_analytics_table_survives_a_training_only_revocation(self):
        # The feed loses only its training right; an analytics consumer keeps working.
        event = _event(
            new_purposes={Purpose.RETRIEVAL, Purpose.ANALYTICS, Purpose.SERVING, Purpose.EXPORT}
        )
        descendant = _descendant(purposes={Purpose.ANALYTICS})
        assert not is_affected(event, descendant)

        decision = evaluate(event, descendant)
        assert decision.actions == (Action.NO_ACTION,)
        assert decision.rule_ids == ("LCB-R010",)
        assert not decision.requires_approval

    def test_no_action_still_cites_lineage_evidence(self):
        event = _event(new_purposes={Purpose.ANALYTICS})
        decision = evaluate(event, _descendant(purposes={Purpose.ANALYTICS}))
        assert decision.paths, "an unaffected verdict must still show why it was considered"


class TestRevokedWithoutReplacement:
    @pytest.mark.parametrize(
        "artifact_class,expected",
        [
            (ArtifactClass.DATASET, (Action.PURGE,)),
            (ArtifactClass.TRANSFORMATION, (Action.PURGE,)),
            (ArtifactClass.FEATURE, (Action.PURGE,)),
            (ArtifactClass.VECTOR_INDEX, (Action.PURGE,)),
            (ArtifactClass.CACHE, (Action.PURGE,)),
            (ArtifactClass.API, (Action.FREEZE,)),
            (ArtifactClass.DEPLOYMENT, (Action.FREEZE,)),
            (ArtifactClass.EXPORT, (Action.QUARANTINE,)),
            (ArtifactClass.MODEL, (Action.FREEZE, Action.QUARANTINE)),
            (ArtifactClass.TRAINING_SNAPSHOT, (Action.FREEZE, Action.QUARANTINE)),
        ],
    )
    def test_each_artifact_class_gets_its_typed_action(self, artifact_class, expected):
        decision = evaluate(_event(), _descendant(artifact_class=artifact_class))
        assert decision.actions == expected
        assert decision.rule_ids, "every action must cite a rule"

    def test_model_containment_disclaims_unlearning(self):
        decision = evaluate(_event(), _descendant(artifact_class=ArtifactClass.MODEL))
        assert any("unlearning" in m for m in decision.missing_evidence)


class TestReplacementAvailable:
    def test_dataset_rebuilds_instead_of_purging(self):
        decision = evaluate(
            _event(replacement=REPLACEMENT),
            _descendant(artifact_class=ArtifactClass.DATASET, rebuildable=True),
        )
        assert decision.actions == (Action.REBUILD,)
        assert decision.rule_ids == ("LCB-R020",)

    def test_model_retrains_and_replaces(self):
        decision = evaluate(
            _event(replacement=REPLACEMENT),
            _descendant(artifact_class=ArtifactClass.MODEL, rebuildable=True),
        )
        assert decision.actions == (Action.RETRAIN, Action.REPLACE)

    def test_vector_index_purges_then_rebuilds_in_that_order(self):
        decision = evaluate(
            _event(replacement=REPLACEMENT),
            _descendant(artifact_class=ArtifactClass.VECTOR_INDEX, rebuildable=True),
        )
        assert decision.actions == (Action.PURGE, Action.REBUILD)

    def test_replacement_that_cannot_rebuild_this_artifact_falls_back_to_purge(self):
        # A replacement source existing does not mean every artifact can use it.
        decision = evaluate(
            _event(replacement=REPLACEMENT),
            _descendant(artifact_class=ArtifactClass.DATASET, rebuildable=False),
        )
        assert decision.actions == (Action.PURGE,)


class TestIncompleteEvidence:
    def test_incomplete_lineage_escalates(self):
        decision = evaluate(_event(), _descendant(complete=False))
        assert decision.actions == (Action.ESCALATE,)
        assert decision.rule_ids == ("LCB-R001",)
        assert decision.missing_evidence

    def test_incomplete_lineage_beats_a_confident_containment_verdict(self):
        # An API with broken lineage escalates rather than being quietly frozen.
        decision = evaluate(
            _event(), _descendant(artifact_class=ArtifactClass.API, complete=False)
        )
        assert decision.actions == (Action.ESCALATE,)

    def test_missing_lineage_path_escalates(self):
        decision = evaluate(_event(), _descendant(has_paths=False))
        assert decision.actions == (Action.ESCALATE,)
        assert decision.rule_ids == ("LCB-R002",)

    def test_unknown_artifact_class_escalates(self):
        decision = evaluate(_event(), _descendant(artifact_class=ArtifactClass.UNKNOWN))
        assert decision.actions == (Action.ESCALATE,)
        assert decision.rule_ids == ("LCB-R003",)

    def test_escalation_is_never_reported_as_contained(self):
        decision = evaluate(_event(), _descendant(complete=False))
        assert decision.is_escalation
        assert not decision.is_destructive


class TestPriority:
    def test_public_high_criticality_outranks_offline_low(self):
        hot = evaluate(
            _event(),
            _descendant(
                artifact_class=ArtifactClass.API,
                exposure=Exposure.PUBLIC,
                criticality=Criticality.HIGH,
            ),
        )
        cold = evaluate(
            _event(),
            _descendant(
                artifact_class=ArtifactClass.EXPORT,
                exposure=Exposure.OFFLINE,
                criticality=Criticality.LOW,
            ),
        )
        assert hot.priority > cold.priority

    def test_priority_is_capped(self):
        decision = evaluate(
            _event(),
            _descendant(
                artifact_class=ArtifactClass.UNKNOWN,
                exposure=Exposure.PUBLIC,
                criticality=Criticality.HIGH,
            ),
        )
        assert decision.priority <= 100

    def test_escalations_are_surfaced_above_equivalent_routine_work(self):
        escalated = evaluate(_event(), _descendant(complete=False, exposure=Exposure.INTERNAL))
        routine = evaluate(_event(), _descendant(exposure=Exposure.INTERNAL))
        assert escalated.priority > routine.priority


class TestDeterminism:
    def test_same_inputs_produce_identical_decisions(self):
        event, descendant = _event(), _descendant()
        first = evaluate(event, descendant)
        second = evaluate(event, descendant)
        assert first == second

    def test_plan_ordering_is_total_and_stable(self):
        event = _event()
        descendants = [
            _descendant(urn=f"urn:li:dataset:(urn:li:dataPlatform:duckdb,license.d{i},PROD)")
            for i in range(5)
        ]
        first = [d.descendant_urn for d in evaluate_all(event, descendants)]
        second = [d.descendant_urn for d in evaluate_all(event, list(reversed(descendants)))]
        assert first == second

    def test_highest_priority_comes_first(self):
        event = _event()
        plan = evaluate_all(
            event,
            [
                _descendant(urn="urn:li:dataset:(urn:li:dataPlatform:duckdb,license.cold,PROD)",
                            artifact_class=ArtifactClass.EXPORT,
                            exposure=Exposure.OFFLINE, criticality=Criticality.LOW),
                _descendant(urn="urn:li:dataset:(urn:li:dataPlatform:rest-api,license.hot,PROD)",
                            artifact_class=ArtifactClass.API,
                            exposure=Exposure.PUBLIC, criticality=Criticality.HIGH),
            ],
        )
        assert "hot" in plan[0].descendant_urn


class TestRightsEventSemantics:
    def test_full_revocation_loses_every_prior_purpose(self):
        event = _event()
        assert event.revokes
        assert Purpose.TRAINING in event.lost_purposes
        assert Purpose.RETRIEVAL in event.lost_purposes

    def test_partial_revocation_loses_only_the_removed_purpose(self):
        event = _event(
            new_purposes={Purpose.RETRIEVAL, Purpose.ANALYTICS, Purpose.SERVING, Purpose.EXPORT}
        )
        assert event.lost_purposes == frozenset({Purpose.TRAINING})

    def test_revoked_license_permits_nothing(self):
        lic = _license(RightsState.REVOKED, {Purpose.TRAINING})
        assert not lic.permits(Purpose.TRAINING)

    def test_naive_timestamps_rejected(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            RightsEvent(
                event_id="e", effective_at=datetime(2026, 7, 24), source_urn=SOURCE,
                prior=_license(RightsState.APPROVED, set()),
                new=_license(RightsState.REVOKED, set()),
                reason="r", requester="x",
            )

    def test_content_hash_is_stable_across_recording_time(self):
        # The ledger hash must identify the substance of an event, not when it
        # happened to be written down.
        a = _event()
        b = a.model_copy(update={"recorded_at": a.recorded_at + timedelta(hours=3)})
        assert a.content_hash() == b.content_hash()

    def test_content_hash_changes_when_substance_changes(self):
        a = _event()
        b = a.model_copy(update={"reason": "different reason"})
        assert a.content_hash() != b.content_hash()

    def test_lineage_path_needs_source_and_target(self):
        with pytest.raises(ValueError, match="at least"):
            LineagePath(hops=(SOURCE,))
