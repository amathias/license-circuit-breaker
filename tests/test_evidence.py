"""Evidence bundle and durable DataHub writeback tests.

The bundle is the artifact a judge reads instead of running the code, so what it
is allowed to *claim* matters more than its formatting. The verdict must be
derived from the probes rather than asserted, simulated runs must be labelled,
and residual exposure must be impossible to lose.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from adapters.containment import AdapterContext, ContainmentError
from adapters.datahub import (
    REVOCATION_EVIDENCE_KEY,
    REVOCATION_PLAN_KEY,
    REVOCATION_STATUS_KEY,
    STATUS_CONTAINED,
    STATUS_ESCALATED,
    STATUS_RESIDUAL,
    DataHubError,
    record_revocation,
)
from adapters.fake_datahub import FakeDataHubClient
from app.approvals import REJECTED, ApprovalStore
from app.evidence import LEGAL_DISCLAIMER, SCOPE_DISCLAIMER, build_bundle
from app.execution import execute_plan
from app.namespace import Namespace, NamespaceViolation
from app.receipts import ReceiptLedger
from app.rights import License, Purpose, RightsEvent, RightsState
from app.store import GovernanceStore
from app.verification import verify_plan
from app.workflow import build_impact_plan, record_containment_outcomes
from demo import graph
from demo.estate import EstatePaths, build_estate, estate_status
from demo.seed import seed

NS = Namespace(
    project_slug="license-circuit-breaker",
    urn_prefix="license.",
    project_tag="project-license-circuit-breaker",
    domain="Demo / License Circuit Breaker",
)
FOREIGN = "urn:li:dataset:(urn:li:dataPlatform:duckdb,lifeboat.reviews.feed,PROD)"


def _event() -> RightsEvent:
    return RightsEvent(
        event_id="evt-evidence-001",
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
def client() -> FakeDataHubClient:
    fake = FakeDataHubClient(namespace=NS)
    seed(fake, NS)
    return fake


@pytest.fixture
def paths(tmp_path) -> EstatePaths:
    built = EstatePaths.under(tmp_path)
    build_estate(built)
    return built


@pytest.fixture
def plan(client):
    return build_impact_plan(client, _event(), NS)


def _full_run(plan, paths, store, fail_adapter: str | None = None):
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

    execution = execute_plan(plan, approval, context, store)
    return approval, execution, verify_plan(plan, paths)


class TestVerdict:
    def test_no_execution_means_not_started(self, plan):
        assert build_bundle(plan).verdict() == "not_started"

    def test_an_outstanding_escalation_yields_escalated_not_contained(
        self, plan, paths, tmp_path
    ):
        approval, execution, verification = _full_run(plan, paths, GovernanceStore(tmp_path))
        bundle = build_bundle(
            plan, approval=approval, execution=execution, verification=verification
        )
        # Every step succeeded and every probe passed. The broken-lineage
        # snapshot is still unresolved, so this is not an all-clear.
        assert not execution.failed
        assert verification.contained is True
        assert bundle.verdict() == "escalated"

    def test_a_failed_action_yields_residual(self, plan, paths, tmp_path):
        approval, execution, verification = _full_run(
            plan, paths, GovernanceStore(tmp_path), fail_adapter="export-quarantine"
        )
        bundle = build_bundle(
            plan, approval=approval, execution=execution, verification=verification
        )
        assert bundle.verdict() == "residual"

    def test_verification_failure_alone_blocks_containment(self, plan, paths, tmp_path):
        approval, execution, _ = _full_run(plan, paths, GovernanceStore(tmp_path))
        # Execution is clean but nothing was verified: the verdict must not be
        # contained on the strength of receipts alone.
        bundle = build_bundle(plan, approval=approval, execution=execution, verification=None)
        assert bundle.verdict() != "contained"

    def test_contained_requires_a_clean_plan_a_clean_run_and_clean_probes(
        self, client, paths, tmp_path
    ):
        # Remove the broken-lineage node so nothing escalates, then the same
        # workflow should reach a genuine all-clear.
        client.lineage[graph.SOURCE] = [
            e for e in client.lineage[graph.SOURCE] if e.downstream_urn != graph.ORPHAN
        ]
        plan = build_impact_plan(client, _event(), NS)
        approval, execution, verification = _full_run(plan, paths, GovernanceStore(tmp_path))

        bundle = build_bundle(
            plan, approval=approval, execution=execution, verification=verification
        )
        assert bundle.verdict() == "contained"
        assert not bundle.residual()


class TestResidualMerging:
    def test_execution_and_verification_residuals_are_merged(
        self, plan, paths, tmp_path
    ):
        approval, execution, verification = _full_run(
            plan, paths, GovernanceStore(tmp_path), fail_adapter="export-quarantine"
        )
        bundle = build_bundle(
            plan, approval=approval, execution=execution, verification=verification
        )
        reasons = {r.reason for r in bundle.residual()}
        assert "action_failed" in reasons
        assert "verification_failed" in reasons
        assert "escalated" in reasons

    def test_duplicate_entries_are_collapsed(self, plan, paths, tmp_path):
        approval, execution, verification = _full_run(
            plan, paths, GovernanceStore(tmp_path), fail_adapter="export-quarantine"
        )
        bundle = build_bundle(
            plan, approval=approval, execution=execution, verification=verification
        )
        keys = [(r.urn, r.reason, r.action) for r in bundle.residual()]
        assert len(keys) == len(set(keys))

    def test_residual_is_deterministically_ordered(self, plan, paths, tmp_path):
        approval, execution, verification = _full_run(
            plan, paths, GovernanceStore(tmp_path), fail_adapter="export-quarantine"
        )
        bundle = build_bundle(
            plan, approval=approval, execution=execution, verification=verification
        )
        keys = [(r.urn, r.reason, r.action or "") for r in bundle.residual()]
        assert keys == sorted(keys)


class TestSerialization:
    def test_json_captures_every_stage(self, plan, paths, tmp_path):
        approval, execution, verification = _full_run(plan, paths, GovernanceStore(tmp_path))
        payload = build_bundle(
            plan,
            approval=approval,
            execution=execution,
            verification=verification,
            estate=estate_status(paths),
        ).to_dict()

        for key in (
            "rights_event",
            "plan",
            "approval",
            "execution",
            "verification",
            "estate",
            "residual_exposure",
            "verdict",
        ):
            assert key in payload, f"{key} missing from the bundle"

    def test_markdown_labels_a_simulated_run(self, plan, paths, tmp_path):
        approval, execution, verification = _full_run(plan, paths, GovernanceStore(tmp_path))
        markdown = build_bundle(
            plan,
            approval=approval,
            execution=execution,
            verification=verification,
            simulated=True,
        ).to_markdown()
        assert "SIMULATED DATAHUB RUN" in markdown

    def test_markdown_omits_the_simulation_banner_on_a_live_run(
        self, plan, paths, tmp_path
    ):
        approval, execution, verification = _full_run(plan, paths, GovernanceStore(tmp_path))
        markdown = build_bundle(
            plan,
            approval=approval,
            execution=execution,
            verification=verification,
            simulated=False,
        ).to_markdown()
        assert "SIMULATED DATAHUB RUN" not in markdown

    def test_markdown_always_carries_both_disclaimers(self, plan):
        markdown = build_bundle(plan).to_markdown()
        assert LEGAL_DISCLAIMER in markdown
        assert SCOPE_DISCLAIMER in markdown

    def test_markdown_names_every_residual_exposure(self, plan, paths, tmp_path):
        approval, execution, verification = _full_run(
            plan, paths, GovernanceStore(tmp_path), fail_adapter="export-quarantine"
        )
        markdown = build_bundle(
            plan, approval=approval, execution=execution, verification=verification
        ).to_markdown()
        assert "## Residual exposure" in markdown
        assert "license.exports.reviews_extract" in markdown
        assert "license.reviews.legacy_snapshot" in markdown

    def test_an_unexecuted_bundle_says_so_rather_than_looking_clean(self, plan):
        markdown = build_bundle(plan).to_markdown()
        assert "_Not executed._" in markdown
        assert "_No approval was recorded. Nothing was enforced._" in markdown

    def test_a_rejected_approval_appears_in_the_report(self, plan, tmp_path):
        rejected = ApprovalStore(GovernanceStore(tmp_path)).record(
            plan, approver="reviewer@example.com", decision=REJECTED, note="too broad"
        )
        markdown = build_bundle(plan, approval=rejected).to_markdown()
        assert "rejected" in markdown
        assert "too broad" in markdown

    def test_write_produces_both_formats(self, plan, paths, tmp_path):
        approval, execution, verification = _full_run(plan, paths, GovernanceStore(tmp_path))
        json_path, markdown_path = build_bundle(
            plan, approval=approval, execution=execution, verification=verification
        ).write(tmp_path / "evidence")

        assert json.loads(json_path.read_text(encoding="utf-8"))["verdict"] == "escalated"
        assert markdown_path.read_text(encoding="utf-8").startswith("# Containment report")


class TestDurableWriteback:
    def test_status_and_evidence_reach_the_catalog(self, client, tmp_path):
        receipt = record_revocation(
            client,
            graph.PREDICT_API,
            NS,
            status=STATUS_CONTAINED,
            event_id="evt-evidence-001",
            plan_hash="a" * 64,
            evidence_ref=str(tmp_path / "report.json"),
            simulated=True,
        )
        assert receipt.verified is True

        entity = client.get_entity(graph.PREDICT_API)
        assert "license-revocation-contained" in entity.tags
        assert entity.custom_properties[REVOCATION_STATUS_KEY] == STATUS_CONTAINED
        assert entity.custom_properties[REVOCATION_PLAN_KEY] == "a" * 64
        assert REVOCATION_EVIDENCE_KEY in entity.custom_properties

    def test_seeded_metadata_is_preserved(self, client):
        # The policy engine reads artifact_class and purposes. A status write
        # that dropped them would break the next impact analysis.
        before = dict(client.get_entity(graph.PREDICT_API).custom_properties)
        record_revocation(
            client,
            graph.PREDICT_API,
            NS,
            status=STATUS_CONTAINED,
            event_id="e",
            plan_hash="h",
            evidence_ref="ref",
        )
        after = client.get_entity(graph.PREDICT_API).custom_properties
        for key, value in before.items():
            assert after[key] == value

    def test_project_tags_are_preserved(self, client):
        record_revocation(
            client,
            graph.PREDICT_API,
            NS,
            status=STATUS_CONTAINED,
            event_id="e",
            plan_hash="h",
            evidence_ref="ref",
        )
        tags = client.get_entity(graph.PREDICT_API).tags
        assert "project-license-circuit-breaker" in tags
        assert "lcb-demo-fixture" in tags

    def test_only_one_status_tag_applies_at_a_time(self, client):
        # An entity contained after an earlier residual run must not carry both
        # and read as ambiguous.
        for status in (STATUS_RESIDUAL, STATUS_CONTAINED):
            record_revocation(
                client,
                graph.PREDICT_API,
                NS,
                status=status,
                event_id="e",
                plan_hash="h",
                evidence_ref="ref",
            )
        tags = client.get_entity(graph.PREDICT_API).tags
        assert "license-revocation-contained" in tags
        assert "license-revocation-residual" not in tags

    def test_a_foreign_urn_is_refused_before_anything_is_written(self, client):
        with pytest.raises(NamespaceViolation):
            record_revocation(
                client,
                FOREIGN,
                NS,
                status=STATUS_CONTAINED,
                event_id="e",
                plan_hash="h",
                evidence_ref="ref",
            )
        assert FOREIGN not in client.entities

    def test_an_unknown_status_is_refused(self, client):
        with pytest.raises(DataHubError, match="unknown revocation status"):
            record_revocation(
                client,
                graph.PREDICT_API,
                NS,
                status="probably_fine",
                event_id="e",
                plan_hash="h",
                evidence_ref="ref",
            )

    def test_writeback_is_idempotent(self, client):
        first = record_revocation(
            client,
            graph.PREDICT_API,
            NS,
            status=STATUS_CONTAINED,
            event_id="e",
            plan_hash="h",
            evidence_ref="ref",
        )
        second = record_revocation(
            client,
            graph.PREDICT_API,
            NS,
            status=STATUS_CONTAINED,
            event_id="e",
            plan_hash="h",
            evidence_ref="ref",
        )
        assert first.verified and second.verified
        assert first.properties[REVOCATION_STATUS_KEY] == second.properties[
            REVOCATION_STATUS_KEY
        ]


class TestOutcomeWriteback:
    def test_each_artifact_receives_the_status_it_earned(self, client, plan, paths, tmp_path):
        approval, execution, verification = _full_run(plan, paths, GovernanceStore(tmp_path))
        bundle = build_bundle(
            plan, approval=approval, execution=execution, verification=verification
        )

        receipts = record_containment_outcomes(
            client,
            plan,
            NS,
            verdict=bundle.verdict(),
            contained_urns=frozenset(bundle.contained_urns),
            residual_urns=frozenset(r.urn for r in bundle.residual()),
            evidence_ref="state/report.json",
        )

        by_urn = {r.urn: r.status for r in receipts}
        assert by_urn[graph.PREDICT_API] == STATUS_CONTAINED
        assert by_urn[graph.ORPHAN] == STATUS_ESCALATED
        assert by_urn[graph.SOURCE] == "escalated"

    def test_a_failed_containment_is_written_back_as_residual(
        self, client, plan, paths, tmp_path
    ):
        approval, execution, verification = _full_run(
            plan, paths, GovernanceStore(tmp_path), fail_adapter="export-quarantine"
        )
        bundle = build_bundle(
            plan, approval=approval, execution=execution, verification=verification
        )
        receipts = record_containment_outcomes(
            client,
            plan,
            NS,
            verdict=bundle.verdict(),
            contained_urns=frozenset(bundle.contained_urns),
            residual_urns=frozenset(r.urn for r in bundle.residual()),
            evidence_ref="state/report.json",
        )
        assert {r.urn: r.status for r in receipts}[graph.EXPORT] == STATUS_RESIDUAL

    def test_every_writeback_is_verified_by_reread(self, client, plan, paths, tmp_path):
        approval, execution, verification = _full_run(plan, paths, GovernanceStore(tmp_path))
        bundle = build_bundle(
            plan, approval=approval, execution=execution, verification=verification
        )
        receipts = record_containment_outcomes(
            client,
            plan,
            NS,
            verdict=bundle.verdict(),
            contained_urns=frozenset(bundle.contained_urns),
            residual_urns=frozenset(r.urn for r in bundle.residual()),
            evidence_ref="state/report.json",
        )
        assert receipts
        assert all(r.verified for r in receipts)

    def test_writebacks_are_recorded_in_the_receipt_ledger(
        self, client, plan, paths, tmp_path
    ):
        ledger = ReceiptLedger(tmp_path / "ledger")
        approval, execution, verification = _full_run(plan, paths, GovernanceStore(tmp_path))
        bundle = build_bundle(
            plan, approval=approval, execution=execution, verification=verification
        )
        record_containment_outcomes(
            client,
            plan,
            NS,
            verdict=bundle.verdict(),
            contained_urns=frozenset(bundle.contained_urns),
            residual_urns=frozenset(r.urn for r in bundle.residual()),
            evidence_ref="state/report.json",
            ledger=ledger,
            simulated=True,
        )
        entries = [e for e in ledger.entries() if e["operation"] == "revocation_writeback"]
        assert entries
        assert all(e["simulated"] is True for e in entries)
        assert ledger.verify_chain()[0] is True

    def test_an_unprobed_artifact_receives_no_status(self, client, plan, paths, tmp_path):
        # The analytics table was left alone and never probed for containment.
        # Writing a status would imply a verdict nothing established.
        approval, execution, verification = _full_run(plan, paths, GovernanceStore(tmp_path))
        bundle = build_bundle(
            plan, approval=approval, execution=execution, verification=verification
        )
        receipts = record_containment_outcomes(
            client,
            plan,
            NS,
            verdict=bundle.verdict(),
            contained_urns=frozenset(bundle.contained_urns),
            residual_urns=frozenset(r.urn for r in bundle.residual()),
            evidence_ref="state/report.json",
        )
        assert graph.ANALYTICS not in {r.urn for r in receipts}

    def test_a_write_failure_does_not_discard_the_successful_receipts(
        self, client, plan, paths, tmp_path
    ):
        approval, execution, verification = _full_run(plan, paths, GovernanceStore(tmp_path))
        bundle = build_bundle(
            plan, approval=approval, execution=execution, verification=verification
        )
        client.fail_next_write = True

        receipts = record_containment_outcomes(
            client,
            plan,
            NS,
            verdict=bundle.verdict(),
            contained_urns=frozenset(bundle.contained_urns),
            residual_urns=frozenset(r.urn for r in bundle.residual()),
            evidence_ref="state/report.json",
        )
        # One target failed; the rest still have durable receipts.
        assert receipts
        assert len(receipts) < len(plan.decisions) + 1
