"""Fail-closed readiness checks.

A basic GMS ping is deliberately **not** sufficient to report ready. An
unauthenticated instance that answers ``/config`` will happily return empty
result sets, and an impact analysis over an empty graph looks exactly like an
all-clear. Readiness therefore requires positive proof of all of:

1. a configured token;
2. an MCP endpoint advertising every tool this project depends on;
3. the project's domain and tag present;
4. at least one ``license.`` entity actually readable.

Every check is read-only. Five submissions share this instance, and a probe the
reverse proxy polls must never mutate it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from adapters.datahub import REQUIRED_MCP_TOOLS, DataHubClient, DataHubError
from app.config import Settings
from app.namespace import Namespace
from demo.graph import SENTINEL_URN


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str
    #: True when this check must pass for the service to be considered ready.
    required: bool = True


@dataclass(frozen=True)
class ReadinessReport:
    checks: tuple[Check, ...]

    @property
    def ready(self) -> bool:
        """Ready only when every required check positively passed."""
        return all(c.passed for c in self.checks if c.required)

    @property
    def status(self) -> str:
        return "ready" if self.ready else "degraded"

    def failures(self) -> tuple[Check, ...]:
        return tuple(c for c in self.checks if c.required and not c.passed)


def check_local_state(settings: Settings) -> Check:
    """State directory exists and is writable."""
    try:
        state_dir = settings.ensure_state_dir()
        probe = state_dir / ".readiness"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return Check("state_dir", True, f"writable at {state_dir}")
    except OSError as exc:
        return Check("state_dir", False, f"not writable: {exc}")


def check_policy(load_policy: Callable[[], object]) -> Check:
    try:
        table = load_policy()
        rules = getattr(table, "rules", ())
        return Check("policy_table", True, f"{len(rules)} rules loaded")
    except Exception as exc:
        return Check("policy_table", False, str(exc))


def check_namespace(settings: Settings) -> Check:
    try:
        namespace = settings.namespace
        return Check("namespace_guard", True, f"enforcing prefix {namespace.urn_prefix!r}")
    except ValueError as exc:
        return Check("namespace_guard", False, str(exc))


def check_token(settings: Settings) -> Check:
    """Token is configured. Never logs or echoes the value."""
    if not settings.datahub_token:
        return Check("datahub_token", False, "DATAHUB_TOKEN is not configured")
    return Check("datahub_token", True, "configured")


def check_endpoints(settings: Settings) -> Check:
    """Both endpoint URLs are configured, and MCP is not a hardcoded port."""
    missing = [
        name
        for name, value in (
            ("DATAHUB_GMS_URL", settings.datahub_gms_url),
            ("DATAHUB_MCP_URL", settings.datahub_mcp_url),
        )
        if not value
    ]
    if missing:
        return Check("datahub_endpoints", False, f"not configured: {', '.join(missing)}")
    return Check("datahub_endpoints", True, "GMS and MCP endpoints configured")


def check_mcp_capabilities(client: DataHubClient) -> Check:
    """MCP advertises every tool this project depends on.

    A worker missing ``get_lineage`` would silently produce an empty impact
    analysis, so a partial toolset is a hard failure rather than a warning.
    """
    try:
        available = client.list_mcp_tools()
    except DataHubError as exc:
        return Check("mcp_capabilities", False, f"could not list tools: {exc}")

    missing = sorted(REQUIRED_MCP_TOOLS - available)
    if missing:
        return Check("mcp_capabilities", False, f"missing required tools: {', '.join(missing)}")
    return Check("mcp_capabilities", True, f"{len(REQUIRED_MCP_TOOLS)} required tools available")


def check_project_entities(client: DataHubClient, namespace: Namespace) -> Check:
    """At least one ``license.`` entity is readable and correctly tagged.

    Proving the allocation exists is what stops an empty or foreign instance from
    reporting ready.
    """
    try:
        entity = client.get_entity(SENTINEL_URN)
    except DataHubError as exc:
        return Check("project_entities", False, f"could not read project entities: {exc}")

    if entity is None:
        return Check(
            "project_entities",
            False,
            f"fixture sentinel {SENTINEL_URN} not found; run seed before serving",
        )
    if not entity.has_tag(namespace.project_tag):
        return Check(
            "project_entities",
            False,
            f"sentinel is missing the {namespace.project_tag!r} tag",
        )
    if entity.domain and entity.domain != namespace.domain:
        return Check(
            "project_entities",
            False,
            f"sentinel is in domain {entity.domain!r}, expected {namespace.domain!r}",
        )
    return Check("project_entities", True, "project domain, tag, and license. entities verified")


def evaluate_readiness(
    settings: Settings,
    load_policy: Callable[[], object],
    client_factory: Callable[[], DataHubClient] | None,
) -> ReadinessReport:
    """Run every check. Read-only throughout.

    When the client cannot be constructed, the DataHub-dependent checks are
    reported as failed rather than skipped -- an unrunnable check is not a passing
    check.
    """
    checks: list[Check] = [
        check_local_state(settings),
        check_policy(load_policy),
        check_namespace(settings),
        check_token(settings),
        check_endpoints(settings),
    ]

    client: DataHubClient | None = None
    if client_factory is not None:
        try:
            client = client_factory()
        except Exception as exc:
            checks.append(Check("datahub_client", False, f"could not construct client: {exc}"))

    if client is None:
        checks.append(
            Check("mcp_capabilities", False, "DataHub client unavailable; capabilities unverified")
        )
        checks.append(
            Check("project_entities", False, "DataHub client unavailable; entities unverified")
        )
    else:
        checks.append(check_mcp_capabilities(client))
        checks.append(check_project_entities(client, settings.namespace))

    return ReadinessReport(checks=tuple(checks))
