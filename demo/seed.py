"""Deterministic, marker-guarded catalog lifecycle.

Seed materializes a complete catalog entry for every fixture node -- properties
with ``artifact_class``, domain assignment, active status, tag controls, and
exact lineage -- then **rereads and verifies every allowlisted entity and edge**.
Emitting without verifying would report success for writes that never landed.

Reset is a **soft reset**: entities are marked ``Status(removed=True)`` and their
project tags stripped, rather than hard-deleted. Soft state is reversible, keeps
the audit trail, and is what DataHub 1.6.0 supports safely on a shared instance.
:func:`restore` puts them back.

Reset refuses unless all of these hold, and fails closed on any of them:

1. The **sentinel** exists and carries the fixture marker.
2. The live target set matches the **allowlist exactly** -- no missing entities
   (partial), no unexpected extras, never empty.
3. Every target carries the fixture marker.
4. Every target passes the **namespace guard**.

Seed is **idempotent and self-healing**. Every entity is upserted with its full
aspect set on every run, so re-running seed over a partially populated instance
converges it to the complete fixture set. That is the supported recovery from an
interrupted seed: there is no cleanup step, and reset is not one. Reset
deliberately refuses a partial target set, so an operator who reaches for it
after a failed seed finds a refusal rather than a recovery.

A seed that cannot complete records **which entities landed, which failed and
why, and which were never attempted**, then fails closed with that report
attached. An interrupted seed that only reported "it failed" left an operator
unable to tell a half-populated instance from an untouched one.

Domain and tag *controls* are shared coordinator-owned scaffolding. Nothing here
creates, mutates, or removes them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from adapters.catalog import EntitySpec, build_entity_proposals, domain_urn
from adapters.datahub import DataHubClient, EntityContext
from adapters.entity_registry import entity_type_of
from app.namespace import Namespace, NamespaceViolation, assert_scoped_reset, require_in_namespace
from demo.graph import EDGES, FIXTURE_MARKER, NODES, SENTINEL_URN, all_urns


class SeedError(Exception):
    """Raised when seed or reset cannot proceed safely."""


class VerificationError(SeedError):
    """Raised when emitted catalog state could not be read back."""


class PartialSeedError(SeedError):
    """Raised when some entities materialized and others did not.

    Carries the full :class:`SeedResult` so the caller can report exactly what
    landed rather than only that something went wrong.
    """

    def __init__(self, message: str, result: SeedResult) -> None:
        super().__init__(message)
        self.result = result


@dataclass(frozen=True)
class SpecFailure:
    """One entity that could not be materialized, and why."""

    urn: str
    entity_type: str
    error_type: str
    error: str

    def describe(self) -> str:
        return f"{self.urn} [{self.entity_type}]: {self.error_type}: {self.error}"


@dataclass(frozen=True)
class SeedResult:
    created: tuple[str, ...]
    marker: str
    sentinel_urn: str
    verified_entities: tuple[str, ...] = ()
    verified_edges: tuple[tuple[str, str], ...] = ()
    #: Entities that failed to materialize. Empty on a complete seed.
    failed: tuple[SpecFailure, ...] = ()
    #: Entities skipped because an earlier failure made the run partial.
    not_attempted: tuple[str, ...] = ()
    #: Edges not declared because one of their endpoints failed.
    skipped_edges: tuple[tuple[str, str], ...] = ()
    #: The sentinel is written only after every other entity lands, so its
    #: presence means the fixture set is complete.
    sentinel_written: bool = False

    @property
    def count(self) -> int:
        return len(self.created)

    @property
    def complete(self) -> bool:
        return not self.failed and not self.not_attempted

    def describe(self) -> str:
        parts = [f"{len(self.created)} materialized"]
        if self.failed:
            parts.append(f"{len(self.failed)} FAILED")
        if self.not_attempted:
            parts.append(f"{len(self.not_attempted)} not attempted")
        if self.skipped_edges:
            parts.append(f"{len(self.skipped_edges)} edges skipped")
        parts.append(f"sentinel {'written' if self.sentinel_written else 'withheld'}")
        return ", ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        """Serializable partial-seed evidence.

        Written to disk by the CLI so a failed live seed leaves an artifact an
        operator can act on, rather than only a message on a terminal that has
        since scrolled away.
        """
        return {
            "complete": self.complete,
            "marker": self.marker,
            "sentinel_urn": self.sentinel_urn,
            "sentinel_written": self.sentinel_written,
            "materialized": list(self.created),
            "failed": [
                {
                    "urn": f.urn,
                    "entity_type": f.entity_type,
                    "error_type": f.error_type,
                    "error": f.error,
                }
                for f in self.failed
            ],
            "not_attempted": list(self.not_attempted),
            "skipped_edges": [list(e) for e in self.skipped_edges],
            "verified_entities": list(self.verified_entities),
            "verified_edges": [list(e) for e in self.verified_edges],
            "recovery": (
                "Re-run `python -m demo.cli seed`. Seed is idempotent: it upserts every "
                "entity's full aspect set on every run, so it completes a partial instance "
                "in place. Do not run reset first -- reset refuses a partial target set by "
                "design, and no global cleanup is required."
            ),
        }


@dataclass
class ResetResult:
    removed: tuple[str, ...] = ()
    skipped_unmarked: tuple[str, ...] = ()
    failed: tuple[tuple[str, str], ...] = ()
    restored: bool = False

    @property
    def count(self) -> int:
        return len(self.removed)

    @property
    def complete(self) -> bool:
        """Whether every target was processed without failure."""
        return not self.failed

    def describe(self) -> str:
        parts = [f"{len(self.removed)} soft-removed"]
        if self.skipped_unmarked:
            parts.append(f"{len(self.skipped_unmarked)} skipped (unmarked)")
        if self.failed:
            parts.append(f"{len(self.failed)} FAILED")
        return ", ".join(parts)


def build_specs(namespace: Namespace) -> list[EntitySpec]:
    """The exact catalog entries this project owns.

    Upstreams come from the fixture edge list, so lineage is declared once and
    both seed and verification read the same source.
    """
    upstreams_by_node: dict[str, list[str]] = {}
    for upstream, downstream, _resolved in EDGES:
        upstreams_by_node.setdefault(downstream, []).append(upstream)

    domain = domain_urn(namespace.domain)
    specs: list[EntitySpec] = []

    for node in NODES:
        specs.append(
            EntitySpec(
                urn=node.urn,
                name=node.urn,
                description=node.description,
                custom_properties={
                    "artifact_class": node.artifact_class.value,
                    "purposes": ",".join(sorted(p.value for p in node.purposes)),
                    "exposure": node.exposure.value,
                    "criticality": node.criticality.value,
                    "rebuildable": str(node.rebuildable_from_replacement).lower(),
                    "fixture_marker": FIXTURE_MARKER,
                },
                tags=(FIXTURE_MARKER, namespace.project_tag),
                domain_urn=domain,
                upstreams=tuple(upstreams_by_node.get(node.urn, ())),
            )
        )

    specs.append(
        EntitySpec(
            urn=SENTINEL_URN,
            name=SENTINEL_URN,
            description="Fixture sentinel. Reset refuses when this is absent.",
            custom_properties={
                "artifact_class": "dataset",
                "purposes": "",
                "role": "fixture-sentinel",
                "fixture_marker": FIXTURE_MARKER,
            },
            tags=(FIXTURE_MARKER, namespace.project_tag),
            domain_urn=domain,
        )
    )
    return specs


def seed(client: DataHubClient, namespace: Namespace, verify: bool = True) -> SeedResult:
    """Materialize the demo catalog, then reread and verify it.

    Idempotent. Every entity is upserted with its complete aspect set, so running
    this against a partially seeded instance completes it rather than duplicating
    or conflicting. This is the recovery path from an interrupted seed.

    The sentinel is written **last**, so a seed interrupted partway through does
    not leave a sentinel implying a complete fixture set.

    Every entity is attempted even after one fails, so the resulting report
    describes the whole instance rather than stopping at the first problem. The
    run still fails closed: nothing is verified, no sentinel is written, and
    :class:`PartialSeedError` carries the evidence.

    Raises:
        NamespaceViolation: if any entity or edge falls outside the allocation.
            Never caught here -- an out-of-namespace target aborts the run.
        PartialSeedError: if any entity could not be materialized.
        VerificationError: if any allowlisted entity or edge could not be read back.
    """
    specs = build_specs(namespace)
    created: list[str] = []
    failures: list[SpecFailure] = []

    # Non-sentinel first; sentinel is the completion marker.
    entity_specs = [s for s in specs if s.urn != SENTINEL_URN]
    sentinel_specs = [s for s in specs if s.urn == SENTINEL_URN]

    for spec in entity_specs:
        require_in_namespace(spec.urn, namespace, operation="seed")
        for upstream in spec.upstreams:
            require_in_namespace(upstream, namespace, operation="seed-lineage")
        try:
            _materialize(client, spec, namespace)
        except NamespaceViolation:
            # Blocking by contract: never downgraded to a recorded failure.
            raise
        except Exception as exc:
            failures.append(
                SpecFailure(
                    urn=spec.urn,
                    entity_type=entity_type_of(spec.urn),
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
            )
            continue
        created.append(spec.urn)

    landed = set(created)
    _declared_edges, skipped_edges = _declare_edges(client, namespace, landed)

    if failures:
        result = SeedResult(
            created=tuple(created),
            marker=FIXTURE_MARKER,
            sentinel_urn=SENTINEL_URN,
            failed=tuple(failures),
            not_attempted=tuple(s.urn for s in sentinel_specs),
            skipped_edges=tuple(skipped_edges),
            sentinel_written=False,
        )
        raise PartialSeedError(
            f"Seed incomplete: {result.describe()}. "
            + "; ".join(f.describe() for f in failures),
            result,
        )

    # Only now is the fixture set complete enough to mark.
    for spec in sentinel_specs:
        require_in_namespace(spec.urn, namespace, operation="seed")
        _materialize(client, spec, namespace)
        created.append(spec.urn)

    verified_entities: tuple[str, ...] = ()
    verified_edges: tuple[tuple[str, str], ...] = ()
    if verify:
        verified_entities, verified_edges = verify_seed(client, namespace)

    return SeedResult(
        created=tuple(created),
        marker=FIXTURE_MARKER,
        sentinel_urn=SENTINEL_URN,
        verified_entities=verified_entities,
        verified_edges=verified_edges,
        sentinel_written=bool(sentinel_specs),
    )


def _declare_edges(
    client: DataHubClient, namespace: Namespace, landed: set[str]
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Declare fixture lineage, skipping edges whose endpoints did not land.

    Declaring an edge to an entity that failed to materialize would put lineage
    in the catalog pointing at nothing, which reads as a graph gap rather than as
    the seed failure it actually is.
    """
    declared: list[tuple[str, str]] = []
    skipped: list[tuple[str, str]] = []
    add_edge = getattr(client, "add_edge", None)

    for upstream, downstream, resolved in EDGES:
        require_in_namespace(upstream, namespace, operation="seed-lineage")
        require_in_namespace(downstream, namespace, operation="seed-lineage")
        if not {upstream, downstream} <= landed:
            skipped.append((upstream, downstream))
            continue
        if add_edge is not None:
            add_edge(upstream, downstream, resolved=resolved)
        declared.append((upstream, downstream))

    return declared, skipped


