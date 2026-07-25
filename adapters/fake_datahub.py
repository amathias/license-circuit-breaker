"""Deterministic in-memory DataHub substitute.

Used by tests and by ``APP_ENV=offline`` so the vertical slice can be exercised
without the shared instance. It implements the same :class:`~adapters.datahub.DataHubClient`
surface and enforces the same namespace guard on writes, so a test that passes here
is testing the real contract rather than a permissive mock.

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
    #: When True, restoration silently does nothing -- models a partial failure
    #: that must surface as residual state rather than a clean receipt.
    swallow_restore: bool = False

    write_log: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)

    def list_mcp_tools(self) -> frozenset[str]:
        return self.tools

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

        self.entities[urn] = EntityContext(
            urn=entity.urn,
            entity_type=entity.entity_type,
            name=entity.name,
            tags=tuple(tags),
            domain=entity.domain,
            owners=entity.owners,
            description=entity.description,
            custom_properties=dict(entity.custom_properties),
        )
        self.write_log.append((urn, tuple(tags)))

    # -- test helpers ---------------------------------------------------

    def add_entity(
        self,
        urn: str,
        *,
        entity_type: str = "dataset",
        name: str = "",
        tags: tuple[str, ...] = (),
        domain: str | None = None,
        custom_properties: dict[str, str] | None = None,
    ) -> EntityContext:
        entity = EntityContext(
            urn=urn,
            entity_type=entity_type,
            name=name or urn,
            tags=tags,
            domain=domain,
            custom_properties=custom_properties or {},
        )
        self.entities[urn] = entity
        return entity

    def add_edge(self, upstream: str, downstream: str, *, resolved: bool = True) -> None:
        self.lineage.setdefault(upstream, []).append(
            LineageEdge(upstream_urn=upstream, downstream_urn=downstream, resolved=resolved)
        )
