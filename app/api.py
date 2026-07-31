"""The judge-facing HTTP API.

Covers the seven states the demo has to show: the rights event, the
DataHub-derived impact graph, per-artifact policy evidence, approval, live
execution, verification with residual exposure, and DataHub writeback -- plus
the demo endpoints a judge probes before and after to see the difference.

Two conventions run through it:

**Refusals get status codes that mean something.** A frozen endpoint answers
``451 Unavailable For Legal Reasons``, not 404 or 500. A judge watching the
network tab can tell containment from an outage, which is the same reason the
service refuses rather than stopping.

**Enforcement endpoints refuse by default.** ``POST /api/execute`` returns 409
with the reason unless a recorded approval covers that exact plan. The gate is
enforced here, not in the browser, so it holds for anything that can reach the
port.

The DataHub client is a process-wide singleton. In offline mode that matters:
the writeback a judge triggers has to still be visible when the console reloads
the graph, and a per-request fake would forget it.
"""

from __future__ import annotations

import threading
from collections.abc import AsyncIterator, Callable
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from adapters.containment import AdapterContext, AdapterRegistry
from adapters.datahub import DataHubClient, DataHubError
from app.approvals import APPROVED, REJECTED, ApprovalError, ApprovalStore, require_approval
from app.clients import build_client, is_offline
from app.config import Settings, get_settings
from app.demo_guard import (
    DemoCapacityError,
    DemoConfirmationError,
    DemoMutationGuard,
)
from app.evidence import EvidenceBundle, build_bundle
from app.execution import (
    ExecutionError,
    ExecutionJournal,
    execute_plan,
    load_report,
    plan_steps,
)
from app.namespace import NamespaceViolation
from app.policy import get_policy
from app.receipts import ReceiptLedger
from app.rights import License, Purpose, RightsEvent, RightsState
from app.store import GovernanceStore
from app.verification import verify_plan
from app.workflow import (
    ImpactPlan,
    WorkflowError,
    build_impact_plan,
    record_containment_outcomes,
)
from demo import graph
from demo.estate import EstatePaths, build_estate, estate_status, reset_estate
from demo.serving import ServingRefused, fetch_export, predict, search

router = APIRouter(prefix="/api")

#: HTTP status for a governed refusal. Distinct from 404 (absent) and 503
#: (unhealthy) so a probe can tell "contained" from "broken".
LEGALLY_UNAVAILABLE = 451
PUBLIC_GUARDED_ENVIRONMENTS = frozenset({"hackathon"})
PUBLIC_READ_ONLY_ENVIRONMENTS = frozenset({"production"})
DemoOperation = Literal["approve", "execute", "writeback", "reset"]

_client_lock = threading.Lock()
_client: DataHubClient | None = None
_client_key: tuple[str, ...] | None = None
_demo_guard = DemoMutationGuard()


def _client_fingerprint(settings: Settings) -> tuple[str, ...]:
    """The configuration a cached client is only valid for."""
    return (
        settings.app_env.lower(),
        settings.datahub_gms_url,
        settings.datahub_mcp_url,
        settings.datahub_urn_prefix,
        settings.datahub_project_tag,
        settings.datahub_domain,
    )


def get_client(settings: Settings, refresh: bool = False) -> DataHubClient:
    """Return the process-wide DataHub client.

    Shared rather than per-request so an offline writeback survives long enough
    for the console to read it back. ``refresh`` drops it, which is what a demo
    reset needs.

    Keyed on the configuration it was built from. Without that, switching
    ``APP_ENV`` or repointing ``DATAHUB_GMS_URL`` would keep serving a client
    built for the old settings -- reads and writeback would silently go to the
    wrong place, which on a shared instance is the worst kind of wrong.
    """
    global _client, _client_key
    fingerprint = _client_fingerprint(settings)
    with _client_lock:
        if refresh or _client is None or _client_key != fingerprint:
            _client = build_client(settings)
            _client_key = fingerprint
        return _client


def _paths(settings: Settings) -> EstatePaths:
    return EstatePaths.under(settings.ensure_state_dir())


