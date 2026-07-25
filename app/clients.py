"""DataHub client construction.

``APP_ENV=offline`` selects the deterministic in-memory fake so the vertical slice
can be demonstrated without the shared instance. Anything produced in that mode is
marked ``simulated`` in receipts and must never be presented as live DataHub
evidence.
"""

from __future__ import annotations

from adapters.datahub import DataHubClient, LiveDataHubClient
from adapters.fake_datahub import FakeDataHubClient
from app.config import Settings

OFFLINE_ENVS = frozenset({"offline", "test"})


def is_offline(settings: Settings) -> bool:
    """Whether this process is running against the in-memory fake."""
    return settings.app_env.lower() in OFFLINE_ENVS


def build_client(settings: Settings) -> DataHubClient:
    """Construct the client this environment should use.

    Raises:
        DataHubError: if live configuration is incomplete. Readiness surfaces this
            as a failed check rather than letting a half-configured client through.
    """
    if is_offline(settings):
        return build_offline_client(settings)

    return LiveDataHubClient(
        gms_url=settings.datahub_gms_url,
        mcp_url=settings.datahub_mcp_url,
        token=settings.datahub_token,
        namespace=settings.namespace,
    )


def build_offline_client(settings: Settings, seeded: bool = True) -> FakeDataHubClient:
    """Build the in-memory client, optionally pre-seeded with the demo graph."""
    from demo.seed import seed

    client = FakeDataHubClient(namespace=settings.namespace)
    if seeded:
        seed(client, settings.namespace)
    return client
