# License Circuit Breaker — Devpost submission

This file is the submission copy of record. Paste from it into the Devpost form; do not
rewrite the claims in the form, because every one of them is calibrated against what the
repository and the deployment can actually show.

- **Public app:** <https://license.datahub-hackathon.aaronmathias.com>
- **Public repository:** <https://github.com/amathias/license-circuit-breaker>
- **License:** Apache 2.0, at the repository root
- **Primary category:** Production ML Agents
- **Demo video:** <https://youtu.be/42FznbyhYlA> (2:18, public, English captions)

> License Circuit Breaker supports compliance operations. It does not provide legal advice,
> does not interpret contract text, and makes no determination about whether any legal or
> contractual obligation has been met. The rights it acts on are the ones an operator
> recorded as structured data.

---

## Tagline

Executable data-rights revocation with DataHub. Revoke one upstream right; contain and
verify every affected descendant.

## Short description

License Circuit Breaker is a DataHub-powered enforcement agent for data and AI supply
chains. When an upstream usage right is revoked, it reads DataHub lineage and entity
context over the DataHub MCP Server, traces every affected descendant, applies a
deterministic rule table to produce a typed containment plan, refuses to act until a human
approves that exact plan, executes real actions against local artifacts, verifies
containment by probing the artifacts themselves rather than trusting its own receipts, and
writes the resulting status and evidence reference back to DataHub through the DataHub
Python SDK.

## The problem

A vendor, contributor, or open-data source changes or revokes usage rights. The
organization then has to answer five questions under time pressure:

- Which derived datasets contain it?
- Which models or vector indexes learned from it?
- Which endpoints or exports are still serving it?
- Can each artifact be disabled, rebuilt, or replaced?
- How can containment be proven rather than asserted?

Catalogs, provenance tools, and license scanners answer the first question well. The
operational response to the rest is still manual, fragmented, and unevidenced. A governance
dashboard that turns a row red has not stopped anything from serving.

## What this does about it

License Circuit Breaker is the execution and verification layer that sits after detection.
It does not claim to invent license detection or data provenance. It claims that a rights
change should propagate through the graph as an executable constraint, and it demonstrates
that end to end:

1. **Structured rights event.** Source URN, prior and new purposes, effective time, reason,
   replacement source, requester — hashed, versioned, and machine-readable. Not prose handed
   to a model.
2. **DataHub-derived impact.** Downstream lineage and entity context come from the catalog.
   Descendants are classified as dataset, transformation, feature table, model, vector
   index, API, or export from DataHub custom properties.
3. **Deterministic policy.** `policy/rules.yaml` is a first-match-wins table with stable
   rule IDs. Every decision cites the rule that produced it and the lineage path that
   justified it. An LLM may explain; it cannot decide.
4. **Fail-closed escalation.** Escalation rules sit at the top of the table by precedence,
   so incomplete lineage always beats a confident-looking containment verdict. Missing
   evidence produces an escalation, never an all-clear.
5. **Server-side approval gate.** Enforcement refuses with HTTP 409 unless a recorded
   approval covers that exact plan hash. Regenerate the plan and the approval stops
   applying, rather than silently authorising a scope nobody reviewed.
6. **Real containment.** Freeze an API, quarantine an export, purge and rebuild a vector
   index, rebuild derived tables from an approved source, retrain and replace a model. Every
   action is idempotent and resumable.
7. **Verification by probe.** Post-action probes read the artifacts, not the receipts. Two
   of them are precision probes that must still *succeed*: the analytics report must stay
   queryable and the approved model must stay served, so containment that broke the branch
   it was told to leave alone fails verification too.
8. **Writeback.** Per-artifact revocation status, plan hash, and evidence reference go back
   to DataHub as `GlobalTags` and `DatasetProperties`, each confirmed by re-read.

### The refusals are the product

- `POST /api/execute` answers **409** with the reason when no approval covers the plan.
- A contained endpoint answers **451 Unavailable For Legal Reasons** — not 404, not 500 —
  so a judge watching the network tab can tell containment from an outage.
