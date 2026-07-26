"""Verification: prove containment by probing the artifacts, not the receipts.

Every probe here reads the real artifact. None of them consults the execution
report, and that independence is the whole point: an adapter that reported
success but changed nothing, an action nobody approved, and a step that was
quietly skipped all fail verification identically, because the artifact is
identically still exposed.

Two directions are checked, not one:

**Containment.** Each artifact the plan proposed to contain is probed for
prohibited material. The endpoint must refuse, the index must hold no partner
vectors, the published export must be gone, the served model's manifest must
cite only approved sources.

**Precision.** Each artifact the plan deliberately left alone is probed for
*availability*. A tool that contains everything is not governing, it is
deleting, and an over-reach is a failure the report should name just as loudly
as a miss.

Probes report ``expected`` and ``observed`` separately so a judge can read what
was actually seen rather than a bare pass or fail.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.execution import VERIFICATION_FAILED, ResidualExposure
from app.rights import Action, ArtifactClass
from app.workflow import ImpactPlan
from demo import graph
from demo.corpus import PARTNER_PREFIX
from demo.estate import (
    ARTIFACTS_BY_URN,
    DUCKDB_TABLE,
    EXPORT,
    MODEL,
    SERVICE,
    VECTOR_INDEX,
    EstateError,
    EstatePaths,
    active_version,
    index_manifest,
    read_table,
    table_row_ids,
    training_manifest,
)
from demo.serving import ServingRefused, fetch_export, predict, search

#: Probe method names, reported verbatim in the evidence bundle.
ENDPOINT_PROBE = "endpoint_probe"
INDEX_SCAN = "index_manifest_scan"
EXPORT_ACCESS = "export_access_probe"
MODEL_MANIFEST = "training_manifest_scan"
TABLE_SCAN = "table_scan"
AVAILABILITY = "availability_probe"


@dataclass(frozen=True)
class Probe:
    """One direct observation of an artifact's current behaviour."""

    urn: str
    method: str
    passed: bool
    expected: str
    observed: str
    #: True for containment probes, False for the precision probes that check an
    #: untouched artifact is still working.
    containment: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "urn": self.urn,
            "method": self.method,
            "passed": self.passed,
            "expected": self.expected,
            "observed": self.observed,
            "kind": "containment" if self.containment else "precision",
        }


@dataclass(frozen=True)
class VerificationReport:
    """What the probes actually saw."""

    verified_at: datetime
    probes: tuple[Probe, ...]

    @property
    def containment_probes(self) -> tuple[Probe, ...]:
        return tuple(p for p in self.probes if p.containment)

    @property
    def precision_probes(self) -> tuple[Probe, ...]:
        return tuple(p for p in self.probes if not p.containment)

    @property
    def failures(self) -> tuple[Probe, ...]:
        return tuple(p for p in self.probes if not p.passed)

    @property
    def contained(self) -> bool:
        """True only when every probe passed.

        Includes the precision probes: a run that broke the unaffected branch
        has not achieved containment, it has achieved damage.
        """
        return bool(self.probes) and not self.failures

    def residual(self) -> tuple[ResidualExposure, ...]:
        """Failed containment probes, as residual exposure."""
        return tuple(
            ResidualExposure(
                urn=probe.urn,
                reason=VERIFICATION_FAILED,
                detail=f"expected {probe.expected}; observed {probe.observed}",
                action=None,
            )
            for probe in self.failures
            if probe.containment
        )

    def describe(self) -> str:
        passed = len(self.probes) - len(self.failures)
        return (
            f"{passed}/{len(self.probes)} probes passed "
            f"({len(self.containment_probes)} containment, "
            f"{len(self.precision_probes)} precision)"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "verified_at": self.verified_at.isoformat(),
            "contained": self.contained,
            "summary": self.describe(),
            "probes": [p.to_dict() for p in self.probes],
            "residual_exposure": [r.to_dict() for r in self.residual()],
        }


# --- containment probes ------------------------------------------------


