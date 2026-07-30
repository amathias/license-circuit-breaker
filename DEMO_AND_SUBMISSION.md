# Demo and Submission Guide: License Circuit Breaker

Background and source material. Every number, rule ID, and exit code below was taken from an
actual offline run — see `examples/containment-report.md` for the captured output.

> **Two files now supersede parts of this one.** [`SUBMISSION.md`](./SUBMISSION.md) is the
> submission copy of record, and [`docs/DEMO_RECORDING.md`](./docs/DEMO_RECORDING.md) is the
> recording runbook of record — it carries the public app URL, the pre-record gate, and the
> stop conditions. Where a timing or a claim differs, those two win. What stays here and is
> not duplicated there: the narrative framing below, and the "Claims to avoid" list.

## Devpost short description

License Circuit Breaker is a DataHub-powered enforcement agent for data and AI supply
chains. When an upstream usage right is revoked, it traces every affected descendant,
applies deterministic policy, executes approved containment or replacement actions,
verifies that prohibited artifacts stopped serving, and writes the evidence back to
DataHub.

---

## Before recording

```bash
python -m demo.cli estate reset && python -m demo.cli estate build
npm --prefix web run build
python -m app.main                       # http://127.0.0.1:8102
```

Open the console and press **Reset demo** once. That rebuilds the estate *and* clears the
approval and run journals, so the recording starts from a state where the gate has nothing
recorded and will genuinely refuse.

Check before you hit record:

- Stage 1 shows all three probes as **exposed**, with `P-` review IDs visible in the search
  results. Those are the partner rows.
- Stage 5 shows "No decision has been recorded."
- The masthead shows **verdict: not_started** and the amber **DataHub: simulated** pill.

Keep the network tab open. The status codes are part of the argument.

---

## Three-minute demo

Target **2:35–2:45**. Timings are the console walkthrough; the CLI equivalent is in
brackets where it differs.

### 0:00–0:18 — Prove exposure

Stage 1. The prediction API answers, vector search returns partner rows, the CSV export
resolves. [`python -m demo.cli probe`]

> A partner has revoked the right to use this review feed. That data has already reached a
> model, a vector index, an endpoint, and an export.

### 0:18–0:50 — Trigger and trace

Stage 2 for the rights event — structured, hashed, with `training` and `retrieval` lost and
`analytics` retained. Stage 3 for the graph. Click `license.reports.review_volume` and show
it is **not** affected, then click `license.reviews.legacy_snapshot` and show its lineage
path is incomplete.

> DataHub supplies the provenance. The agent cites the exact lineage path behind every
> decision — and when the path has a gap, it says so instead of guessing.

### 0:50–1:20 — Policy and approval

Stage 4: eight decisions, each with a rule ID. Point at `LCB-R050` freezing the API,
`LCB-R040` purging the index, `LCB-R001` escalating the broken-lineage snapshot, and
`LCB-R010` clearing the analytics report.

Then **press Execute before approving.** The server answers **409** and the console renders
the reason verbatim. [`python -m demo.cli contain` → exit 8]

> The plan is complete and correct, and nothing has been touched, because no human has said
> so. The gate is on the server, not in the browser.

Now approve. Note that the approval binds to the plan hash shown above it.

### 1:20–2:05 — Execute

Stage 6. Eight steps: freeze, quarantine, purge, rebuild ×3, retrain, replace. Point out
step 4 reporting **no change** — the rebuild was already satisfied, and it says so rather
than claiming work it did not do.

> These actions really execute against local artifacts. Each one is idempotent, so a resumed
> run cannot double-apply.

### 2:05–2:30 — Verify

Stage 7. Eight probes pass: six containment, two precision. Highlight the two precision
probes — the analytics report is still queryable and the approved model is still serving.

Then Stage 8, writeback: 8/8 statuses verified, each confirmed by re-read.

> Verification reads the artifacts, never the receipts. And it fails on over-reach too: if
> containment had broken the branch it was supposed to leave alone, these two probes would
> fail.

### 2:30–2:42 — Close

The verdict is **escalated**, not contained, and the residual table names why.

