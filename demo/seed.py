"""Deterministic, marker-guarded seed and reset.

Seed is idempotent: running it twice produces the same graph. Reset is
sentinel-guarded and marker-scoped, which is what makes it safe to run against an
instance shared with four other submissions.

Reset refuses unless all three hold:

1. The **sentinel** entity exists. Its absence means the fixtures were never
   seeded here, or the client points at the wrong instance.
2. Every target carries the **fixture marker**. Entities in the ``license.``
   namespace that this project did not seed are left alone.
3. Every target passes the **namespace guard**.

Any one failing aborts the whole reset. There is no partial best-effort mode --
a half-executed reset against shared state is worse than none.
"""

from __future__ import annotations

from dataclasses import dataclass

from adapters.datahub import DataHubClient, EntityContext
from app.namespace import Namespace, NamespaceViolation, assert_scoped_reset, require_in_namespace
from demo.graph import EDGES, FIXTURE_MARKER, NODES, SENTINEL_URN


class SeedError(Exception):
    """Raised when seed or reset cannot proceed safely."""


@dataclass(frozen=True)
class SeedResult:
    created: tuple[str, ...]
    marker: str
    sentinel_urn: str

    @property
    def count(self) -> int:
        return len(self.created)


@dataclass(frozen=True)
class ResetResult:
    removed: tuple[str, ...]
    skipped_unmarked: tuple[str, ...]

    @property
    def count(self) -> int:
        return len(self.removed)


def seed(client: DataHubClient, namespace: Namespace) -> SeedResult:
    """Create the demo graph. Idempotent and namespace-guarded.

    The sentinel is written **last**, so a seed interrupted partway through does
    not leave a sentinel implying a complete fixture set.
    """
    created: list[str] = []

    for node in NODES:
        require_in_namespace(node.urn, namespace, operation="seed")
        _upsert(
            client,
            urn=node.urn,
            entity_type=node.entity_type,
            name=node.urn,
            namespace=namespace,
            properties={
                "artifact_class": node.artifact_class.value,
                "purposes": ",".join(sorted(p.value for p in node.purposes)),
                "exposure": node.exposure.value,
                "criticality": node.criticality.value,
                "rebuildable": str(node.rebuildable_from_replacement).lower(),
                "description": node.description,
            },
        )
        created.append(node.urn)

    for upstream, downstream, resolved in EDGES:
        require_in_namespace(upstream, namespace, operation="seed-lineage")
        require_in_namespace(downstream, namespace, operation="seed-lineage")
        add_edge = getattr(client, "add_edge", None)
        if add_edge is not None:
            add_edge(upstream, downstream, resolved=resolved)

    # Sentinel last: it asserts "the full fixture set is present".
    require_in_namespace(SENTINEL_URN, namespace, operation="seed-sentinel")
    _upsert(
        client,
        urn=SENTINEL_URN,
        entity_type="dataset",
        name=SENTINEL_URN,
        namespace=namespace,
        properties={"role": "fixture-sentinel"},
    )
    created.append(SENTINEL_URN)

    return SeedResult(created=tuple(created), marker=FIXTURE_MARKER, sentinel_urn=SENTINEL_URN)


def reset(client: DataHubClient, namespace: Namespace) -> ResetResult:
    """Remove only entities this project seeded.

    Raises:
        SeedError: if the sentinel is absent.
        NamespaceViolation: if any target falls outside the allocation.
    """
    sentinel = client.get_entity(SENTINEL_URN)
    if sentinel is None:
        raise SeedError(
            f"Reset refused: fixture sentinel {SENTINEL_URN!r} not found. "
            "Either the demo was never seeded here, or the client is pointed at "
            "the wrong DataHub instance. Run seed first."
        )
    if not sentinel.has_tag(FIXTURE_MARKER):
        raise SeedError(
            f"Reset refused: sentinel exists but does not carry the {FIXTURE_MARKER!r} "
            "marker, so it was not created by this project's seed."
        )

    candidates = [node.urn for node in NODES] + [SENTINEL_URN]

    removable: list[str] = []
    skipped: list[str] = []
    for urn in candidates:
        entity = client.get_entity(urn)
        if entity is None:
            continue
        # An entity in our namespace that we did not mark is not ours to remove.
        if not entity.has_tag(FIXTURE_MARKER):
            skipped.append(urn)
            continue
        removable.append(urn)

    # Fails on an empty list, so "nothing to remove" can never become "remove all".
    assert_scoped_reset(removable, namespace)

    for urn in removable:
        _clear(client, urn, namespace)

    return ResetResult(removed=tuple(removable), skipped_unmarked=tuple(skipped))


def _upsert(
    client: DataHubClient,
    *,
    urn: str,
    entity_type: str,
    name: str,
    namespace: Namespace,
    properties: dict[str, str],
) -> None:
    """Create or update one marked entity."""
    add_entity = getattr(client, "add_entity", None)
    if add_entity is not None:
        add_entity(
            urn,
            entity_type=entity_type,
            name=name,
            tags=(FIXTURE_MARKER, namespace.project_tag),
            domain=namespace.domain,
            custom_properties=properties,
        )
        return

    # Live client: the tag write is the guarded operation.
    client.set_tags(urn, [FIXTURE_MARKER, namespace.project_tag])


def _clear(client: DataHubClient, urn: str, namespace: Namespace) -> None:
    """Remove this project's markers from one entity."""
    require_in_namespace(urn, namespace, operation="reset")
    remove_entity = getattr(client, "entities", None)
    if isinstance(remove_entity, dict):
        remove_entity.pop(urn, None)
        return
    client.set_tags(urn, [])


def verify_isolation(client: DataHubClient, namespace: Namespace, urns: list[str]) -> list[str]:
    """Return the subset of ``urns`` this project may act on.

    Used before any bulk operation to prove foreign entities were filtered out
    rather than merely not encountered.
    """
    safe: list[str] = []
    for urn in urns:
        try:
            require_in_namespace(urn, namespace, operation="isolation-check")
        except NamespaceViolation:
            continue
        safe.append(urn)
    return safe


def entity_is_ours(entity: EntityContext | None, namespace: Namespace) -> bool:
    """Whether an entity carries both this project's tag and the fixture marker."""
    if entity is None:
        return False
    return entity.has_tag(FIXTURE_MARKER) and entity.has_tag(namespace.project_tag)
