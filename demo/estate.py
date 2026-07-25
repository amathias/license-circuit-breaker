"""The disposable local data estate.

Everything the containment adapters act on is built here, and all of it is real:
a DuckDB warehouse, a TF-IDF vector index, a trained sentiment classifier with a
training manifest, a CSV export, and a serving control plane. Freezing the API
genuinely stops it answering; purging the index genuinely removes vectors;
quarantining the export genuinely moves the file; retraining genuinely produces a
new classifier whose manifest cites a different source.

Every artifact lives under ``APP_STATE_DIR/estate`` and is disposable. Nothing
here is a stub or a mock -- the *enterprise* connectors a production deployment
would need (Snowflake, a real feature store, a real model registry) are out of
scope and are not pretended at.

The registry is the bridge between the two halves of the product: DataHub knows
the URN, this knows where that URN's bytes live locally. An adapter that cannot
resolve its target's local path refuses to run rather than reporting a
containment it did not perform.
"""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from demo import graph
from demo.corpus import (
    APPROVED_REVIEWS,
    PARTNER_PREFIX,
    PARTNER_REVIEWS,
    Review,
    normalize,
)
from demo.tfidf import TfidfModel, fit_tfidf

#: Bumped when the on-disk layout changes incompatibly. ``status`` reports it so
#: a stale estate is visible rather than mysteriously failing a probe.
ESTATE_VERSION = 2

#: Serving states for the control plane. ``blocked`` is the state a freeze puts a
#: service into; the service keeps running and keeps answering, but it answers
#: with a refusal. A stopped process would prove nothing -- a judge could not
#: tell containment from a crash.
SERVING = "serving"
BLOCKED = "blocked"


class EstateError(Exception):
    """Raised when the estate cannot be built, read, or resolved."""


# --- layout ------------------------------------------------------------


@dataclass(frozen=True)
class EstatePaths:
    """Absolute locations of every estate artifact.

    Derived from ``APP_STATE_DIR``; nothing here is hardcoded, because the
    deployed state root is a POSIX path and local development is not.
    """

    root: Path

    @classmethod
    def under(cls, state_dir: Path | str) -> EstatePaths:
        return cls(root=Path(state_dir).resolve() / "estate")

    @property
    def warehouse(self) -> Path:
        return self.root / "warehouse.duckdb"

    @property
    def index_dir(self) -> Path:
        return self.root / "index"

    @property
    def index_manifest(self) -> Path:
        return self.index_dir / "manifest.json"

    @property
    def index_vectors(self) -> Path:
        return self.index_dir / "vectors.json"

    @property
    def model_dir(self) -> Path:
        return self.root / "models"

    @property
    def export_dir(self) -> Path:
        return self.root / "exports"

    @property
    def quarantine_dir(self) -> Path:
        return self.root / "quarantine"

    @property
    def serving_path(self) -> Path:
        return self.root / "serving.json"

    @property
    def registry_path(self) -> Path:
        return self.root / "registry.json"

    def model_root(self, name: str) -> Path:
        return self.model_dir / name

    def ensure(self) -> None:
        for directory in (
            self.root,
            self.index_dir,
            self.model_dir,
            self.export_dir,
            self.quarantine_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)


# --- registry ----------------------------------------------------------

DUCKDB_TABLE = "duckdb_table"
VECTOR_INDEX = "vector_index"
MODEL = "model"
EXPORT = "export"
SERVICE = "service"


@dataclass(frozen=True)
class ArtifactRecord:
    """Where one DataHub URN's local bytes actually live."""

    urn: str
    kind: str
    location: str
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "urn": self.urn,
            "kind": self.kind,
            "location": self.location,
            "description": self.description,
        }


