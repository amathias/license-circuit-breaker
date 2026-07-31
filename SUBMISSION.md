# License Circuit Breaker — Devpost submission

This file is the submission copy of record. Paste from it into the Devpost form; do not
rewrite the claims in the form, because every one of them is calibrated against what the
repository and the deployment can actually show.

- **Public app:** <https://license.datahub-hackathon.aaronmathias.com>
- **Public repository:** <https://github.com/amathias/license-circuit-breaker>
- **License:** Apache 2.0, at the repository root
- **Primary category:** Production ML Agents
- **Demo video:** <https://youtu.be/kDOZsckifQw> (2:25, public, English captions)

> License Circuit Breaker supports compliance operations. It does not provide legal advice,
> does not interpret contract text, and makes no determination about whether any legal or
> contractual obligation has been met. The rights it acts on are the ones an operator
> recorded as structured data.

---

## Tagline

Executable data-rights revocation with DataHub. Trace every represented descendant, contain what
can be proved, and escalate what cannot.

## Short description

License Circuit Breaker is a DataHub-powered enforcement agent for data and AI supply
chains. When an upstream usage right is revoked, it reads DataHub lineage and entity
context over the DataHub MCP Server, traces every represented descendant, applies a
deterministic rule table to produce a typed containment plan, refuses to act until a human
approves that exact plan, executes real actions against disposable local artifacts, and verifies
containment by probing the artifacts rather than trusting its own receipts. It also implements
supported DataHub status writeback; the public deployment has separately verified a reversible
write/reread/restore slice.

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
5. **Server-side approval gate.** In every execution-enabled mode, enforcement
   refuses with HTTP 409 unless a recorded approval covers that exact plan hash. Regenerate the
   plan and the approval stops applying.
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

- In every execution-enabled mode, `POST /api/execute` answers **409** with the
  reason when no approval covers the plan.
- The hosted judge workflow automatically obtains a fresh one-time confirmation for each
  approval, execution, writeback, and artifact reset. The operations are serialized,
  cooldown-controlled, rate-limited, and fixed to this project's isolated scenario.
- Public reset cannot delete governance history and invalidates the prior approval. Public resume
  and the internal adapter fault injector are not exposed.
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

The reproducible repository examples are explicitly simulated and use a deterministic in-memory
DataHub substitute with the same client surface and namespace guard. The offline console and
reports label that boundary clearly.

Public-environment verification established 12 active project entities, 9 lineage edges, strong
readiness, reversible write/reread/restore behavior, reset/readiness/restore transitions, and no
new rows in sibling projects. The hosted judge console exposes the fixed end-to-end workflow
through bounded operation confirmations; it does not expose arbitrary targets or fault injection.

Two boundaries remain important:

- The guarded hosted workflow was exercised separately from the recorded offline demo. It
  produced a truthful `residual` verdict and verified all 8 durable DataHub outcome writes by
  reread; a final artifact reset preserved the audit history and invalidated the approval.
- The offline workflow escalates one unresolved descendant. Live MCP evidence leaves five
  multi-hop descendants unresolved, so the live result escalates them instead of issuing an
  unearned all-clear.

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

The complete execution journey is reproducible locally and in the hosted judge console. Hosted
mutations are limited to the fixed disposable scenario and protected by one-time confirmations,
single-flight execution, cooldown, and sliding-window limits.

1. A governance operator selects a source asset and records a rights change. The demo revokes
   **training** and **retrieval** on a partner review feed and retains **analytics** — which
   is what makes the unaffected branch provable rather than asserted.
2. The agent reads the source's context and walks downstream lineage from DataHub.
3. Descendants are classified and matched against the rule table. Eight decisions come back,
   each citing a rule ID and a lineage path.
4. The operator inspects the impact graph, including the branch that is *not* affected and
   the one whose lineage path is incomplete.
5. In execution-enabled mode, enforcement is attempted before approval and refuses — 409, reason
   rendered verbatim.
6. The operator approves that exact plan hash.
7. Containment executes: freeze, quarantine, purge, rebuild ×3, retrain, replace. One step
   reports no change because the rebuild was already satisfied, rather than claiming work it
   did not do.
8. Verification re-probes every artifact — six containment probes and two precision probes.
9. The implemented durable writeback records status and evidence in DataHub and confirms each by
   reread; the live closeout instead used the reversible write/reread/restore slice.
10. The verdict is `escalated`, not `contained`, and the residual table names why.

## Setup, testing, and adoption

**Judges can test it three ways, none of which cost anything.**

*Interactively against the public deployment.* Open
<https://license.datahub-hackathon.aaronmathias.com>, attempt execution to see the approval gate
refuse, approve the exact plan, execute, verify the artifact probes, and write the verified
outcomes back to DataHub. The console obtains the required one-time confirmations automatically;
nothing requires credentials.

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

- **Matching DataHub's real entity and aspect contracts.** The local substitute originally hid
  invalid entity/aspect combinations. I added validation against DataHub 1.6.0's registry so bad
  proposals fail before GMS sees them.
- **Treating ambiguous metadata as unsafe.** Real MCP envelopes and indexed lineage differed from
  early assumptions. The adapters now parse the supported shapes strictly and distinguish an empty
  graph from an unreadable one; incomplete multi-hop evidence escalates.
- **Preventing a false all-clear.** A descendant can serve content derived from revoked data even
  when its own declared purpose is not revoked. The policy now propagates contamination downstream
  while preserving the explicitly unaffected analytics branch.
- **Keeping evidence public-safe.** Automated checks scan the shippable file set for local paths,
  secrets, and unlabeled simulated evidence.

## Accomplishments

- **Containment that is verified, not asserted.** The verdict is derived: `contained`
  requires every step completed, every probe passed, and an empty residual list. The demo's
  honest answer is `escalated`, and the tooling reports it as a non-zero exit.
- **Verification that fails on over-reach.** Two precision probes must still succeed after
  containment. Breaking the branch you were told to leave alone is a failure, not a rounding
  error.
- **Approval bound to a plan hash**, enforced server-side, with the local console's Execute button
  deliberately left enabled so the 409 refusal can be watched rather than described.
- **Refusals that carry meaning** — 451, 409, 503 — so containment is legible from a network
  tab.
- **Fail-closed by construction.** Escalation rules outrank containment rules by precedence.
  An unparseable URN raises. An empty target list is an error, not a no-op. A malformed
  lineage payload raises rather than reading as an empty graph.
- **Verified live DataHub integration**, including a reversible catalog writeback confirmed by
  reread and restored, plus matched reset/readiness/restore behavior.
- **Passing quality and packaging gates**, including automated tests, linting, strict-mode
  TypeScript, isolated package installation, and public-safety scanning.

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
  analysis walks.
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
- That repository examples are live receipts. They are deterministic, clearly labeled simulations;
  live verification is summarized separately above.
