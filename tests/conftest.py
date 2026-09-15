"""Keep every test independent of developer credentials and runtime state."""

from __future__ import annotations

import os

import pytest

from app.config import Settings, reset_settings_cache

# Apply before test modules import the ASGI application. Tests which exercise
# dotenv loading supply an explicit temporary _env_file instead.
Settings.model_config["env_file"] = None
for _field in Settings.model_fields.values():
    os.environ.pop(str(_field.alias), None)
os.environ["APP_ENV"] = "local"


@pytest.fixture(autouse=True)
def isolated_configuration(monkeypatch, tmp_path):
    import app.api as api

    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("APP_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("DATAHUB_GMS_URL", "")
    monkeypatch.setenv("DATAHUB_MCP_URL", "")
    monkeypatch.setenv("DATAHUB_TOKEN", "")
    reset_settings_cache()
    monkeypatch.setattr(api, "_client", None)
    monkeypatch.setattr(api, "_client_key", None)
    api._demo_guard.reset()
    yield
    reset_settings_cache()