#: URN -> local artifact. Anything absent from this map has no local
#: representation, which an adapter must treat as a refusal, never as success.
LOCAL_ARTIFACTS: tuple[ArtifactRecord, ...] = (
    ArtifactRecord(graph.SOURCE, DUCKDB_TABLE, "partner_feed", "Licensed partner review feed"),
    ArtifactRecord(
        graph.REPLACEMENT_SOURCE, DUCKDB_TABLE, "approved_feed", "Approved replacement feed"
    ),
    ArtifactRecord(graph.NORMALIZED, DUCKDB_TABLE, "normalized", "Cleaned partner reviews"),
    ArtifactRecord(graph.FEATURES, DUCKDB_TABLE, "review_sentiment", "Sentiment feature table"),
    ArtifactRecord(graph.MODEL, MODEL, "review_sentiment", "Sentiment classifier"),
    ArtifactRecord(graph.VECTOR_INDEX, VECTOR_INDEX, "index", "Review search index"),
    ArtifactRecord(graph.PREDICT_API, SERVICE, "predict_api", "Prediction and search endpoint"),
    ArtifactRecord(graph.EXPORT, EXPORT, "reviews_extract.csv", "CSV extract of reviews"),
    ArtifactRecord(graph.ANALYTICS, DUCKDB_TABLE, "review_volume", "Aggregate volume report"),
    ArtifactRecord(
        graph.APPROVED_MODEL, MODEL, "approved_sentiment", "Model trained only on approved data"
    ),
    ArtifactRecord(
        graph.ORPHAN, DUCKDB_TABLE, "legacy_snapshot", "Legacy snapshot with broken lineage"
    ),
)

ARTIFACTS_BY_URN: dict[str, ArtifactRecord] = {a.urn: a for a in LOCAL_ARTIFACTS}


def resolve_artifact(urn: str) -> ArtifactRecord:
    """Look up one URN's local artifact.

    Raises:
        EstateError: if the URN has no local representation. Adapters must not
            silently succeed on a target they cannot locate.
    """
    record = ARTIFACTS_BY_URN.get(urn)
    if record is None:
        raise EstateError(
            f"{urn!r} has no local artifact in this estate, so no containment "
            "action can be performed on it here."
        )
    return record


# --- serving control plane ---------------------------------------------


@dataclass
class ServingControl:
    """Per-service serving state, persisted as JSON.

    The demo API reads this on every request. Freezing writes ``blocked`` here;
    that is the whole mechanism, and it is deliberately observable -- a judge can
    open the file and see the state the probe reports.
    """

    path: Path
    states: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> ServingControl:
        if not path.exists():
            return cls(path=path, states={})
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise EstateError(f"serving control plane at {path} is unreadable: {exc}") from exc
        return cls(path=path, states=dict(raw.get("services", {})))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"estate_version": ESTATE_VERSION, "services": self.states}
        self.path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def state(self, urn: str) -> str:
        return str(self.states.get(urn, {}).get("state", SERVING))

    def is_blocked(self, urn: str) -> bool:
        return self.state(urn) == BLOCKED

    def set_state(self, urn: str, state: str, reason: str = "") -> bool:
        """Set one service's state. Returns True when this call changed it.

        The return value is what makes a freeze idempotent *and* honest: a second
        freeze reports ``changed=False`` rather than claiming to have frozen an
        already-frozen service.
        """
        if state not in (SERVING, BLOCKED):
            raise EstateError(f"unknown serving state {state!r}")
        changed = self.state(urn) != state
        self.states[urn] = {
            "state": state,
            "reason": reason,
            "changed_at": datetime.now(UTC).isoformat(),
        }
        self.save()
        return changed


# --- build -------------------------------------------------------------


@dataclass(frozen=True)
class EstateBuildResult:
    """What a build produced, for the CLI and the readiness probe."""

    root: Path
    tables: tuple[str, ...]
    index_entries: int
    model_versions: tuple[str, ...]
    export_rows: int
    rebuilt: bool

    def describe(self) -> str:
        return (
            f"{len(self.tables)} tables, {self.index_entries} indexed documents, "
            f"{len(self.model_versions)} models, {self.export_rows} exported rows"
        )


