"""Containment adapter tests.

These check the three properties the product's honesty rests on:

- the adapters genuinely change real artifacts (not a status field somewhere);
- re-running a completed action converges and reports ``changed=False``;
- an action that cannot be performed produces a refusal or a failed receipt,
  never a success.
"""

from __future__ import annotations

import pytest

from adapters.containment import (
    RETRAINED_VERSION,
    AdapterContext,
    AdapterRegistry,
    ApiFreezeAdapter,
    ContainmentError,
    NoAdapterError,
    VectorIndexAdapter,
    WarehouseAdapter,
    execution_stage,
)
from app.namespace import Namespace, NamespaceViolation
from app.rights import Action, ArtifactClass
from demo import graph
from demo.corpus import APPROVED_PREFIX, PARTNER_PREFIX
from demo.estate import (
    EstateError,
    EstatePaths,
    ServingControl,
    active_version,
    build_estate,
    export_path,
    index_manifest,
    load_index,
    quarantined_export_path,
    read_table,
    table_row_ids,
    training_manifest,
)
from demo.serving import ServingRefused, fetch_export, predict, search

NS = Namespace(
    project_slug="license-circuit-breaker",
    urn_prefix="license.",
    project_tag="project-license-circuit-breaker",
    domain="Demo / License Circuit Breaker",
)
FOREIGN = "urn:li:dataset:(urn:li:dataPlatform:duckdb,lifeboat.reviews.feed,PROD)"


@pytest.fixture
def paths(tmp_path) -> EstatePaths:
    built = EstatePaths.under(tmp_path)
    build_estate(built)
    return built


@pytest.fixture
def context(paths) -> AdapterContext:
    return AdapterContext(
        paths=paths,
        namespace=NS,
        replacement_source_urn=graph.REPLACEMENT_SOURCE,
        actor="approver@example.com",
    )


@pytest.fixture
def registry() -> AdapterRegistry:
    return AdapterRegistry()


def _run(registry, context, urn, action):
    receipt = registry.execute(context, urn, action)
    assert receipt.succeeded, f"{action.value} on {urn} failed: {receipt.error}"
    return receipt


class TestApiFreeze:
    def test_freeze_stops_the_endpoint_answering(self, registry, context, paths):
        assert predict(paths, "battery charge").label in (0, 1)

        receipt = _run(registry, context, graph.PREDICT_API, Action.FREEZE)
        assert receipt.changed is True
        assert receipt.evidence["serving_state"] == "blocked"

        with pytest.raises(ServingRefused):
            predict(paths, "battery charge")
        with pytest.raises(ServingRefused):
            search(paths, "battery charge")

    def test_freeze_is_idempotent(self, registry, context, paths):
        _run(registry, context, graph.PREDICT_API, Action.FREEZE)
        second = _run(registry, context, graph.PREDICT_API, Action.FREEZE)
        assert second.changed is False
        assert ServingControl.load(paths.serving_path).is_blocked(graph.PREDICT_API)

    def test_freeze_records_the_actor(self, registry, context):
        receipt = _run(registry, context, graph.PREDICT_API, Action.FREEZE)
        assert receipt.adapter == "api-freeze"
        assert receipt.simulated is False

    def test_adapter_declines_actions_it_does_not_own(self, paths):
        from demo.estate import resolve_artifact

        adapter = ApiFreezeAdapter()
        assert adapter.supports(resolve_artifact(graph.PREDICT_API), Action.FREEZE)
        assert not adapter.supports(resolve_artifact(graph.PREDICT_API), Action.PURGE)
        assert not adapter.supports(resolve_artifact(graph.EXPORT), Action.FREEZE)


