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

**Entity model.** Every node is a ``dataset`` URN carrying an ``artifact_class``
custom property; the platform segment (``mlflow``, ``feast``, ``vectorstore``,
``rest-api``, ``file``) keeps the artifact's nature visible in DataHub. Native
``mlModel`` / ``mlFeatureTable`` URNs are the better semantic fit, but DataHub
1.6.0 does not register ``datasetProperties`` or ``upstreamLineage`` on either
type, so they cannot carry the property set the policy engine reads or the
lineage the impact analysis walks. See ``docs/DECISIONS.md`` ADR-024.

:class:`DemoNode` enforces this at import time rather than trusting the prose:
declaring a node with a non-``dataset`` URN raises immediately.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.namespace import parse_urn
from app.rights import ArtifactClass, Criticality, Exposure, Purpose

#: The one entity type this project writes. See the module docstring.
ENTITY_TYPE = "dataset"

#: Marker applied to every seeded entity. Reset removes only entities carrying it.
FIXTURE_MARKER = "lcb-demo-fixture"

#: Sentinel entity written last during seed and checked first during reset.
#: Its absence means the fixtures were never seeded, or the client is pointed at
#: the wrong instance -- either way reset must refuse rather than guess.
SENTINEL_NAME = "license.__fixture_sentinel__"
SENTINEL_URN = f"urn:li:dataset:(urn:li:dataPlatform:duckdb,{SENTINEL_NAME},PROD)"


def _dataset(name: str, platform: str = "duckdb") -> str:
    return f"urn:li:dataset:(urn:li:dataPlatform:{platform},{name},PROD)"


@dataclass(frozen=True)
class DemoNode:
    """One node in the seeded graph.

    The entity type is not a field. It was one, and it drifted: three nodes
    declared ``mlModel``/``mlFeatureTable`` while nothing read the field and the
    seed emitted dataset aspects regardless, so the contradiction was invisible
    until a live seed returned 422. The type is now derived from the URN and
    checked, which is the only version of that fact that cannot go stale.
    """

    urn: str
    artifact_class: ArtifactClass
    purposes: frozenset[Purpose]
    exposure: Exposure = Exposure.INTERNAL
    criticality: Criticality = Criticality.MEDIUM
    rebuildable_from_replacement: bool = False
    description: str = ""

    def __post_init__(self) -> None:
        if self.entity_type != ENTITY_TYPE:
            raise ValueError(
                f"{self.urn!r} is a {self.entity_type!r} URN, but this project's fixture "
                f"graph is uniformly {ENTITY_TYPE!r}. DataHub 1.6.0 does not register "
                "datasetProperties or upstreamLineage on ML entity types, so a native URN "
                "cannot carry the artifact_class properties or the lineage this demo needs. "
                "See docs/DECISIONS.md ADR-024."
            )

    @property
    def entity_type(self) -> str:
        """The DataHub entity type encoded in :attr:`urn`."""
        return parse_urn(self.urn).entity_type


# --- The revoked source and its descendants -----------------------------

SOURCE = _dataset("license.reviews.partner_feed")
REPLACEMENT_SOURCE = _dataset("license.reviews.approved_feed")

NORMALIZED = _dataset("license.reviews.normalized")
# Platform carries what the entity type no longer can: these are the feature
# table and the model, and DataHub shows them under feast and mlflow.
FEATURES = _dataset("license.features.review_sentiment", platform="feast")
MODEL = _dataset("license.models.review_sentiment", platform="mlflow")
VECTOR_INDEX = _dataset("license.indexes.review_search", platform="vectorstore")
PREDICT_API = _dataset("license.services.predict_api", platform="rest-api")
EXPORT = _dataset("license.exports.reviews_extract", platform="file")

# Precision-proving branches.
ANALYTICS = _dataset("license.reports.review_volume")
APPROVED_MODEL = _dataset("license.models.approved_sentiment", platform="mlflow")
ORPHAN = _dataset("license.reviews.legacy_snapshot")


NODES: tuple[DemoNode, ...] = (
    DemoNode(
        urn=SOURCE,
        artifact_class=ArtifactClass.DATASET,
        purposes=frozenset({Purpose.TRAINING, Purpose.RETRIEVAL, Purpose.ANALYTICS}),
        criticality=Criticality.HIGH,
        description="Licensed partner review feed. Subject of the rights revocation.",
    ),
    DemoNode(
        urn=REPLACEMENT_SOURCE,
        artifact_class=ArtifactClass.DATASET,
        purposes=frozenset({Purpose.TRAINING, Purpose.RETRIEVAL, Purpose.ANALYTICS}),
        description="Approved replacement feed. Rebuild and retrain draw from this.",
    ),
    DemoNode(
        urn=NORMALIZED,
        artifact_class=ArtifactClass.DATASET,
        purposes=frozenset({Purpose.TRAINING, Purpose.RETRIEVAL}),
        rebuildable_from_replacement=True,
        description="Cleaned reviews derived from the partner feed.",
    ),
    DemoNode(
        urn=FEATURES,
        artifact_class=ArtifactClass.FEATURE,
        purposes=frozenset({Purpose.TRAINING}),
        rebuildable_from_replacement=True,
        description="Sentiment features computed from normalized reviews.",
    ),
    DemoNode(
        urn=MODEL,
        artifact_class=ArtifactClass.MODEL,
        purposes=frozenset({Purpose.TRAINING, Purpose.SERVING}),
        criticality=Criticality.HIGH,
        rebuildable_from_replacement=True,
        description="Sentiment classifier trained on the revoked features.",
    ),
    DemoNode(
        urn=VECTOR_INDEX,
        artifact_class=ArtifactClass.VECTOR_INDEX,
        purposes=frozenset({Purpose.RETRIEVAL}),
        rebuildable_from_replacement=True,
        description="Local vector index built from revoked review text.",
    ),
    DemoNode(
        urn=PREDICT_API,
        artifact_class=ArtifactClass.API,
        purposes=frozenset({Purpose.SERVING}),
        exposure=Exposure.PUBLIC,
        criticality=Criticality.HIGH,
        description="Prediction and search endpoint serving revoked-derived content.",
    ),
    DemoNode(
        urn=EXPORT,
        artifact_class=ArtifactClass.EXPORT,
        purposes=frozenset({Purpose.EXPORT}),
        exposure=Exposure.OFFLINE,
        description="CSV extract that has left the platform boundary.",
    ),
    # Proves precision: descends from the source but uses only analytics, a
    # purpose the demo revocation does not remove.
    DemoNode(
        urn=ANALYTICS,
        artifact_class=ArtifactClass.DATASET,
        purposes=frozenset({Purpose.ANALYTICS}),
        criticality=Criticality.LOW,
        description="Aggregate review-volume report. Unaffected by a training revocation.",
    ),
    # Proves precision: an entirely separate approved branch.
    DemoNode(
        urn=APPROVED_MODEL,
        artifact_class=ArtifactClass.MODEL,
        purposes=frozenset({Purpose.TRAINING, Purpose.SERVING}),
        description="Model trained only on the approved feed. Must remain untouched.",
    ),
    # Proves fail-closed behavior: its upstream edge does not resolve.
    DemoNode(
        urn=ORPHAN,
        artifact_class=ArtifactClass.DATASET,
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