def _connect(paths: EstatePaths):
    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover - duckdb is a declared dependency
        raise EstateError("duckdb is required to build the local data estate") from exc

    paths.ensure()
    return duckdb.connect(str(paths.warehouse))


def _rows(reviews: tuple[Review, ...]) -> list[tuple[str, str, int, int]]:
    return [(r.review_id, r.text, r.label, r.rating) for r in reviews]


def build_warehouse(paths: EstatePaths) -> tuple[str, ...]:
    """Create the DuckDB chain from the fixed corpora.

    Fully idempotent: every table is replaced from the same deterministic input,
    so building twice yields byte-identical content.
    """
    connection = _connect(paths)
    try:
        connection.execute(
            "CREATE OR REPLACE TABLE partner_feed "
            "(review_id VARCHAR, text VARCHAR, label INTEGER, rating INTEGER)"
        )
        connection.executemany(
            "INSERT INTO partner_feed VALUES (?, ?, ?, ?)", _rows(PARTNER_REVIEWS)
        )

        connection.execute(
            "CREATE OR REPLACE TABLE approved_feed "
            "(review_id VARCHAR, text VARCHAR, label INTEGER, rating INTEGER)"
        )
        connection.executemany(
            "INSERT INTO approved_feed VALUES (?, ?, ?, ?)", _rows(APPROVED_REVIEWS)
        )

        _rebuild_derived(connection, source_table="partner_feed", source_feed="partner")

        # The unaffected analytics branch. Aggregate counts use only the
        # analytics purpose, which the demo revocation retains, so containment
        # must leave this table exactly as built.
        connection.execute(
            "CREATE OR REPLACE TABLE review_volume AS "
            "SELECT rating, COUNT(*) AS review_count FROM partner_feed "
            "GROUP BY rating ORDER BY rating"
        )

        # Broken-lineage node: real rows, no resolvable upstream edge.
        connection.execute(
            "CREATE OR REPLACE TABLE legacy_snapshot AS "
            "SELECT review_id, text FROM partner_feed LIMIT 6"
        )

        tables = tuple(
            sorted(name for (name,) in connection.execute("SHOW TABLES").fetchall())
        )
    finally:
        connection.close()
    return tables


def _rebuild_derived(connection, source_table: str, source_feed: str) -> None:
    """Rebuild ``normalized`` and ``review_sentiment`` from one feed table.

    Rebuild containment reruns exactly this against ``approved_feed``, which is
    what makes the rebuilt dataset provably free of partner rows rather than
    merely re-labelled.
    """
    rows = connection.execute(
        f"SELECT review_id, text, label, rating FROM {source_table} ORDER BY review_id"  # noqa: S608
    ).fetchall()

    connection.execute(
        "CREATE OR REPLACE TABLE normalized "
        "(review_id VARCHAR, text VARCHAR, label INTEGER, rating INTEGER, source_feed VARCHAR)"
    )
    connection.executemany(
        "INSERT INTO normalized VALUES (?, ?, ?, ?, ?)",
        [(rid, normalize(text), label, rating, source_feed) for rid, text, label, rating in rows],
    )

    connection.execute(
        "CREATE OR REPLACE TABLE review_sentiment AS "
        "SELECT review_id, text AS feature_text, label, source_feed FROM normalized"
    )


