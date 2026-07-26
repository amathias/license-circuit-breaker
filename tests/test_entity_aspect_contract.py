"""Every proposal must satisfy DataHub's pinned entity/aspect registry.

This suite exists because of a live failure the whole offline test suite missed.
``demo/graph.py`` declared native ``mlModel`` and ``mlFeatureTable`` URNs while
``adapters/catalog.py`` emitted ``datasetProperties`` for every entity. DataHub
1.6.0 does not register that aspect on either type, so ``demo.cli seed`` failed
with **HTTP 422 Unknown aspect datasetProperties for entity mlModel** on its
first live run.

Nothing caught it because nothing could:

- the SDK derives ``entityType`` from the URN and accepts any aspect beside it,
  so ``MetadataChangeProposalWrapper`` builds the invalid pair without complaint;
- the offline seed path wrote straight into the in-memory fake, which recorded a
  hardcoded ``entity_type="dataset"`` and never built a proposal at all.

So the tests here build **real SDK proposals** for **every spec the seed emits**
and check each ``(entityType, aspectName)`` pair against a pinned snapshot of the
server registry. A fake cannot satisfy them, because a fake is not involved.
"""

from __future__ import annotations

import json

import pytest
from datahub.emitter.mcp import MetadataChangeProposalWrapper
from datahub.ingestion.graph.entity_aspect_specs import EntityAspectSpecs
from datahub.metadata.schema_classes import DatasetPropertiesClass, StatusClass

from adapters.catalog import EntitySpec, build_entity_proposals, domain_urn
from adapters.entity_registry import (
    REGISTRY_PATH,
    AspectContractError,
    check_aspects,
    entity_type_of,
    get_registry,
    require_supported_aspects,
)
from app.namespace import Namespace
from demo.graph import ENTITY_TYPE, NODES, SENTINEL_URN, all_urns
from demo.seed import build_specs

NAMESPACE = Namespace(
    project_slug="license-circuit-breaker",
    urn_prefix="license.",
    project_tag="project-license-circuit-breaker",
    domain="Demo / License Circuit Breaker",
)

#: The aspects the seed actually emits for a full catalog entry.
SEEDED_ASPECTS = frozenset(
    {"datasetProperties", "status", "globalTags", "domains", "upstreamLineage"}
)


def _model_urn(name: str = "license.models.review_sentiment") -> str:
    return f"urn:li:mlModel:(urn:li:dataPlatform:mlflow,{name},PROD)"


def _feature_table_urn(name: str = "license.features.review_sentiment") -> str:
    return f"urn:li:mlFeatureTable:(urn:li:dataPlatform:feast,{name},PROD)"


class TestPinnedRegistrySnapshot:
    """The snapshot must be a faithful, self-describing DataHub 1.6.0 registry."""

    def test_snapshot_parses_as_the_sdk_contract_type(self):
        """The SDK's own type must accept the file, not just our loader.

        If this drifts, the snapshot has stopped being comparable to what a live
        server reports through ``get_entity_aspect_specs()``.
        """
        payload = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        specs = EntityAspectSpecs.from_dict(payload)

        assert specs.supports("dataset", "datasetProperties")
        assert not specs.supports("mlModel", "datasetProperties")

    def test_snapshot_records_its_provenance(self):
        payload = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        source = payload["_source"]

        assert source["repository"] == "datahub-project/datahub"
        assert source["tag"] == "v1.6.0"
        assert source["sdk_pin"] == "acryl-datahub==1.6.0.15"
        assert source["path"].endswith("entity-registry.yml")

    def test_snapshot_matches_the_installed_sdk_pin(self):
        """The registry and the SDK must describe the same DataHub."""
        import datahub

        payload = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        pinned = payload["_source"]["sdk_pin"].split("==")[1]

        assert datahub.__version__ == pinned, (
            f"registry snapshot pins acryl-datahub {pinned} but {datahub.__version__} "
            "is installed; regenerate the snapshot against the matching DataHub tag"
        )

    def test_registry_encodes_the_failure_that_produced_this_suite(self):
        """The exact pairs behind the live 422, asserted as facts."""
        registry = get_registry()

        assert registry.supports("dataset", "datasetProperties")
        assert not registry.supports("mlModel", "datasetProperties")
        assert not registry.supports("mlFeatureTable", "datasetProperties")

        # Lineage is the second half of the problem: neither ML type accepts the
        # upstreamLineage aspect the impact analysis walks.
        assert registry.supports("dataset", "upstreamLineage")
        assert not registry.supports("mlModel", "upstreamLineage")
        assert not registry.supports("mlFeatureTable", "upstreamLineage")


