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
from demo.estate import EstatePaths, build_estate, estate_status
from demo.graph import NODES, SENTINEL_URN
from demo.seed import seed
from demo.serving import ServingRefused, predict

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


class TestContainCommand:
    """The exit codes the README tells a judge to expect.

    ``contain`` is the one command the quickstart asks anyone to run, and its
    exit codes are documented rather than incidental: 8 means the gate refused
    and nothing was touched, 9 means the run completed and the verdict is short
    of contained. Both are load-bearing claims in the README, so both are
    asserted here rather than left to a subprocess test that measures nothing.
    """

    @pytest.fixture
    def estate(self):
        build_estate(EstatePaths.under(cli.get_settings().ensure_state_dir()))

    def test_without_an_approval_the_gate_refuses(self, monkeypatch, seeded, estate):
        _patch_client(monkeypatch, seeded)
        assert cli.main(["contain"]) == 8

    def test_a_refused_run_touches_nothing(self, monkeypatch, seeded, estate, capsys):
        _patch_client(monkeypatch, seeded)
        paths = EstatePaths.under(cli.get_settings().ensure_state_dir())
        before = estate_status(paths)

        cli.main(["contain"])
        capsys.readouterr()

        # The plan is computed in full and the artifacts are left alone. A
        # refusal that had already purged the index would not be a refusal.
        assert estate_status(paths) == before
        assert predict(paths, "the battery lasts all weekend").model_version

    def test_the_plan_is_still_shown_when_the_gate_refuses(
        self, monkeypatch, seeded, estate, capsys
    ):
        _patch_client(monkeypatch, seeded)
        cli.main(["contain"])
        out = capsys.readouterr().out
        assert "LCB-R050" in out, "the plan was not shown before the refusal"
        assert "Re-run with --approve" in out

    def test_an_approved_run_escalates_rather_than_claiming_containment(
        self, monkeypatch, seeded, estate, capsys
    ):
        _patch_client(monkeypatch, seeded)
        # 9, not 0: one descendant is reachable only through a lineage path
        # DataHub cannot complete, so the run is honest about not being done.
        assert cli.main(["contain", "--approve"]) == 9
        out = capsys.readouterr().out
        assert "Verdict: ESCALATED" in out
        assert "license.reviews.legacy_snapshot" in out

    def test_an_approved_run_actually_stops_the_prohibited_serving(
        self, monkeypatch, seeded, estate
    ):
        _patch_client(monkeypatch, seeded)
        cli.main(["contain", "--approve"])

        paths = EstatePaths.under(cli.get_settings().ensure_state_dir())
        with pytest.raises(ServingRefused):
            predict(paths, "the battery lasts all weekend")

    def test_an_approved_run_writes_both_evidence_formats(
        self, monkeypatch, seeded, estate
    ):
        _patch_client(monkeypatch, seeded)
        cli.main(["contain", "--approve"])

        evidence = cli.get_settings().ensure_state_dir() / "evidence"
        reports = list(evidence.rglob("containment-report.md"))
        assert len(reports) == 1, f"expected one report, found {len(reports)}"
        assert (reports[0].parent / "containment-report.json").is_file()

        text = reports[0].read_text(encoding="utf-8")
        assert "SIMULATED DATAHUB RUN" in text
        # The report is rebuilt after the writeback so it carries the receipts.
        assert "## DataHub writeback" in text
        assert "'urn':" not in text, "receipts were rendered as a Python repr"

    def test_a_failing_adapter_downgrades_the_verdict(self, monkeypatch, seeded, estate, capsys):
        _patch_client(monkeypatch, seeded)
        assert cli.main(["contain", "--approve", "--fail-adapter", "export-quarantine"]) == 9
        assert "Verdict: RESIDUAL" in capsys.readouterr().out
