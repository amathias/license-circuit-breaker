"""What may reach a public repository.

This repository is published. These tests scan the exact file set that would
ship -- ``git ls-files --cached --others --exclude-standard``, which is tracked
files plus untracked files that ``.gitignore`` does not exclude -- and fail on
anything that should not leave the machine it was built on.

The scan is over the working tree rather than a commit, so a leak is caught
before it is committed rather than after it is pushed.

Two of these are here because the leak already happened once. A captured
evidence report was generated into a temporary directory, and the absolute path
of that directory -- including the developer's home directory name -- was
embedded in the report body and in the DataHub custom properties it recorded.
Nothing failed. It was only noticed by reading the file.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

#: Extensions worth scanning. Lockfiles and binaries carry no prose and are
#: large enough to make the scan slow for no benefit.
TEXT_SUFFIXES = {".py", ".md", ".ts", ".tsx", ".json", ".yaml", ".yml", ".toml", ".html",
                 ".css", ".txt", ".cfg", ".ini", ".example"}

SKIP_NAMES = {"package-lock.json", "LICENSE"}

#: Credential shapes. Deliberately narrow: a pattern that fires on the word
#: "token" would be muted within a week and would then be protecting nothing.
CREDENTIAL_PATTERNS = (
    re.compile(r"DATAHUB_(GMS_)?TOKEN\s*[=:]\s*['\"]?[A-Za-z0-9._\-]{12,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\."),
    re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{20,}"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
)

#: Test fixtures must announce themselves. Allowing a value only when it says
#: ``fixture-`` in the value itself keeps the suite scannable without punching a
#: hole for ``tests/``: a real token pasted into a test is published just as
#: surely as one pasted into ``app/``.
FIXTURE_MARKER = re.compile(r"fixture-|not-a-real-secret|example\.invalid")

#: Absolute paths that name a person's account on the machine that built the
#: file. ``C:\Users\Public`` and ``/home/runner`` are not identifying.
HOME_PATH_PATTERN = re.compile(
    r"(?:[A-Za-z]:[\\/]+Users[\\/]+|/Users/|/home/)(?!Public\b|Default\b|runner\b|user\b)"
    r"[A-Za-z0-9._-]+",
)


def _shippable_files() -> list[Path]:
    """Exactly the files a clean clone of this working tree would contain."""
    # S607: `git` is resolved from PATH deliberately. Every argument is a
    # constant, no shell is used, and pinning an absolute path would make the
    # test fail on any machine with a different git install.
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],  # noqa: S607
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"git ls-files failed: {result.stderr}"
    names = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    assert names, "git ls-files returned nothing"
    return [REPO / name for name in names]


def _scannable() -> list[Path]:
    return [
        path
        for path in _shippable_files()
        if path.is_file() and path.suffix in TEXT_SUFFIXES and path.name not in SKIP_NAMES
    ]


@pytest.fixture(scope="module")
def scannable() -> list[Path]:
    found = _scannable()
    assert found, "no scannable files were found; the file list is probably wrong"
    return found


class TestNothingSecretShips:
    def test_no_env_file_is_shippable(self):
        names = {path.name for path in _shippable_files()}
        assert ".env" not in names, ".env is not gitignored and would be published"

    def test_no_runtime_state_is_shippable(self):
        for path in _shippable_files():
            relative = path.relative_to(REPO).as_posix()
            assert not relative.startswith(".state/"), f"runtime state would ship: {relative}"
            assert not relative.startswith(".venv/"), f"the virtualenv would ship: {relative}"
            assert "node_modules/" not in relative, f"node_modules would ship: {relative}"
            assert not relative.startswith("web/dist/"), f"build output would ship: {relative}"

    def test_no_credential_shaped_string_ships(self, scannable):
        offenders: list[str] = []
        for path in scannable:
            text = path.read_text(encoding="utf-8", errors="replace")
            for pattern in CREDENTIAL_PATTERNS:
                for match in pattern.finditer(text):
                    if FIXTURE_MARKER.search(match.group(0)):
                        continue
                    offenders.append(f"{path.relative_to(REPO)}: {match.group(0)[:24]}...")
        assert not offenders, "credential-shaped strings found:\n" + "\n".join(offenders)

    def test_the_env_template_carries_names_but_no_values(self):
        template = (REPO / ".env.example").read_text(encoding="utf-8")
        assert "DATAHUB_TOKEN=" in template, "the template must still declare the variable"
        for line in template.splitlines():
            if line.startswith("DATAHUB_TOKEN="):
                assert line.strip() == "DATAHUB_TOKEN=", "the template carries a token value"


class TestNothingIdentifyingShips:
    def test_no_home_directory_path_ships(self, scannable):
        # A generated artifact that embeds an absolute path publishes the
        # account name of whoever generated it. Evidence reports are the usual
        # source: they record where they were written.
        offenders: list[str] = []
        for path in scannable:
            text = path.read_text(encoding="utf-8", errors="replace")
            for match in HOME_PATH_PATTERN.finditer(text):
                offenders.append(f"{path.relative_to(REPO)}: {match.group(0)}")
        assert not offenders, "home-directory paths found:\n" + "\n".join(offenders)


class TestPublicationRequirements:
    def test_an_apache_license_is_present(self):
        text = (REPO / "LICENSE").read_text(encoding="utf-8")
        assert "Apache License" in text
        assert "Version 2.0" in text

    def test_the_readme_disclaims_legal_advice(self):
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        assert "does not provide legal advice" in readme

    def test_the_readme_does_not_claim_live_datahub_verification(self):
        # Nothing in this repository has been run against a live instance. A
        # README that implied otherwise would be the single most damaging
        # untrue claim it could make.
        readme = (REPO / "README.md").read_text(encoding="utf-8").lower()
        assert "no live datahub evidence" in readme

    def test_every_committed_example_is_labelled_simulated(self):
        report = REPO / "examples" / "containment-report.md"
        assert report.is_file(), "the captured containment report is missing"
        assert "SIMULATED DATAHUB RUN" in report.read_text(encoding="utf-8")
