"""Append-only, sanitized receipt ledger.

Receipts are the judging evidence that reads and writebacks actually happened.
They live under ``APP_STATE_DIR`` as JSON Lines and are never committed to git --
``.gitignore`` excludes the state directory, and `GITHUB_PUBLISHING.md` forbids
committing runtime receipts.

Two properties matter:

- **Sanitized.** Tokens and authorization headers must never reach disk. Redaction
  runs on every value before it is written, not at the call sites, so a new caller
  cannot forget.
- **Append-only.** The file is opened in append mode and entries carry a sequence
  number and the prior entry's hash, so a silently truncated or reordered ledger is
  detectable. This is tamper-evident, not tamper-proof, and is described that way.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REDACTED = "[REDACTED]"

#: Keys whose values are always replaced, regardless of nesting depth.
_SECRET_KEYS = frozenset(
    {
        "token",
        "datahub_token",
        "datahub_gms_token",
        "authorization",
        "auth",
        "password",
        "secret",
        "api_key",
        "apikey",
        "private_key",
        "bearer",
    }
)

#: Values that look like credentials even under an innocuous key name.
_SECRET_VALUE_PATTERNS = (
    re.compile(r"^Bearer\s+\S+", re.IGNORECASE),
    re.compile(r"^eyJ[A-Za-z0-9_-]{10,}\."),  # JWT
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),  # GitHub tokens
)


def sanitize(value: Any) -> Any:
    """Recursively redact secrets from a value before it is persisted."""
    if isinstance(value, dict):
        return {
            key: (REDACTED if str(key).lower() in _SECRET_KEYS else sanitize(val))
            for key, val in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [sanitize(item) for item in value]
    if isinstance(value, str):
        for pattern in _SECRET_VALUE_PATTERNS:
            if pattern.search(value):
                return REDACTED
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    return value


class ReceiptLedger:
    """Tamper-evident append-only ledger of governance operations."""

    def __init__(self, state_dir: Path | str, filename: str = "receipts.jsonl") -> None:
        self._path = Path(state_dir) / filename
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return self._path

    def append(
        self,
        *,
        operation: str,
        urn: str | None = None,
        succeeded: bool,
        simulated: bool = False,
        detail: str = "",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record one operation and return the persisted entry.

        Args:
            simulated: True when the operation ran against the in-memory fake.
                This flag is what keeps simulated runs from being mistaken for
                live DataHub evidence; it is never inferred, always explicit.
        """
        previous = self._last_entry()
        entry = {
            "seq": (previous["seq"] + 1) if previous else 1,
            "recorded_at": datetime.now(UTC).isoformat(),
            "operation": operation,
            "urn": urn,
            "succeeded": succeeded,
            "simulated": simulated,
            "detail": detail,
            "payload": sanitize(payload or {}),
            "prior_hash": previous["entry_hash"] if previous else None,
        }
        entry["entry_hash"] = _hash_entry(entry)

        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
        return entry

    def entries(self) -> Iterator[dict[str, Any]]:
        """Yield every persisted entry in order."""
        if not self._path.exists():
            return
        with self._path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    yield json.loads(line)

    def verify_chain(self) -> tuple[bool, str]:
        """Check that the hash chain and sequence numbers are intact.

        Returns ``(ok, detail)``. Detects truncation from the front, reordering,
        and edited entries. It cannot detect an attacker who rewrites the whole
        file, which is why this is described as tamper-evident.
        """
        expected_seq = 1
        prior_hash: str | None = None

        for entry in self.entries():
            if entry.get("seq") != expected_seq:
                return False, f"sequence break at entry {entry.get('seq')}, expected {expected_seq}"
            if entry.get("prior_hash") != prior_hash:
                return False, f"hash chain break at entry {entry.get('seq')}"

            recorded = entry.get("entry_hash")
            recomputed = _hash_entry({k: v for k, v in entry.items() if k != "entry_hash"})
            if recorded != recomputed:
                return False, f"entry {entry.get('seq')} was modified after it was written"

            prior_hash = recorded
            expected_seq += 1

        return True, f"{expected_seq - 1} entries verified"

    def _last_entry(self) -> dict[str, Any] | None:
        last = None
        for entry in self.entries():
            last = entry
        return last


def _hash_entry(entry: dict[str, Any]) -> str:
    payload = {k: v for k, v in entry.items() if k != "entry_hash"}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