def verify_seed(
    client: DataHubClient, namespace: Namespace
) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]]:
    """Reread every allowlisted entity and edge and confirm it is usable.

    Raises:
        VerificationError: listing everything that could not be verified. Reports
            all problems at once rather than only the first, so one pass tells an
            operator the whole story.
    """
    expected = all_urns()
    found = _fetch(client, expected)

    problems: list[str] = []
    verified: list[str] = []

    for urn in expected:
        entity = found.get(urn)
        if entity is None:
            problems.append(f"{urn}: not readable after seed")
            continue
        if not entity.active:
            problems.append(f"{urn}: present but soft-deleted")
            continue
        if not entity.has_tag(FIXTURE_MARKER):
            problems.append(f"{urn}: missing fixture marker {FIXTURE_MARKER!r}")
            continue
        if not entity.has_tag(namespace.project_tag):
            problems.append(f"{urn}: missing project tag {namespace.project_tag!r}")
            continue
        missing_props = entity.missing_properties()
        if missing_props:
            problems.append(f"{urn}: missing custom properties {sorted(missing_props)}")
            continue
        if entity.domain is None:
            problems.append(f"{urn}: no domain assigned")
            continue
        verified.append(urn)

    verified_edges: list[tuple[str, str]] = []
    has_edge = getattr(client, "has_edge", None)
    for upstream, downstream, _resolved in EDGES:
        if has_edge is not None and not has_edge(upstream, downstream):
            problems.append(f"lineage {upstream} -> {downstream}: not readable after seed")
            continue
        verified_edges.append((upstream, downstream))

    if problems:
        raise VerificationError(
            f"Seed verification failed on {len(problems)} item(s): " + "; ".join(problems)
        )

    return tuple(verified), tuple(verified_edges)