def _store(settings: Settings) -> GovernanceStore:
    return GovernanceStore(settings.ensure_state_dir())


def _ledger(settings: Settings) -> ReceiptLedger:
    return ReceiptLedger(settings.ensure_state_dir())


def _request_client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown-client"


def _capacity_error(error: DemoCapacityError) -> HTTPException:
    return HTTPException(
        status_code=429,
        detail="the public demo is busy; retry after the indicated delay",
        headers={"Retry-After": str(error.retry_after_seconds)},
    )


def _guarded_mutation(
    operation: DemoOperation,
) -> Callable[..., AsyncIterator[None]]:
    """Build a route dependency that runs before request-body validation."""

    async def dependency(
        request: Request,
        confirmation: Annotated[
            str | None,
            Header(alias="X-Demo-Confirmation", max_length=128),
        ] = None,
    ) -> AsyncIterator[None]:
        environment = get_settings().app_env.casefold()
        if environment in PUBLIC_READ_ONLY_ENVIRONMENTS:
            raise HTTPException(
                status_code=403,
                detail="mutations are disabled in the production environment",
            )
        try:
            if environment in PUBLIC_GUARDED_ENVIRONMENTS:
                _demo_guard.begin_public(
                    _request_client_key(request),
                    operation,
                    confirmation or "",
                )
            else:
                _demo_guard.begin_unrestricted()
        except DemoConfirmationError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except DemoCapacityError as exc:
            raise _capacity_error(exc) from exc
        try:
            yield
        finally:
            _demo_guard.finish()

    return dependency


# --- rights event ------------------------------------------------------


def demo_rights_event() -> RightsEvent:
    """The rights event the demo revokes.

    Training and retrieval are removed; analytics is retained. Retaining one
    purpose is what makes the unaffected branch provable rather than asserted.
    """
    from datetime import UTC, datetime

    return RightsEvent(
        event_id="evt-lcb-demo-001",
        effective_at=datetime(2026, 8, 1, 9, 0, tzinfo=UTC),
        source_urn=graph.SOURCE,
        prior=License(
            license_id="PARTNER-2026-01",
            name="Partner review feed agreement",
            permitted_purposes=frozenset(
                {Purpose.TRAINING, Purpose.RETRIEVAL, Purpose.ANALYTICS}
            ),
            evidence_ref="operator-supplied: vendor notice 2026-08-01",
        ),
        new=License(
            license_id="PARTNER-2026-01",
            name="Partner review feed agreement",
            permitted_purposes=frozenset({Purpose.ANALYTICS}),
            state=RightsState.RESTRICTED,
            evidence_ref="operator-supplied: vendor notice 2026-08-01",
        ),
        reason="Partner revoked training and retrieval rights effective immediately",
        replacement_source_urn=graph.REPLACEMENT_SOURCE,
        requester="governance@example.com",
    )


@router.get("/rights-event")
def rights_event() -> dict[str, Any]:
    """The structured rights event, as recorded by the operator."""
    import json

    event = demo_rights_event()
    return {
        "event": json.loads(event.model_dump_json()),
        "content_hash": event.content_hash(),
        "revokes": event.revokes,
        "lost_purposes": sorted(p.value for p in event.lost_purposes),
        "note": (
            "These rights are what an operator asserted. This tool does not "
            "interpret contract text and does not provide legal advice."
        ),
    }


# --- plan and graph ----------------------------------------------------


def _build_plan(settings: Settings) -> ImpactPlan:
    client = get_client(settings)
    try:
        return build_impact_plan(
            client,
            demo_rights_event(),
            settings.namespace,
            ledger=_ledger(settings),
            simulated=is_offline(settings),
        )
    except (WorkflowError, DataHubError) as exc:
        raise HTTPException(
            status_code=503, detail=f"could not read DataHub context: {exc}"
        ) from exc
    except NamespaceViolation as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/plan")
