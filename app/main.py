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
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.api import get_client
from app.api import router as api_router
from app.clients import is_offline
from app.config import get_settings
from app.policy import get_policy
from app.readiness import evaluate_readiness

STARTED_AT = time.monotonic()

#: Returned by readiness when any required check fails. Health stays 200 --
#: the process is alive and restarting it would not fix an unreachable DataHub.
SERVICE_UNAVAILABLE = 503

#: Where the built judge console lands. Served only when it has been built, so
#: the API is usable without a Node toolchain present.
WEB_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"


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
def readiness(response: Response) -> ReadinessResponse:
    """Fail-closed readiness. Read-only; never mutates shared state.

    Reports ready only with positive proof of a token, the required MCP tools, the
    project domain and tag, and readable ``license.`` entities. A reachable GMS is
    not sufficient -- an empty or foreign instance would otherwise look healthy
    while producing an impact analysis indistinguishable from an all-clear.

    A degraded report returns **503**, so a reverse proxy or orchestrator can act
    on it without parsing the body. Health stays 200 throughout: the process is
    alive and should not be restarted just because DataHub is unreachable. The
    full check list is still returned with the 503, because "not ready" without
    a reason is not an answer.
    """
    settings = get_settings()
    report = evaluate_readiness(
        settings,
        load_policy=get_policy,
        client_factory=lambda: get_client(settings),
    )
    if not report.ready:
        response.status_code = SERVICE_UNAVAILABLE
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


# The governance workflow: rights event, graph, plan, approval, execution,
# verification, evidence, writeback, and the demo probes.
app.include_router(api_router)

if WEB_DIST.is_dir():
    # Mounted last so it cannot shadow /api. Absent when the console has not
    # been built, which keeps the API runnable without a Node toolchain.
    app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="console")


def main() -> None:
    """Run the app. Entry point for ``python -m app.main``."""
    import uvicorn

    settings = get_settings()
    uvicorn.run(app, host=settings.app_host, port=settings.app_port)


if __name__ == "__main__":
    main()
