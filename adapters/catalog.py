"""Live DataHub catalog lifecycle.

Emits the aspects a real catalog entry needs -- properties, domain, status, tags,
and lineage -- as ``MetadataChangeProposalWrapper`` objects through the DataHub
1.6.0 Python SDK, rather than only stamping tags.

Entity model: everything is a ``dataset`` URN carrying an ``artifact_class``
custom property. Models, feature tables, indexes, APIs, and exports are all
represented this way. Native ``mlModel`` / ``mlFeatureTable`` entities would be a
better semantic fit, but they need their own aspect sets and lineage handling; a
uniform dataset model is what can be verified deterministically against a live
instance in one milestone. The ``artifact_class`` property carries the semantics
the policy engine needs.

Domain and tag *controls* -- the domain entity and the tag entities themselves --
are shared, coordinator-owned scaffolding. This module references them and never
creates, mutates, or removes them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.namespace import Namespace, require_in_namespace

if TYPE_CHECKING:  # pragma: no cover
    pass


class CatalogError(Exception):
    """Raised when a catalog operation cannot be completed or verified."""


@dataclass(frozen=True)
class EntitySpec:
    """Everything needed to materialize one catalog entry."""

    urn: str
    name: str
    description: str
    custom_properties: dict[str, str]
    tags: tuple[str, ...]
    domain_urn: str
    upstreams: tuple[str, ...] = ()


@dataclass
class VerificationResult:
    """Outcome of rereading and verifying what was emitted."""

    verified_entities: list[str] = field(default_factory=list)
    verified_edges: list[tuple[str, str]] = field(default_factory=list)
    missing_entities: list[str] = field(default_factory=list)
    missing_edges: list[tuple[str, str]] = field(default_factory=list)
    property_mismatches: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.missing_entities or self.missing_edges or self.property_mismatches)

    def describe(self) -> str:
        if self.ok:
            return (
                f"{len(self.verified_entities)} entities and "
                f"{len(self.verified_edges)} edges verified"
            )
        parts = []
        if self.missing_entities:
            parts.append(f"{len(self.missing_entities)} entities missing")
        if self.missing_edges:
            parts.append(f"{len(self.missing_edges)} edges missing")
        if self.property_mismatches:
            parts.append(f"{len(self.property_mismatches)} property mismatches")
        return "; ".join(parts)


def domain_urn(domain_name: str) -> str:
    """URN for the coordinator-owned project domain.

    DataHub domain URNs use an id, not a display name. The id is derived
    deterministically so seed and readiness agree without a lookup.
    """
    slug = domain_name.strip().lower().replace(" / ", "-").replace(" ", "-")
    return f"urn:li:domain:{slug}"


def tag_urn(tag: str) -> str:
    return f"urn:li:tag:{tag}"


class LiveCatalog:
    """Emits and verifies catalog state through the DataHub SDK.

    Every mutating call is namespace-guarded before a proposal is constructed, so
    a foreign URN never reaches the emitter.
    """

    def __init__(self, gms_url: str, token: str, namespace: Namespace) -> None:
        if not gms_url:
            raise CatalogError("DATAHUB_GMS_URL is not configured")
        self._gms_url = gms_url.rstrip("/")
        self._token = token
        self._namespace = namespace
        self._emitter: Any = None

    def _get_emitter(self) -> Any:
        if self._emitter is None:
            try:
                from datahub.emitter.rest_emitter import DatahubRestEmitter
            except ImportError as exc:  # pragma: no cover - dependency is declared
                raise CatalogError(
                    "acryl-datahub is required for live catalog writes. "
                    "Install with: pip install 'acryl-datahub>=1.6.0,<1.7'"
                ) from exc
            # The token is handed to the emitter and never logged or persisted.
            self._emitter = DatahubRestEmitter(gms_server=self._gms_url, token=self._token or None)
        return self._emitter

    # -- proposal construction ------------------------------------------

    def build_proposals(self, spec: EntitySpec) -> list[Any]:
        """Build the full aspect set for one entity.

        Raises:
            NamespaceViolation: if the entity or any upstream is out of namespace.
        """
        require_in_namespace(spec.urn, self._namespace, operation="catalog-upsert")
        for upstream in spec.upstreams:
            require_in_namespace(upstream, self._namespace, operation="catalog-lineage")

        from datahub.emitter.mcp import MetadataChangeProposalWrapper
        from datahub.metadata.schema_classes import (
            DatasetPropertiesClass,
            DomainsClass,
            GlobalTagsClass,
            StatusClass,
            TagAssociationClass,
        )

        proposals = [
            MetadataChangeProposalWrapper(
                entityUrn=spec.urn,
                aspect=DatasetPropertiesClass(
                    name=spec.name,
                    description=spec.description,
                    customProperties=dict(spec.custom_properties),
                ),
            ),
            # Explicitly active. Soft reset flips this to removed=True, so seed
            # must assert the active state rather than relying on a default.
            MetadataChangeProposalWrapper(entityUrn=spec.urn, aspect=StatusClass(removed=False)),
            MetadataChangeProposalWrapper(
                entityUrn=spec.urn,
                aspect=GlobalTagsClass(
                    tags=[TagAssociationClass(tag=tag_urn(t)) for t in spec.tags]
                ),
            ),
            MetadataChangeProposalWrapper(
                entityUrn=spec.urn, aspect=DomainsClass(domains=[spec.domain_urn])
            ),
        ]

        if spec.upstreams:
            proposals.append(self._build_lineage(spec.urn, spec.upstreams))

        return proposals

    def _build_lineage(self, urn: str, upstreams: tuple[str, ...]) -> Any:
        from datahub.emitter.mcp import MetadataChangeProposalWrapper
        from datahub.metadata.schema_classes import (
            DatasetLineageTypeClass,
            UpstreamClass,
            UpstreamLineageClass,
        )

        return MetadataChangeProposalWrapper(
            entityUrn=urn,
            aspect=UpstreamLineageClass(
                upstreams=[
                    UpstreamClass(dataset=u, type=DatasetLineageTypeClass.TRANSFORMED)
                    for u in upstreams
                ]
            ),
        )

    # -- emission -------------------------------------------------------

    def emit(self, proposals: list[Any]) -> int:
        """Emit proposals. Returns the count emitted."""
        emitter = self._get_emitter()
        for proposal in proposals:
            emitter.emit(proposal)
        return len(proposals)

    def upsert(self, spec: EntitySpec) -> int:
        return self.emit(self.build_proposals(spec))

    def set_custom_properties(
        self, urn: str, name: str, description: str, properties: dict[str, str]
    ) -> None:
        """Rewrite ``datasetProperties`` with a complete property set.

        The aspect is replace-semantics in DataHub, so the caller must supply the
        merged set. :meth:`adapters.datahub.LiveDataHubClient.set_properties`
        does that merge, which is why this takes the final state rather than a
        patch: one place decides what survives, and it is the one that just read
        the entity.
        """
        require_in_namespace(urn, self._namespace, operation="catalog-properties")

        from datahub.emitter.mcp import MetadataChangeProposalWrapper
        from datahub.metadata.schema_classes import DatasetPropertiesClass

        self.emit(
            [
                MetadataChangeProposalWrapper(
                    entityUrn=urn,
                    aspect=DatasetPropertiesClass(
                        name=name,
                        description=description,
                        customProperties={str(k): str(v) for k, v in properties.items()},
                    ),
                )
            ]
        )

    def set_status(self, urn: str, removed: bool) -> None:
        """Soft-delete or restore one entity.

        Soft status is the reset mechanism: it is reversible, leaves the audit
        trail intact, and never touches the shared domain or tag controls.
        """
        require_in_namespace(urn, self._namespace, operation="catalog-status")

        from datahub.emitter.mcp import MetadataChangeProposalWrapper
        from datahub.metadata.schema_classes import StatusClass

        self.emit(
            [MetadataChangeProposalWrapper(entityUrn=urn, aspect=StatusClass(removed=removed))]
        )