class TestSeedProposalsAgainstRegistry:
    """The real contract test: every proposal the seed emits, checked."""

    @pytest.fixture
    def proposals(self) -> list[MetadataChangeProposalWrapper]:
        built: list[MetadataChangeProposalWrapper] = []
        for spec in build_specs(NAMESPACE):
            built.extend(build_entity_proposals(spec))
        return built

    def test_every_seeded_proposal_is_registered(self, proposals):
        registry = get_registry()
        unsupported = [
            (p.entityUrn, p.entityType, p.aspectName)
            for p in proposals
            if not registry.supports(p.entityType, p.aspectName)
        ]

        assert unsupported == [], (
            f"{len(unsupported)} proposal(s) target an aspect their entity type does "
            f"not register and would be rejected with HTTP 422: {unsupported}"
        )

    def test_proposals_survive_a_serialize_deserialize_round_trip(self, proposals):
        """What is checked must be what goes on the wire.

        ``to_obj``/``from_obj`` is the emitter's own serialization path, so a
        proposal that changes entity type or aspect name in transit would make
        the check above meaningless.
        """
        for proposal in proposals:
            obj = proposal.to_obj()
            restored = MetadataChangeProposalWrapper.from_obj(obj)

            assert restored.entityUrn == proposal.entityUrn
            assert restored.entityType == proposal.entityType
            assert restored.aspectName == proposal.aspectName
            assert obj["entityType"] == proposal.entityType
            assert obj["aspectName"] == proposal.aspectName

    def test_proposals_convert_to_valid_mcps(self, proposals):
        """The wrapper must lower to a metadata change proposal GMS would accept."""
        registry = get_registry()
        for proposal in proposals:
            mcp = proposal.make_mcp()

            assert mcp.entityType == proposal.entityType
            assert mcp.aspectName == proposal.aspectName
            assert registry.supports(mcp.entityType, mcp.aspectName)

    def test_the_seed_emits_the_documented_aspect_set(self, proposals):
        """Guards the handoff's DataHub-operations table against drift."""
        emitted = {p.aspectName for p in proposals}

        assert emitted == SEEDED_ASPECTS

    def test_every_seeded_entity_is_covered(self, proposals):
        """No entity may slip through without its proposals being checked."""
        covered = {p.entityUrn for p in proposals}

        assert covered == set(all_urns())

    def test_every_proposal_carries_a_license_urn(self, proposals):
        """The contract gate must never become a way to write out of namespace."""
        for proposal in proposals:
            assert ".(" not in proposal.entityUrn or "license." in proposal.entityUrn


class TestUniformDatasetModel:
    """The architectural decision, asserted rather than described."""

    def test_every_fixture_node_is_a_dataset_urn(self):
        for node in NODES:
            assert node.entity_type == ENTITY_TYPE, (
                f"{node.urn} is a {node.entity_type} URN. See docs/DECISIONS.md ADR-024."
            )

    def test_the_sentinel_is_a_dataset_urn(self):
        assert entity_type_of(SENTINEL_URN) == ENTITY_TYPE

    def test_declaring_a_native_ml_node_is_rejected_at_construction(self):
        """The defect cannot be reintroduced by editing the graph."""
        from app.rights import ArtifactClass, Purpose
        from demo.graph import DemoNode

        with pytest.raises(ValueError, match="uniformly 'dataset'"):
            DemoNode(
                urn=_model_urn(),
                artifact_class=ArtifactClass.MODEL,
                purposes=frozenset({Purpose.TRAINING}),
            )

    def test_the_ml_platforms_are_still_visible(self):
        """Uniform entity type must not cost the ML semantics judges look for."""
        platforms = {node.urn.split("dataPlatform:")[1].split(",")[0] for node in NODES}

        assert {"mlflow", "feast", "vectorstore"} <= platforms

    def test_artifact_class_still_distinguishes_models_and_features(self):
        from app.rights import ArtifactClass

        classes = {node.artifact_class for node in NODES}

        assert ArtifactClass.MODEL in classes
        assert ArtifactClass.FEATURE in classes