def read_table(paths: EstatePaths, table: str) -> list[dict[str, Any]]:
    """Read one warehouse table as dictionaries.

    Raises:
        EstateError: if the warehouse or the table is absent.
    """
    if not paths.warehouse.exists():
        raise EstateError(f"warehouse {paths.warehouse} does not exist; build the estate first")

    connection = _connect(paths)
    try:
        existing = {name for (name,) in connection.execute("SHOW TABLES").fetchall()}
        if table not in existing:
            raise EstateError(f"table {table!r} does not exist in {paths.warehouse}")
        cursor = connection.execute(f"SELECT * FROM {table}")  # noqa: S608
        columns = [d[0] for d in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
    finally:
        connection.close()


# --- vector index ------------------------------------------------------


def build_index(paths: EstatePaths, source_urn: str, source_feed: str) -> int:
    """Build the vector index from one feed's normalized rows.

    The manifest records the source URN and every indexed identifier, so
    "does this index still hold revoked content?" is answered by reading the
    artifact rather than by trusting that a purge ran.
    """
    table = "normalized"
    rows = read_table(paths, table)
    documents = [str(row["text"]) for row in rows]
    model = fit_tfidf(documents)

    entries = [
        {
            "review_id": str(row["review_id"]),
            "text": str(row["text"]),
            "source_feed": str(row.get("source_feed", source_feed)),
            "terms": model.transform(str(row["text"])),
        }
        for row in rows
    ]

    paths.index_dir.mkdir(parents=True, exist_ok=True)
    _write_json(paths.index_vectors, {"model": model.to_dict(), "entries": entries})

    row_ids = sorted(str(row["review_id"]) for row in rows)
    _write_json(
        paths.index_manifest,
        {
            "estate_version": ESTATE_VERSION,
            "built_at": datetime.now(UTC).isoformat(),
            "source_urns": [source_urn],
            "source_feed": source_feed,
            "vector_count": len(entries),
            "vocabulary_size": len(model.vocabulary),
            "row_ids": row_ids,
            "content_hash": _hash(row_ids),
        },
    )
    return len(entries)


def load_index(paths: EstatePaths) -> tuple[TfidfModel | None, list[dict[str, Any]]]:
    """Load the index. Returns ``(None, [])`` when it has been purged."""
    if not paths.index_vectors.exists():
        return None, []
    payload = json.loads(paths.index_vectors.read_text(encoding="utf-8"))
    entries = list(payload.get("entries", ()))
    raw_model = payload.get("model")
    if not raw_model or not entries:
        return None, entries
    return TfidfModel.from_dict(raw_model), entries


def index_manifest(paths: EstatePaths) -> dict[str, Any]:
    """Read the index manifest, or an empty mapping when absent."""
    if not paths.index_manifest.exists():
        return {}
    return json.loads(paths.index_manifest.read_text(encoding="utf-8"))


# --- model -------------------------------------------------------------


def train_model(
    paths: EstatePaths,
    name: str,
    source_urn: str,
    source_table: str,
    version: str,
) -> dict[str, Any]:
    """Train one classifier version and write its training manifest.

    The manifest names the training source URN and every row identifier that
    contributed. That is the evidence a retrain is verified against: an active
    model whose manifest still cites the revoked source has not been contained,
    however many times an adapter reported success.
    """
    rows = read_table(paths, source_table)
    if not rows:
        raise EstateError(f"cannot train {name!r}: {source_table} is empty")

    text_column = "feature_text" if "feature_text" in rows[0] else "text"
    documents = [str(row[text_column]) for row in rows]
    labels = [int(row["label"]) for row in rows]

    vectorizer = fit_tfidf(documents)
    matrix = [vectorizer.dense(document) for document in documents]

    from sklearn.linear_model import LogisticRegression

    # liblinear with a fixed seed is deterministic on a corpus this size, so the
    # same feed always produces the same coefficients and the same manifest hash.
    classifier = LogisticRegression(solver="liblinear", random_state=0)
    classifier.fit(matrix, labels)

    accuracy = float(classifier.score(matrix, labels))
    row_ids = sorted(str(row["review_id"]) for row in rows)

    version_dir = paths.model_root(name) / version
    version_dir.mkdir(parents=True, exist_ok=True)

    _write_json(
        version_dir / "model.json",
        {
            "algorithm": "tfidf+logistic_regression",
            "vectorizer": vectorizer.to_dict(),
            "coefficients": [float(v) for v in classifier.coef_[0]],
            "intercept": float(classifier.intercept_[0]),
        },
    )

    manifest = {
        "estate_version": ESTATE_VERSION,
        "model": name,
        "version": version,
        "trained_at": datetime.now(UTC).isoformat(),
        "training_sources": [source_urn],
        "training_table": source_table,
        "row_count": len(rows),
        "row_ids": row_ids,
        "training_accuracy": round(accuracy, 4),
        "content_hash": _hash(row_ids),
    }
    _write_json(version_dir / "training_manifest.json", manifest)
    _write_json(paths.model_root(name) / "active.json", {"active_version": version})
    return manifest


def active_version(paths: EstatePaths, name: str) -> str | None:
    """The version this model currently serves, or None when unset."""
    pointer = paths.model_root(name) / "active.json"
    if not pointer.exists():
        return None
    return json.loads(pointer.read_text(encoding="utf-8")).get("active_version")


def training_manifest(paths: EstatePaths, name: str, version: str | None = None) -> dict[str, Any]:
    """Read a model's training manifest. Defaults to the active version."""
    version = version or active_version(paths, name)
    if version is None:
        return {}
    manifest_path = paths.model_root(name) / version / "training_manifest.json"
    if not manifest_path.exists():
        return {}
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def load_scorer(paths: EstatePaths, name: str) -> dict[str, Any] | None:
    """Load the active model's scoring bundle, or None when unavailable."""
    version = active_version(paths, name)
    if version is None:
        return None
    model_path = paths.model_root(name) / version / "model.json"
    if not model_path.exists():
        return None
    return json.loads(model_path.read_text(encoding="utf-8"))


def model_versions(paths: EstatePaths, name: str) -> tuple[str, ...]:
    root = paths.model_root(name)
    if not root.is_dir():
        return ()
    return tuple(sorted(p.name for p in root.iterdir() if p.is_dir()))


# --- export ------------------------------------------------------------


def build_export(paths: EstatePaths) -> int:
    """Write the CSV extract and its manifest from the normalized table."""
    rows = read_table(paths, "normalized")
    paths.export_dir.mkdir(parents=True, exist_ok=True)

    target = paths.export_dir / "reviews_extract.csv"
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["review_id", "text", "label", "rating", "source_feed"])
        for row in rows:
            writer.writerow(
                [
                    row["review_id"],
                    row["text"],
                    row["label"],
                    row["rating"],
                    row.get("source_feed", ""),
                ]
            )

    row_ids = sorted(str(row["review_id"]) for row in rows)
    _write_json(
        paths.export_dir / "reviews_extract.manifest.json",
        {
            "estate_version": ESTATE_VERSION,
            "built_at": datetime.now(UTC).isoformat(),
            "row_count": len(rows),
            "row_ids": row_ids,
            "content_hash": _hash(row_ids),
        },
    )
    return len(rows)