> One artifact is reachable through a lineage path DataHub cannot complete, so it is
> escalated for manual confirmation. The tool does not report an all-clear it has not
> earned. License Circuit Breaker turns provenance into an executable data-rights control.

---

## What to say about simulation

Say it once, early, plainly:

> DataHub reads and writeback in this recording run against a deterministic in-memory
> substitute, and everything produced is labelled simulated. The local artifact changes are
> real and are probed directly.

Do not narrate around it. The console banner and the report header both state it, and a
judge who spots it unmentioned will discount everything else.

---

## Submission narrative

### Problem

License and provenance tools can identify risk, but organizations still have to manually
disable every derivative dataset, model, RAG system, API, and export.

### Solution

License Circuit Breaker propagates a structured rights event through DataHub lineage,
produces an approved containment plan, executes local actions, verifies behavior, and
records evidence.

### What makes it original

The differentiator is downstream execution and verification, not license detection or a
compliance dashboard. Three specifics worth naming:

- **The verdict is derived, never asserted.** `contained` requires every step completed,
  every probe passed, and an empty residual list. The demo's honest answer is `escalated`.
- **Approval binds to a plan hash.** Change the graph, and the approval stops applying
  rather than silently authorizing a scope nobody reviewed.
- **Refusals carry meaning.** 451 for containment, 409 for the gate, 503 for degraded — a
  judge can read containment off the network tab.

### DataHub usage to state explicitly

- Reads downstream data and ML lineage over the official MCP integration.
- Uses entity types, tags, domain, and custom properties to classify impact.
- Writes revocation status, plan hash, and an evidence reference back via the DataHub SDK,
  each confirmed by re-read.
- Fails closed outside its assigned `license.` namespace.

## Judging evidence map

| Criterion | What judges should see |
|---|---|
| Use of DataHub | Descendants discovered through lineage, including one the estate does not make obvious, plus verified writeback |
| Technical execution | Deterministic rule table, plan-bound approval, five real containment adapters, artifact-level probes, residual exposure |
| Originality | Executes and verifies revocation rather than only detecting incompatible licenses |
| Real-world usefulness | A concrete vendor-data and AI supply-chain response workflow |
| Submission quality | Reproducible from the README, honest labelling of what is simulated |

## Repository evidence

| Artifact | Status |
|---|---|
| `examples/rights-event.json` | present |
| `examples/impact-plan.json` | present |
| `examples/containment-report.md` | present — captured verbatim from an offline run |
| Policy decision table and rule IDs | `policy/rules.yaml`, cited by every decision |
| Before/after endpoint and index results | in the containment report, and reproducible via `demo.cli probe` |
| Model and source manifests | in the estate; read by the `training_manifest_scan` probe |
| Architecture and limitations | `README.md`, `docs/DECISIONS.md` |
| DataHub before/after screenshots | **not captured** — requires a live instance |

## Claims to avoid

- "Provides legal advice."
- "Automatically interprets every license or contract."
- "Finds all copies, including untracked offline files."
- "Cryptographically proves universal model unlearning."
- "Verified against a live DataHub instance." — not true of anything **in this
  repository**, and not true of the recording. The deployed build *was* verified live
  by the deployment coordinator (see `COORDINATOR_HANDOFF.md`), but that run is not
  committed here, is not what the video shows, and is not reproducible by a judge from
  this checkout. If it comes up, say where it happened and that the repository's own
  artifacts are simulated — do not let a live result borrow credibility for the
  recording.

Prefer: "Executes and verifies policy actions for descendants represented in the
demonstrated DataHub graph."

## Recording checklist

- [x] Video is public and under three minutes: <https://youtu.be/42FznbyhYlA> (2:18).
- [ ] Prohibited content is visibly served before the trigger.
- [ ] DataHub-derived impact is legible, including the unaffected branch.
- [ ] The gate is shown refusing before any approval is recorded.
- [ ] Policy evidence and the approval's plan binding are shown.
- [ ] Post-action probes are shown, including the two precision probes.
- [ ] The escalated verdict is stated, not glossed.
- [ ] Simulated DataHub is stated aloud once.
- [ ] No private contract text, credentials, or copyrighted music appears.