- `GET /api/readiness` answers **503** with the full check list when degraded.
- Anything outside the project's `license.` namespace is refused before a write is built.
- The documented demo exits **9, not 0**. One descendant reaches the estate through a
  lineage path DataHub cannot complete, so the verdict is `escalated`. An exit 0 there would
  mean the escalation had been lost.

## Category fit

Select exactly one primary category in Devpost: **Production ML Agents**. The demo graph is an
end-to-end ML lineage
chain — source feed → normalized dataset → feature table → trained classifier → vector index
→ prediction endpoint → export — and the agent walks exactly that chain to respond to a
production failure mode that ML teams actually have: a model and a retrieval index that are
still serving output derived from data the organization has lost the right to use. The
response is ML-shaped too, not generic: retrain from an approved source, replace the served
version, purge and rebuild the index, and then verify by reading the *active* model's
training manifest rather than by trusting that the retrain step said it worked.

The implementation also does real approval-gated work: it understands connected data through
DataHub, takes action on real artifacts, and writes results back to the catalog confirmed by
re-read. That is supporting product behavior, not a second category selection.

**Not Metadata-Aware Code Generation.** Nothing here generates code for a repository.

## DataHub integration

The hackathon requires open-source DataHub plus at least one eligible agent tool. This
project uses two DataHub surfaces, one for reads and one for writes.

**Eligible integration: the DataHub MCP Server.** All context reads go over the official
MCP Streamable HTTP transport using the `mcp` Python SDK's `ClientSession` —
`adapters/mcp_client.py`. The client initializes the session, introspects the advertised
tool schemas, clamps `max_results` to the server-advertised maximum, batches `get_entities`,
and calls `get_lineage` with `upstream=false` and bounded hops.

**Writes use the DataHub Python SDK** (`acryl-datahub` pinned to the 1.6 line) emitting real
aspect proposals — `adapters/catalog.py`.

| Operation | Mechanism | Aspects | Scope |
|---|---|---|---|
| Read entity context | `get_entities` via MCP, batched | — | `license.` only |
| Read downstream lineage | `get_lineage` via MCP, `upstream=false`, bounded hops | — | from `license.` sources |
| Read advertised tools | `tools/list` via MCP | — | read-only introspection |
| Seed the demo graph | DataHub SDK proposals | `DatasetProperties`, `Status`, `GlobalTags`, `Domains`, `UpstreamLineage` | `license.` only |
| Reversible tag writeback | DataHub SDK proposal | `GlobalTags` | `license.` only, restored immediately |
| Durable revocation writeback | DataHub SDK proposals | `GlobalTags`, `DatasetProperties` | `license.` only |
| Soft reset / restore | DataHub SDK proposals | `GlobalTags`, `Status` | `license.` + fixture marker only |

What the graph is actually used for, beyond a lookup:

- **Lineage decides the blast radius.** Descendants are discovered by traversal, not from an
  inventory list. One of them is reachable only through a path the catalog cannot complete,
  and that gap is what produces the escalation.
- **Entity context decides classification.** `artifact_class`, `purposes`, `exposure`, and
  `criticality` custom properties drive which rule fires and how the priority score is
  computed.
- **Tags and domain decide scope.** The project tag and the assigned domain are what
  readiness checks coverage against, and the namespace guard refuses anything outside
  `license.` before a proposal is built.
- **Writes are confirmed by re-read.** A write that cannot be read back is reported as
  unverified rather than as success.

Every proposal is checked against a pinned snapshot of DataHub 1.6.0's server-side
entity/aspect registry (`adapters/datahub_entity_registry_1_6_0.json`) before it is built
and again before it is emitted, so an aspect an entity type does not register raises offline
instead of returning 422 from GMS.

## Live evidence versus repository artifacts

This is the distinction judges should hold onto, because the two are not interchangeable and
this project does not let one borrow credibility from the other.

**Everything in the public repository is simulated.** Every artifact under `examples/` and
every receipt produced by the documented quickstart carries `"simulated": true` and was
generated against a deterministic in-memory DataHub substitute with the same client surface
and the same namespace guard. Under `APP_ENV=offline` the console shows a banner saying so
and the evidence report opens with one. No live DataHub receipt is committed here, and none
will be.