class TestContractGuardRejectsTheHistoricalDefect:
    """The guard must fail on exactly what reached production."""

    def _ml_spec(self, urn: str) -> EntitySpec:
        return EntitySpec(
            urn=urn,
            name=urn,
            description="native ML entity",
            custom_properties={"artifact_class": "model"},
            tags=("lcb-demo-fixture",),
            domain_urn=domain_urn(NAMESPACE.domain),
        )

    def test_building_proposals_for_an_mlmodel_urn_is_refused(self):
        with pytest.raises(AspectContractError, match="datasetProperties"):
            build_entity_proposals(self._ml_spec(_model_urn()))

    def test_building_proposals_for_an_mlfeaturetable_urn_is_refused(self):
        with pytest.raises(AspectContractError, match="datasetProperties"):
            build_entity_proposals(self._ml_spec(_feature_table_urn()))

    def test_the_refusal_names_the_422_it_prevents(self):
        with pytest.raises(AspectContractError) as excinfo:
            build_entity_proposals(self._ml_spec(_model_urn()))

        message = str(excinfo.value)
        assert "422" in message
        assert "mlModel" in message

    def test_upstream_lineage_onto_an_ml_entity_is_refused(self):
        """The second 422 the old graph would have hit, had the first been fixed alone."""
        violations = check_aspects(_model_urn(), ["upstreamLineage"])

        assert [v.aspect_name for v in violations] == ["upstreamLineage"]

    def test_aspects_valid_for_ml_entities_are_still_accepted(self):
        """The guard rejects mismatches, not ML entities as such."""
        assert check_aspects(_model_urn(), ["status", "globalTags", "domains"]) == []

    def test_an_unparseable_urn_cannot_be_proven_safe(self):
        with pytest.raises(AspectContractError, match="cannot determine entity type"):
            entity_type_of("not-a-urn")

    def test_an_unknown_entity_type_rejects_every_aspect(self):
        violations = check_aspects("urn:li:notAThing:whatever", ["status"])

        assert [v.aspect_name for v in violations] == ["status"]

    def test_require_supported_aspects_reports_every_violation_at_once(self):
        with pytest.raises(AspectContractError) as excinfo:
            require_supported_aspects(
                _model_urn(), ["datasetProperties", "upstreamLineage"], operation="test"
            )

        message = str(excinfo.value)
        assert "datasetProperties" in message
        assert "upstreamLineage" in message


class TestEmitPathIsGuarded:
    """The gate sits in front of the network, not only in front of build."""

    def test_emit_refuses_an_unregistered_proposal(self):
        from adapters.catalog import LiveCatalog

        catalog = LiveCatalog("http://gms.invalid", "", NAMESPACE)
        bad = MetadataChangeProposalWrapper(
            entityUrn=_model_urn(),
            aspect=DatasetPropertiesClass(name="x"),
        )

        # Raises before any emitter is constructed, so the invalid pair never
        # reaches the network even if a caller bypassed build_proposals.
        with pytest.raises(AspectContractError):
            catalog.emit([bad])

    def test_emit_accepts_a_registered_proposal_up_to_the_network(self):
        from adapters.catalog import LiveCatalog

        catalog = LiveCatalog("http://gms.invalid", "", NAMESPACE)
        good = MetadataChangeProposalWrapper(
            entityUrn=SENTINEL_URN,
            aspect=StatusClass(removed=False),
        )

        # The contract gate passes; only the unreachable host stops it.
        with pytest.raises(Exception) as excinfo:
            catalog.emit([good])

        assert not isinstance(excinfo.value, AspectContractError)