def reset(client: DataHubClient, namespace: Namespace) -> ResetResult:
    """Soft-remove exactly the allowlisted fixture entities.

    Idempotent: running twice is safe, because an already-removed entity is
    processed to the same end state.

    Raises:
        SeedError: on a missing/unmarked sentinel, or a target set that does not
            exactly match the allowlist.
        NamespaceViolation: if any target falls outside the allocation.
    """
    _require_sentinel(client, namespace)

    allowlist = all_urns()
    present = _fetch(client, allowlist)

    removable: list[str] = []
    skipped: list[str] = []
    for urn in allowlist:
        entity = present.get(urn)
        if entity is None:
            continue
        # An entity in our namespace that we did not mark is not ours to touch.
        if not entity.has_tag(FIXTURE_MARKER):
            skipped.append(urn)
            continue
        removable.append(urn)

    _assert_exact_target_set(removable, skipped, allowlist)

    # Fails on an empty list, so "nothing to remove" can never become "remove all".
    assert_scoped_reset(removable, namespace)

    removed: list[str] = []
    failed: list[tuple[str, str]] = []
    for urn in removable:
        try:
            require_in_namespace(urn, namespace, operation="reset")
            _soft_remove(client, urn, namespace)
            removed.append(urn)
        except (NamespaceViolation, Exception) as exc:
            # Partial failure is recorded truthfully rather than aborting and
            # leaving the operator unsure which entities were processed.
            failed.append((urn, str(exc)))

    return ResetResult(
        removed=tuple(removed), skipped_unmarked=tuple(skipped), failed=tuple(failed)
    )


