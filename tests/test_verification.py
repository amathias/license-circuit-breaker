"""Verification tests.

The verifier's value is entirely in what it refuses to confirm. These tests
mostly break containment on purpose -- skip an action, restore a file, leave a
model unswapped -- and assert that the probes notice, because a verifier that
only passes is a verifier nobody should trust.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from adapters.containment import AdapterContext, ContainmentError
from adapters.fake_datahub import FakeDataHubClient
from app.approvals import ApprovalStore
from app.execution import VERIFICATION_FAILED, execute_plan
from app.namespace import Namespace
from app.rights import ArtifactClass, License, Purpose, RightsEvent, RightsState
from app.store import GovernanceStore
from app.verification import (
    AVAILABILITY,
    ENDPOINT_PROBE,
    EXPORT_ACCESS,
    INDEX_SCAN,
    MODEL_MANIFEST,
    TABLE_SCAN,
    probe_endpoint,
    probe_export,
    probe_for,
    probe_index,
    probe_model,
    probe_table,
    verify_plan,
)
from app.workflow import build_impact_plan
from demo import graph
from demo.estate import (
    EstatePaths,
    activate_version,
    build_estate,
    export_path,
    quarantined_export_path,
    read_table,
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
        event_id="evt-verify-001",
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
def plan():
    client = FakeDataHubClient(namespace=NS)
    seed(client, NS)
    return build_impact_plan(client, _event(), NS)


def _contain(plan, paths, store, fail_adapter: str | None = None):
    approval = ApprovalStore(store).record(plan, approver="approver@example.com")
    context = AdapterContext(
        paths=paths,
        namespace=NS,
        replacement_source_urn=graph.REPLACEMENT_SOURCE,
        actor="approver@example.com",
    )
    if fail_adapter:
        def inject(adapter: str, urn: str, action) -> None:
            if adapter == fail_adapter:
                raise ContainmentError(f"{adapter} was told to fail")

        context.fault_injector = inject
    return execute_plan(plan, approval, context, store)


class TestBeforeContainment:
    """Every containment probe must fail on an untouched estate.

    Otherwise a passing probe proves nothing -- it might have passed before the
    adapters ran at all.
    """

    def test_endpoint_probe_fails_while_the_api_is_serving(self, paths):
        probe = probe_endpoint(paths, graph.PREDICT_API)
        assert probe.passed is False
        assert "still serving" in probe.observed

    def test_index_probe_fails_while_partner_vectors_are_indexed(self, paths):
        probe = probe_index(paths, graph.VECTOR_INDEX)
        assert probe.passed is False
        assert "partner-derived documents still retrievable" in probe.observed

    def test_export_probe_fails_while_the_export_is_published(self, paths):
        probe = probe_export(paths, graph.EXPORT)
        assert probe.passed is False
        assert "still readable" in probe.observed

    def test_model_probe_fails_while_the_partner_model_is_served(self, paths):
        probe = probe_model(paths, graph.MODEL)
        assert probe.passed is False
        assert "partner rows" in probe.observed

    def test_table_probe_fails_while_partner_rows_remain(self, paths):
        probe = probe_table(paths, graph.NORMALIZED)
        assert probe.passed is False
        assert "24 partner rows remain" in probe.observed

    def test_the_whole_report_fails_before_containment(self, plan, paths):
        report = verify_plan(plan, paths)
        assert report.contained is False
        assert len(report.failures) == len(report.containment_probes)


class TestAfterContainment:
    def test_every_probe_passes(self, plan, paths, tmp_path):
        _contain(plan, paths, GovernanceStore(tmp_path))
        report = verify_plan(plan, paths)
        assert report.contained is True, [p.observed for p in report.failures]

    def test_both_containment_and_precision_probes_run(self, plan, paths, tmp_path):
        _contain(plan, paths, GovernanceStore(tmp_path))
        report = verify_plan(plan, paths)
        assert report.containment_probes
        assert report.precision_probes

    def test_the_approved_branch_is_probed_even_though_it_is_not_in_the_plan(
        self, plan, paths, tmp_path
    ):
        # Nothing in the pipeline would notice if containment reached it, which
        # is exactly why it needs an independent probe.
        _contain(plan, paths, GovernanceStore(tmp_path))
        report = verify_plan(plan, paths)
        assert graph.APPROVED_MODEL in {p.urn for p in report.precision_probes}

    def test_the_unaffected_analytics_table_is_probed_for_availability(
        self, plan, paths, tmp_path
    ):
        _contain(plan, paths, GovernanceStore(tmp_path))
        report = verify_plan(plan, paths)
        analytics = next(p for p in report.probes if p.urn == graph.ANALYTICS)
        assert analytics.method == AVAILABILITY
        assert analytics.containment is False
        assert analytics.passed is True

    def test_escalated_artifacts_are_not_probed(self, plan, paths, tmp_path):
        # Nothing was attempted on them, so a probe would imply a verdict the
        # evidence does not support.
        _contain(plan, paths, GovernanceStore(tmp_path))
        report = verify_plan(plan, paths)
        assert graph.ORPHAN not in {p.urn for p in report.probes}


class TestDetectsSkippedContainment:
    """The Phase 5 exit condition: a skipped action must prevent an all-clear."""

    def test_a_deliberately_failed_quarantine_is_caught(self, plan, paths, tmp_path):
        _contain(plan, paths, GovernanceStore(tmp_path), fail_adapter="export-quarantine")
        report = verify_plan(plan, paths)

        assert report.contained is False
        failure = next(p for p in report.failures if p.method == EXPORT_ACCESS)
        assert "still readable at the published path" in failure.observed

    def test_a_deliberately_failed_freeze_is_caught(self, plan, paths, tmp_path):
        _contain(plan, paths, GovernanceStore(tmp_path), fail_adapter="api-freeze")
        report = verify_plan(plan, paths)

        assert report.contained is False
        assert any(p.method == ENDPOINT_PROBE for p in report.failures)

    def test_a_failed_probe_becomes_residual_exposure(self, plan, paths, tmp_path):
        _contain(plan, paths, GovernanceStore(tmp_path), fail_adapter="export-quarantine")
        residual = verify_plan(plan, paths).residual()

        assert len(residual) == 1
        assert residual[0].urn == graph.EXPORT
        assert residual[0].reason == VERIFICATION_FAILED

    def test_verification_is_independent_of_the_execution_report(
        self, plan, paths, tmp_path
    ):
        # Contain everything, then quietly put the export back. Execution still
        # says every step succeeded; only a probe of the artifact itself can
        # notice, and that independence is the point of the whole module.
        _contain(plan, paths, GovernanceStore(tmp_path))
        assert verify_plan(plan, paths).contained is True

        quarantined_export_path(paths).replace(export_path(paths))
        assert verify_plan(plan, paths).contained is False

    def test_an_unswapped_model_is_caught(self, plan, paths, tmp_path):
        _contain(plan, paths, GovernanceStore(tmp_path))
        # Roll the served model back to the partner-trained version.
        activate_version(paths, "review_sentiment", "v1")

        report = verify_plan(plan, paths)
        failure = next(p for p in report.failures if p.method == MODEL_MANIFEST)
        assert "partner rows" in failure.observed


class TestDetectsOverReach:
    def test_destroying_the_unaffected_branch_fails_verification(
        self, plan, paths, tmp_path
    ):
        # Over-reach is a failure, not a success. A tool that contains
        # everything is deleting, not governing.
        _contain(plan, paths, GovernanceStore(tmp_path))
        from demo.estate import purge_table

        purge_table(paths, "review_volume")

        report = verify_plan(plan, paths)
        assert report.contained is False
        analytics = next(p for p in report.failures if p.urn == graph.ANALYTICS)
        assert analytics.containment is False

    def test_a_broken_endpoint_is_not_containment(self, paths):
        # Deleting the model would make predict() raise EstateError, not
        # ServingRefused. That is an outage, and it must not read as a freeze.
        import shutil

        shutil.rmtree(paths.model_root("review_sentiment"))
        probe = probe_endpoint(paths, graph.PREDICT_API)
        assert probe.passed is False
        assert "broken rather than contained" in probe.observed


class TestProbeReporting:
    def test_probes_report_expectation_and_observation_separately(self, paths):
        probe = probe_export(paths, graph.EXPORT)
        assert probe.expected
        assert probe.observed
        assert probe.expected != probe.observed

    def test_probes_are_deterministically_ordered(self, plan, paths, tmp_path):
        _contain(plan, paths, GovernanceStore(tmp_path))
        report = verify_plan(plan, paths)
        keys = [(not p.containment, p.urn) for p in report.probes]
        assert keys == sorted(keys)

    def test_the_report_serializes(self, plan, paths, tmp_path):
        _contain(plan, paths, GovernanceStore(tmp_path))
        payload = verify_plan(plan, paths).to_dict()
        assert payload["contained"] is True
        assert len(payload["probes"]) == 8
        assert payload["residual_exposure"] == []

    def test_probe_selection_is_declared_per_artifact_class(self):
        assert probe_for(ArtifactClass.API) == ENDPOINT_PROBE
        assert probe_for(ArtifactClass.VECTOR_INDEX) == INDEX_SCAN
        assert probe_for(ArtifactClass.EXPORT) == EXPORT_ACCESS
        assert probe_for(ArtifactClass.MODEL) == MODEL_MANIFEST
        assert probe_for(ArtifactClass.FEATURE) == TABLE_SCAN
        assert probe_for(ArtifactClass.CACHE) == "unsupported"

    def test_an_unreadable_table_fails_rather_than_passing_vacuously(self, paths):
        from demo.estate import _connect

        connection = _connect(paths)
        try:
            connection.execute("DROP TABLE normalized")
        finally:
            connection.close()

        probe = probe_table(paths, graph.NORMALIZED)
        assert probe.passed is False
        assert "could not read" in probe.observed

    def test_a_missing_training_manifest_fails_rather_than_passing_vacuously(
        self, paths
    ):
        (paths.model_root("review_sentiment") / "active.json").unlink()
        probe = probe_model(paths, graph.MODEL)
        assert probe.passed is False
        assert "no training manifest" in probe.observed

    def test_an_empty_report_is_not_contained(self):
        from app.verification import VerificationReport

        empty = VerificationReport(verified_at=datetime.now(UTC), probes=())
        assert empty.contained is False

    def test_the_analytics_table_really_is_left_alone(self, plan, paths, tmp_path):
        before = read_table(paths, "review_volume")
        _contain(plan, paths, GovernanceStore(tmp_path))
        assert read_table(paths, "review_volume") == before