def plan() -> dict[str, Any]:
    """The deterministic containment plan for the demo rights event."""
    settings = get_settings()
    built = _build_plan(settings)
    ApprovalStore(_store(settings)).remember_plan(built)

    return {
        **built.to_dict(),
        "simulated": is_offline(settings),
        "steps": [step.to_dict() for step in plan_steps(built)],
    }


@router.get("/graph")
def impact_graph() -> dict[str, Any]:
    """The DataHub-derived impact graph, annotated with policy decisions.

    Lineage comes from DataHub; the classification, action, and rule on each
    node come from the policy table. Both are returned so the console can show
    which of the two produced any given piece of the picture.
    """
    settings = get_settings()
    client = get_client(settings)
    built = _build_plan(settings)

    decisions = {d.descendant_urn: d for d in built.decisions}
    try:
        edges = client.get_downstream_lineage(graph.SOURCE, max_depth=6)
    except DataHubError as exc:
        raise HTTPException(
            status_code=503, detail=f"could not read lineage: {exc}"
        ) from exc

    urns = [graph.SOURCE, *{e.downstream_urn for e in edges}]
    try:
        entities = client.get_entities(urns)
    except DataHubError as exc:
        raise HTTPException(
            status_code=503, detail=f"could not read entity context: {exc}"
        ) from exc

    nodes: list[dict[str, Any]] = []
    for urn in urns:
        entity = entities.get(urn)
        decision = decisions.get(urn)
        nodes.append(
            {
                "urn": urn,
                "label": urn.rsplit(",", 2)[-2] if urn.count(",") >= 2 else urn,
                "is_source": urn == graph.SOURCE,
                "artifact_class": (
                    entity.custom_properties.get("artifact_class") if entity else None
                ),
                "purposes": (
                    sorted(
                        p
                        for p in (entity.custom_properties.get("purposes", "").split(","))
                        if p
                    )
                    if entity
                    else []
                ),
                "exposure": entity.custom_properties.get("exposure") if entity else None,
                "criticality": entity.custom_properties.get("criticality") if entity else None,
                "tags": sorted(entity.tags) if entity else [],
                "domain": entity.domain if entity else None,
                "revocation_status": (
                    entity.custom_properties.get("lcb_revocation_status") if entity else None
                ),
                "decision": (
                    {
                        "actions": [a.value for a in decision.actions],
                        "rule_ids": list(decision.rule_ids),
                        "rationale": decision.rationale,
                        "priority": decision.priority,
                        "missing_evidence": list(decision.missing_evidence),
                        "paths": [
                            {"hops": list(p.hops), "complete": p.complete}
                            for p in decision.paths
                        ],
                    }
                    if decision
                    else None
                ),
            }
        )

    return {
        "source": graph.SOURCE,
        "simulated": is_offline(settings),
        "nodes": sorted(nodes, key=lambda n: n["urn"]),
        "edges": [
            {
                "upstream": edge.upstream_urn,
                "downstream": edge.downstream_urn,
                "resolved": edge.resolved,
            }
            for edge in edges
        ],
    }


@router.get("/policy/rules")
def policy_rules() -> dict[str, Any]:
    """The deterministic rule table every decision cites."""
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


# --- approval ----------------------------------------------------------


class DemoConfirmationRequest(BaseModel):
    """Name the one public operation a confirmation may authorize."""

    model_config = ConfigDict(extra="forbid")
    operation: DemoOperation


class DemoConfirmationResponse(BaseModel):
    confirmation: str
    operation: DemoOperation
    expires_in_seconds: int


@router.post("/demo/confirmation", response_model=DemoConfirmationResponse)
def issue_demo_confirmation(
    payload: DemoConfirmationRequest,
    request: Request,
) -> DemoConfirmationResponse:
    """Issue a short-lived, one-use confirmation for the anonymous demo."""
    environment = get_settings().app_env.casefold()
    if environment in PUBLIC_READ_ONLY_ENVIRONMENTS:
        raise HTTPException(
            status_code=403,
            detail="mutations are disabled in the production environment",
        )
    try:
        confirmation, ttl = _demo_guard.issue_confirmation(
            _request_client_key(request),
            payload.operation,
        )
    except DemoCapacityError as exc:
        raise _capacity_error(exc) from exc
    return DemoConfirmationResponse(
        confirmation=confirmation,
        operation=payload.operation,
        expires_in_seconds=ttl,
    )