def probe_endpoint(paths: EstatePaths, urn: str) -> Probe:
    """The endpoint must refuse to answer.

    A refusal, not an error. If the service were simply stopped the probe could
    not distinguish containment from an outage, so a blocked service that still
    responds is what proves the freeze took effect.
    """
    try:
        prediction = predict(paths, "the battery lasts all weekend and charges fast")
    except ServingRefused as exc:
        return Probe(
            urn=urn,
            method=ENDPOINT_PROBE,
            passed=True,
            expected="the endpoint refuses to serve",
            observed=f"refused: {exc.reason}",
        )
    except EstateError as exc:
        return Probe(
            urn=urn,
            method=ENDPOINT_PROBE,
            passed=False,
            expected="the endpoint refuses to serve",
            observed=f"the endpoint is broken rather than contained: {exc}",
        )
    return Probe(
        urn=urn,
        method=ENDPOINT_PROBE,
        passed=False,
        expected="the endpoint refuses to serve",
        observed=(
            f"still serving predictions from model {prediction.model_version} "
            f"trained on {', '.join(prediction.training_sources) or 'unknown sources'}"
        ),
    )


def probe_index(paths: EstatePaths, urn: str) -> Probe:
    """The index must hold nothing traceable to the revoked feed."""
    manifest = index_manifest(paths)
    rows = [str(r) for r in manifest.get("row_ids", ())]
    partner = sorted(r for r in rows if r.startswith(PARTNER_PREFIX))

    # A live retrieval as well as a manifest read: a manifest can be rewritten
    # without the vectors changing, and the search surface is what a user hits.
    try:
        hits = search(paths, "battery charge fast")
        served = sorted({h.review_id for h in hits if h.review_id.startswith(PARTNER_PREFIX)})
    except ServingRefused:
        served = []

    leaked = sorted(set(partner) | set(served))
    return Probe(
        urn=urn,
        method=INDEX_SCAN,
        passed=not leaked,
        expected="no indexed document traces to the revoked partner feed",
        observed=(
            f"{len(rows)} documents indexed, none from the partner feed"
            if not leaked
            else f"{len(leaked)} partner-derived documents still retrievable: {leaked[:5]}"
        ),
    )


def probe_export(paths: EstatePaths, urn: str) -> Probe:
    """The published export path must no longer resolve."""
    try:
        content = fetch_export(paths)
    except ServingRefused as exc:
        return Probe(
            urn=urn,
            method=EXPORT_ACCESS,
            passed=True,
            expected="the published export is no longer retrievable",
            observed=f"refused: {exc.reason}",
        )
    rows = max(len(content.splitlines()) - 1, 0)
    return Probe(
        urn=urn,
        method=EXPORT_ACCESS,
        passed=False,
        expected="the published export is no longer retrievable",
        observed=f"still readable at the published path with {rows} rows",
    )


def probe_model(paths: EstatePaths, urn: str) -> Probe:
    """The served model must cite only approved training sources."""
    record = ARTIFACTS_BY_URN.get(urn)
    name = record.location if record else "review_sentiment"

    manifest = training_manifest(paths, name)
    if not manifest:
        return Probe(
            urn=urn,
            method=MODEL_MANIFEST,
            passed=False,
            expected="the served model cites only approved training sources",
            observed="no training manifest is available for the served version",
        )

    rows = [str(r) for r in manifest.get("row_ids", ())]
    partner = sorted(r for r in rows if r.startswith(PARTNER_PREFIX))
    sources = list(manifest.get("training_sources", ()))
    tainted = [s for s in sources if s in (graph.SOURCE, graph.NORMALIZED, graph.FEATURES)]

    passed = not partner and not tainted
    return Probe(
        urn=urn,
        method=MODEL_MANIFEST,
        passed=passed,
        expected="the served model cites only approved training sources",
        observed=(
            f"serving {active_version(paths, name)} trained on {len(rows)} rows "
            f"from {sources}"
            if passed
            else (
                f"serving {active_version(paths, name)} trained on "
                f"{len(partner)} partner rows from {sources}"
            )
        ),
    )


def probe_table(paths: EstatePaths, urn: str) -> Probe:
    """A derived table must hold no partner rows."""
    record = ARTIFACTS_BY_URN.get(urn)
    table = record.location if record else ""
    try:
        rows = table_row_ids(paths, table)
    except EstateError as exc:
        return Probe(
            urn=urn,
            method=TABLE_SCAN,
            passed=False,
            expected="no rows traceable to the revoked partner feed",
            observed=f"could not read {table!r}: {exc}",
        )

    partner = sorted(r for r in rows if r.startswith(PARTNER_PREFIX))
    return Probe(
        urn=urn,
        method=TABLE_SCAN,
        passed=not partner,
        expected="no rows traceable to the revoked partner feed",
        observed=(
            f"{len(rows)} rows, none from the partner feed"
            if not partner
            else f"{len(partner)} partner rows remain in {table}"
        ),
    )


