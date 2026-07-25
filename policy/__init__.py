"""Deterministic policy rule table.

A package so ``rules.yaml`` ships as package data. Resolving it by walking up
from ``app/policy.py`` worked from a source checkout and broke in an installed
archive, where no ``policy/`` directory sits beside the package.
"""

from __future__ import annotations

from pathlib import Path


def rules_path() -> Path:
    """Absolute path to the rule table, in a source tree or an installed archive."""
    return Path(__file__).resolve().parent / "rules.yaml"