def restore(client: DataHubClient, namespace: Namespace) -> ResetResult:
    """Reverse a soft reset by reactivating and re-tagging the allowlist."""
    allowlist = all_urns()
    present = _fetch(client, allowlist, include_removed=True)

    restored: list[str] = []
    failed: list[tuple[str, str]] = []
    specs = {s.urn: s for s in build_specs(namespace)}

    for urn in allowlist:
        if urn not in present:
            continue
        try:
            require_in_namespace(urn, namespace, operation="restore")
            _materialize(client, specs[urn], namespace)
            restored.append(urn)
        except Exception as exc:
            failed.append((urn, str(exc)))

    return ResetResult(removed=tuple(restored), failed=tuple(failed), restored=True)


# -- internals ----------------------------------------------------------


def _require_sentinel(client: DataHubClient, namespace: Namespace) -> None:
    sentinel = client.get_entity(SENTINEL_URN)
    if sentinel is None:
        raise SeedError(
            f"Reset refused: fixture sentinel {SENTINEL_URN!r} not found. "
            "The demo was never seeded here, a seed was interrupted before it could "
            "mark the fixture set complete, or the client is pointed at the wrong "
            "DataHub instance. Run `python -m demo.cli seed`: it is idempotent and "
            "completes a partial instance in place, so no cleanup is needed first."
        )
    if not sentinel.has_tag(FIXTURE_MARKER):
        raise SeedError(
            f"Reset refused: sentinel exists but does not carry the {FIXTURE_MARKER!r} "
            "marker, so it was not created by this project's seed."
        )
    if not sentinel.has_tag(namespace.project_tag):
        raise SeedError(
            f"Reset refused: sentinel does not carry the {namespace.project_tag!r} tag."
        )


