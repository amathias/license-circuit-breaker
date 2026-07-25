"""CLI exit-code regressions.

The slice previously exited zero whenever it produced a plan, regardless of what
happened to the writeback. A writeback that landed and was not restored left the
shared instance dirty while CI and the coordinator's promotion check saw a pass.
"""

from __future__ import annotations

import pytest

from adapters.fake_datahub import FakeDataHubClient
from app.namespace import Namespace
from demo import cli
from demo.graph import NODES, SENTINEL_URN
from demo.seed import seed

NS = Namespace(
    project_slug="license-circuit-breaker",
    urn_prefix="license.",
    project_tag="project-license-circuit-breaker",
    domain="Demo / License Circuit Breaker",
)


@pytest.fixture(autouse=True)
def offline_env(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("APP_ENV", "offline")
    monkeypatch.setenv("APP_STATE_DIR", str(state))
    monkeypatch.setenv("DATAHUB_GMS_URL", "http://localhost:8080")
    monkeypatch.setenv("DATAHUB_MCP_URL", "http://localhost:8000/mcp")
    monkeypatch.setenv("DATAHUB_TOKEN", "fixture-token")
    cli.get_settings.cache_clear()
    yield
    cli.get_settings.cache_clear()


def _patch_client(monkeypatch, client):
    monkeypatch.setattr(cli, "build_client", lambda settings: client)


@pytest.fixture
def seeded() -> FakeDataHubClient:
    client = FakeDataHubClient(namespace=NS)
    seed(client, NS)
    return client


class TestSliceExitCodes:
    def test_clean_slice_exits_zero(self, monkeypatch, seeded):
        _patch_client(monkeypatch, seeded)
        assert cli.main(["slice"]) == 0

    def test_unrestored_writeback_exits_nonzero(self, monkeypatch, seeded):
        # The write landed and rollback did not apply. The plan is fine; the run
        # is not, because the shared instance may retain state.
        seeded.swallow_restore = True
        _patch_client(monkeypatch, seeded)
        assert cli.main(["slice"]) != 0

    def test_unverified_writeback_exits_nonzero(self, monkeypatch, seeded):
        seeded.fail_next_write = True
        _patch_client(monkeypatch, seeded)
        assert cli.main(["slice"]) != 0

    def test_verification_read_failure_exits_nonzero(self, monkeypatch, seeded):
        seeded.fail_verify_read = True
        _patch_client(monkeypatch, seeded)
        assert cli.main(["slice"]) != 0

    def test_failure_is_reported_on_stderr(self, monkeypatch, seeded, capsys):
        seeded.swallow_restore = True
        _patch_client(monkeypatch, seeded)
        cli.main(["slice"])
        captured = capsys.readouterr()
        assert "FAILED" in captured.err or "RESIDUAL" in captured.err


class TestSeedExitCodes:
    def test_seed_exits_zero_when_verified(self, monkeypatch):
        client = FakeDataHubClient(namespace=NS)
        _patch_client(monkeypatch, client)
        assert cli.main(["seed"]) == 0

    def test_seed_reports_verified_counts(self, monkeypatch, capsys):
        client = FakeDataHubClient(namespace=NS)
        _patch_client(monkeypatch, client)
        cli.main(["seed"])
        assert "Verified:" in capsys.readouterr().out


class TestResetExitCodes:
    def test_reset_without_sentinel_exits_nonzero(self, monkeypatch):
        client = FakeDataHubClient(namespace=NS)
        _patch_client(monkeypatch, client)
        assert cli.main(["reset"]) == 2

    def test_reset_of_a_seeded_graph_exits_zero(self, monkeypatch, seeded):
        _patch_client(monkeypatch, seeded)
        assert cli.main(["reset"]) == 0

    def test_partial_target_set_exits_nonzero(self, monkeypatch, seeded):
        del seeded.entities[NODES[2].urn]
        _patch_client(monkeypatch, seeded)
        assert cli.main(["reset"]) != 0

    def test_restore_after_reset_exits_zero(self, monkeypatch, seeded):
        _patch_client(monkeypatch, seeded)
        assert cli.main(["reset"]) == 0
        assert cli.main(["restore"]) == 0
        assert seeded.get_entity(SENTINEL_URN).active


class TestVerifyCommand:
    def test_intact_ledger_exits_zero(self, monkeypatch, seeded):
        _patch_client(monkeypatch, seeded)
        cli.main(["slice"])
        assert cli.main(["verify"]) == 0


class TestSimulationLabelling:
    def test_offline_runs_are_labelled_simulated(self, monkeypatch, seeded, capsys):
        _patch_client(monkeypatch, seeded)
        cli.main(["slice"])
        assert "SIMULATED" in capsys.readouterr().out