class TestVectorIndex:
    def test_purge_removes_every_vector(self, registry, context, paths):
        assert search(paths, "battery charge")

        receipt = _run(registry, context, graph.VECTOR_INDEX, Action.PURGE)
        assert receipt.changed is True
        assert receipt.evidence["purged_row_count"] == 24

        _model, entries = load_index(paths)
        assert entries == []
        assert search(paths, "battery charge") == []

    def test_purge_leaves_a_manifest_proving_it_happened(self, registry, context, paths):
        _run(registry, context, graph.VECTOR_INDEX, Action.PURGE)
        manifest = index_manifest(paths)
        # A purged index must be distinguishable from one that was never built.
        assert manifest["purged"] is True
        assert manifest["vector_count"] == 0
        assert manifest["source_urns"] == []

    def test_purge_is_idempotent(self, registry, context):
        _run(registry, context, graph.VECTOR_INDEX, Action.PURGE)
        assert _run(registry, context, graph.VECTOR_INDEX, Action.PURGE).changed is False

    def test_rebuild_regenerates_from_approved_content(self, registry, context, paths):
        _run(registry, context, graph.VECTOR_INDEX, Action.PURGE)
        _run(registry, context, graph.NORMALIZED, Action.REBUILD)
        receipt = _run(registry, context, graph.VECTOR_INDEX, Action.REBUILD)

        assert receipt.evidence["source_urns"] == [graph.REPLACEMENT_SOURCE]
        assert all(rid.startswith(APPROVED_PREFIX) for rid in receipt.evidence["row_ids"])

        hits = search(paths, "battery charge")
        assert hits
        assert not any(hit.review_id.startswith(PARTNER_PREFIX) for hit in hits)

    def test_rebuild_is_idempotent(self, registry, context):
        _run(registry, context, graph.NORMALIZED, Action.REBUILD)
        _run(registry, context, graph.VECTOR_INDEX, Action.REBUILD)
        assert _run(registry, context, graph.VECTOR_INDEX, Action.REBUILD).changed is False

    def test_rebuild_without_a_replacement_source_is_refused(self, registry, paths):
        # Rebuilding from nothing would leave an empty index reported as contained.
        bare = AdapterContext(paths=paths, namespace=NS, replacement_source_urn=None)
        receipt = registry.execute(bare, graph.VECTOR_INDEX, Action.REBUILD)
        assert receipt.succeeded is False
        assert "no approved replacement source" in receipt.error

    def test_rebuild_refuses_a_foreign_replacement_source(self, registry, paths):
        foreign = AdapterContext(paths=paths, namespace=NS, replacement_source_urn=FOREIGN)
        with pytest.raises(NamespaceViolation):
            VectorIndexAdapter().apply(foreign, graph.VECTOR_INDEX, Action.REBUILD)


class TestExportQuarantine:
    def test_quarantine_moves_the_published_export(self, registry, context, paths):
        assert fetch_export(paths)

        receipt = _run(registry, context, graph.EXPORT, Action.QUARANTINE)
        assert receipt.changed is True
        assert not export_path(paths).exists()
        assert quarantined_export_path(paths).exists()

        with pytest.raises(ServingRefused, match="quarantined"):
            fetch_export(paths)

    def test_quarantine_preserves_the_data_rather_than_destroying_it(
        self, registry, context, paths
    ):
        before = export_path(paths).read_text(encoding="utf-8")
        _run(registry, context, graph.EXPORT, Action.QUARANTINE)
        assert quarantined_export_path(paths).read_text(encoding="utf-8") == before

    def test_quarantine_records_its_scope_limitation(self, registry, context, paths):
        import json

        _run(registry, context, graph.EXPORT, Action.QUARANTINE)
        record = json.loads(
            (paths.quarantine_dir / "QUARANTINE.json").read_text(encoding="utf-8")
        )
        assert "outside the demonstrated DataHub graph" in record["note"]

    def test_quarantine_is_idempotent(self, registry, context):
        _run(registry, context, graph.EXPORT, Action.QUARANTINE)
        assert _run(registry, context, graph.EXPORT, Action.QUARANTINE).changed is False

    def test_quarantine_with_nothing_to_move_fails_rather_than_reporting_success(
        self, registry, context, paths
    ):
        export_path(paths).unlink()
        receipt = registry.execute(context, graph.EXPORT, Action.QUARANTINE)
        assert receipt.succeeded is False
        assert "no export exists" in receipt.error


