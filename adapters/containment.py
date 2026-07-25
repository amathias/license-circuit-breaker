"""Containment adapters: the part that actually does something.

Each adapter performs one family of typed actions against a real local artifact
and returns a machine-readable receipt. Nothing here is simulated -- freezing
writes the serving control plane, purging deletes vectors, quarantine moves the
file, retraining fits a new classifier from approved data.

Three invariants hold across every adapter:

**Guarded.** The target URN passes the namespace guard and every filesystem
write passes :func:`~app.namespace.require_path_within` against the estate root
before anything is touched. An adapter that cannot prove its target is ours does
not run.

**Idempotent.** Re-running a completed action converges rather than compounding,
and the receipt reports ``changed=False``. This is what makes resume safe: a run
that died halfway can simply be run again.

**Honest.** An action that cannot be performed raises rather than returning a
success receipt. A missing adapter, an unresolvable artifact, or a failed write
becomes residual exposure in the report -- never a quiet all-clear.

Enterprise connectors -- a real feature store, model registry, or warehouse --
are explicitly out of scope and are not stubbed out to look implemented. The
registry refuses URNs it has no adapter for, which is how the report learns that
an artifact was *not* contained.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from app.namespace import Namespace, require_in_namespace, require_path_within
from app.rights import Action, ArtifactClass
from demo.estate import (
    BLOCKED,
    DUCKDB_TABLE,
    EXPORT,
    MODEL,
    SERVICE,
    VECTOR_INDEX,
    ArtifactRecord,
    EstateError,
    EstatePaths,
    ServingControl,
    activate_version,
    active_version,
    build_index,
    export_path,
    index_manifest,
    load_index,
    model_versions,
    purge_table,
    quarantined_export_path,
    rebuild_derived_from,
    resolve_artifact,
    table_row_ids,
    train_model,
    training_manifest,
)

#: The version a retrain produces. Fixed rather than incrementing, so a resumed
#: or repeated run converges on the same artifact instead of accumulating
#: v2, v3, v4... for one approved plan.
RETRAINED_VERSION = "v2-approved"

#: Warehouse table the approved retrain reads from.
APPROVED_FEED_TABLE = "approved_feed"


class ContainmentError(Exception):
    """Raised when an adapter cannot complete its action."""


class NoAdapterError(ContainmentError):
    """Raised when no adapter supports a (URN, action) pair.

    Deliberately its own type: the executor records this as *unhandled residual
    exposure* rather than as an adapter failure, because the two mean different
    things to an operator. One is "we tried and it broke"; the other is "this
    product cannot contain that artifact class at all."
    """


@dataclass(frozen=True)
class AdapterReceipt:
    """Machine-readable evidence of one containment attempt."""

    urn: str
    action: Action
    adapter: str
    attempted_at: datetime
    succeeded: bool
    #: False when the artifact was already in the target state. An idempotent
    #: re-run must be distinguishable from a first application, or "we froze it"
    #: becomes unfalsifiable.
    changed: bool
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    #: Always False. These adapters act on real local artifacts. The flag exists
    #: so a receipt is self-describing when read outside this codebase.
    simulated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "urn": self.urn,
            "action": self.action.value,
            "adapter": self.adapter,
            "attempted_at": self.attempted_at.isoformat(),
            "succeeded": self.succeeded,
            "changed": self.changed,
            "detail": self.detail,
            "evidence": self.evidence,
            "error": self.error,
            "simulated": self.simulated,
        }


@dataclass
class AdapterContext:
    """Everything an adapter is allowed to touch.

    ``fault_injector`` exists so the deliberate demo failure is a first-class,
    tested code path rather than something staged by editing files between takes.
    It is called before the adapter acts and may raise to fail that step.
    """

    paths: EstatePaths
    namespace: Namespace
    replacement_source_urn: str | None = None
    actor: str = "unknown"
    fault_injector: Callable[[str, str, Action], None] | None = None

    def guard(self, urn: str, operation: str) -> ArtifactRecord:
        """Namespace-check the URN and resolve its local artifact."""
        require_in_namespace(urn, self.namespace, operation=operation)
        return resolve_artifact(urn)

    def guard_path(self, path: Path, operation: str) -> Path:
        """Refuse any filesystem target outside the estate root."""
        return require_path_within(path, self.paths.root, operation=operation)

    def maybe_fail(self, adapter: str, urn: str, action: Action) -> None:
        if self.fault_injector is not None:
            self.fault_injector(adapter, urn, action)


class ContainmentAdapter(Protocol):
    """The surface the registry and the executor depend on."""

    name: str

    def supports(self, record: ArtifactRecord, action: Action) -> bool: ...

    def apply(self, context: AdapterContext, urn: str, action: Action) -> AdapterReceipt: ...


def _receipt(
    adapter: str,
    urn: str,
    action: Action,
    *,
    changed: bool,
    detail: str,
    evidence: dict[str, Any] | None = None,
) -> AdapterReceipt:
    return AdapterReceipt(
        urn=urn,
        action=action,
        adapter=adapter,
        attempted_at=datetime.now(UTC),
        succeeded=True,
        changed=changed,
        detail=detail,
        evidence=evidence or {},
    )


# --- 1. API freeze -----------------------------------------------------


class ApiFreezeAdapter:
    """Stops a live endpoint serving revoked-derived content.

    The service keeps running and keeps answering; it answers with a refusal.
    That distinction matters for verification: a probe against a stopped process
    cannot tell containment from an outage, but a probe that receives an explicit
    "blocked by an approved containment action" can.
    """

    name = "api-freeze"

    def supports(self, record: ArtifactRecord, action: Action) -> bool:
        return record.kind == SERVICE and action is Action.FREEZE

    def apply(self, context: AdapterContext, urn: str, action: Action) -> AdapterReceipt:
        context.guard(urn, operation="freeze")
        context.maybe_fail(self.name, urn, action)

        control = ServingControl.load(context.guard_path(context.paths.serving_path, "freeze"))
        reason = f"frozen by approved containment action, actor={context.actor}"
        changed = control.set_state(urn, BLOCKED, reason=reason)

        return _receipt(
            self.name,
            urn,
            action,
            changed=changed,
            detail=(
                "serving state set to blocked"
                if changed
                else "already blocked; no change made"
            ),
            evidence={
                "serving_state": control.state(urn),
                "control_plane": str(context.paths.serving_path),
            },
        )


# --- 2. Vector index purge and rebuild ---------------------------------


class VectorIndexAdapter:
    """Purges revoked vectors and rebuilds the index from approved content.

    Purge deletes the vectors outright and leaves a manifest recording that it
    happened, so a purged index is distinguishable from one that was never
    built. Rebuild regenerates from whatever the warehouse now holds -- which,
    after the dataset rebuild that the executor sequences first, is approved
    data.
    """

    name = "vector-index"

    def supports(self, record: ArtifactRecord, action: Action) -> bool:
        return record.kind == VECTOR_INDEX and action in (Action.PURGE, Action.REBUILD)

    def apply(self, context: AdapterContext, urn: str, action: Action) -> AdapterReceipt:
        context.guard(urn, operation=action.value)
        context.maybe_fail(self.name, urn, action)

        if action is Action.PURGE:
            return self._purge(context, urn)
        return self._rebuild(context, urn)

    def _purge(self, context: AdapterContext, urn: str) -> AdapterReceipt:
        paths = context.paths
        vectors = context.guard_path(paths.index_vectors, "purge-index")
        manifest_path = context.guard_path(paths.index_manifest, "purge-index")

        before = index_manifest(paths)
        purged_ids = list(before.get("row_ids", ()))
        changed = vectors.exists()
        if changed:
            vectors.unlink()

        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(
                {
                    "purged": True,
                    "purged_at": datetime.now(UTC).isoformat(),
                    "purged_row_count": len(purged_ids),
                    "source_urns": [],
                    "row_ids": [],
                    "vector_count": 0,
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )

        _model, entries = load_index(paths)
        if entries:  # pragma: no cover - defensive
            raise ContainmentError(f"purge of {urn} left {len(entries)} vectors behind")

        return _receipt(
            self.name,
            urn,
            Action.PURGE,
            changed=changed,
            detail=(
                f"purged {len(purged_ids)} vectors" if changed else "index already purged"
            ),
            evidence={"purged_row_count": len(purged_ids), "vectors_remaining": 0},
        )

    def _rebuild(self, context: AdapterContext, urn: str) -> AdapterReceipt:
        paths = context.paths
        context.guard_path(paths.index_vectors, "rebuild-index")
        source = context.replacement_source_urn
        if source is None:
            raise ContainmentError(
                f"cannot rebuild {urn}: the rights event names no approved replacement source"
            )
        require_in_namespace(source, context.namespace, operation="rebuild-index-source")

        before = index_manifest(paths).get("content_hash")
        count = build_index(paths, source_urn=source, source_feed="approved")
        after = index_manifest(paths)

        return _receipt(
            self.name,
            urn,
            Action.REBUILD,
            changed=after.get("content_hash") != before,
            detail=f"rebuilt {count} vectors from {source}",
            evidence={
                "vector_count": count,
                "source_urns": after.get("source_urns", []),
                "row_ids": after.get("row_ids", []),
                "content_hash": after.get("content_hash"),
            },
        )


# --- 3. Export quarantine ----------------------------------------------


class ExportQuarantineAdapter:
    """Moves a published export out of reach and records why.

    The file is moved, not deleted: quarantine has to be reversible for the demo
    to reset, and destroying the only copy of an extract is not what a governance
    team wants from a containment tool. The published path genuinely stops
    resolving, which is what the access probe checks.
    """

    name = "export-quarantine"

    def supports(self, record: ArtifactRecord, action: Action) -> bool:
        return record.kind == EXPORT and action is Action.QUARANTINE

    def apply(self, context: AdapterContext, urn: str, action: Action) -> AdapterReceipt:
        context.guard(urn, operation="quarantine")
        context.maybe_fail(self.name, urn, action)

        paths = context.paths
        published = context.guard_path(export_path(paths), "quarantine-source")
        target = context.guard_path(quarantined_export_path(paths), "quarantine-target")
        target.parent.mkdir(parents=True, exist_ok=True)

        if not published.exists():
            if not target.exists():
                raise ContainmentError(
                    f"cannot quarantine {urn}: no export exists at {published} and nothing "
                    "is already quarantined"
                )
            return _receipt(
                self.name,
                urn,
                action,
                changed=False,
                detail="export was already quarantined",
                evidence={
                    "published_path_exists": False,
                    "quarantine_path": str(target),
                },
            )

        shutil.move(str(published), str(target))
        (target.parent / "QUARANTINE.json").write_text(
            json.dumps(
                {
                    "urn": urn,
                    "quarantined_at": datetime.now(UTC).isoformat(),
                    "actor": context.actor,
                    "reason": "approved containment action for a revoked upstream right",
                    "original_path": str(published),
                    "note": (
                        "Quarantine covers this tracked export only. Copies distributed "
                        "outside the demonstrated DataHub graph are not addressed."
                    ),
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )

        return _receipt(
            self.name,
            urn,
            action,
            changed=True,
            detail=f"moved the published export to {target.name} under quarantine",
            evidence={
                "published_path_exists": published.exists(),
                "quarantine_path": str(target),
            },
        )


# --- 4. Model retrain and replace --------------------------------------


class ModelAdapter:
    """Retrains a classifier from approved data and swaps what is served.

    Retrain and replace are separate actions with separate receipts. A retrain
    produces a new version but changes nothing about what answers requests; only
    replace flips the active pointer. Collapsing them would enforce a swap the
    approver never approved.

    Containment here stops the revoked-derived model *serving*. It is not, and is
    never described as, proof that a model has unlearned its training data.
    """

    name = "model-lifecycle"

    def supports(self, record: ArtifactRecord, action: Action) -> bool:
        return record.kind == MODEL and action in (Action.RETRAIN, Action.REPLACE)

    def apply(self, context: AdapterContext, urn: str, action: Action) -> AdapterReceipt:
        record = context.guard(urn, operation=action.value)
        context.maybe_fail(self.name, urn, action)

        if action is Action.RETRAIN:
            return self._retrain(context, urn, record.location)
        return self._replace(context, urn, record.location)

    def _retrain(self, context: AdapterContext, urn: str, name: str) -> AdapterReceipt:
        paths = context.paths
        source = context.replacement_source_urn
        if source is None:
            raise ContainmentError(
                f"cannot retrain {urn}: the rights event names no approved replacement source"
            )
        require_in_namespace(source, context.namespace, operation="retrain-source")
        context.guard_path(paths.model_root(name), "retrain")

        existing = training_manifest(paths, name, RETRAINED_VERSION)
        manifest = train_model(
            paths,
            name=name,
            source_urn=source,
            source_table=APPROVED_FEED_TABLE,
            version=RETRAINED_VERSION,
            # Retraining must not change what is served. That is `replace`.
            activate=False,
        )

        return _receipt(
            self.name,
            urn,
            Action.RETRAIN,
            changed=existing.get("content_hash") != manifest["content_hash"],
            detail=(
                f"trained {name}/{RETRAINED_VERSION} on {manifest['row_count']} approved rows; "
                f"the served version is still {active_version(paths, name)}"
            ),
            evidence={
                "version": RETRAINED_VERSION,
                "training_sources": manifest["training_sources"],
                "row_ids": manifest["row_ids"],
                "training_accuracy": manifest["training_accuracy"],
                "content_hash": manifest["content_hash"],
                "active_version": active_version(paths, name),
                "limitation": (
                    "Stops the revoked-derived model serving. Not proof of model unlearning."
                ),
            },
        )

    def _replace(self, context: AdapterContext, urn: str, name: str) -> AdapterReceipt:
        paths = context.paths
        context.guard_path(paths.model_root(name), "replace")

        if RETRAINED_VERSION not in model_versions(paths, name):
            raise ContainmentError(
                f"cannot replace {urn}: version {RETRAINED_VERSION!r} has not been trained. "
                "Retrain must succeed before the served model is swapped."
            )

        changed = activate_version(paths, name, RETRAINED_VERSION)
        manifest = training_manifest(paths, name)

        return _receipt(
            self.name,
            urn,
            Action.REPLACE,
            changed=changed,
            detail=(
                f"serving {name}/{RETRAINED_VERSION}"
                if changed
                else f"{name} already serving {RETRAINED_VERSION}"
            ),
            evidence={
                "active_version": active_version(paths, name),
                "training_sources": manifest.get("training_sources", []),
                "row_ids": manifest.get("row_ids", []),
            },
        )


# --- 5. Warehouse dataset rebuild and purge ----------------------------


class WarehouseAdapter:
    """Rebuilds derived warehouse tables from an approved feed, or purges them.

    Rebuild regenerates rows rather than filtering them, so the result contains
    no partner identifiers by construction rather than by a delete that might
    have missed some.
    """

    name = "warehouse"

    #: Only these tables are derived. Rebuilding a *source* feed would be
    #: meaningless, and purging one would destroy the input a rebuild needs.
    DERIVED_TABLES = frozenset({"normalized", "review_sentiment"})

    def supports(self, record: ArtifactRecord, action: Action) -> bool:
        return record.kind == DUCKDB_TABLE and action in (Action.REBUILD, Action.PURGE)

    def apply(self, context: AdapterContext, urn: str, action: Action) -> AdapterReceipt:
        record = context.guard(urn, operation=action.value)
        context.maybe_fail(self.name, urn, action)
        table = record.location

        if table not in self.DERIVED_TABLES:
            raise ContainmentError(
                f"cannot {action.value} {urn}: {table!r} is not a derived table. "
                "Rebuilding or purging a source feed would destroy the input a "
                "rebuild depends on."
            )

        context.guard_path(context.paths.warehouse, action.value)

        if action is Action.PURGE:
            before = table_row_ids(context.paths, table)
            removed = purge_table(context.paths, table)
            return _receipt(
                self.name,
                urn,
                action,
                changed=removed > 0,
                detail=f"purged {removed} rows from {table}",
                evidence={
                    "table": table,
                    "rows_removed": removed,
                    "rows_remaining": len(table_row_ids(context.paths, table)),
                    "purged_row_ids": before,
                },
            )

        source = context.replacement_source_urn
        if source is None:
            raise ContainmentError(
                f"cannot rebuild {urn}: the rights event names no approved replacement source"
            )
        require_in_namespace(source, context.namespace, operation="rebuild-source")

        before = table_row_ids(context.paths, table)
        rebuild_derived_from(
            context.paths, source_table=APPROVED_FEED_TABLE, source_feed="approved"
        )
        after = table_row_ids(context.paths, table)
        changed = after != before

        # The derived tables are regenerated as a chain, so rebuilding the
        # upstream dataset already puts the feature table into its target state.
        # Reporting that honestly as "no change" beats claiming a second rebuild
        # accomplished something.
        return _receipt(
            self.name,
            urn,
            action,
            changed=changed,
            detail=(
                f"rebuilt {table} with {len(after)} rows from {source}"
                if changed
                else f"{table} already holds the {len(after)} rebuilt rows; no change made"
            ),
            evidence={
                "table": table,
                "row_count": len(after),
                "row_ids": after,
                "rebuilt_from": source,
            },
        )


# --- registry ----------------------------------------------------------


DEFAULT_ADAPTERS: tuple[ContainmentAdapter, ...] = (
    ApiFreezeAdapter(),
    VectorIndexAdapter(),
    ExportQuarantineAdapter(),
    ModelAdapter(),
    WarehouseAdapter(),
)


@dataclass(frozen=True)
class AdapterRegistry:
    """Resolves a (URN, action) pair to the adapter that can perform it."""

    adapters: tuple[ContainmentAdapter, ...] = DEFAULT_ADAPTERS

    def resolve(self, urn: str, action: Action) -> ContainmentAdapter:
        """Find the adapter for one action.

        Raises:
            NoAdapterError: when nothing supports it. Recorded as unhandled
                residual exposure -- never swallowed into a success.
            EstateError: when the URN has no local artifact at all.
        """
        record = resolve_artifact(urn)
        for adapter in self.adapters:
            if adapter.supports(record, action):
                return adapter
        raise NoAdapterError(
            f"no containment adapter implements {action.value!r} for {record.kind!r} "
            f"artifact {urn}"
        )

    def execute(self, context: AdapterContext, urn: str, action: Action) -> AdapterReceipt:
        """Run one action, converting failures into a truthful failed receipt.

        The namespace guard runs first, before artifact resolution, so a foreign
        URN is refused on isolation grounds rather than incidentally because this
        estate happens not to contain it. :class:`NamespaceViolation` propagates
        -- it is never a receipt, because it means the caller asked for something
        it has no authority over at all.

        :class:`NoAdapterError` also propagates: the executor must be able to
        tell "unsupported" from "attempted and failed".
        """
        require_in_namespace(urn, context.namespace, operation=f"containment-{action.value}")
        adapter = self.resolve(urn, action)
        try:
            return adapter.apply(context, urn, action)
        except NoAdapterError:
            raise
        except (ContainmentError, EstateError, OSError, ValueError) as exc:
            return AdapterReceipt(
                urn=urn,
                action=action,
                adapter=adapter.name,
                attempted_at=datetime.now(UTC),
                succeeded=False,
                changed=False,
                detail=f"{action.value} failed",
                error=str(exc),
            )

    def supported_actions(self, urn: str) -> frozenset[Action]:
        """Every action this registry can perform on one URN."""
        try:
            record = resolve_artifact(urn)
        except EstateError:
            return frozenset()
        return frozenset(
            action
            for action in Action
            if any(adapter.supports(record, action) for adapter in self.adapters)
        )


#: Actions that never touch an artifact and therefore need no adapter.
NON_EXECUTABLE_ACTIONS = frozenset({Action.NO_ACTION, Action.ESCALATE})


def execution_stage(action: Action, artifact_class: ArtifactClass) -> int:
    """Deterministic ordering key for a containment plan.

    Sequence matters for correctness, not just tidiness:

    1. **Freeze** first -- stop revoked content reaching anyone while the rest
       of the plan runs.
    2. **Quarantine** distributed copies.
    3. **Purge** stored content, upstream tables before the index built from them.
    4. **Rebuild** from approved data, again upstream first, so the index and
       features regenerate from rebuilt tables rather than revoked ones.
    5. **Retrain**, then **replace** last, because swapping the served model is
       the step a rollback would most want to be able to undo.
    """
    order = {
        Action.FREEZE: 0,
        Action.QUARANTINE: 10,
        Action.PURGE: 20,
        Action.REBUILD: 30,
        Action.RETRAIN: 40,
        Action.REPLACE: 50,
    }
    within = {
        ArtifactClass.DATASET: 0,
        ArtifactClass.TRANSFORMATION: 1,
        ArtifactClass.FEATURE: 2,
        ArtifactClass.VECTOR_INDEX: 3,
        ArtifactClass.MODEL: 4,
    }
    return order.get(action, 90) + within.get(artifact_class, 5)


__all__ = [
    "APPROVED_FEED_TABLE",
    "DEFAULT_ADAPTERS",
    "NON_EXECUTABLE_ACTIONS",
    "RETRAINED_VERSION",
    "AdapterContext",
    "AdapterReceipt",
    "AdapterRegistry",
    "ApiFreezeAdapter",
    "ContainmentAdapter",
    "ContainmentError",
    "ExportQuarantineAdapter",
    "ModelAdapter",
    "NoAdapterError",
    "VectorIndexAdapter",
    "WarehouseAdapter",
    "execution_stage",
]
