"""Deterministic in-memory DataHub substitute.

Used by tests and by ``APP_ENV=offline`` so the vertical slice can be exercised
without the shared instance. It implements the same
:class:`~adapters.datahub.DataHubClient` surface and enforces the same namespace
guard on writes, so a test that passes here is testing the real contract rather
than a permissive mock.

It also models the parts of a real catalog entry that the live path must
maintain -- custom properties, domain, active status, tags, and lineage -- so
readiness and seed verification are exercised offline rather than only in
production.

This is explicitly **not** a source of live evidence. Receipts produced against it
are marked ``simulated`` and must never be presented as proof of DataHub writeback.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from adapters.datahub import (
    REQUIRED_MCP_TOOLS,
    DataHubError,
    EntityContext,
    LineageEdge,
)
from app.namespace import Namespace, require_in_namespace


@dataclass
class FakeDataHubClient:
    """In-memory graph with the same guards as the live client."""

    namespace: Namespace
    entities: dict[str, EntityContext] = field(default_factory=dict)
    lineage: dict[str, list[LineageEdge]] = field(default_factory=dict)
    tools: frozenset[str] = REQUIRED_MCP_TOOLS

    #: Set to raise on the next call, to exercise failure paths.
    fail_next_write: bool = False
    fail_next_read: bool = False
    #: URNs whose creation raises, modelling a server that rejects some entities
    #: and accepts others -- the shape of a seed that stops partway through.
    fail_on_create: frozenset[str] = frozenset()
    #: When True, restoration silently does nothing -- models a partial failure
    #: that must surface as residual state rather than a clean receipt.
    swallow_restore: bool = False
    #: When True, the re-read that verifies a write raises. The write has already
    #: landed, so restoration must still run.
    fail_verify_read: bool = False

    write_log: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)
    #: Records batch sizes so tests can prove get_entities is actually batched.
    batch_log: list[int] = field(default_factory=list)

    _verify_reads: int = 0

    def list_mcp_tools(self) -> frozenset[str]:
        return self.tools

    def get_entities(self, urns: list[str]) -> dict[str, EntityContext]:
        if self.fail_next_read:
            self.fail_next_read = False
            raise DataHubError("simulated read failure")
        self.batch_log.append(len(urns))
        return {u: self.entities[u] for u in urns if u in self.entities}

    def get_entity(self, urn: str) -> EntityContext | None:
        if self.fail_next_read:
            self.fail_next_read = False
            raise DataHubError("simulated read failure")
        return self.entities.get(urn)

    def get_downstream_lineage(self, urn: str, max_depth: int = 5) -> list[LineageEdge]:
        """Breadth-first downstream walk, depth-limited and cycle-safe."""
        seen: set[str] = {urn}
        edges: list[LineageEdge] = []
        frontier = [urn]
        depth = 0

        while frontier and depth < max_depth:
            next_frontier: list[str] = []
            for node in frontier:
                for edge in self.lineage.get(node, []):
                    edges.append(edge)
                    if edge.downstream_urn not in seen:
                        seen.add(edge.downstream_urn)
                        next_frontier.append(edge.downstream_urn)
            frontier = next_frontier
            depth += 1

        return edges

    def get_tags(self, urn: str) -> list[str]:
        # The armed counter alone decides; `fail_verify_read` is cleared when it
        # arms so the restoring read can still succeed.
        if self._verify_reads > 0:
            self._verify_reads -= 1
            raise DataHubError("simulated verification re-read failure")
        entity = self.entities.get(urn)
        return list(entity.tags) if entity else []

    def set_tags(self, urn: str, tags: list[str]) -> None:
        # Same guard as the live client: the fake must not be more permissive,
        # or isolation tests would pass here and fail in production.
        require_in_namespace(urn, self.namespace, operation="set_tags")

        if self.fail_next_write:
            self.fail_next_write = False
            raise DataHubError("simulated write failure")

        entity = self.entities.get(urn)
        if entity is None:
            raise DataHubError(f"cannot tag unknown entity {urn!r}")

        if self.swallow_restore and len(tags) < len(entity.tags):
            # Model the write landing but the rollback quietly not applying.
            self.write_log.append((urn, tuple(tags)))
            return

        self.entities[urn] = _replace_tags(entity, tuple(tags))
        self.write_log.append((urn, tuple(tags)))
        if self.fail_verify_read:
            # Arm exactly one failing read, then disarm. The restoring write must
            # be able to confirm itself, or the test would be asserting a double
            # failure rather than "verification failed but rollback still ran".
            self.fail_verify_read = False
            self._verify_reads = 1

    def set_properties(self, urn: str, properties: dict[str, str]) -> None:
        """Merge custom properties, guarded exactly like a tag write."""
        require_in_namespace(urn, self.namespace, operation="set_properties")

        if self.fail_next_write:
            self.fail_next_write = False
            raise DataHubError("simulated write failure")

        entity = self.entities.get(urn)
        if entity is None:
            raise DataHubError(f"cannot set properties on unknown entity {urn!r}")

        merged = {**entity.custom_properties, **{str(k): str(v) for k, v in properties.items()}}
        self.entities[urn] = _replace(entity, custom_properties=merged)
        self.write_log.append((urn, tuple(sorted(properties))))

    def set_status(self, urn: str, removed: bool) -> None:
        """Soft delete or restore, guarded exactly like a tag write."""
        require_in_namespace(urn, self.namespace, operation="set_status")
        entity = self.entities.get(urn)
        if entity is None:
            raise DataHubError(f"cannot change status of unknown entity {urn!r}")
        self.entities[urn] = _replace(entity, active=not removed)

    # -- test / seed helpers --------------------------------------------

    def add_entity(
        self,
        urn: str,
        *,
        entity_type: str = "dataset",
        name: str = "",
        tags: tuple[str, ...] = (),
        domain: str | None = None,
        description: str | None = None,
        custom_properties: dict[str, str] | None = None,
        active: bool = True,
    ) -> EntityContext:
        if urn in self.fail_on_create:
            raise DataHubError(f"simulated rejection creating {urn!r}")

        entity = EntityContext(
            urn=urn,
            entity_type=entity_type,
            name=name or urn,
            tags=tags,
            domain=domain,
            description=description,
            custom_properties=custom_properties or {},
            active=active,
        )
        self.entities[urn] = entity
        return entity

    def add_edge(self, upstream: str, downstream: str, *, resolved: bool = True) -> None:
        existing = self.lineage.setdefault(upstream, [])
        if any(e.downstream_urn == downstream for e in existing):
            return  # idempotent
        existing.append(
            LineageEdge(upstream_urn=upstream, downstream_urn=downstream, resolved=resolved)
        )

    def has_edge(self, upstream: str, downstream: str) -> bool:
        return any(e.downstream_urn == downstream for e in self.lineage.get(upstream, []))


def _replace_tags(entity: EntityContext, tags: tuple[str, ...]) -> EntityContext:
    return _replace(entity, tags=tags)


def _replace(entity: EntityContext, **changes) -> EntityContext:
    values = {
        "urn": entity.urn,
        "entity_type": entity.entity_type,
        "name": entity.name,
        "tags": entity.tags,
        "domain": entity.domain,
        "owners": entity.owners,
        "description": entity.description,
        "custom_properties": dict(entity.custom_properties),
        "active": entity.active,
    }
    values.update(changes)
    return EntityContext(**values)
