"""Shared environment contract for License Circuit Breaker.

The variable names here are portfolio-wide and owned by the coordinator
(``../COORDINATOR_PLAN.md``). Do not rename them from this project chat; propose
the change to the coordinator instead.

The deployed state root is a POSIX path, so nothing may hardcode it -- local
Windows development sets ``APP_STATE_DIR`` to somewhere writable and everything
else derives from that.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.namespace import Namespace

# Coordinator-assigned allocation. These are defaults, not suggestions: the
# values must match ../COORDINATOR_PLAN.md and COORDINATOR_HANDOFF.md.
PROJECT_SLUG = "license-circuit-breaker"
INTERNAL_PORT = 8102
DATAHUB_DOMAIN = "Demo / License Circuit Breaker"
DATAHUB_PROJECT_TAG = "project-license-circuit-breaker"
DATAHUB_URN_PREFIX = "license."


class Settings(BaseSettings):
    """Runtime configuration, populated from the environment or a .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    project_slug: str = Field(default=PROJECT_SLUG, alias="PROJECT_SLUG")
    app_env: str = Field(default="local", alias="APP_ENV")
    app_host: str = Field(default="127.0.0.1", alias="APP_HOST")
    app_port: int = Field(default=INTERNAL_PORT, alias="APP_PORT")
    app_public_url: str = Field(default="", alias="APP_PUBLIC_URL")
    app_state_dir: Path = Field(default=Path(".state"), alias="APP_STATE_DIR")

    datahub_gms_url: str = Field(default="", alias="DATAHUB_GMS_URL")
    datahub_mcp_url: str = Field(default="", alias="DATAHUB_MCP_URL")
    datahub_token: str = Field(default="", alias="DATAHUB_TOKEN")

    datahub_domain: str = Field(default=DATAHUB_DOMAIN, alias="DATAHUB_DOMAIN")
    datahub_project_tag: str = Field(default=DATAHUB_PROJECT_TAG, alias="DATAHUB_PROJECT_TAG")
    datahub_urn_prefix: str = Field(default=DATAHUB_URN_PREFIX, alias="DATAHUB_URN_PREFIX")

    demo_fixture_root: Path = Field(
        default=Path("demo/fixtures/license-circuit-breaker"), alias="DEMO_FIXTURE_ROOT"
    )

    @field_validator("datahub_urn_prefix")
    @classmethod
    def _prefix_must_not_be_empty(cls, value: str) -> str:
        # An empty prefix would make every namespace check pass, silently removing
        # the only thing protecting the other four submissions.
        if not value.strip():
            raise ValueError("DATAHUB_URN_PREFIX must not be empty")
        return value

    @property
    def namespace(self) -> Namespace:
        """This project's allocation, as enforced by the namespace guard."""
        return Namespace(
            project_slug=self.project_slug,
            urn_prefix=self.datahub_urn_prefix,
            project_tag=self.datahub_project_tag,
            domain=self.datahub_domain,
        )

    @property
    def datahub_configured(self) -> bool:
        """Whether enough DataHub configuration exists to attempt a connection."""
        return bool(self.datahub_gms_url and self.datahub_token)

    def datahub_env(self) -> dict[str, str]:
        """Environment for DataHub tooling launched as a subprocess.

        The shared contract names the token ``DATAHUB_TOKEN``; ``mcp-server-datahub``
        reads ``DATAHUB_GMS_TOKEN``. Mapping here keeps the shared contract intact
        rather than renaming it for one project's dependency. Pending coordinator
        confirmation -- see IMPLEMENTATION_PLAN.md open item 2.
        """
        return {
            "DATAHUB_GMS_URL": self.datahub_gms_url,
            "DATAHUB_GMS_TOKEN": self.datahub_token,
        }

    def ensure_state_dir(self) -> Path:
        """Create and return the state directory."""
        self.app_state_dir.mkdir(parents=True, exist_ok=True)
        return self.app_state_dir


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached settings. Call ``get_settings.cache_clear()`` in tests."""
    return Settings()


def reset_settings_cache() -> None:
    """Drop cached settings so the next call re-reads the environment."""
    get_settings.cache_clear()


__all__ = [
    "DATAHUB_DOMAIN",
    "DATAHUB_PROJECT_TAG",
    "DATAHUB_URN_PREFIX",
    "INTERNAL_PORT",
    "PROJECT_SLUG",
    "Settings",
    "get_settings",
    "reset_settings_cache",
]
