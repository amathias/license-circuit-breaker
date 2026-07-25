"""License and lineage context validation.

Turns raw DataHub context into the typed :class:`~app.rights.Descendant` objects
the policy evaluator consumes, and refuses to fabricate anything it cannot read.

The important behavior is what happens on *bad* input. An unresolvable lineage
edge, a missing entity, or absent purpose metadata all produce a descendant that
the policy table will escalate -- never one that quietly looks contained. That is
the difference between a governance tool and a false all-clear.
"""

from __future__ import annotations

from dataclasses import dataclass

from adapters.datahub import DataHubClient, EntityContext, LineageEdge
from app.namespace import Namespace, is_in_namespace
from app.rights import (
    ArtifactClass,
    Criticality,
    Descendant,
    Exposure,
    LineagePath,
    Purpose,
)


@dataclass(frozen=True)
class ContextValidation:
    """Outcome of validating one entity's governance context."""

    urn: str
    present: bool
    in_namespace: bool
    has_project_tag: bool
    in_project_domain: bool
    issues: tuple[str, ...] = ()

    @property
    def usable(self) -> bool:
        """Whether this entity may be acted on at all."""
        return self.present and self.in_namespace and self.has_project_tag


def validate_entity(
    entity: EntityContext | None, urn: str, namespace: Namespace
) -> ContextValidation:
    """Check one entity against the project's allocation."""
    issues: list[str] = []

    present = entity is not None
    if not present:
        issues.append("entity not found in DataHub")

    in_ns = is_in_namespace(urn, namespace)
    if not in_ns:
        issues.append(f"URN is outside the {namespace.urn_prefix!r} namespace")

    has_tag = bool(entity and entity.has_tag(namespace.project_tag))
    if present and not has_tag:
        issues.append(f"entity does not carry the {namespace.project_tag!r} tag")

    in_domain = bool(entity and entity.domain == namespace.domain)
    if present and not in_domain:
        # Not fatal on its own -- domain assignment can lag ingestion -- but it is
        # recorded so an operator can see the allocation is not fully applied.
        issues.append(f"entity is not in the {namespace.domain!r} domain")

    return ContextValidation(
        urn=urn,
        present=present,
        in_namespace=in_ns,
        has_project_tag=has_tag,
        in_project_domain=in_domain,
        issues=tuple(issues),
    )


def _parse_purposes(entity: EntityContext | None) -> frozenset[Purpose]:
    """Read declared purposes from custom properties.

    Returns an empty set when unreadable. The caller records that as missing
    evidence rather than assuming either full or zero usage.
    """
    if entity is None:
        return frozenset()
    raw = entity.custom_properties.get("purposes", "")
    parsed: set[Purpose] = set()
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            parsed.add(Purpose(token))
        except ValueError:
            continue
    return frozenset(parsed)


def _parse_enum(entity: EntityContext | None, key: str, enum_cls, default):
    if entity is None:
        return default
    try:
        return enum_cls(entity.custom_properties.get(key, ""))
    except ValueError:
        return default


def build_paths(
    source_urn: str, target_urn: str, edges: list[LineageEdge]
) -> tuple[LineagePath, ...]:
    """Reconstruct concrete lineage paths from source to target.

    A path containing an unresolved edge is marked incomplete, which is what makes
    LCB-R001 fire. Depth is capped to keep a cyclic graph from looping forever.
    """
    adjacency: dict[str, list[LineageEdge]] = {}
    for edge in edges:
        adjacency.setdefault(edge.upstream_urn, []).append(edge)

    paths: list[LineagePath] = []
    max_depth = 12

    def walk(node: str, trail: tuple[str, ...], complete: bool, depth: int) -> None:
        if depth > max_depth:
            return
        if node == target_urn and len(trail) >= 2:
            paths.append(LineagePath(hops=trail, complete=complete))
            return
        for edge in adjacency.get(node, []):
            if edge.downstream_urn in trail:
                continue  # cycle
            walk(
                edge.downstream_urn,
                (*trail, edge.downstream_urn),
                complete and edge.resolved,
                depth + 1,
            )

    walk(source_urn, (source_urn,), True, 0)
    return tuple(paths)


def discover_descendants(
    client: DataHubClient,
    source_urn: str,
    namespace: Namespace,
    max_depth: int = 6,
    lost_purposes: frozenset[Purpose] | None = None,
) -> tuple[list[Descendant], list[ContextValidation]]:
    """Traverse downstream lineage and build typed descendants.

    Returns the descendants alongside the validation record for each, so the UI and
    the evidence bundle can show *why* something was included or skipped.

    Foreign-namespace entities are excluded from the returned descendants but still
    reported in the validations -- silently dropping them would hide a real
    cross-project lineage link from the operator.

    When ``lost_purposes`` is supplied, each descendant is additionally marked with
    ``contaminated_upstream`` if any ancestor on a path to it uses a revoked
    purpose. See :func:`app.policy.is_affected` for why derived content must stay
    in scope even when the artifact's own declared purpose was never revoked.
    """
    edges = client.get_downstream_lineage(source_urn, max_depth=max_depth)

    targets: list[str] = []
    for edge in edges:
        if edge.downstream_urn not in targets and edge.downstream_urn != source_urn:
            targets.append(edge.downstream_urn)

    # Purposes for every node, so contamination can be evaluated along a path.
    purposes_by_urn: dict[str, frozenset[Purpose]] = {}
    for urn in targets:
        purposes_by_urn[urn] = _parse_purposes(client.get_entity(urn))

    descendants: list[Descendant] = []
    validations: list[ContextValidation] = []

    for urn in targets:
        entity = client.get_entity(urn)
        validation = validate_entity(entity, urn, namespace)
        validations.append(validation)

        if not validation.in_namespace:
            continue

        paths = build_paths(source_urn, urn, edges)
        purposes = purposes_by_urn.get(urn, frozenset())

        artifact_class = _parse_enum(entity, "artifact_class", ArtifactClass, ArtifactClass.UNKNOWN)
        exposure = _parse_enum(entity, "exposure", Exposure, Exposure.INTERNAL)
        criticality = _parse_enum(entity, "criticality", Criticality, Criticality.MEDIUM)
        rebuildable = bool(entity) and entity.custom_properties.get("rebuildable") == "true"

        descendants.append(
            Descendant(
                urn=urn,
                artifact_class=artifact_class,
                exposure=exposure,
                criticality=criticality,
                owners=tuple(entity.owners) if entity else (),
                paths=paths,
                current_purposes=purposes,
                rebuildable_from_replacement=rebuildable,
                contaminated_upstream=_is_contaminated(
                    urn, paths, purposes_by_urn, lost_purposes
                ),
            )
        )

    descendants.sort(key=lambda d: d.urn)
    return descendants, validations


def _is_contaminated(
    urn: str,
    paths: tuple[LineagePath, ...],
    purposes_by_urn: dict[str, frozenset[Purpose]],
    lost_purposes: frozenset[Purpose] | None,
) -> bool:
    """Whether any ancestor on a path to ``urn`` uses a revoked purpose.

    The source itself is excluded. Treating the revoked source as contaminating
    would mark every descendant in scope and destroy the precision that makes the
    unaffected branch meaningful.
    """
    if not lost_purposes:
        return False

    for path in paths:
        # hops[0] is the source; hops[-1] is this artifact. Only intermediates count.
        for hop in path.hops[1:-1]:
            if hop == urn:
                continue
            if purposes_by_urn.get(hop, frozenset()) & lost_purposes:
                return True
    return False
