"""FastAPI application for License Circuit Breaker.

Serves the governance API on the coordinator-assigned internal port 8102.

``/api/health`` and ``/api/readiness`` are required by the shared contract in
``../COORDINATOR_PLAN.md``. Readiness verifies DataHub connectivity but must never
mutate shared state -- five submissions share one instance, and a readiness probe
that writes would corrupt other projects' demos every time the proxy polled it.
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import FastAPI
from pydantic import BaseModel

from app.clients import build_client, is_offline
from app.config import get_settings
from app.policy import get_policy
from app.readiness import evaluate_readiness

STARTED_AT = time.monotonic()


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Create the state directory once at startup.

    Readiness must not mutate anything, so it can only *check* the directory --
    creating it is startup's job.
    """
    get_settings().ensure_state_dir()
    yield


app = FastAPI(
    title="License Circuit Breaker",
    description=(
        "Executable data-rights revocation with DataHub. Supports compliance "
        "operations; does not provide legal advice."
    ),
    version="0.1.0",
    lifespan=lifespan,
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
    #: True when running against the in-memory fake. Judges and the coordinator
    #: must be able to tell simulated runs from live ones at a glance.
    simulated: bool


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


@app.get("/api/readiness", response_model=ReadinessResponse)
def readiness() -> ReadinessResponse:
    """Fail-closed readiness. Read-only; never mutates shared state.

    Reports ready only with positive proof of a token, the required MCP tools, the
    project domain and tag, and readable ``license.`` entities. A reachable GMS is
    not sufficient -- an empty or foreign instance would otherwise look healthy
    while producing an impact analysis indistinguishable from an all-clear.
    """
    settings = get_settings()
    report = evaluate_readiness(
        settings,
        load_policy=get_policy,
        client_factory=lambda: build_client(settings),
    )
    return ReadinessResponse(
        status=report.status,
        project_slug=settings.project_slug,
        checks=[
            CheckResult(name=c.name, passed=c.passed, detail=c.detail) for c in report.checks
        ],
        namespace={
            "urn_prefix": settings.datahub_urn_prefix,
            "project_tag": settings.datahub_project_tag,
            "domain": settings.datahub_domain,
        },
        simulated=is_offline(settings),
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
