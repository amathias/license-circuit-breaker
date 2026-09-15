"""Boundary contracts for the review fixes; no live services or credentials."""

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from adapters.catalog import LiveCatalog
from adapters.datahub import LiveDataHubClient
from app.config import Settings, get_settings, reset_settings_cache
from app.demo_guard import DemoCapacityError, DemoMutationGuard
from app.main import app
from app.receipts import ReceiptLedger
from app.store import GovernanceStore


def test_property_write_emits_sdk_patch_without_stale_metadata_read():
    from demo import graph

    ns = Settings(_env_file=None).namespace
    catalog = LiveCatalog("https://datahub.invalid", "test-token", ns)
    emitter = Mock()
    catalog._emitter = emitter
    client = LiveDataHubClient(
        mcp_url="https://mcp.invalid", gms_url="https://datahub.invalid",
        token="test-token", namespace=ns,
    )
    client._catalog = catalog
    client.get_entity = Mock(side_effect=AssertionError("must not merge indexed metadata"))
    client.set_properties(graph.SOURCE, {"lcb_revocation_status": "residual"})
    proposal = emitter.emit.call_args.args[0].to_obj()
    assert proposal["changeType"] == "PATCH"
    assert proposal["aspectName"] == "datasetProperties"
    assert json.loads(proposal["aspect"]["value"]) == [{
        "op": "add", "path": "/customProperties/lcb_revocation_status", "value": "residual",
    }]


def test_global_pending_confirmations_bound_distinct_clients():
    guard = DemoMutationGuard()
    for index in range(3):
        guard.issue_confirmation(str(index), "approve", global_pending_limit=3)
    with pytest.raises(DemoCapacityError):
        guard.issue_confirmation("new-client", "approve", global_pending_limit=3)


def test_capacity_retry_preserves_confirmation():
    now = [0.0]
    guard = DemoMutationGuard(clock=lambda: now[0])
    first, _ = guard.issue_confirmation("client", "approve")
    guard.begin_public("client", "approve", first)
    second, _ = guard.issue_confirmation("client", "execute")
    with pytest.raises(DemoCapacityError):
        guard.begin_public("client", "execute", second)
    guard.finish()
    now[0] = 2
    guard.begin_public("client", "execute", second)
    guard.finish()


def test_expensive_read_budget_expires():
    now = [0.0]
    guard = DemoMutationGuard(clock=lambda: now[0])
    guard.consume_read(limit=1)
    with pytest.raises(DemoCapacityError):
        guard.consume_read(limit=1)
    now[0] = 61
    guard.consume_read(limit=1)


@pytest.mark.parametrize("query", ["limit=1000", "limit=-1", "q=" + "a" * 2001])
def test_search_input_is_bounded(query):
    assert TestClient(app).get("/api/demo/search?" + query).status_code == 422


def test_plan_inspection_does_not_append_ledger(monkeypatch):
    monkeypatch.setenv("APP_ENV", "offline")
    reset_settings_cache()
    ledger = ReceiptLedger(get_settings().app_state_dir)
    ledger.append(operation="baseline", succeeded=True, simulated=True)
    before = list(ledger.entries())
    client = TestClient(app)
    assert client.get("/api/plan").status_code == 200
    assert client.get("/api/approvals").status_code == 200
    assert list(ledger.entries()) == before


def test_corrupt_ledger_blocks_api_evidence():
    ledger = ReceiptLedger(get_settings().app_state_dir)
    ledger.append(operation="baseline", succeeded=True, simulated=True)
    ledger.path.write_text("[]\n", encoding="utf-8")
    assert not ledger.verify_chain()[0]
    assert TestClient(app).get("/api/evidence").status_code == 503


def test_concurrent_processes_preserve_receipt_chain(tmp_path):
    code = (
        "import sys; from app.receipts import ReceiptLedger; "
        "ledger=ReceiptLedger(sys.argv[1]); "
        "[ledger.append(operation='process',succeeded=True,simulated=True) for _ in range(20)]"
    )
    children = [subprocess.Popen([sys.executable, "-c", code, str(tmp_path)])  # noqa: S603
                for _ in range(3)]
    try:
        assert all(child.wait(timeout=30) == 0 for child in children)
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait()
    ledger = ReceiptLedger(tmp_path)
    assert len(list(ledger.entries())) == 60
    assert ledger.verify_chain()[0]


def test_event_hash_is_stable_across_process_hash_seeds():
    code = "from demo.events import demo_rights_event; print(demo_rights_event().content_hash())"
    hashes = [subprocess.check_output(  # noqa: S603
        [sys.executable, "-c", code], env={**os.environ, "PYTHONHASHSEED": str(seed)}, text=True,
    ).strip() for seed in (1, 2, 3)]
    assert len(set(hashes)) == 1


def test_dotenv_environment_also_controls_documentation(tmp_path):
    (tmp_path / ".env").write_text("APP_ENV=hackathon\n", encoding="utf-8")
    environment = {key: value for key, value in os.environ.items() if key != "APP_ENV"}
    code = (
        "from app.main import app; from app.config import get_settings; "
        "assert get_settings().app_env == 'hackathon'; "
        "assert app.docs_url is None and app.openapi_url is None"
    )
    subprocess.run(  # noqa: S603
        [sys.executable, "-c", code], cwd=tmp_path, env=environment, check=True,
        capture_output=True, text=True,
    )


def test_concurrent_schema_initialization_preserves_run_fingerprint(tmp_path):
    with ThreadPoolExecutor(max_workers=8) as pool:
        stores = list(pool.map(lambda _: GovernanceStore(tmp_path), range(16)))
    with stores[0].connect() as connection:
        columns = [row[1] for row in connection.execute("PRAGMA table_info(runs)")]
    assert columns.count("estate_fingerprint") == 1
