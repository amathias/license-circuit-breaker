"""Receipt ledger tests: sanitization and tamper evidence."""

from __future__ import annotations

import json

import pytest

from app.receipts import REDACTED, ReceiptLedger, sanitize


@pytest.fixture
def ledger(tmp_path) -> ReceiptLedger:
    return ReceiptLedger(tmp_path)


class TestSanitization:
    @pytest.mark.parametrize(
        "key",
        ["token", "datahub_token", "DATAHUB_GMS_TOKEN", "Authorization", "password", "api_key"],
    )
    def test_secret_keys_are_redacted(self, key):
        assert sanitize({key: "super-secret"})[key] == REDACTED

    def test_redaction_is_case_insensitive(self):
        assert sanitize({"ToKeN": "x"})["ToKeN"] == REDACTED

    def test_nested_secrets_are_redacted(self):
        result = sanitize({"outer": {"inner": {"token": "x"}}})
        assert result["outer"]["inner"]["token"] == REDACTED

    def test_secrets_inside_lists_are_redacted(self):
        result = sanitize({"items": [{"token": "x"}, {"safe": "y"}]})
        assert result["items"][0]["token"] == REDACTED
        assert result["items"][1]["safe"] == "y"

    def test_bearer_header_value_is_redacted_even_under_a_safe_key(self):
        assert sanitize({"detail": "Bearer abc123xyz"})["detail"] == REDACTED

    def test_jwt_shaped_value_is_redacted(self):
        jwt = "eyJhbGciOiJIUzI1NiJ9.payload.sig"
        assert sanitize({"detail": jwt})["detail"] == REDACTED

    def test_ordinary_values_survive(self):
        payload = {"urn": "urn:li:dataset:(x,license.a,PROD)", "count": 3, "ok": True}
        assert sanitize(payload) == payload


class TestLedgerPersistence:
    def test_entries_are_appended_in_order(self, ledger):
        ledger.append(operation="a", succeeded=True)
        ledger.append(operation="b", succeeded=True)
        seqs = [e["seq"] for e in ledger.entries()]
        assert seqs == [1, 2]

    def test_secrets_never_reach_disk(self, ledger):
        ledger.append(
            operation="writeback",
            succeeded=True,
            payload={"token": "hunter2", "authorization": "Bearer abc123def"},
        )
        raw = ledger.path.read_text(encoding="utf-8")
        assert "hunter2" not in raw
        assert "abc123def" not in raw
        assert REDACTED in raw

    def test_simulated_flag_is_explicit_and_persisted(self, ledger):
        ledger.append(operation="writeback", succeeded=True, simulated=True)
        assert next(iter(ledger.entries()))["simulated"] is True

    def test_simulated_defaults_to_false(self, ledger):
        ledger.append(operation="writeback", succeeded=True)
        assert next(iter(ledger.entries()))["simulated"] is False

    def test_failures_are_recorded_too(self, ledger):
        ledger.append(operation="writeback", succeeded=False, detail="boom")
        entry = next(iter(ledger.entries()))
        assert entry["succeeded"] is False
        assert entry["detail"] == "boom"


class TestTamperEvidence:
    def test_intact_chain_verifies(self, ledger):
        for i in range(5):
            ledger.append(operation=f"op{i}", succeeded=True)
        ok, detail = ledger.verify_chain()
        assert ok, detail

    def test_empty_ledger_verifies(self, ledger):
        ok, _ = ledger.verify_chain()
        assert ok

    def test_edited_entry_is_detected(self, ledger):
        ledger.append(operation="writeback", succeeded=False)
        ledger.append(operation="writeback", succeeded=True)

        lines = ledger.path.read_text(encoding="utf-8").strip().splitlines()
        tampered = json.loads(lines[0])
        tampered["succeeded"] = True  # flip a failure into a success
        lines[0] = json.dumps(tampered, sort_keys=True)
        ledger.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        ok, detail = ledger.verify_chain()
        assert not ok
        assert "modified" in detail

    def test_removed_entry_is_detected(self, ledger):
        for i in range(3):
            ledger.append(operation=f"op{i}", succeeded=True)

        lines = ledger.path.read_text(encoding="utf-8").strip().splitlines()
        del lines[1]
        ledger.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        ok, detail = ledger.verify_chain()
        assert not ok
        assert "sequence break" in detail or "hash chain break" in detail

    def test_reordered_entries_are_detected(self, ledger):
        for i in range(3):
            ledger.append(operation=f"op{i}", succeeded=True)

        lines = ledger.path.read_text(encoding="utf-8").strip().splitlines()
        lines[0], lines[1] = lines[1], lines[0]
        ledger.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        ok, _ = ledger.verify_chain()
        assert not ok

    def test_chain_links_each_entry_to_its_predecessor(self, ledger):
        ledger.append(operation="a", succeeded=True)
        ledger.append(operation="b", succeeded=True)
        entries = list(ledger.entries())
        assert entries[0]["prior_hash"] is None
        assert entries[1]["prior_hash"] == entries[0]["entry_hash"]