class ApprovalRequest(BaseModel):
    """A human decision about the current plan."""

    model_config = ConfigDict(extra="forbid")

    approver: str = Field(min_length=1, max_length=120)
    decision: str = APPROVED
    note: str = Field(default="", max_length=500)
    #: Optional narrowing. Omit to approve the plan's full enforcement scope.
    scope: dict[str, list[str]] | None = None


@router.get("/approvals")
def list_approvals() -> dict[str, Any]:
    """Every decision recorded for the demo rights event, newest first."""
    settings = get_settings()
    approvals = ApprovalStore(_store(settings))
    built = _build_plan(settings)
    current = approvals.latest_for_plan(built.plan_hash())

    return {
        "plan_hash": built.plan_hash(),
        "current": current.to_dict() if current else None,
        "history": [a.to_dict() for a in approvals.for_event(built.event.event_id)],
    }


@router.post(
    "/approvals",
    dependencies=[Depends(_guarded_mutation("approve"))],
)
def record_approval(request: ApprovalRequest = Body(...)) -> dict[str, Any]:
    """Record a decision against the current plan.

    The decision binds to this exact plan. If the graph changes and the plan is
    regenerated, this approval stops applying rather than silently authorizing a
    scope nobody reviewed.
    """
    settings = get_settings()
    built = _build_plan(settings)

    if request.decision not in (APPROVED, REJECTED):
        raise HTTPException(
            status_code=422, detail=f"unknown decision {request.decision!r}"
        )

    try:
        approval = ApprovalStore(_store(settings)).record(
            built,
            approver=request.approver,
            decision=request.decision,
            note=request.note,
            scope=request.scope,
        )
    except ApprovalError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {"approval": approval.to_dict(), "plan_hash": built.plan_hash()}


# --- execution ---------------------------------------------------------


class ExecuteRequest(BaseModel):
    """Optional resume control for one containment run.

    Fault injection remains an internal adapter-testing capability and is not
    accepted from the HTTP boundary.
    """

    model_config = ConfigDict(extra="forbid")

    #: Resume an existing run instead of starting a new one.
    run_id: str | None = None


@router.post(
    "/execute",
    dependencies=[Depends(_guarded_mutation("execute"))],
)
def execute(request: ExecuteRequest = Body(default=ExecuteRequest())) -> dict[str, Any]:
    """Run the approved containment plan.

    Refuses with 409 unless a recorded approval covers this exact plan. The
    reason is returned verbatim, because "no approval", "rejected", and "the
    plan changed after review" need different responses from an operator.
    """
    settings = get_settings()
    if (
        settings.app_env.casefold() in PUBLIC_GUARDED_ENVIRONMENTS
        and request.run_id is not None
    ):
        raise HTTPException(
            status_code=403,
            detail=(
                "the public demo starts a fresh run; resuming an existing run "
                "is available only in a trusted local environment"
            ),
        )
    built = _build_plan(settings)
    store = _store(settings)

    try:
        approval = require_approval(ApprovalStore(store), built)
    except ApprovalError as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": type(exc).__name__, "message": str(exc)},
        ) from exc

    context = AdapterContext(
        paths=_paths(settings),
        namespace=settings.namespace,
        replacement_source_urn=built.event.replacement_source_urn,
        actor=approval.approver,
    )

    try:
        report = execute_plan(
            built,
            approval,
            context,
            store,
            registry=AdapterRegistry(),
            run_id=request.run_id,
            ledger=_ledger(settings),
        )
    except ExecutionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return {"execution": report.to_dict(), "approval_id": approval.approval_id}


@router.get("/runs")
def runs() -> dict[str, Any]:
    """Every run recorded against the current plan."""
    settings = get_settings()
    built = _build_plan(settings)
    return {
        "plan_hash": built.plan_hash(),
        "runs": ExecutionJournal(_store(settings)).runs_for_plan(built.plan_hash()),
    }


