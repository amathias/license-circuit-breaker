"""The demo supply chain, as a deterministic declaration.

One licensed source reaches a cleaned dataset, a feature table, a model, a vector
index, a serving API, and an export. Alongside it sit two branches that exist to
prove precision rather than volume:

- an **approved branch** that descends from a different, unaffected source;
- an **analytics consumer** that uses only a purpose the revocation does not remove;
- a **broken-lineage node** whose upstream edge DataHub cannot resolve, which must
  force an escalation instead of a confident verdict.

Every URN carries the ``license.`` prefix and every entity carries the fixture
marker, so seed and reset can never touch another submission's entities.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.rights import ArtifactClass, Criticality, Exposure, Purpose

#: Marker applied to every seeded entity. Reset removes only entities carrying it.
FIXTURE_MARKER = "lcb-demo-fixture"

#: Sentinel entity written last during seed and checked first during reset.
#: Its absence means the fixtures were never seeded, or the client is pointed at
#: the wrong instance -- either way reset must refuse rather than guess.
SENTINEL_NAME = "license.__fixture_sentinel__"
SENTINEL_URN = f"urn:li:dataset:(urn:li:dataPlatform:duckdb,{SENTINEL_NAME},PROD)"


def _dataset(name: str, platform: str = "duckdb") -> str:
    return f"urn:li:dataset:(urn:li:dataPlatform:{platform},{name},PROD)"


def _model(name: str) -> str:
    return f"urn:li:mlModel:(urn:li:dataPlatform:mlflow,{name},PROD)"


def _feature_table(name: str) -> str:
    return f"urn:li:mlFeatureTable:(urn:li:dataPlatform:feast,{name},PROD)"


@dataclass(frozen=True)
class DemoNode:
    """One node in the seeded graph."""

    urn: str
    artifact_class: ArtifactClass
    entity_type: str
    purposes: frozenset[Purpose]
    exposure: Exposure = Exposure.INTERNAL
    criticality: Criticality = Criticality.MEDIUM
    rebuildable_from_replacement: bool = False
    description: str = ""


# --- The revoked source and its descendants -----------------------------

SOURCE = _dataset("license.reviews.partner_feed")
REPLACEMENT_SOURCE = _dataset("license.reviews.approved_feed")

NORMALIZED = _dataset("license.reviews.normalized")
FEATURES = _feature_table("license.features.review_sentiment")
MODEL = _model("license.models.review_sentiment")
VECTOR_INDEX = _dataset("license.indexes.review_search", platform="vectorstore")
PREDICT_API = _dataset("license.services.predict_api", platform="rest-api")
EXPORT = _dataset("license.exports.reviews_extract", platform="file")

# Precision-proving branches.
ANALYTICS = _dataset("license.reports.review_volume")
APPROVED_MODEL = _model("license.models.approved_sentiment")
ORPHAN = _dataset("license.reviews.legacy_snapshot")


NODES: tuple[DemoNode, ...] = (
    DemoNode(
        urn=SOURCE,
        artifact_class=ArtifactClass.DATASET,
        entity_type="dataset",
        purposes=frozenset({Purpose.TRAINING, Purpose.RETRIEVAL, Purpose.ANALYTICS}),
        criticality=Criticality.HIGH,
        description="Licensed partner review feed. Subject of the rights revocation.",
    ),
    DemoNode(
        urn=REPLACEMENT_SOURCE,
        artifact_class=ArtifactClass.DATASET,
        entity_type="dataset",
        purposes=frozenset({Purpose.TRAINING, Purpose.RETRIEVAL, Purpose.ANALYTICS}),
        description="Approved replacement feed. Rebuild and retrain draw from this.",
    ),
    DemoNode(
        urn=NORMALIZED,
        artifact_class=ArtifactClass.DATASET,
        entity_type="dataset",
        purposes=frozenset({Purpose.TRAINING, Purpose.RETRIEVAL}),
        rebuildable_from_replacement=True,
        description="Cleaned reviews derived from the partner feed.",
    ),
    DemoNode(
        urn=FEATURES,
        artifact_class=ArtifactClass.FEATURE,
        entity_type="mlFeatureTable",
        purposes=frozenset({Purpose.TRAINING}),
        rebuildable_from_replacement=True,
        description="Sentiment features computed from normalized reviews.",
    ),
    DemoNode(
        urn=MODEL,
        artifact_class=ArtifactClass.MODEL,
        entity_type="mlModel",
        purposes=frozenset({Purpose.TRAINING, Purpose.SERVING}),
        criticality=Criticality.HIGH,
        rebuildable_from_replacement=True,
        description="Sentiment classifier trained on the revoked features.",
    ),
    DemoNode(
        urn=VECTOR_INDEX,
        artifact_class=ArtifactClass.VECTOR_INDEX,
        entity_type="dataset",
        purposes=frozenset({Purpose.RETRIEVAL}),
        rebuildable_from_replacement=True,
        description="Local vector index built from revoked review text.",
    ),
    DemoNode(
        urn=PREDICT_API,
        artifact_class=ArtifactClass.API,
        entity_type="dataset",
        purposes=frozenset({Purpose.SERVING}),
        exposure=Exposure.PUBLIC,
        criticality=Criticality.HIGH,
        description="Prediction and search endpoint serving revoked-derived content.",
    ),
    DemoNode(
        urn=EXPORT,
        artifact_class=ArtifactClass.EXPORT,
        entity_type="dataset",
        purposes=frozenset({Purpose.EXPORT}),
        exposure=Exposure.OFFLINE,
        description="CSV extract that has left the platform boundary.",
    ),
    # Proves precision: descends from the source but uses only analytics, a
    # purpose the demo revocation does not remove.
    DemoNode(
        urn=ANALYTICS,
        artifact_class=ArtifactClass.DATASET,
        entity_type="dataset",
        purposes=frozenset({Purpose.ANALYTICS}),
        criticality=Criticality.LOW,
        description="Aggregate review-volume report. Unaffected by a training revocation.",
    ),
    # Proves precision: an entirely separate approved branch.
    DemoNode(
        urn=APPROVED_MODEL,
        artifact_class=ArtifactClass.MODEL,
        entity_type="mlModel",
        purposes=frozenset({Purpose.TRAINING, Purpose.SERVING}),
        description="Model trained only on the approved feed. Must remain untouched.",
    ),
    # Proves fail-closed behavior: its upstream edge does not resolve.
    DemoNode(
        urn=ORPHAN,
        artifact_class=ArtifactClass.DATASET,
        entity_type="dataset",
        purposes=frozenset({Purpose.TRAINING}),
        description="Legacy snapshot with unresolvable lineage. Must escalate.",
    ),
)


#: ``(upstream, downstream, resolved)``. ``resolved=False`` models a lineage edge
#: DataHub reports but cannot resolve.
EDGES: tuple[tuple[str, str, bool], ...] = (
    (SOURCE, NORMALIZED, True),
    (NORMALIZED, FEATURES, True),
    (FEATURES, MODEL, True),
    (NORMALIZED, VECTOR_INDEX, True),
    (MODEL, PREDICT_API, True),
    (NORMALIZED, EXPORT, True),
    (SOURCE, ANALYTICS, True),
    (SOURCE, ORPHAN, False),
    (REPLACEMENT_SOURCE, APPROVED_MODEL, True),
)


NODES_BY_URN: dict[str, DemoNode] = {node.urn: node for node in NODES}


def all_urns() -> list[str]:
    """Every URN the seed creates, including the sentinel."""
    return [node.urn for node in NODES] + [SENTINEL_URN]
