"""Clean-archive packaging regressions.

``pyproject.toml`` previously declared only ``app`` and ``adapters``. A wheel built
from that omitted ``demo`` (the fixture graph and the seed/slice CLI) and
``policy`` (the rule table), so an installed archive imported but could not seed,
slice, or evaluate a single rule. Nothing in the source-tree test suite noticed,
because the source tree has those directories on ``sys.path`` regardless.

The archive is assembled from ``git ls-files --cached --others --exclude-standard``:
exactly the files that would ship, honouring ``.gitignore`` so ``.env``, ``.state``,
and ``.venv`` are excluded. Using the file list rather than ``git archive HEAD``
means the test validates the tree being worked on, instead of failing on every
packaging fix until it happens to be committed.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _run(cmd: list[str], cwd: Path | None = None, timeout: int = 900):
    # S603: every argument list here is built from constants and pytest tmp_path
    # values -- no user or network input reaches it. A shell is never used.
    return subprocess.run(  # noqa: S603
        cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False
    )


def _export_tree(destination: Path) -> Path:
    """Copy every shippable file into ``destination``.

    Mirrors what a clean checkout would contain: tracked files plus untracked
    files that are not gitignored.
    """
    result = _run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=REPO
    )
    assert result.returncode == 0, f"git ls-files failed: {result.stderr}"

    files = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    assert files, "git ls-files returned nothing"

    destination.mkdir(parents=True, exist_ok=True)
    for relative in files:
        source = REPO / relative
        if not source.is_file():
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return destination


def _isolated_python(venv: Path) -> Path:
    return venv / ("Scripts" if sys.platform == "win32" else "bin") / (
        "python.exe" if sys.platform == "win32" else "python"
    )


class TestPackageDeclaration:
    """Fast checks that do not need a build."""

    def test_declares_every_runtime_package(self):
        content = (REPO / "pyproject.toml").read_text(encoding="utf-8")
        for package in ("app", "adapters", "demo", "policy"):
            assert f'"{package}"' in content, f"{package} is not declared in packages"

    def test_declares_live_datahub_dependencies(self):
        content = (REPO / "pyproject.toml").read_text(encoding="utf-8")
        assert '"acryl-datahub==1.6.0.15"' in content
        assert '"mcp==1.28.1"' in content

    def test_ships_the_rule_table_as_package_data(self):
        content = (REPO / "pyproject.toml").read_text(encoding="utf-8")
        assert "package-data" in content
        assert "*.yaml" in content

    def test_ships_the_entity_registry_as_package_data(self):
        # A wheel without it builds proposals it cannot check, which is exactly
        # the failure the snapshot exists to prevent.
        content = (REPO / "pyproject.toml").read_text(encoding="utf-8")
        assert "adapters = [" in content
        assert "*.json" in content

    def test_declares_a_console_script(self):
        content = (REPO / "pyproject.toml").read_text(encoding="utf-8")
        assert "[project.scripts]" in content

    def test_rule_table_resolves_through_the_package(self):
        # This is what breaks in an installed archive when policy/ is not a
        # package: the loader walks up from app/ and finds nothing.
        from policy import rules_path

        assert rules_path().is_file()

    def test_entity_registry_resolves_through_the_package(self):
        from adapters.entity_registry import REGISTRY_PATH, get_registry

        assert REGISTRY_PATH.is_file()
        assert get_registry().supports("dataset", "datasetProperties")


@pytest.mark.slow
class TestCleanArchiveInstall:
    """Build from a git archive and install into an isolated environment."""

    def test_archive_installs_and_exposes_the_cli(self, tmp_path):
        if shutil.which("git") is None:  # pragma: no cover
            pytest.skip("git is unavailable")

        # 1. Export exactly what would ship.
        src = _export_tree(tmp_path / "src")

        # Secrets and runtime state must never reach the archive.
        assert not (src / ".env").exists(), ".env leaked into the archive"
        assert not (src / ".state").exists(), "runtime receipts leaked into the archive"
        assert not (src / ".venv").exists(), "virtualenv leaked into the archive"

        # The archive must contain the packages, not just the source tree.
        for package in ("app", "adapters", "demo", "policy"):
            assert (src / package).is_dir(), f"{package} missing from the archive"
        assert (src / "policy" / "rules.yaml").is_file(), "rule table missing from the archive"
        assert (
            src / "adapters" / "datahub_entity_registry_1_6_0.json"
        ).is_file(), "entity/aspect registry snapshot missing from the archive"

        # 2. Install into an isolated environment.
        venv = tmp_path / "venv"
        result = _run([sys.executable, "-m", "venv", str(venv)])
        assert result.returncode == 0, f"venv creation failed: {result.stderr}"

        python = _isolated_python(venv)
        result = _run([str(python), "-m", "pip", "install", "--quiet", str(src)])
        assert result.returncode == 0, f"install failed:\n{result.stdout}\n{result.stderr}"

        # 3. Prove the packages import from the installed location, not the repo.
        probe = (
            "import app, adapters, demo, policy;"
            "from app.policy import load_policy;"
            "from policy import rules_path;"
            "from demo.cli import main;"
            "from demo.graph import SENTINEL_URN;"
            "from adapters.entity_registry import get_registry;"
            "t = load_policy();"
            "assert len(t.rules) > 0, 'no rules loaded';"
            "assert rules_path().is_file(), 'rule table not installed';"
            "r = get_registry();"
            "assert r.supports('dataset', 'datasetProperties'), 'registry not installed';"
            "assert not r.supports('mlModel', 'datasetProperties'), 'registry is wrong';"
            "print('IMPORT_OK', len(t.rules))"
        )
        result = _run([str(python), "-c", probe], cwd=tmp_path)
        assert result.returncode == 0, f"import probe failed:\n{result.stdout}\n{result.stderr}"
        assert "IMPORT_OK" in result.stdout

        # 4. Prove the seed/slice CLI is available and runnable.
        result = _run([str(python), "-m", "demo.cli", "--help"], cwd=tmp_path)
        assert result.returncode == 0, f"CLI unavailable:\n{result.stderr}"
        for command in ("seed", "reset", "slice", "verify"):
            assert command in result.stdout, f"CLI is missing the {command!r} command"

        # 5. Prove the console-script entry point was installed.
        script = venv / ("Scripts" if sys.platform == "win32" else "bin") / (
            "license-circuit-breaker.exe" if sys.platform == "win32" else "license-circuit-breaker"
        )
        assert script.exists(), "console script was not installed"

    def test_offline_slice_runs_from_the_installed_archive(self, tmp_path):
        """The installed archive can actually run the slice end to end."""
        if shutil.which("git") is None:  # pragma: no cover
            pytest.skip("git is unavailable")

        src = _export_tree(tmp_path / "src")

        venv = tmp_path / "venv"
        assert _run([sys.executable, "-m", "venv", str(venv)]).returncode == 0
        python = _isolated_python(venv)
        result = _run([str(python), "-m", "pip", "install", "--quiet", str(src)])
        assert result.returncode == 0, f"install failed:\n{result.stderr}"

        state = tmp_path / "state"
        state.mkdir()
        env_script = (
            "import os;"
            f"os.environ['APP_ENV']='offline';"
            f"os.environ['APP_STATE_DIR']=r'{state}';"
            "from demo.cli import main;"
            "raise SystemExit(main(['slice']))"
        )
        result = _run([str(python), "-c", env_script], cwd=tmp_path)
        assert result.returncode == 0, f"slice failed:\n{result.stdout}\n{result.stderr}"
        assert "SIMULATED" in result.stdout
