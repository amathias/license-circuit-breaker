"""Durable governance state.

One SQLite database under ``APP_STATE_DIR`` holds the rights events, the plans
generated from them, the approvals recorded against those plans, and the
execution journal. Everything an operator needs to answer "who approved what,
and what actually ran" survives a process restart, because an approval that
lives only in memory is not an approval -- it is a UI state.

Connections are opened per operation rather than held. FastAPI runs sync
endpoints in a threadpool and the CLI is a separate process entirely, so a
shared connection would be the wrong shape for both. At demo volume the cost is
irrelevant.

The receipt ledger in :mod:`app.receipts` remains separate and append-only. This
database is queryable working state; that file is the tamper-evident record.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

#: Bumped when the schema changes incompatibly.
SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS rights_events (
    event_id      TEXT NOT NULL,
    version       INTEGER NOT NULL,
    source_urn    TEXT NOT NULL,
    content_hash  TEXT NOT NULL,
    payload       TEXT NOT NULL,
    recorded_at   TEXT NOT NULL,
    PRIMARY KEY (event_id, version)
);

CREATE TABLE IF NOT EXISTS plans (
    plan_hash     TEXT PRIMARY KEY,
    event_id      TEXT NOT NULL,
    event_hash    TEXT NOT NULL,
    payload       TEXT NOT NULL,
    generated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approvals (
    approval_id   TEXT PRIMARY KEY,
    plan_hash     TEXT NOT NULL,
    event_id      TEXT NOT NULL,
    event_hash    TEXT NOT NULL,
    decision      TEXT NOT NULL,
    approver      TEXT NOT NULL,
    note          TEXT NOT NULL DEFAULT '',
    scope         TEXT NOT NULL,
    decided_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS approvals_by_plan ON approvals (plan_hash);
CREATE INDEX IF NOT EXISTS approvals_by_event ON approvals (event_id);

CREATE TABLE IF NOT EXISTS runs (
    run_id        TEXT PRIMARY KEY,
    plan_hash     TEXT NOT NULL,
    approval_id   TEXT NOT NULL,
    status        TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    finished_at   TEXT
);

CREATE INDEX IF NOT EXISTS runs_by_plan ON runs (plan_hash);

CREATE TABLE IF NOT EXISTS steps (
    run_id        TEXT NOT NULL,
    seq           INTEGER NOT NULL,
    urn           TEXT NOT NULL,
    action        TEXT NOT NULL,
    status        TEXT NOT NULL,
    changed       INTEGER NOT NULL DEFAULT 0,
    detail        TEXT NOT NULL DEFAULT '',
    error         TEXT,
    evidence      TEXT NOT NULL DEFAULT '{}',
    recorded_at   TEXT NOT NULL,
    PRIMARY KEY (run_id, seq)
);

CREATE TABLE IF NOT EXISTS schema_meta (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
"""

DATABASE_NAME = "governance.db"


class StoreError(Exception):
    """Raised when governance state cannot be read or written."""


class GovernanceStore:
    """Owns the SQLite database and its schema."""

    def __init__(self, state_dir: Path | str, filename: str = DATABASE_NAME) -> None:
        self._path = Path(state_dir)
        self._path.mkdir(parents=True, exist_ok=True)
        self._path = self._path / filename
        self._initialize()

    @property
    def path(self) -> Path:
        return self._path

    def _initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(_SCHEMA)
            connection.execute(
                "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('version', ?)",
                (str(SCHEMA_VERSION),),
            )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Open a transactional connection.

        Commits on clean exit and rolls back on any exception, so a failure part
        way through recording a decision never leaves half of it behind.
        """
        connection = sqlite3.connect(self._path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        # Foreign keys are off by default in SQLite and the demo relies on
        # application-level integrity, but WAL keeps a concurrent reader (the
        # judge console polling a run) from blocking the writer.
        connection.execute("PRAGMA journal_mode=WAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


__all__ = ["DATABASE_NAME", "SCHEMA_VERSION", "GovernanceStore", "StoreError"]