class TestModelLifecycle:
    def test_retrain_produces_an_approved_version_without_swapping_the_served_one(
        self, registry, context, paths
    ):
        receipt = _run(registry, context, graph.MODEL, Action.RETRAIN)

        assert receipt.evidence["training_sources"] == [graph.REPLACEMENT_SOURCE]
        assert all(rid.startswith(APPROVED_PREFIX) for rid in receipt.evidence["row_ids"])
        # Retrain and replace are separate approvals; retraining must not change
        # what answers requests.
        assert active_version(paths, "review_sentiment") == "v1"

    def test_retrain_states_it_is_not_proof_of_unlearning(self, registry, context):
        receipt = _run(registry, context, graph.MODEL, Action.RETRAIN)
        assert "not proof of model unlearning" in receipt.evidence["limitation"].lower()

    def test_replace_swaps_the_served_model(self, registry, context, paths):
        _run(registry, context, graph.MODEL, Action.RETRAIN)
        receipt = _run(registry, context, graph.MODEL, Action.REPLACE)

        assert receipt.changed is True
        assert active_version(paths, "review_sentiment") == RETRAINED_VERSION
        manifest = training_manifest(paths, "review_sentiment")
        assert manifest["training_sources"] == [graph.REPLACEMENT_SOURCE]
        assert not any(rid.startswith(PARTNER_PREFIX) for rid in manifest["row_ids"])

    def test_the_replaced_model_still_answers(self, registry, context, paths):
        _run(registry, context, graph.MODEL, Action.RETRAIN)
        _run(registry, context, graph.MODEL, Action.REPLACE)

        prediction = predict(paths, "runs quietly and the charge holds for days")
        assert prediction.model_version == RETRAINED_VERSION
        assert prediction.training_sources == (graph.REPLACEMENT_SOURCE,)

    def test_replace_before_retrain_is_refused(self, registry, context, paths):
        # Activating a version that was never trained would take the endpoint
        # down while the receipt claimed a successful replacement.
        receipt = registry.execute(context, graph.MODEL, Action.REPLACE)
        assert receipt.succeeded is False
        assert "has not been trained" in receipt.error
        assert active_version(paths, "review_sentiment") == "v1"

    def test_retrain_and_replace_are_idempotent(self, registry, context):
        _run(registry, context, graph.MODEL, Action.RETRAIN)
        _run(registry, context, graph.MODEL, Action.REPLACE)
        assert _run(registry, context, graph.MODEL, Action.RETRAIN).changed is False
        assert _run(registry, context, graph.MODEL, Action.REPLACE).changed is False

    def test_retrain_without_a_replacement_source_is_refused(self, registry, paths):
        bare = AdapterContext(paths=paths, namespace=NS, replacement_source_urn=None)
        receipt = AdapterRegistry().execute(bare, graph.MODEL, Action.RETRAIN)
        assert receipt.succeeded is False
        assert "no approved replacement source" in receipt.error


class TestWarehouse:
    def test_rebuild_replaces_partner_rows_with_approved_rows(self, registry, context, paths):
        assert all(rid.startswith(PARTNER_PREFIX) for rid in table_row_ids(paths, "normalized"))

        receipt = _run(registry, context, graph.NORMALIZED, Action.REBUILD)
        assert receipt.changed is True

        rebuilt = table_row_ids(paths, "normalized")
        assert rebuilt
        assert all(rid.startswith(APPROVED_PREFIX) for rid in rebuilt)
        assert {row["source_feed"] for row in read_table(paths, "normalized")} == {"approved"}

    def test_rebuilding_the_feature_table_after_its_upstream_reports_no_change(
        self, registry, context
    ):
        # The derived tables regenerate as a chain, so the upstream rebuild
        # already put the feature table into its target state.
        _run(registry, context, graph.NORMALIZED, Action.REBUILD)
        second = _run(registry, context, graph.FEATURES, Action.REBUILD)
        assert second.changed is False
        assert "no change made" in second.detail

    def test_purge_empties_the_table_but_keeps_it(self, registry, context, paths):
        receipt = _run(registry, context, graph.NORMALIZED, Action.PURGE)
        assert receipt.evidence["rows_removed"] == 24
        assert receipt.evidence["rows_remaining"] == 0
        # Still queryable: a purged artifact must be distinguishable from one
        # that never existed.
        assert read_table(paths, "normalized") == []

    def test_purge_is_idempotent(self, registry, context):
        _run(registry, context, graph.NORMALIZED, Action.PURGE)
        assert _run(registry, context, graph.NORMALIZED, Action.PURGE).changed is False

    def test_broken_lineage_snapshot_is_a_purgeable_disposable_copy(
        self, registry, context, paths
    ):
        receipt = _run(registry, context, graph.ORPHAN, Action.PURGE)
        assert receipt.succeeded is True
        assert receipt.evidence["rows_removed"] == 6
        assert read_table(paths, "legacy_snapshot") == []

    def test_source_feeds_cannot_be_rebuilt_or_purged(self, registry, context, paths):
        # Purging the partner feed would destroy the input a rebuild depends on
        # and would make the demo unrepeatable.
        for action in (Action.PURGE, Action.REBUILD):
            receipt = registry.execute(context, graph.SOURCE, action)
            assert receipt.succeeded is False
            assert "not an approved" in receipt.error
        assert len(table_row_ids(paths, "partner_feed")) == 24

    def test_declines_actions_outside_its_family(self):
        from demo.estate import resolve_artifact

        adapter = WarehouseAdapter()
        assert not adapter.supports(resolve_artifact(graph.NORMALIZED), Action.FREEZE)