def export_path(paths: EstatePaths) -> Path:
    return paths.export_dir / "reviews_extract.csv"


def quarantined_export_path(paths: EstatePaths) -> Path:
    return paths.quarantine_dir / "reviews_extract.csv"


# --- whole-estate lifecycle --------------------------------------------


def build_estate(paths: EstatePaths) -> EstateBuildResult:
    """Build every local artifact deterministically.

    Safe to re-run: each step replaces its output from the same fixed corpus, so
    a rebuild after a partial failure converges rather than compounding.
    """
    rebuilt = paths.root.exists()
    paths.ensure()

    tables = build_warehouse(paths)
    entries = build_index(paths, source_urn=graph.SOURCE, source_feed="partner")
    train_model(
        paths,
        name="review_sentiment",
        source_urn=graph.FEATURES,
        source_table="review_sentiment",
        version="v1",
    )
    # The approved-branch model is trained from the approved feed and must never
    # be touched by containment. Isolation tests assert exactly that.
    _train_approved_model(paths)
    export_rows = build_export(paths)

    control = ServingControl.load(paths.serving_path)
    control.set_state(graph.PREDICT_API, SERVING, reason="estate build")

    _write_json(
        paths.registry_path,
        {
            "estate_version": ESTATE_VERSION,
            "built_at": datetime.now(UTC).isoformat(),
            "artifacts": [a.to_dict() for a in LOCAL_ARTIFACTS],
        },
    )

    return EstateBuildResult(
        root=paths.root,
        tables=tables,
        index_entries=entries,
        model_versions=model_versions(paths, "review_sentiment"),
        export_rows=export_rows,
        rebuilt=rebuilt,
    )