# --- verification and evidence -----------------------------------------


@router.get("/verify")
def verify() -> dict[str, Any]:
    """Probe every artifact and report what is actually observed now."""
    settings = get_settings()
    built = _build_plan(settings)
    return verify_plan(built, _paths(settings)).to_dict()


def _bundle_for(
    settings: Settings, run_id: str | None = None
) -> tuple[EvidenceBundle, ImpactPlan]:
    """Assemble the current evidence cycle from durable state only.

    A new decision starts a new cycle. Until that exact approval has a run, the
    default evidence view must not reuse an older execution or attach it to the
    new decision. An explicit ``run_id`` remains a historical lookup and uses
    the approval actually recorded on that run.
    """
    built = _build_plan(settings)
    store = _store(settings)
    approvals = ApprovalStore(store)
    journal = ExecutionJournal(store)

    approval = approvals.latest_for_plan(built.plan_hash())
    execution = None

    target: str | None = None
    if run_id is not None:
        run_row = journal.run(run_id)
        if run_row is not None and run_row["plan_hash"] == built.plan_hash():
            recorded_approval = approvals.get(run_row["approval_id"])
            if recorded_approval is not None:
                approval = recorded_approval
                target = run_id
    elif approval is not None and approval.approved:
        target = next(
            (
                candidate["run_id"]
                for candidate in journal.runs_for_plan(built.plan_hash())
                if candidate["approval_id"] == approval.approval_id
            ),
            None,
        )

    if target is not None and approval is not None:
        execution = load_report(store, built, target, approval.approval_id)

    verification = verify_plan(built, _paths(settings)) if execution else None
    bundle = build_bundle(
        built,
        approval=approval,
        execution=execution,
        verification=verification,
        estate=estate_status(_paths(settings)),
        simulated=is_offline(settings),
    )
    return bundle, built


@router.get("/evidence")
def evidence(run_id: str | None = None) -> dict[str, Any]:
    """The evidence bundle for a run, assembled from durable state."""
    settings = get_settings()
    bundle, _ = _bundle_for(settings, run_id)
    return bundle.to_dict()


@router.post(
    "/writeback",
    dependencies=[Depends(_guarded_mutation("writeback"))],
)
def writeback() -> dict[str, Any]:
    """Write the durable revocation outcome back to DataHub.

    Refuses with 409 when nothing has been executed: writing a governance status
    for a containment that never ran would put a claim in the catalog with
    nothing behind it.
    """
    settings = get_settings()
    bundle, built = _bundle_for(settings)

    if bundle.execution is None:
        raise HTTPException(
            status_code=409,
            detail="nothing has been executed, so there is no outcome to write back",
        )

    state_dir = settings.ensure_state_dir()
    evidence_dir = state_dir / "evidence" / bundle.execution.run_id
    json_path, _markdown = bundle.write(evidence_dir)

    public_evidence_ref = (
        f"license-circuit-breaker://evidence/{bundle.execution.run_id}"
    )
    evidence_ref = (
        public_evidence_ref
        if settings.app_env.casefold() in PUBLIC_GUARDED_ENVIRONMENTS
        else str(json_path)
    )
    receipts = record_containment_outcomes(
        get_client(settings),
        built,
        settings.namespace,
        verdict=bundle.verdict(),
        contained_urns=frozenset(bundle.contained_urns),
        residual_urns=frozenset(r.urn for r in bundle.residual()),
        evidence_ref=evidence_ref,
        ledger=_ledger(settings),
        simulated=is_offline(settings),
    )

    return {
        "verdict": bundle.verdict(),
        "simulated": is_offline(settings),
        "evidence_path": evidence_ref,
        "verified": sum(1 for r in receipts if r.verified),
        "attempted": len(receipts),
        "receipts": [r.to_dict() for r in receipts],
    }


# --- the demo estate ---------------------------------------------------