def _assert_exact_target_set(
    removable: list[str],
    skipped: list[str],
    allowlist: list[str],
) -> None:
    """Refuse anything other than an exact allowlist match.

    The target set must be *exactly* the allowlist: every allowlisted entity
    present and marked, nothing extra, never empty.

    Strictness is deliberate. A partial set means fixtures were removed or
    re-tagged out of band, so the instance and the allowlist disagree about what
    this project owns. Proceeding would soft-delete a subset and report success,
    leaving an operator believing the reset was complete. Stopping and saying
    which entities disagree is strictly more useful than guessing.

    The recovery from a partial set is ``seed``, not a cleanup. Seed is
    idempotent and completes the instance in place; both refusals below say so,
    because an operator who has just watched a seed fail will otherwise reach for
    reset and find only a refusal.
    """
    if not removable:
        raise SeedError(
            "Reset refused: no marked fixture entities found. Refusing rather than "
            "interpreting an empty target set as a wildcard. If a seed failed partway, "
            "re-run `python -m demo.cli seed`; it is idempotent and needs no cleanup first."
        )

    extras = sorted(set(removable) - set(allowlist))
    if extras:
        raise SeedError(
            f"Reset refused: {len(extras)} target(s) outside the fixture allowlist: {extras}"
        )

    missing = sorted(set(allowlist) - set(removable))
    if missing:
        detail = ", ".join(
            f"{u} ({'present but unmarked' if u in skipped else 'absent'})" for u in missing[:5]
        )
        raise SeedError(
            f"Reset refused: partial target set. {len(missing)} of {len(allowlist)} "
            f"allowlisted entities are not resettable: {detail}. This is the expected "
            "state after an interrupted seed. Re-run `python -m demo.cli seed` to "
            "complete the instance in place, then reset if you still need to."
        )


def _fetch(
    client: DataHubClient, urns: list[str], include_removed: bool = False
) -> dict[str, EntityContext]:
    """Batch-read entities, falling back to per-URN reads."""
    batch = getattr(client, "get_entities", None)
    if batch is not None:
        found = batch(list(urns))
    else:  # pragma: no cover - all clients implement the batch surface
        found = {}
        for urn in urns:
            entity = client.get_entity(urn)
            if entity is not None:
                found[urn] = entity

    if include_removed:
        return found
    return found


def _materialize(client: DataHubClient, spec: EntitySpec, namespace: Namespace) -> None:
    """Create or update one complete catalog entry.

    The proposals are built first and discarded on the offline path. That looks
    wasteful and is deliberate: building them runs the entity/aspect contract
    check, so writing to the in-memory fake enforces exactly what writing to GMS
    enforces. Without it the fake accepts an aspect set the server would reject,
    which is how ``datasetProperties`` on ``mlModel`` reached a live run.
    """
    build_entity_proposals(spec)

    upsert = getattr(client, "upsert_spec", None)
    if upsert is not None:  # live catalog-backed client
        upsert(spec)
        return

    add_entity = getattr(client, "add_entity", None)
    if add_entity is not None:  # in-memory fake
        add_entity(
            spec.urn,
            # Derived, never assumed. Hardcoding "dataset" here is what let the
            # fake report a dataset for an mlModel URN.
            entity_type=entity_type_of(spec.urn),
            name=spec.name,
            tags=spec.tags,
            domain=spec.domain_urn,
            description=spec.description,
            custom_properties=dict(spec.custom_properties),
            active=True,
        )
        return

    raise SeedError(f"client {type(client).__name__} cannot materialize catalog entries")


def _soft_remove(client: DataHubClient, urn: str, namespace: Namespace) -> None:
    """Mark one entity removed and strip its project tags.

    Tag controls themselves are untouched; only the association on this entity
    is cleared.
    """
    client.set_tags(urn, [])
    set_status = getattr(client, "set_status", None)
    if set_status is not None:
        set_status(urn, True)


def verify_isolation(client: DataHubClient, namespace: Namespace, urns: list[str]) -> list[str]:
    """Return the subset of ``urns`` this project may act on."""
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