def _train_approved_model(paths: EstatePaths) -> None:
    """Train the untouched approved-branch model from ``approved_feed``."""
    connection = _connect(paths)
    try:
        connection.execute(
            "CREATE OR REPLACE TABLE approved_features AS "
            "SELECT review_id, text AS feature_text, label, 'approved' AS source_feed "
            "FROM approved_feed"
        )
    finally:
        connection.close()

    train_model(
        paths,
        name="approved_sentiment",
        source_urn=graph.REPLACEMENT_SOURCE,
        source_table="approved_features",
        version="v1",
    )


def reset_estate(paths: EstatePaths) -> bool:
    """Delete the whole estate directory. Returns True when something was removed.

    Scoped to ``APP_STATE_DIR/estate`` by construction -- it removes the estate
    root itself and never a parent, so a misconfigured state directory cannot
    turn a reset into a wider delete.
    """
    if not paths.root.exists():
        return False
    if paths.root.name != "estate":  # pragma: no cover - defensive
        raise EstateError(
            f"refusing to reset {paths.root}: not an estate root. "
            "Reset removes only the directory named 'estate'."
        )
    shutil.rmtree(paths.root)
    return True


def estate_status(paths: EstatePaths) -> dict[str, Any]:
    """A read-only snapshot for readiness, the CLI, and the judge console."""
    control = ServingControl.load(paths.serving_path)
    manifest = index_manifest(paths)
    model = training_manifest(paths, "review_sentiment")

    return {
        "estate_version": ESTATE_VERSION,
        "root": str(paths.root),
        "built": paths.registry_path.exists(),
        "warehouse_present": paths.warehouse.exists(),
        "index": {
            "present": paths.index_vectors.exists(),
            "vector_count": manifest.get("vector_count", 0),
            "source_urns": manifest.get("source_urns", []),
            "holds_partner_rows": any(
                str(rid).startswith(PARTNER_PREFIX) for rid in manifest.get("row_ids", ())
            ),
        },
        "model": {
            "active_version": active_version(paths, "review_sentiment"),
            "training_sources": model.get("training_sources", []),
            "holds_partner_rows": any(
                str(rid).startswith(PARTNER_PREFIX) for rid in model.get("row_ids", ())
            ),
        },
        "export": {
            "published": export_path(paths).exists(),
            "quarantined": quarantined_export_path(paths).exists(),
        },
        "serving": {urn: control.state(urn) for urn in (graph.PREDICT_API,)},
    }


# --- helpers -----------------------------------------------------------


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _hash(values: list[str]) -> str:
    encoded = json.dumps(sorted(values), separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "ARTIFACTS_BY_URN",
    "BLOCKED",
    "DUCKDB_TABLE",
    "ESTATE_VERSION",
    "EXPORT",
    "LOCAL_ARTIFACTS",
    "MODEL",
    "SERVICE",
    "SERVING",
    "VECTOR_INDEX",
    "ArtifactRecord",
    "EstateBuildResult",
    "EstateError",
    "EstatePaths",
    "ServingControl",
    "active_version",
    "build_estate",
    "build_export",
    "build_index",
    "build_warehouse",
    "estate_status",
    "export_path",
    "index_manifest",
    "load_index",
    "load_scorer",
    "model_versions",
    "quarantined_export_path",
    "read_table",
    "reset_estate",
    "resolve_artifact",
    "train_model",
    "training_manifest",
]