# --- precision probes --------------------------------------------------


def probe_availability(paths: EstatePaths, urn: str) -> Probe:
    """An artifact the plan left alone must still work.

    Over-reach is a real failure. Containing an analytics table whose purpose
    was never revoked would be a governance tool destroying data it had no
    mandate to touch, and the report should say so as loudly as it reports a
    miss.
    """
    record = ARTIFACTS_BY_URN.get(urn)
    if record is None:
        return Probe(
            urn=urn,
            method=AVAILABILITY,
            passed=False,
            expected="the unaffected artifact is still available",
            observed="no local artifact could be resolved",
            containment=False,
        )

    if record.kind == MODEL:
        manifest = training_manifest(paths, record.location)
        available = bool(manifest) and active_version(paths, record.location) is not None
        observed = (
            f"serving {active_version(paths, record.location)} from "
            f"{manifest.get('training_sources', [])}"
            if available
            else "the model is no longer available"
        )
    else:
        try:
            rows = read_table(paths, record.location)
            available = bool(rows)
            observed = f"{len(rows)} rows still queryable"
        except EstateError as exc:
            available = False
            observed = f"unreadable: {exc}"

    return Probe(
        urn=urn,
        method=AVAILABILITY,
        passed=available,
        expected="the unaffected artifact is still available",
        observed=observed,
        containment=False,
    )


# --- dispatch ----------------------------------------------------------

_CONTAINMENT_PROBES = {
    SERVICE: probe_endpoint,
    VECTOR_INDEX: probe_index,
    EXPORT: probe_export,
    MODEL: probe_model,
    DUCKDB_TABLE: probe_table,
}


def verify_plan(plan: ImpactPlan, paths: EstatePaths) -> VerificationReport:
    """Probe every artifact the plan had an opinion about.

    Containment probes run for descendants the plan proposed to change.
    Precision probes run for descendants it deliberately left alone. Escalated
    descendants get neither: nothing was attempted, the plan already reports
    them as unresolved, and probing them would imply a verdict the evidence does
    not support.
    """
    probes: list[Probe] = []

    for decision in plan.decisions:
        record = ARTIFACTS_BY_URN.get(decision.descendant_urn)
        if record is None:
            continue

        if decision.is_escalation:
            continue

        if decision.is_destructive:
            probe = _CONTAINMENT_PROBES.get(record.kind)
            if probe is not None:
                probes.append(probe(paths, decision.descendant_urn))
        elif Action.NO_ACTION in decision.actions:
            probes.append(probe_availability(paths, decision.descendant_urn))

    # The approved branch never appears in the impact plan at all, which is
    # exactly why it is worth probing: nothing in the pipeline would notice if
    # containment had reached it.
    if graph.APPROVED_MODEL not in {p.urn for p in probes}:
        probes.append(probe_availability(paths, graph.APPROVED_MODEL))

    probes.sort(key=lambda p: (not p.containment, p.urn))
    return VerificationReport(verified_at=datetime.now(UTC), probes=tuple(probes))


def probe_for(artifact_class: ArtifactClass) -> str:
    """The probe method that would be used for an artifact class."""
    mapping = {
        ArtifactClass.API: ENDPOINT_PROBE,
        ArtifactClass.DEPLOYMENT: ENDPOINT_PROBE,
        ArtifactClass.VECTOR_INDEX: INDEX_SCAN,
        ArtifactClass.EXPORT: EXPORT_ACCESS,
        ArtifactClass.MODEL: MODEL_MANIFEST,
        ArtifactClass.DATASET: TABLE_SCAN,
        ArtifactClass.FEATURE: TABLE_SCAN,
    }
    return mapping.get(artifact_class, "unsupported")


__all__ = [
    "AVAILABILITY",
    "ENDPOINT_PROBE",
    "EXPORT_ACCESS",
    "INDEX_SCAN",
    "MODEL_MANIFEST",
    "TABLE_SCAN",
    "Probe",
    "VerificationReport",
    "probe_availability",
    "probe_endpoint",
    "probe_export",
    "probe_for",
    "probe_index",
    "probe_model",
    "probe_table",
    "verify_plan",
]