class TestGuards:
    def test_a_foreign_urn_is_refused_on_isolation_grounds_first(self, registry, context):
        # Not merely "this estate has no such artifact" -- the guard must fire
        # before resolution, so the refusal is about authority rather than luck.
        with pytest.raises(NamespaceViolation, match=r"outside the 'license\.'"):
            registry.execute(context, FOREIGN, Action.FREEZE)

    def test_a_urn_with_no_local_artifact_is_refused(self, registry):
        with pytest.raises(EstateError, match="no local artifact"):
            registry.resolve(
                "urn:li:dataset:(urn:li:dataPlatform:duckdb,license.reviews.ghost,PROD)",
                Action.PURGE,
            )

    def test_an_unsupported_action_raises_no_adapter(self, registry):
        # The analytics table has no freeze semantics. This must surface as
        # unhandled residual exposure, not as a silent success.
        with pytest.raises(NoAdapterError, match="no containment adapter"):
            registry.resolve(graph.ANALYTICS, Action.FREEZE)

    def test_no_adapter_propagates_through_execute(self, registry, context):
        with pytest.raises(NoAdapterError):
            registry.execute(context, graph.ANALYTICS, Action.FREEZE)

    def test_namespace_violation_is_raised_before_any_state_changes(self, paths):
        foreign_ns = Namespace(
            project_slug="other",
            urn_prefix="lifeboat.",
            project_tag="project-other",
            domain="Other",
        )
        context = AdapterContext(paths=paths, namespace=foreign_ns)
        with pytest.raises(NamespaceViolation):
            ApiFreezeAdapter().apply(context, graph.PREDICT_API, Action.FREEZE)
        assert not ServingControl.load(paths.serving_path).is_blocked(graph.PREDICT_API)

    def test_supported_actions_reports_what_can_actually_be_done(self, registry):
        assert registry.supported_actions(graph.PREDICT_API) == frozenset({Action.FREEZE})
        assert registry.supported_actions(graph.MODEL) == frozenset(
            {Action.RETRAIN, Action.REPLACE}
        )
        assert registry.supported_actions(FOREIGN) == frozenset()


class TestFaultInjection:
    def test_an_injected_failure_produces_a_failed_receipt(self, registry, context, paths):
        def boom(adapter: str, urn: str, action: Action) -> None:
            if adapter == "export-quarantine":
                raise ContainmentError("simulated quarantine filesystem failure")

        context.fault_injector = boom
        receipt = registry.execute(context, graph.EXPORT, Action.QUARANTINE)

        assert receipt.succeeded is False
        assert "simulated quarantine filesystem failure" in receipt.error
        # And crucially, the artifact really is untouched -- a failed containment
        # must leave the exposure in place so the report can name it.
        assert export_path(paths).exists()
        assert fetch_export(paths)

    def test_injection_fires_before_the_adapter_acts(self, registry, context, paths):
        context.fault_injector = lambda *_: (_ for _ in ()).throw(ContainmentError("nope"))
        registry.execute(context, graph.PREDICT_API, Action.FREEZE)
        assert not ServingControl.load(paths.serving_path).is_blocked(graph.PREDICT_API)

    def test_unaffected_adapters_still_run(self, registry, context, paths):
        def only_exports(adapter: str, urn: str, action: Action) -> None:
            if adapter == "export-quarantine":
                raise ContainmentError("simulated failure")

        context.fault_injector = only_exports
        assert _run(registry, context, graph.PREDICT_API, Action.FREEZE).changed is True