**The live verification happened, and it is the deployment coordinator's, not the
repository's.** The shared DataHub instance is coordinator-owned; this project's chat has
never held a session on it. The coordinator ran the live sequence against the exact deployed
commit `eb815889c7743fcc723cc1ad9182b72838476a93` and reported back:

| Live step | Result |
|---|---|
| Public readiness | 200, 10/10 checks, 12 active entities, 9 lineage edges |
| Guarded reversible slice | 8 decisions, 5 escalations, 2 destructive recommendations, `all_clear=false` |
| Slice writeback | `started=true`, `verified=true`, `restored=true`, `residual_risk=false` |
| Soft reset | exactly 12 fixtures removed; readiness correctly degraded to 503 |
| Restore | exactly 12 restored; readiness back to 200, 10/10, 9 edges |
| Judge console `GET /` | 200 |
| Sibling isolation | zero new rows in any of the four sibling projects on the shared instance |

Those receipts live on the deployment host under `APP_STATE_DIR` and are deliberately not
committed. `COORDINATOR_HANDOFF.md` records them in full, including their limits.

**Two things remain unexercised live**, and are stated rather than glossed:

- The *durable* revocation writeback (`POST /api/writeback`, and the writeback stage of
  `demo.cli contain`). The closeout exercised the *reversible* slice writeback, which is the
  mechanism for proving catalog-write capability without leaving state behind. The durable
  path is implemented and covered offline only.
- A stage-by-stage console walkthrough against live data. `GET /` returned 200, which proves
  the console is built and served; nothing beyond that was captured.

**The live run escalates more than the offline demo, and that is correct.** Offline, one
descendant escalates. Live, five do: `get_lineage` returns a degree and never a parent, so
descendants beyond the first hop come back unresolved and are escalated rather than claimed
contained. Same refusal to issue an unearned all-clear, reached from more incomplete
evidence.

## Architecture

```text
Judge console (React + TypeScript)   —  formats; never computes
        │  /api
        ▼
FastAPI application
        ├── rights registry            structured, hashed, versioned events
        ├── DataHub context adapter    MCP reads → entities, lineage, tags, domain
        ├── policy engine              policy/rules.yaml, first-match-wins, cites rule IDs
        ├── impact graph + planner     typed actions, priority score, lineage-path evidence
        ├── approval gate              server-side, bound to one plan hash
        ├── containment adapters       freeze · quarantine · purge · rebuild · retrain · replace
        ├── verification engine        probes artifacts, not receipts
        ├── evidence ledger            hash-chained receipts, secrets redacted before write
        └── DataHub writeback          SDK proposals, confirmed by re-read
        ▼
Disposable local estate: DuckDB tables · TF-IDF vector index · toy classifier ·
prediction endpoint · CSV export
```

The console formats and never decides: every verdict, status, and residual-exposure figure
it renders is read from the API. That is deliberate, so nothing a judge sees on screen was
computed by the page showing it.

**Built with:** Python 3.12, FastAPI, Pydantic, NetworkX, DuckDB, scikit-learn, httpx;
React + TypeScript + Vite; `acryl-datahub` SDK and the `mcp` Python SDK; pytest and ruff.

## User journey

1. A governance operator selects a source asset and records a rights change. The demo revokes
   **training** and **retrieval** on a partner review feed and retains **analytics** — which
   is what makes the unaffected branch provable rather than asserted.
2. The agent reads the source's context and walks downstream lineage from DataHub.
3. Descendants are classified and matched against the rule table. Eight decisions come back,
   each citing a rule ID and a lineage path.
4. The operator inspects the impact graph, including the branch that is *not* affected and
   the one whose lineage path is incomplete.
5. Enforcement is attempted before approval and refuses — 409, reason rendered verbatim.
6. The operator approves that exact plan hash.
7. Containment executes: freeze, quarantine, purge, rebuild ×3, retrain, replace. One step
   reports no change because the rebuild was already satisfied, rather than claiming work it
   did not do.
8. Verification re-probes every artifact — six containment probes and two precision probes.
9. Status and evidence go back to DataHub, each confirmed by re-read.
10. The verdict is `escalated`, not `contained`, and the residual table names why.

## Setup, testing, and adoption

**Judges can test it three ways, none of which cost anything.**

