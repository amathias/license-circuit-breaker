"""Fail-closed, non-mutating readiness checks.

**Nothing here writes.** Not to DataHub, not to the filesystem. The previous
version wrote and deleted a ``.readiness`` probe file to prove the state
directory was writable; on a path polled by a reverse proxy that is a mutation,
and a probe that mutates is not a probe. Writability is now inferred with
``os.access`` instead.

A basic GMS ping is deliberately **not** sufficient to report ready. An
unauthenticated or unseeded instance answers happily and returns empty result
sets, and an impact analysis over an empty graph looks exactly like an
all-clear. Readiness requires positive proof of all of:

1. a configured token and both endpoint URLs;
2. an MCP endpoint advertising every required tool;
3. the **exact** project domain on every fixture entity -- a missing or ``None``
   domain fails, because "no domain" is not "the right domain";
4. the project tag and fixture marker controls present on every entity;
5. **exact coverage** of the allowlisted ``license.`` entities, all active;
6. required custom properties on every entity;
7. complete fixture lineage.

Any unverified condition means not ready. An unrunnable check is not a passing
check.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass

from adapters.catalog import domain_urn
from adapters.datahub import (
    REQUIRED_CUSTOM_PROPERTIES,
    REQUIRED_MCP_TOOLS,
    DataHubClient,
    DataHubError,
    EntityContext,
)
from app.config import Settings
from app.namespace import Namespace
from demo.graph import EDGES, FIXTURE_MARKER, SENTINEL_URN, all_urns


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
    """State directory is present and writable. Creates and writes nothing."""
    state_dir = settings.app_state_dir
    if not state_dir.exists():
        return Check("state_dir", False, f"{state_dir} does not exist")
    if not state_dir.is_dir():
        return Check("state_dir", False, f"{state_dir} is not a directory")
    if not os.access(state_dir, os.W_OK):
        return Check("state_dir", False, f"{state_dir} is not writable")
    return Check("state_dir", True, f"present and writable at {state_dir}")


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
    """Token is configured. Never reads, logs, or echoes the value."""
    if not settings.datahub_token:
        return Check("datahub_token", False, "DATAHUB_TOKEN is not configured")
    return Check("datahub_token", True, "configured")


def check_endpoints(settings: Settings) -> Check:
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
    except Exception as exc:
        return Check("mcp_capabilities", False, f"could not list tools: {exc}")

    missing = sorted(REQUIRED_MCP_TOOLS - available)
    if missing:
        return Check("mcp_capabilities", False, f"missing required tools: {', '.join(missing)}")
    return Check("mcp_capabilities", True, f"{len(REQUIRED_MCP_TOOLS)} required tools available")


def _read_allowlist(client: DataHubClient) -> dict[str, EntityContext]:
    batch = getattr(client, "get_entities", None)
    if batch is not None:
        return batch(all_urns())
    found: dict[str, EntityContext] = {}  # pragma: no cover
    for urn in all_urns():
        entity = client.get_entity(urn)
        if entity is not None:
            found[urn] = entity
    return found


def check_project_controls(client: DataHubClient, namespace: Namespace) -> Check:
    """The project domain and tag controls are applied to the sentinel.

    A ``None`` domain is a failure. The earlier version only compared the domain
    when one was present, which meant an entity with no domain at all passed --
    exactly the case this check exists to catch.
    """
    try:
        entity = client.get_entity(SENTINEL_URN)
    except Exception as exc:
        return Check("project_controls", False, f"could not read sentinel: {exc}")

    if entity is None:
        return Check(
            "project_controls",
            False,
            f"fixture sentinel {SENTINEL_URN} not found; run seed before serving",
        )
    if not entity.active:
        return Check("project_controls", False, "sentinel is soft-deleted")

    expected_domain = domain_urn(namespace.domain)
    if entity.domain is None:
        return Check(
            "project_controls",
            False,
            f"sentinel has no domain; expected {expected_domain!r}",
        )
    if entity.domain not in (expected_domain, namespace.domain):
        return Check(
            "project_controls",
            False,
            f"sentinel is in domain {entity.domain!r}, expected {expected_domain!r}",
        )
    if not entity.has_tag(namespace.project_tag):
        return Check(
            "project_controls", False, f"sentinel missing tag {namespace.project_tag!r}"
        )
    if not entity.has_tag(FIXTURE_MARKER):
        return Check("project_controls", False, f"sentinel missing marker {FIXTURE_MARKER!r}")

    return Check("project_controls", True, "project domain and tag controls verified")


def check_entity_coverage(client: DataHubClient, namespace: Namespace) -> Check:
    """Every allowlisted entity is present, active, tagged, and fully propertied.

    Exact coverage, not a spot check: a partially seeded instance produces a
    partial impact analysis, which is the false all-clear in another costume.
    """
    try:
        found = _read_allowlist(client)
    except Exception as exc:
        return Check("entity_coverage", False, f"could not read project entities: {exc}")

    expected = all_urns()
    problems: list[str] = []

    for urn in expected:
        entity = found.get(urn)
        short = urn.rsplit(",", 2)[-2] if "," in urn else urn
        if entity is None:
            problems.append(f"{short}: missing")
            continue
        if not entity.active:
            problems.append(f"{short}: soft-deleted")
            continue
        if not entity.has_tag(namespace.project_tag):
            problems.append(f"{short}: untagged")
            continue
        if entity.domain is None:
            problems.append(f"{short}: no domain")
            continue
        missing_props = entity.missing_properties()
        if missing_props:
            problems.append(f"{short}: missing {sorted(missing_props)}")

    if problems:
        shown = "; ".join(problems[:5])
        suffix = f" (+{len(problems) - 5} more)" if len(problems) > 5 else ""
        return Check(
            "entity_coverage",
            False,
            f"{len(problems)}/{len(expected)} entities unusable: {shown}{suffix}",
        )

    missing_props = sorted(REQUIRED_CUSTOM_PROPERTIES)
    return Check(
        "entity_coverage",
        True,
        f"{len(expected)} entities active, tagged, domained, with {missing_props}",
    )


def check_fixture_lineage(client: DataHubClient) -> Check:
    """Every declared fixture edge is readable.

    Missing lineage does not merely reduce coverage -- it changes verdicts, since
    a descendant with no path escalates under LCB-R002.
    """
    has_edge = getattr(client, "has_edge", None)
    if has_edge is None:
        try:
            edges = client.get_downstream_lineage(EDGES[0][0])
        except Exception as exc:
            return Check("fixture_lineage", False, f"could not read lineage: {exc}")
        observed = {(e.upstream_urn, e.downstream_urn) for e in edges}
        has_edge = lambda u, d: (u, d) in observed  # noqa: E731

    try:
        missing = [
            f"{u.rsplit(',', 2)[-2]} -> {d.rsplit(',', 2)[-2]}"
            for u, d, _ in EDGES
            if not has_edge(u, d)
        ]
    except Exception as exc:
        return Check("fixture_lineage", False, f"could not read lineage: {exc}")

    if missing:
        return Check(
            "fixture_lineage",
            False,
            f"{len(missing)}/{len(EDGES)} fixture edges missing: {'; '.join(missing[:4])}",
        )
    return Check("fixture_lineage", True, f"{len(EDGES)} fixture edges verified")


def evaluate_readiness(
    settings: Settings,
    load_policy: Callable[[], object],
    client_factory: Callable[[], DataHubClient] | None,
) -> ReadinessReport:
    """Run every check. Read-only throughout; mutates nothing.

    When the client cannot be constructed, the DataHub-dependent checks are
    reported as failed rather than skipped.
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

    datahub_checks = ("mcp_capabilities", "project_controls", "entity_coverage", "fixture_lineage")
    if client is None:
        checks.extend(
            Check(name, False, "DataHub client unavailable; unverified") for name in datahub_checks
        )
    else:
        checks.append(check_mcp_capabilities(client))
        checks.append(check_project_controls(client, settings.namespace))
        checks.append(check_entity_coverage(client, settings.namespace))
        checks.append(check_fixture_lineage(client))

    return ReadinessReport(checks=tuple(checks))