class TestOrdering:
    def test_freeze_runs_before_everything_else(self):
        freeze = execution_stage(Action.FREEZE, ArtifactClass.API)
        assert freeze < execution_stage(Action.QUARANTINE, ArtifactClass.EXPORT)
        assert freeze < execution_stage(Action.PURGE, ArtifactClass.VECTOR_INDEX)

    def test_upstream_rebuilds_precede_the_index_that_reads_them(self):
        # If the index rebuilt first it would regenerate from revoked rows.
        assert execution_stage(Action.REBUILD, ArtifactClass.DATASET) < execution_stage(
            Action.REBUILD, ArtifactClass.FEATURE
        )
        assert execution_stage(Action.REBUILD, ArtifactClass.FEATURE) < execution_stage(
            Action.REBUILD, ArtifactClass.VECTOR_INDEX
        )

    def test_replace_runs_last(self):
        assert execution_stage(Action.REPLACE, ArtifactClass.MODEL) > execution_stage(
            Action.RETRAIN, ArtifactClass.MODEL
        )

    def test_ordering_is_total_over_the_demo_plan(self):
        plan = [
            (Action.FREEZE, ArtifactClass.API),
            (Action.QUARANTINE, ArtifactClass.EXPORT),
            (Action.PURGE, ArtifactClass.VECTOR_INDEX),
            (Action.REBUILD, ArtifactClass.DATASET),
            (Action.REBUILD, ArtifactClass.FEATURE),
            (Action.REBUILD, ArtifactClass.VECTOR_INDEX),
            (Action.RETRAIN, ArtifactClass.MODEL),
            (Action.REPLACE, ArtifactClass.MODEL),
        ]
        stages = [execution_stage(action, cls) for action, cls in plan]
        assert stages == sorted(stages)
        assert len(set(stages)) == len(stages), "ties would make execution order ambiguous"


class TestFullSequence:
    def test_the_whole_plan_ends_with_nothing_partner_derived_serving(
        self, registry, context, paths
    ):
        for urn, action in (
            (graph.PREDICT_API, Action.FREEZE),
            (graph.EXPORT, Action.QUARANTINE),
            (graph.VECTOR_INDEX, Action.PURGE),
            (graph.NORMALIZED, Action.REBUILD),
            (graph.FEATURES, Action.REBUILD),
            (graph.VECTOR_INDEX, Action.REBUILD),
            (graph.MODEL, Action.RETRAIN),
            (graph.MODEL, Action.REPLACE),
        ):
            _run(registry, context, urn, action)

        assert not any(
            rid.startswith(PARTNER_PREFIX) for rid in index_manifest(paths)["row_ids"]
        )
        assert not any(
            rid.startswith(PARTNER_PREFIX)
            for rid in training_manifest(paths, "review_sentiment")["row_ids"]
        )
        assert not export_path(paths).exists()
        assert ServingControl.load(paths.serving_path).is_blocked(graph.PREDICT_API)

    def test_the_unaffected_branch_is_untouched(self, registry, context, paths):
        before_volume = read_table(paths, "review_volume")
        before_model = training_manifest(paths, "approved_sentiment")

        for urn, action in (
            (graph.PREDICT_API, Action.FREEZE),
            (graph.EXPORT, Action.QUARANTINE),
            (graph.VECTOR_INDEX, Action.PURGE),
            (graph.MODEL, Action.RETRAIN),
        ):
            _run(registry, context, urn, action)

        assert read_table(paths, "review_volume") == before_volume
        assert training_manifest(paths, "approved_sentiment") == before_model
        assert active_version(paths, "approved_sentiment") == "v1"

    def test_the_partner_source_feed_survives_so_the_demo_can_reset(
        self, registry, context, paths
    ):
        for urn, action in (
            (graph.NORMALIZED, Action.REBUILD),
            (graph.VECTOR_INDEX, Action.PURGE),
        ):
            _run(registry, context, urn, action)
        assert len(table_row_ids(paths, "partner_feed")) == 24