@router.get("/estate")
def estate() -> dict[str, Any]:
    """What the local artifacts currently hold and serve."""
    return estate_status(_paths(get_settings()))


class PredictRequest(BaseModel):
    text: str = Field(default="the battery lasts all weekend and charges fast", min_length=1)


@router.post("/demo/predict")
def demo_predict(request: PredictRequest = Body(default=PredictRequest())) -> dict[str, Any]:
    """The prediction endpoint a judge probes before and after containment."""
    paths = _paths(get_settings())
    try:
        return predict(paths, request.text).to_dict()
    except ServingRefused as exc:
        raise HTTPException(
            status_code=LEGALLY_UNAVAILABLE,
            detail={"urn": exc.urn, "reason": exc.reason},
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail=f"the estate is not built: {exc}"
        ) from exc


@router.get("/demo/search")
def demo_search(q: str = "battery charge", limit: int = 3) -> dict[str, Any]:
    """The retrieval endpoint. Returns partner content until the index is purged."""
    paths = _paths(get_settings())
    try:
        hits = search(paths, q, limit=limit)
    except ServingRefused as exc:
        raise HTTPException(
            status_code=LEGALLY_UNAVAILABLE,
            detail={"urn": exc.urn, "reason": exc.reason},
        ) from exc
    return {"query": q, "hits": [hit.to_dict() for hit in hits], "count": len(hits)}


@router.get("/demo/export")
def demo_export() -> dict[str, Any]:
    """The published export. Stops resolving once quarantined."""
    paths = _paths(get_settings())
    try:
        content = fetch_export(paths)
    except ServingRefused as exc:
        raise HTTPException(
            status_code=LEGALLY_UNAVAILABLE,
            detail={"urn": exc.urn, "reason": exc.reason},
        ) from exc
    lines = content.splitlines()
    return {
        "rows": max(len(lines) - 1, 0),
        "header": lines[0] if lines else "",
        "preview": lines[1:6],
    }


class ResetRequest(BaseModel):
    #: Also discard approvals and run journals. Off by default: an operator
    #: resetting the artifacts rarely means to erase the audit trail too.
    clear_governance: bool = False


@router.post(
    "/demo/reset",
    dependencies=[Depends(_guarded_mutation("reset"))],
)
def demo_reset(request: ResetRequest = Body(default=ResetRequest())) -> dict[str, Any]:
    """Rebuild the disposable estate and preserve public governance history."""
    settings = get_settings()
    public_guarded = settings.app_env.casefold() in PUBLIC_GUARDED_ENVIRONMENTS
    if public_guarded and request.clear_governance:
        raise HTTPException(
            status_code=403,
            detail=(
                "the public demo may reset disposable artifacts but may not "
                "delete approval or execution history"
            ),
        )
    paths = _paths(settings)
    plan_to_invalidate = _build_plan(settings) if public_guarded else None

    reset_estate(paths)
    result = build_estate(paths)
    get_client(settings, refresh=True)

    cleared = False
    if request.clear_governance:
        with _store(settings).connect() as connection:
            for table in ("steps", "runs", "approvals", "plans"):
                connection.execute(f"DELETE FROM {table}")  # noqa: S608
        cleared = True

    approval_invalidated = False
    if public_guarded:
        assert plan_to_invalidate is not None
        ApprovalStore(_store(settings)).record(
            plan_to_invalidate,
            approver="public-demo-reset",
            decision=REJECTED,
            note=(
                "Public demo reset restored the disposable estate and "
                "invalidated the prior approval. Review and approve the exact "
                "plan before executing again."
            ),
        )
        approval_invalidated = True

    return {
        "rebuilt": True,
        "summary": result.describe(),
        "governance_cleared": cleared,
        "approval_invalidated": approval_invalidated,
        "estate": estate_status(paths),
    }


__all__ = [
    "LEGALLY_UNAVAILABLE",
    "PUBLIC_GUARDED_ENVIRONMENTS",
    "PUBLIC_READ_ONLY_ENVIRONMENTS",
    "demo_rights_event",
    "get_client",
    "router",
]