*Read-only against the public deployment.* `GET /api/health` and `GET /api/readiness` on
<https://license.datahub-hackathon.aaronmathias.com> report the application's state, and the
judge console is served at `/`. Nothing there requires credentials.

*Locally, with no catalog, no credentials, and no network,* which is the reproducible path:

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev]"     # Windows
# .venv/bin/python -m pip install -e ".[dev]"           # macOS / Linux
cp .env.example .env                                    # set APP_ENV=offline

python -m demo.cli estate build          # build the disposable local estate
python -m demo.cli probe                 # partner content is being served
python -m demo.cli contain --approve     # plan, approve, execute, verify, write back
python -m demo.cli probe                 # the same probes now refuse
```

`contain` exits **9**. Drop `--approve` to watch the gate refuse instead: the plan is
computed in full, nothing is touched, exit **8**.

*By reading the captured output* in `examples/containment-report.md` — a verbatim offline
run, decisions, probes, residual exposure, and all.

For the console, `npm --prefix web install && npm --prefix web run build`, then
`python -m app.main` on <http://127.0.0.1:8102>. Node is optional: the API and the CLI run
without it, and the static mount is added after the API router so it can never shadow a
route.

**Adoption path for a real team.** The rights model, the rule table, and the containment
adapters are the three seams. `policy/rules.yaml` is data, so an organization's own policy is
an edit to a table rather than a code change; adapters implement a small typed interface, so
a real serving platform slots in where the demo's local endpoint sits; and the namespace
guard means a first deployment can be scoped to one allocation before it is trusted with
more. What does not move is the shape: deterministic policy owns the verdict, approval is
server-side and plan-bound, and verification probes artifacts rather than receipts.

## Challenges

Every one of these was found by a gate rather than guessed at, and each is recorded as an ADR.

- **The entity model contradicted itself, and nothing local could catch it.** The catalog
  adapter documented a uniform `dataset` URN model while the fixture graph minted native
  `mlModel` and `mlFeatureTable` URNs. The live seed failed with HTTP 422 "Unknown aspect
  datasetProperties for entity mlModel". The SDK will happily attach any aspect to any URN,
  and the offline fake hardcoded the entity type, so the contradiction was invisible until
  GMS saw it. The fix was not just to pick a model but to make the fake enforce what GMS
  enforces: every proposal is now checked against a pinned snapshot of DataHub 1.6.0's
  registry (ADR-024).
- **A pinned dependency changed a transport signature and the tests could not see it.**
  `mcp` 1.28 removed `headers=` from `streamable_http_client`, so every session raised before
  issuing a request. The suite had asserted on *source text* — true of a call that cannot
  execute. Tests now bind against `inspect.signature` of the installed library, and one test
  drives the genuine unpatched transport (ADR-026).
- **The real payload envelopes were nothing like the assumed ones.** `{"result": [...]}` not
  `entities`; `customProperties` a list of key/value pairs, not a mapping; tags nested two
  levels deep; lineage arriving as `{"downstreams": {"total": N, "searchResults": [...]}}`
  and not parsed at all. Readiness reported 12 entities unusable against a correctly seeded
  instance. Worse, an unreadable response and a genuinely empty catalog rendered identically.
  They are opposite conditions, and a payload the normalizers cannot read now raises an error
  naming the keys it actually received (ADR-027).
- **A boolean is not a count.** `bool` subclasses `int`, so `isinstance(total, int)` admitted
  `False`, `False == 0`, and zero was the value licensing a missing result set to be read as
  an empty descendant set. A malformed payload would have produced an empty impact set — a
  clean bill of health. Caught by an independent pre-deployment review before it ever
  reached a live gate (ADR-028).
- **A successful seed does not guarantee readable lineage.** The live closeout found nine
  `upstreamLineage` aspects present in primary storage while eight of the corresponding edges
  were missing from the graph index. Seed verifies aspects, not the index, so that state
  passes seeding and fails readiness. The repair is an exact-URN, aspect-scoped
  `restoreIndices` — emphatically *not* a reseed, which would rewrite correct primary aspects
  to work around a stale index (ADR-029).
- **An evidence report leaked an absolute path, and nothing caught it.** The writeback
  section interpolated a receipt list into an f-string and emitted a dict repr carrying the
  developer's home directory name, in a file bound for a public repository. Public safety is
  now a test that scans the exact shippable file set, not a checklist (ADR-022, ADR-023).
- **A false all-clear in the first slice run.** The prediction API and the CSV export came
  back `no_action`, because the affected check compared only each descendant's own declared
  purposes — so an API declaring `serving` looked clean while serving output from a model
  trained on revoked data. Contamination now propagates downstream from any descendant using
  a revoked purpose, while deliberately not propagating from the source itself, which is what
  preserves the unaffected analytics branch (ADR-013).

## Accomplishments

- **Containment that is verified, not asserted.** The verdict is derived: `contained`
  requires every step completed, every probe passed, and an empty residual list. The demo's
  honest answer is `escalated`, and the tooling reports it as a non-zero exit.
- **Verification that fails on over-reach.** Two precision probes must still succeed after
  containment. Breaking the branch you were told to leave alone is a failure, not a rounding
  error.
- **Approval bound to a plan hash**, enforced server-side, with the console's Execute button
  deliberately left enabled so the refusal can be watched rather than described.
- **Refusals that carry meaning** — 451, 409, 503 — so containment is legible from a network
  tab.
- **Fail-closed by construction.** Escalation rules outrank containment rules by precedence.
  An unparseable URN raises. An empty target list is an error, not a no-op. A malformed
  lineage payload raises rather than reading as an empty graph.
- **A live closeout that passed end to end** on the deployed commit, including a reversible
  catalog writeback confirmed by re-read and rolled back, and a matched reset/restore pair
  with a correct 503 in between.
- **727 tests, 90.69% line coverage**, ruff clean, a strict-mode TypeScript console, a
  packaging gate that installs a built archive into an isolated virtualenv, a public-safety
  gate that scans the shippable file set, and 29 architectural decision records.

## Limitations

Stated plainly, because a governance tool that oversells is worse than one that does less.

- **Not legal advice.** It supports compliance operations. It does not interpret contract
  text and makes no determination about whether any obligation has been met.
- **Containment covers descendants represented in the DataHub graph.** Untracked copies,
  offline extracts, and systems outside the catalog are not addressed.
- **Stopping a model from serving is not proof of unlearning.** The tool says so in the rule
  that produces the action.
- **The receipt ledger is hash-chained and tamper-evident, not tamper-proof.**
- **Purpose metadata is read from custom properties this project seeds.** In a real estate
  it would come from the organization's own governance metadata.
- **Every fixture entity is a `dataset` URN** carrying an `artifact_class` custom property,
  so the model and the feature table appear in DataHub as datasets on the `mlflow` and
  `feast` platforms rather than as native `mlModel` and `mlFeatureTable` entities. DataHub
  1.6.0 registers neither `datasetProperties` nor `upstreamLineage` on those types, so a
  native entity cannot carry the properties the policy engine reads or the lineage the impact
  analysis walks (ADR-024).
- **Multi-hop lineage is degree-based live.** `get_lineage` returns a degree and never a
  parent, so descendants beyond the first hop are reported unresolved and escalated rather
  than given a fabricated one-hop path.
- **Single-operator assumption.** Seed and reset are not mutually exclusive across processes.
- **The console is a build artifact, not a checked-in one.** A deployment that does not run
  the Node build serves the API correctly and serves no console.

## Claims this submission does not make

- That it provides legal advice, or automatically interprets any license or contract.
- That it finds every copy, including untracked offline files.
- That it cryptographically proves model unlearning.
- That anything **in this repository** was verified against a live DataHub instance. The
  deployed build was, by the deployment coordinator; that run is not committed here, is not
  what any recording shows, and is not reproducible by a judge from this checkout.

## Remaining manual steps before the Devpost form is submitted

1. Put the verified public demo URL, <https://youtu.be/42FznbyhYlA>, in the Devpost video field.
2. Copy the tagline, short description, and the sections above into the Devpost form.
3. Confirm on Devpost that the repository is public and the Apache 2.0 license is visible
   from the repository About area.
4. Re-check the official rules and the deadline near submission time.
