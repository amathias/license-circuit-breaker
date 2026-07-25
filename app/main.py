"""FastAPI application for License Circuit Breaker.

Serves the governance API on the coordinator-assigned internal port 8102.

``/api/health`` and ``/api/readiness`` are required by the shared contract in
``../COORDINATOR_PLAN.md``. Readiness verifies DataHub connectivity but must never
mutate shared state -- five submissions share one instance, and a readiness probe
that writes would corrupt other projects' demos every time the proxy polled it.
"""

from __future__ import annotations

import time
from typing import Any, Literal

import httpx
from fastapi import FastAPI
from pydantic import BaseModel

from app.config import Settings, get_settings
from app.policy import get_policy

STARTED_AT = time.monotonic()

app = FastAPI(
    title="License Circuit Breaker",
    description=(
        "Executable data-rights revocation with DataHub. Supports compliance "
        "operations; does not provide legal advice."
    ),
    version="0.1.0",
)


class HealthResponse(BaseModel):
    status: Literal["ok"]
    project_slug: str
    version: str
    uptime_seconds: float


class CheckResult(BaseModel):
    name: str
    passed: bool
    detail: str


class ReadinessResponse(BaseModel):
    status: Literal["ready", "degraded"]
    project_slug: str
    checks: list[CheckResult]
    namespace: dict[str, str]


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Liveness only. Does not touch DataHub or the filesystem."""
    settings = get_settings()
    return HealthResponse(
        status="ok",
        project_slug=settings.project_slug,
        version=app.version,
        uptime_seconds=round(time.monotonic() - STARTED_AT, 3),
    )


def _check_state_dir(settings: Settings) -> CheckResult:
    try:
        state_dir = settings.ensure_state_dir()
        probe = state_dir / ".readiness"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return CheckResult(name="state_dir", passed=True, detail=f"writable at {state_dir}")
    except OSError as exc:
        return CheckResult(name="state_dir", passed=False, detail=f"not writable: {exc}")


def _check_policy() -> CheckResult:
    try:
        table = get_policy()
        return CheckResult(
            name="policy_table",
            passed=True,
            detail=f"v{table.version}, {len(table.rules)} rules loaded",
        )
    except Exception as exc:  # noqa: BLE001 - readiness reports, never raises
        return CheckResult(name="policy_table", passed=False, detail=str(exc))


def _check_namespace(settings: Settings) -> CheckResult:
    try:
        namespace = settings.namespace
        return CheckResult(
            name="namespace_guard",
            passed=True,
            detail=f"enforcing prefix {namespace.urn_prefix!r}",
        )
    except ValueError as exc:
        # A misconfigured namespace is worse than an absent one: it would let
        # writes reach another project's entities.
        return CheckResult(name="namespace_guard", passed=False, detail=str(exc))


def _check_datahub(settings: Settings) -> CheckResult:
    """Read-only reachability probe against DataHub GMS."""
    if not settings.datahub_configured:
        return CheckResult(
            name="datahub",
            passed=False,
            detail="DATAHUB_GMS_URL/DATAHUB_TOKEN not configured",
        )
    url = settings.datahub_gms_url.rstrip("/") + "/config"
    try:
        response = httpx.get(url, timeout=5.0)
        if response.status_code == 200:
            return CheckResult(name="datahub", passed=True, detail=f"GMS reachable at {url}")
        return CheckResult(
            name="datahub", passed=False, detail=f"GMS returned HTTP {response.status_code}"
        )
    except httpx.HTTPError as exc:
        return CheckResult(name="datahub", passed=False, detail=f"GMS unreachable: {exc}")


@app.get("/api/readiness", response_model=ReadinessResponse)
def readiness() -> ReadinessResponse:
    """Verify local state and DataHub connectivity without mutating shared state."""
    settings = get_settings()
    checks = [
        _check_state_dir(settings),
        _check_policy(),
        _check_namespace(settings),
        _check_datahub(settings),
    ]
    return ReadinessResponse(
        status="ready" if all(c.passed for c in checks) else "degraded",
        project_slug=settings.project_slug,
        checks=checks,
        namespace={
            "urn_prefix": settings.datahub_urn_prefix,
            "project_tag": settings.datahub_project_tag,
            "domain": settings.datahub_domain,
        },
    )


@app.get("/api/policy/rules")
def policy_rules() -> dict[str, Any]:
    """Expose the deterministic rule table.

    Judges and operators can read exactly which rules can fire, and every impact
    decision cites ids from this list.
    """
    table = get_policy()
    return {
        "version": table.version,
        "rules": [
            {
                "id": rule.id,
                "description": rule.description,
                "precedence": rule.precedence,
                "when": dict(rule.when),
                "actions": [a.value for a in rule.actions],
                "missing_evidence": list(rule.missing_evidence),
                "requires_approval": rule.requires_approval,
            }
            for rule in table.rules
        ],
    }


def main() -> None:
    """Run the app. Entry point for ``python -m app.main``."""
    import uvicorn

    settings = get_settings()
    uvicorn.run(app, host=settings.app_host, port=settings.app_port)


if __name__ == "__main__":
    main()
