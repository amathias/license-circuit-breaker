"""Client-side gate on DataHub's entity/aspect registry.

DataHub registers a fixed aspect set per entity type. Emitting an aspect the
target entity does not register is rejected by GMS with **HTTP 422 "Unknown
aspect X for entity Y"** -- and the Python SDK will not catch it first:
``MetadataChangeProposalWrapper`` derives ``entityType`` from the URN and accepts
any aspect object alongside it without complaint. A mismatch therefore survives
every offline test and fails on the first live write.

That is exactly how ``datasetProperties`` came to be emitted onto ``mlModel``
URNs. This module closes the gap by checking every proposal against a **pinned
snapshot of the server-side registry** before it is built, so the contract breaks
in a test rather than against the shared instance.

The snapshot in ``datahub_entity_registry_1_6_0.json`` is derived from
``metadata-models/src/main/resources/entity-registry.yml`` at DataHub tag
``v1.6.0``, matching the coordinator's stack and the ``acryl-datahub==1.6.0.15``
pin. Its shape is what
:class:`datahub.ingestion.graph.entity_aspect_specs.EntityAspectSpecs` produces,
so the SDK's own type parses it and a live server's registry can be diffed
against it without a translation layer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.namespace import NamespaceViolation, parse_urn

#: The pinned snapshot. Regenerate only against a specific DataHub tag, and
#: record that tag in the file's ``_source`` block.
REGISTRY_PATH = Path(__file__).resolve().parent / "datahub_entity_registry_1_6_0.json"


class AspectContractError(Exception):
    """Raised when a proposal targets an aspect its entity type does not register.

    This is the offline equivalent of the server's 422. It is deliberately not a
    subclass of ``CatalogError``: a catalog error means a write could not be
    completed, whereas this means the write was malformed and must never be sent.
    """


@dataclass(frozen=True)
class AspectViolation:
    """One (entity type, aspect) pair the registry does not accept."""

    urn: str
    entity_type: str
    aspect_name: str

    def describe(self) -> str:
        return (
            f"aspect {self.aspect_name!r} is not registered for entity type "
            f"{self.entity_type!r} (urn {self.urn})"
        )


@dataclass(frozen=True)
class EntityAspectRegistry:
    """Which aspects each entity type accepts, per the pinned snapshot."""

    entity_aspects: dict[str, frozenset[str]]
    source: dict[str, str]

    def known_entity_type(self, entity_type: str) -> bool:
        return entity_type in self.entity_aspects

    def supports(self, entity_type: str, aspect_name: str) -> bool:
        """Whether ``entity_type`` registers ``aspect_name``.

        An unknown entity type returns False rather than raising: the caller is
        asking whether a write is safe, and "this entity type is not in the
        registry" is a no, not a question.
        """
        return aspect_name in self.entity_aspects.get(entity_type, frozenset())

    def aspects_for(self, entity_type: str) -> frozenset[str]:
        return self.entity_aspects.get(entity_type, frozenset())


@lru_cache(maxsize=1)
def get_registry() -> EntityAspectRegistry:
    """Load and cache the pinned registry snapshot."""
    with REGISTRY_PATH.open(encoding="utf-8") as handle:
        payload = json.load(handle)

    return EntityAspectRegistry(
        entity_aspects={
            entity: frozenset(aspects) for entity, aspects in payload["entity_aspects"].items()
        },
        source=dict(payload.get("_source", {})),
    )


def entity_type_of(urn: str) -> str:
    """The DataHub entity type encoded in ``urn``.

    Uses the namespace guard's parser rather than a second one, so a URN this
    project would refuse to act on is also one it refuses to classify.

    Raises:
        AspectContractError: if the URN cannot be parsed. An unparseable URN has
            no provable entity type, so it cannot be proven safe to write.
    """
    try:
        return parse_urn(urn).entity_type
    except NamespaceViolation as exc:
        raise AspectContractError(f"cannot determine entity type for {urn!r}: {exc}") from exc


def check_aspects(urn: str, aspect_names: list[str]) -> list[AspectViolation]:
    """Return every aspect in ``aspect_names`` that ``urn``'s entity type rejects.

    Reports all violations rather than the first, so one run tells an operator
    the whole story instead of one aspect at a time.
    """
    entity_type = entity_type_of(urn)
    registry = get_registry()

    if not registry.known_entity_type(entity_type):
        # An entity type absent from the registry cannot accept any aspect, so
        # every proposed aspect is a violation.
        return [AspectViolation(urn, entity_type, name) for name in aspect_names]

    return [
        AspectViolation(urn, entity_type, name)
        for name in aspect_names
        if not registry.supports(entity_type, name)
    ]


def require_supported_aspects(urn: str, aspect_names: list[str], operation: str) -> None:
    """Fail closed unless ``urn``'s entity type registers every named aspect.

    Raises:
        AspectContractError: listing every unsupported aspect.
    """
    violations = check_aspects(urn, aspect_names)
    if not violations:
        return

    registry = get_registry()
    entity_type = entity_type_of(urn)
    detail = "; ".join(v.describe() for v in violations)
    raise AspectContractError(
        f"{operation!r} refused: {detail}. DataHub "
        f"{registry.source.get('tag', 'unknown')} registers "
        f"{len(registry.aspects_for(entity_type))} aspect(s) for {entity_type!r}. "
        "Emitting this would be rejected by GMS with HTTP 422."
    )


__all__ = [
    "REGISTRY_PATH",
    "AspectContractError",
    "AspectViolation",
    "EntityAspectRegistry",
    "check_aspects",
    "entity_type_of",
    "get_registry",
    "require_supported_aspects",
]
