# Architectural Decisions

Maintained per `AGENTS.md`. Newest last. Each entry records what was decided, why,
and what would cause it to be revisited.

---

## ADR-001: Namespace guard is built before any writing code path

**Date:** 2026-07-24 · **Status:** accepted

Five hackathon submissions share one DataHub instance. `COORDINATOR_PLAN.md` requires
that seed, reset, mutation, and writeback fail closed outside this project's `license.`
allocation.

The guard (`app/namespace.py`) was implemented and tested *before* any code that
writes. Retrofitting isolation onto existing call sites reliably misses one, and the
cost of a miss is corrupting another team's demo state.

Design points:

- **Fail closed.** An unparseable URN raises rather than passing through — anything
  that cannot be *proven* in-namespace is refused.
- **No global selectors.** `*`, `all`, `%`, and empty string are rejected outright
  rather than pattern-matched.
- **Empty target list is an error, not a no-op.** Callers have historically treated
  "no targets" as "everything"; on a shared instance that is catastrophic.
- **Filesystem targets get the same treatment.** Containment adapters act on real
  paths, so `require_path_within` resolves symlinks and `..` before comparing, and
  refuses the root itself so a purge cannot wipe every fixture.

**Revisit if:** the coordinator changes the allocation, or DataHub URN formats change.

---

## ADR-002: Deterministic policy lives in a data table, not code

**Date:** 2026-07-24 · **Status:** accepted

`PROJECT_BRIEF.md` requires that policy decisions be deterministic, independently
testable, and cite rule IDs. `AGENTS.md` forbids an LLM from making the final
enforcement decision.

Rules live in `policy/rules.yaml` as an ordered table with stable IDs (`LCB-R001`…),
evaluated by `app/policy.py`. Judges and operators can read the entire decision space
in one file, and `GET /api/policy/rules` exposes it at runtime.

Loader validates: duplicate IDs, duplicate precedences (which would make evaluation
order depend on file ordering — a silent non-determinism), and unknown actions.

The table ends with an unconditional escalation backstop, so evaluation always
produces a decision rather than falling through.

**Revisit if:** rules need to combine additively rather than first-match-wins.

---

## ADR-003: Escalation outranks containment

**Date:** 2026-07-24 · **Status:** accepted

The fail-closed rules occupy the lowest precedence band, so incomplete lineage,
a missing lineage path, or an unclassifiable artifact always beats a
confident-looking containment verdict.

During testing, an artifact with *no* lineage paths matched the generic
"lineage incomplete" rule (`LCB-R001`) before the specific "no path could be
cited" rule (`LCB-R002`), because absent paths also report as incomplete. Both
escalate, so behavior was safe, but the operator-facing diagnosis was wrong.
`LCB-R002` now evaluates first. The more specific diagnosis wins.

Escalations also receive a priority bonus, so incomplete evidence is never buried
at the bottom of a long plan.

**Revisit if:** operators report escalation fatigue on realistically messy graphs.

---

## ADR-004: A descendant is not automatically in scope

**Date:** 2026-07-24 · **Status:** accepted

`is_affected` compares the purposes an artifact currently uses against the purposes
the rights event actually removed. An analytics table downstream of a feed that lost
only its *training* right keeps operating.

This precision is the difference between the product and a blunt subtree delete, and
`PROJECT_BRIEF.md` requires an unaffected branch in the demo to prove it.

**Revisit if:** purpose metadata proves unavailable from DataHub for real artifacts.

---

## ADR-005: Containment is claimed only when verification passes

**Date:** 2026-07-24 · **Status:** accepted

`ContainmentEvidence.contained` requires both `succeeded` and
`verification_passed is True`. A successful adapter call with no passing verification
is not containment.

This is what prevents the false all-clear that `BUILD_PLAN.md` makes a phase exit
condition. `verification_passed` is deliberately tri-state (`None` = not yet verified)
so "not checked" cannot be mistaken for "passed".

**Revisit if:** an adapter genuinely cannot be verified, in which case it must report
residual exposure rather than relaxing this rule.

---

## ADR-006: Readiness probes must never mutate shared state

**Date:** 2026-07-24 · **Status:** accepted

`/api/readiness` checks state directory writability, policy table load, namespace
guard configuration, and DataHub reachability via a read-only `GET /config`.

On a shared instance polled by a reverse proxy, a readiness probe that wrote would
corrupt four other submissions on every poll. `tests/test_api.py` asserts no write
verbs are issued during a readiness call.

Health and readiness are kept separate: health stays green when DataHub is down, so
the proxy does not restart a healthy process over a remote dependency.

---

## ADR-007: Local TF-IDF vector index instead of sentence-transformers

**Date:** 2026-07-24 · **Status:** accepted

The demo needs a RAG-style retrieval surface. `sentence-transformers` would pull a
multi-hundred-megabyte model, which breaks the clean-checkout test judges run and
adds latency to a demo video capped at three minutes.

A TF-IDF vector index with cosine similarity, persisted under `APP_STATE_DIR`, is
deterministic, instant, and dependency-light. It will be described honestly as a
local deterministic vector index — not as a semantic embedding store.

**Revisit if:** the demo narrative requires semantic (not lexical) retrieval.

---

## ADR-008: Python 3.13 for Milestone A, 3.12 pinned for Milestone B

**Date:** 2026-07-24 · **Status:** provisional

`pyproject.toml` declares `requires-python = ">=3.12"`. Only 3.13.2 is installed on
the development machine.

Milestone A depends on FastAPI, Pydantic, PyYAML, and pytest, all of which work on
3.13, so work proceeded rather than blocking on an install. `datahub-agent-context`
compatibility with 3.13 is unverified and does not enter until Milestone B.

**Action:** verify `datahub-agent-context` on 3.13 at the start of Milestone B. If it
fails, install 3.12 and recreate the venv — cheap, since no DataHub code exists yet.

---

## ADR-009: `DATAHUB_TOKEN` mapped to `DATAHUB_GMS_TOKEN` at launch

**Date:** 2026-07-24 · **Status:** provisional — pending coordinator confirmation

The shared contract names the token `DATAHUB_TOKEN`. `mcp-server-datahub` reads
`DATAHUB_GMS_TOKEN`. `Settings.datahub_env()` maps between them when launching
DataHub tooling, rather than renaming a portfolio-wide contract to suit one
project's dependency.

Escalated to the coordinator as open item 2 in `IMPLEMENTATION_PLAN.md`.

---

## ADR-010: Writeback is reversible by construction

**Date:** 2026-07-25 · **Status:** accepted

`reversible_tag_writeback` captures the prior aspect, writes, immediately re-reads
to prove the write landed, then restores the prior state.

Two reasons. First, the shared instance hosts five submissions and a demo that
accumulates residue every run degrades everyone's state. Second, a write is not
evidence until DataHub reports it back — the re-read is the proof, not the 200
response.

Restoration runs even when verification fails: a write that landed but could not be
confirmed still has to be rolled back. `verified` and `restored` are recorded
separately and `clean` requires both, so an unrestored write can never be reported
as a successful one.

**Revisit if:** the demo needs durable revocation status in DataHub, which is a
different operation with its own lifecycle.

---

## ADR-011: The fake enforces the same guards as the live client

**Date:** 2026-07-25 · **Status:** accepted

`FakeDataHubClient` calls the same `require_in_namespace` guard on writes that
`HttpDataHubClient` does.

A permissive fake would make every isolation test vacuous — foreign writes would
pass in tests and fail only in production, which is the worst possible place to
discover it. `tests/test_isolation.py::TestFakeMatchesLiveGuards` asserts the guard
is actually wired in, so the fake cannot silently drift more permissive.

---

## ADR-012: Readiness requires positive proof, not reachability

**Date:** 2026-07-25 · **Status:** accepted

A reachable GMS is explicitly **not** sufficient to report ready. Readiness requires
a configured token, an MCP endpoint advertising every required tool, the project
domain and tag, and at least one readable `license.` entity.

The failure this prevents: an unauthenticated or unseeded instance answers
`/config` happily and returns empty result sets. An impact analysis over an empty
graph is indistinguishable from an all-clear. A worker missing `get_lineage` fails
the same way. Unrunnable checks are reported failed, never skipped — an unrunnable
check is not a passing check.

---

## ADR-013: Contamination propagates downstream from affected descendants

**Date:** 2026-07-25 · **Status:** accepted

The first offline run of the vertical slice reported `no_action` for the prediction
API and the CSV export.

`is_affected` compared only a descendant's own declared purposes against the
revoked set. The API declares `serving` and the export declares `export`; the demo
event revokes `training` and `retrieval`. Neither matched, so both looked clean —
despite the API serving predictions from a model trained on revoked data.

That is a false all-clear, the specific failure this product exists to prevent, and
it directly contradicted the demo narrative in `DEMO_AND_SUBMISSION.md`.

An artifact is now in scope when its own purposes are revoked **or** an ancestor on
a lineage path uses a revoked purpose. Propagation starts at the descendants, not
the source: treating the revoked source as contaminating would sweep in the
analytics branch and destroy the precision that makes an unaffected result
meaningful.

Found by running the slice rather than by a test — the unit tests used synthetic
descendants and never exercised a full path. Four regression tests now cover it.

**Revisit if:** artifacts need per-column or per-partition contamination rather
than whole-artifact.

---

## ADR-014: MCP uses the official SDK session, not hand-rolled JSON-RPC

**Date:** 2026-07-25 · **Status:** accepted · **Supersedes part of ADR-009**

The first implementation posted JSON-RPC envelopes to the MCP URL with `httpx` and
parsed SSE frames by hand. It never called `initialize`, which a conforming server
is entitled to reject — the transport worked only by accident of the server being
lenient.

`adapters/mcp_client.py` now uses `mcp.ClientSession` over
`streamable_http_client` with a real handshake. Tool schemas are introspected
rather than assumed: `max_results` is clamped to the server's advertised maximum,
and arguments a given worker does not declare are dropped instead of sent.

`get_lineage` passes `upstream=False` explicitly. Some builds default to upstream,
and relying on the default would return ancestors and produce a silently empty
impact set. `max_hops` is bounded so traversal cannot wander into another
project's subgraph before the namespace filter runs.

`get_entities` is batched — one call for the whole demo graph instead of twelve.

**Revisit if:** the application becomes async, at which point the session should be
held open rather than opened per call.

---

## ADR-015: Seed emits a full catalog entry and verifies it by rereading

**Date:** 2026-07-25 · **Status:** accepted

Live seed previously called only `set_tags`. A live instance received tags and
nothing else: no dataset properties, no `artifact_class`, no domain, no explicit
active status, no lineage. The graph looked seeded and was unusable — and because
the offline fake was populated directly, no test noticed.

`adapters/catalog.py` emits `DatasetProperties`, `Status(removed=False)`,
`GlobalTags`, `Domains`, and `UpstreamLineage` as SDK proposals. Seed then
**rereads every allowlisted entity and edge** and raises `VerificationError`
listing everything that failed. Emitting without verifying reports success for
writes that never landed.

**Entity model:** everything is a `dataset` URN carrying an `artifact_class`
custom property, including models, feature tables, indexes, APIs, and exports.
Native `mlModel` / `mlFeatureTable` entities are the better semantic fit but need
their own aspect sets and lineage handling. A uniform dataset model is what can be
verified deterministically in one milestone, and `artifact_class` carries the
semantics the policy engine actually consumes. This closes the open question from
task 4 of the original plan.

**Revisit if:** the demo needs native ML entity types in the DataHub UI.

---

## ADR-016: Reset is a soft, exactly-allowlisted, reversible operation

**Date:** 2026-07-25 · **Status:** accepted

Reset previously replaced tags with an empty list, which is neither a removal nor
a state a catalog can represent meaningfully.

Reset now sets `Status(removed=True)` and clears the project tag association;
`restore` reverses both. Soft state is reversible, preserves the audit trail, and
never touches the shared domain or tag control entities — those are
coordinator-owned scaffolding this project references and never mutates.

The target set must match the allowlist **exactly**. Missing, extra, unmarked, or
empty all fail closed. Strictness is deliberate: a partial set means fixtures were
removed or re-tagged out of band, so the instance and the allowlist disagree about
what this project owns. Soft-deleting a subset and reporting success would leave
an operator believing the reset was complete. Partial failures during execution
are recorded per-entity rather than aborting silently.

**Revisit if:** hard deletion is ever required, which would need its own approval
gate.

---

## ADR-017: Restoration runs in `finally`, from the moment the write is attempted

**Date:** 2026-07-25 · **Status:** accepted · **Extends ADR-010**

ADR-010's rollback ran only after a successful write and re-read. If the verifying
re-read raised, the write had already landed and the exception took the short path
out — leaving a stray tag on an instance shared with four other submissions.

Rollback now runs in `finally`, armed the moment the write is attempted. The
receipt records `started`, `write_failed`, `verified`, and `restored`
independently, plus a derived `residual_risk` for "may have landed, was not
restored". Collapsing these into one success flag would hide the fact that the
instance was touched.

The CLI exits non-zero unless the writeback was both verified **and** restored. A
good plan with a dirty writeback is not a passing run, and exiting zero would let
CI and the coordinator's promotion check treat residue as success.

---

## ADR-018: Readiness mutates nothing at all

**Date:** 2026-07-25 · **Status:** accepted · **Extends ADR-012**

ADR-012's readiness wrote and deleted a `.readiness` probe file to test
writability. On an endpoint a reverse proxy polls, that is a filesystem mutation
on every poll. A probe that mutates is not a probe.

Writability is now inferred with `os.access`, and a missing state directory is
*reported*, not created — creation belongs to application startup.

Two checks were also genuinely broken. The domain comparison was conditional on a
domain being present, so an entity with `domain=None` passed the check that exists
to catch exactly that. And coverage was proven only against the sentinel, so a
partially seeded instance read as ready. Readiness now requires the exact project
domain, both tag controls, every allowlisted entity active with required custom
properties, and complete fixture lineage.

---

## ADR-019: Packaging ships every runtime package, proven by installing it

**Date:** 2026-07-25 · **Status:** accepted

`pyproject.toml` declared only `app` and `adapters`. A wheel built from it omitted
`demo` (the fixture graph and the seed/slice CLI) and `policy` (the rule table), so
an installed archive imported but could not seed, slice, or evaluate a single
rule. The source-tree test suite could not notice, because a source checkout has
those directories on `sys.path` regardless.

`policy` is now a package shipping `rules.yaml` as package data, and the loader
resolves it through the package rather than by walking up from `app/`. The live
`acryl-datahub` and `mcp` dependencies are declared. A console script is exposed.

`tests/test_packaging.py` assembles the shippable file list, installs it into an
isolated virtualenv, imports every package, loads the rule table, and runs the
offline slice end to end. It also asserts `.env`, `.state`, and `.venv` never reach
the archive. It is marked `slow` — roughly twelve minutes, dominated by two
isolated `acryl-datahub` installs.

The archive is built from `git ls-files --cached --others --exclude-standard`
rather than `git archive HEAD`, so it validates the tree being worked on. Using
`HEAD` meant every packaging fix failed its own test until it happened to be
committed.

---

## ADR-020: The console formats; it never decides

**Date:** 2026-07-26 · **Status:** accepted

Every verdict, status, probe result, and residual exposure the judge console shows
is read from the API. The page computes nothing. If the page and the evidence
report ever disagreed, the report would be the one to trust, so the page is given
nothing to disagree with.

Two consequences that look like bugs and are not:

The **Execute button stays enabled without an approval.** Disabling it would hide
the control being demonstrated. Pressing it produces a real 409 from the server
with the real reason, rendered verbatim. The gate lives in `app/api.py`, so it
holds for anything that can reach the port — curl included — rather than for
whatever the browser chooses to send.

The graph is **hand-drawn SVG** with a deterministic layered layout, not a graph
library. The picture is identical in every take of a recorded demo, and the
console installs from two runtime dependencies rather than fifty. Colour encodes
the policy verdict, never the DataHub entity type: the artifact class is written
on the node anyway, and what a judge is looking for is what happens to it.

---

## ADR-021: The console/API contract is read from the OpenAPI schema

**Date:** 2026-07-26 · **Status:** accepted

`tests/test_console.py` asserts that every `/api/...` path the console fetches is
actually served. The first implementation walked `app.routes` recursively. That
passed in a full-suite run and failed when the module ran alone: this FastAPI
version keeps an included router as an opaque `_IncludedRouter` container rather
than flattening its endpoints onto the application, so the walk found the
container and none of the routes inside it. Whether the test passed depended on
which other test module had run first.

The check now reads `app.openapi()["paths"]`. That is the supported way to ask
what an application serves, it is what a judge reading `/docs` sees, and it does
not depend on a private attribute whose shape has already changed once.

The lesson generalizes: an assertion that reads a framework's internals is not
testing the framework's behavior, it is testing this month's implementation of it.

---

## ADR-022: The writeback section of the report is a table

**Date:** 2026-07-26 · **Status:** accepted

`EvidenceBundle.to_markdown` rendered the writeback dictionary by interpolating
each value into a bullet. For scalars that reads fine. For the `receipts` list it
emitted a Python dict repr: one unreadable line, several thousand characters long,
in the section a reviewer opens the report to read — and it carried the absolute
evidence path once per receipt.

Receipts now render as a table of artifact, status, tag, aspects, and verified.
Regressions assert that `'urn':` never appears in the output and that an
unverified receipt reports `NO` rather than being quietly formatted as success.

---

## ADR-023: Public-safety is a test, not a checklist

**Date:** 2026-07-26 · **Status:** accepted

This repository is published. `tests/test_public_safety.py` scans the exact file
set that would ship — `git ls-files --cached --others --exclude-standard`, so it
covers uncommitted work rather than only what is already in a commit — and fails
on credential shapes, on `.env`, on runtime state, and on absolute home-directory
paths.

The home-path check exists because that leak already happened. A captured evidence
report was generated into a temporary directory, and the absolute path of that
directory — including the developer's account name — was embedded in the report
body and in the `lcb_evidence_ref` custom property it recorded into the catalog.
Nothing failed. It was caught by reading the file. The example is now generated
with a relative `APP_STATE_DIR`, and a test enforces it.

Credential detection is deliberately narrow, and fixtures must announce themselves
in the value (`fixture-token`, not `abc123`). A pattern that fired on the word
"token" would be muted within a week and would then be protecting nothing, and a
blanket exemption for `tests/` would be worse: a real token pasted into a test is
published exactly as surely as one pasted into `app/`.

---

## ADR-024: One entity type, enforced against a pinned aspect registry

**Date:** 2026-07-26 · **Status:** accepted · **Supersedes the entity-model note in ADR-015**

The first live seed failed with **HTTP 422 "Unknown aspect datasetProperties for
entity mlModel."** The code contradicted itself: `adapters/catalog.py` and the
coordinator handoff both documented a uniform `dataset`-URN model carrying an
`artifact_class` custom property, while `demo/graph.py` minted native `mlModel`
and `mlFeatureTable` URNs for three nodes. The seed then attached
`DatasetPropertiesClass` to all of them.

**Why nothing caught it.** Three layers each had a reason not to:

- The SDK derives `entityType` from the URN and accepts any aspect object beside
  it. `MetadataChangeProposalWrapper(entityUrn=<mlModel urn>, aspect=DatasetProperties)`
  builds without complaint; only GMS rejects it.
- The offline seed path never built a proposal at all. It called
  `add_entity(..., entity_type="dataset")` on the in-memory fake — hardcoded — so
  the fake recorded a dataset for an `mlModel` URN and every offline assertion
  passed.
- `DemoNode.entity_type` was a hand-declared field that nothing read. It said
  `mlModel`; the emitted aspects said dataset; neither was checked against the
  other.

**Decision: keep the uniform dataset model, and make it executable.**

Native ML entities are the better semantic fit, and the pinned registry shows
precisely why they are not viable here. DataHub 1.6.0 registers on `mlModel` and
`mlFeatureTable` neither `datasetProperties` — which carries `artifact_class`,
`purposes`, `exposure`, and `criticality`, the entire input to the policy engine —
nor `upstreamLineage`, which is the edge set the impact analysis walks. Native
support means `mlModelProperties`/`mlFeatureTableProperties`, a different lineage
mechanism, and a live run to verify all of it. That was not available this
session, and shipping a half-migrated model would have been worse than either
end state.

So the three ML URNs became dataset URNs. The platform segment keeps the
semantics visible — `mlflow`, `feast`, `vectorstore`, `rest-api`, `file` — and
`artifact_class` still carries what the policy engine consumes. Nothing about the
demo's ML story is lost: the training path, the feature table, and the model are
all still distinct, still classified, and still shown under their real platforms.

Three enforcement points replace the prose:

1. `DemoNode.__post_init__` derives the entity type from the URN and raises at
   import time on anything that is not `dataset`. The stale field is gone; a
   fact that can be derived is not stored.
2. `adapters/entity_registry.py` checks every proposal against a pinned snapshot
   of DataHub's server-side registry, taken from `entity-registry.yml` at tag
   `v1.6.0` to match the `acryl-datahub==1.6.0.15` pin. The snapshot is stored in
   the shape `EntityAspectSpecs.to_dict()` produces, so the SDK's own type parses
   it and a live server's registry can be diffed against it directly. The gate
   sits in `build_entity_proposals` *and* in `LiveCatalog.emit`, the last point
   before the network.
3. Both seed paths now build the same real SDK proposals. The offline path
   discards them, which looks wasteful and is the entire point: writing to the
   fake now enforces exactly what writing to GMS enforces, and the entity type it
   records is derived from the URN rather than hardcoded.

`tests/test_entity_aspect_contract.py` asserts the failing pairs as facts
(`mlModel` + `datasetProperties`, `mlFeatureTable` + `upstreamLineage`), builds
every proposal the seed emits, round-trips each through the emitter's own
`to_obj`/`from_obj`, and checks every `(entityType, aspectName)` against the
snapshot. A fake cannot satisfy that suite, because no fake is involved in it.

**Cost, stated plainly:** the catalog shows a model as a dataset on the `mlflow`
platform rather than as a native `mlModel`. That is a real fidelity loss and the
right trade only until native aspect sets can be implemented *and verified live*.
Reversing this decision means implementing those aspect sets end to end — not
just changing the URNs back.

---

## ADR-025: Seed is the recovery path, and says so

**Date:** 2026-07-26 · **Status:** accepted

The 422 above stopped a live seed partway through, which exposed a second defect
that had nothing to do with aspects.

The seed raised on the first failure. It reported that it had failed and nothing
else — not which entities had landed, not which were never attempted. A
half-populated shared instance was indistinguishable from an untouched one
without querying DataHub by hand.

Worse, the only documented cleanup was `reset`, and `reset` **refuses a partial
target set by design** (ADR-016): a partial set means the instance and the
allowlist disagree, so soft-deleting a subset and reporting success would be a
lie. Correct, and it left the operator with a refusal from the cleanup path and
no supported way forward. The only remaining lever was a global cleanup, which is
exactly what must never be run on an instance shared with four other submissions.

**Decision:** seed is the recovery, and it is idempotent by construction — every
entity is upserted with its complete aspect set on every run, so re-running it
completes a partial instance in place. It always was; nothing said so, and the
error messages pointed the other way.

- Seed attempts **every** entity even after one fails, so the report describes
  the whole instance rather than stopping at the first problem. It still fails
  closed: no sentinel is written, nothing is verified, and `PartialSeedError`
  carries a `SeedResult` naming what landed, what failed with which error, what
  was never attempted, and which lineage edges were skipped.
- Edges to an entity that failed are not declared. Lineage pointing at nothing
  reads as a graph gap rather than as the seed failure it actually is.
- `NamespaceViolation` is still never downgraded to a recorded failure. It
  aborts, as ADR-016 requires.
- The CLI writes the report to `APP_STATE_DIR/seed-partial.json` and exits **7**,
  so a failed live seed leaves an artifact rather than terminal scrollback.
- Both reset refusals now name `seed` as the recovery and say it is idempotent.
  The sentinel-missing refusal matters most: after a partial seed the sentinel is
  deliberately absent, so that is the message an operator actually hits.

---

## ADR-026: The MCP transport owns its HTTP client, and tests bind to the real signature

**Date:** 2026-07-26 · **Status:** accepted

The live gate seeded the fixture successfully — all 12 dataset URNs active, no
foreign ML URNs — and then failed on MCP verification and readiness with
`TypeError: streamable_http_client() got an unexpected keyword argument
'headers'`.

`mcp` 1.28 changed the transport signature to:

```python
streamable_http_client(url, *, http_client: httpx.AsyncClient | None = None,
                       terminate_on_close: bool = True)
```

`headers=`, `timeout=`, and `sse_read_timeout=` are gone. Everything HTTP-shaped
now comes from an `httpx.AsyncClient` the caller supplies. The call site still
passed `headers=`, so every session raised before issuing a request.

**Why the suite missed it.** `tests/test_mcp_client.py` asserted on *source text* —
`assert "streamable_http_client" in source`. That is true of a call that cannot
execute. The suite never touched the transport, so a signature change in a pinned
dependency was invisible to it. Source-text assertions confirm an import survived
a refactor; they cannot confirm a call works.

**Decision: pass a client we build and own, and bind the tests to the installed
signature.**

The client is constructed with `headers` carrying the bearer token, a split
timeout, and `follow_redirects=True`. Redirect-following is not a preference:
`mcp` sets it on every client it builds, and a transport that stops following
redirects fails against a server that issues one.

The split timeout matters and the old code had neither half. It stored a
`timeout` and passed it nowhere. The request budget (30s) and the SSE read budget
(300s) are different numbers for a reason: the GET stream idles between messages,
so applying the request timeout to it would tear down a healthy session against a
quiet server. Both defaults are asserted equal to `mcp`'s own.

**Lifecycle is the sharp edge.** `streamable_http_client` enters the client's
context *only when it created the client itself* — read the source: `if not
client_provided: await stack.enter_async_context(client)`. A client we pass in is
never closed by the transport. Since this transport opens one session per call,
an unclosed client would leak a connection pool per MCP request. The client is
therefore opened in an `async with` that unwinds on success, on handshake
failure, and on an exception thrown back into the generator at the `yield`.

**Regressions that can actually catch this.** Three layers:

1. `inspect.signature(streamable_http_client).bind(...)` against the installed
   `mcp` — asserting directly that `headers=` is rejected and `http_client=` is
   accepted.
2. A transport spy that binds every call against that same real signature before
   yielding. A mock that accepted anything would have let the regression through
   exactly as the old suite did.
3. One test that drives the **genuine** transport, unpatched, at a dead port and
   asserts the failure is a connection failure rather than an
   unexpected-keyword `TypeError`.

Reintroducing `headers=` fails twelve of them, including (3).

**Redaction and failure reporting.** The token is scrubbed from anything raised
out of a session, by two passes: the literal token value wherever it appears, and
any `Bearer <value>` sequence, which covers a credential this transport never
saw. Separately, the transport runs inside an `anyio` task group, so a refused
connection arrived as `unhandled errors in a TaskGroup (1 sub-exception)` — true,
and useless in a readiness report. Exception groups are now flattened to their
leaf causes, so readiness names `ConnectTimeout` or `ConnectionRefusedError`
instead.

**Not verified live.** The fix was derived from the installed signature and the
transport's source, not from a live run. The live gate must be re-run.

---

## ADR-027: Payload normalization is strict, and an unreadable response is an error

**Date:** 2026-07-26 · **Status:** accepted

The third live-gate failure, and the most misleading of the three. The transport
fix from ADR-026 worked and tool discovery passed. The seed was correct — a
read-only DB audit confirmed all 12 allowlisted `dataset` URNs active. Readiness
still reported **12/12 entities unusable**.

**Two failures were tangled together and had to be separated.** Shared OpenSearch
was down for part of the run, which makes `get_entities` genuinely fail. When it
recovered and the same call returned data that readiness still could not use, the
remaining fault was proven to be ours: parser compatibility, not infrastructure.
That distinction is the whole diagnosis, and the old code actively obscured it,
because both conditions rendered as "entities missing."

**The envelope.** `get_entities` returns:

```json
{"result": [{"urn": ..., "type": "dataset", "name": ...,
             "properties": {"name": ..., "description": ...,
                            "customProperties": [{"key": ..., "value": ...}]},
             "tags": {"tags": [{"tag": {"urn": "urn:li:tag:NAME"}}]},
             "domain": {"domain": {"urn": "urn:li:domain:..."}}}]}
```

Four separate mismatches, each independently fatal:

- `_iter_entities` looked for `entities`/`results`/`data` and returned `[]` for
  `result`;
- `customProperties` is a **list of `{key, value}` pairs**, not a mapping, and
  `dict()` on it yielded `{}` — so every entity failed the
  `artifact_class`/`purposes` coverage check;
- `tags` is `{"tags": [{"tag": {"urn": ...}}]}`; the old code iterated the outer
  object and produced the single literal string `"tags"` as a tag name, so every
  project-tag check failed;
- `domain` is `{"domain": {"urn": ...}}`; the old code looked for `name`/`urn`/`id`
  on the *outer* object and returned `None`, which readiness reports as "no
  domain."

**Decision: normalize strictly against the observed envelopes, and raise on
anything else.**

The root cause is not any single mismatch — it is that the normalizers were
written to guess. Each tried a list of plausible keys and returned `[]` when none
matched. That made a parser bug indistinguishable from an empty catalog. `[]` is
a claim about the world; "I could not read this" is a claim about the message,
and rendering them identically is how a correctly seeded instance came to look
empty. `PayloadError` now separates them, and its message names the keys that
*were* present.

Strict also means no permissive widening. The old accepted key names are gone
rather than kept alongside the real one: an envelope this project has never
observed is one it cannot claim to understand.

**Lineage is a descendant list, not an edge list.** `get_lineage` returns:

```json
{"downstreams": {"total": 1, "searchResults": [{"entity": {"urn": ...}, "degree": 1}]}}
```

`degree` says how far away a node is, never *through which parent*. So only
`degree == 1` yields a provable edge. A deeper descendant is real — DataHub found
it downstream — but this envelope cannot say by what route, and emitting it as a
one-hop edge would let the containment report cite a lineage path that does not
exist. Fabricated path evidence is worse than absent path evidence in a tool
whose central claim is that every action cites a path. Degree > 1 is therefore
emitted with `resolved=False`, which marks reconstructed paths incomplete and
escalates under LCB-R001.

Two further strictness rules follow from the same principle:

- a `searchResults` key that is absent is accepted **only** when the server also
  says `total` is 0 or absent, so a dropped key can never read as an empty graph;
- `total > len(searchResults)` raises. A truncated descendant set is a smaller
  blast radius than the real one, which is the exact false all-clear this project
  exists to prevent.

> **The first bullet is superseded by ADR-028.** "0 or absent" was not strict
> enough in either half. An *absent* total proves nothing, and `isinstance(total,
> int)` admitted `False`, which compares equal to 0 — so two different malformed
> payloads still read as an empty graph. Absence is no longer accepted, and a
> boolean is no longer a number. The second bullet stands unchanged.

**`has_edge` on the live client.** `check_fixture_lineage` verified the 9 declared
edges by walking once from the source. Given the envelope above, that walk can
only ever prove edges *leaving the source*, so the check could not pass live no
matter how good the parser was. The live client now answers `has_edge(upstream,
downstream)` by asking about that specific upstream — one MCP call per edge. That
is the only question this envelope answers exactly, and a readiness probe is
worth its round trips.

**Errored results.** With `isError=true`, a non-JSON text block, and no
`structuredContent`, `extract_payload` returns `None` and the message rendered as
`MCP tool 'get_entities' returned an error: None` — discarding the server's only
explanation, which during the outage would have said so. `extract_error_text`
falls back to the raw text blocks, still scrubbed of the token, and says
explicitly when the server supplied nothing.

**Consequence to expect on the next live run.** Two things will differ from the
offline demo, both correctly. The fixture's deliberately unresolvable edge
(`SOURCE -> ORPHAN`) is materialized live as a real `UpstreamLineage` aspect, so
DataHub resolves it. And descendants beyond the first hop are reported
unresolved, per the reasoning above. The live verdict should still be `escalated`,
but for a different reason than offline. Reconstructing exact multi-hop parentage
would need one `get_lineage` call per node; that is a change to the request
pattern and is flagged for the coordinator rather than adopted unilaterally.

**Not verified live.** Every payload here was captured by the coordinator and
replayed in tests. No live run has exercised this code.

---

## ADR-028: A boolean is not a count, and an absent count is not zero

**Date:** 2026-07-26 · **Status:** accepted · **Amends:** ADR-027

Found by the coordinator's independent pre-deployment review of the payload
parser candidate `f1050e2`, before it reached the live gate. Two fail-open
readings survived in `_to_lineage_edges`, and both turn a malformed lineage
payload into an *empty impact set* — the one output this project must never
produce without evidence, because an empty impact set is a clean bill of health.

**`bool` is a subclass of `int`.** `isinstance(False, int)` is `True`, so the
guards `isinstance(total, int)` and `isinstance(degree, int)` admitted booleans
as numbers. The two values that get through are exactly the two that matter:

- `total=False` compares equal to `0`, and `0` was the value that licensed an
  absent `searchResults` to be read as "this node has no descendants";
- `degree=True` compares equal to `1`, and `1` is the only degree this envelope
  treats as a **provable** one-hop edge — so a non-number would have produced
  path evidence the server never asserted. `degree=False` is the mirror image: a
  silently unresolved edge.

**An absent total proves nothing.** ADR-027 accepted a missing `searchResults`
when `total` was "0 or absent". Absence is not zero. A server that drops both
keys, or a response truncated in transit, is indistinguishable from a genuinely
childless node under that rule, and the parser resolved the ambiguity in the
permissive direction.

**Decision: counts must be proven, and an empty descendant set must be positively
attested.**

`_require_count` accepts only a genuine non-negative integer: a boolean raises
before any numeric handling, and so does a float, a string, a container, and a
negative. `_optional_count` allows the field to be *absent* but never invents a
value for it. A missing `searchResults` is then accepted on one condition only —
`total` is present and is exactly integer `0`.

Three notes on the edges of that rule:

- **Floats are rejected, not truncated.** `0.0 == 0` is the same fail-open
  reading as `False`, and a count DataHub sends as `1.0` is a shape this project
  has not observed and cannot claim to understand.
- **Negatives are rejected as malformed.** There is no descendant set of size
  −1, and a negative total would compare below any real result length and so
  pass the truncation check that ADR-027 added.
- **`total < len(searchResults)` is deliberately still accepted.** It is
  inconsistent, but it errs toward a *larger* blast radius than reported, and the
  results themselves are the evidence the parser acts on. Only truncation —
  fewer results than the server counted — is a false all-clear.

**Audit of the remaining count-like fields in this parser.** `downstreams`
carries `start` and `count` in the captured envelope; neither is read, so neither
can carry the trap, and a test pins that by setting both to booleans and
asserting the parse is unaffected. `status.removed` is the one field that
genuinely *is* a boolean, and it already rejects `1`/`0` for the same reason in
reverse — `bool("false")` is `True`, which would report a soft-deleted entity as
live. No other numeric field is consumed by `_iter_entities`,
`_custom_properties`, `_tag_names`, `_domain_urn`, or `_to_entity_context`.

**Scope.** Parser-local and behavior-preserving for every payload the live server
has actually been observed to send: the captured envelope, its `degree`-1 and
deeper variants, and an exact `total: 0` empty set all parse as before. What
changed is which *malformed* payloads are accepted, and the direction of the
change is closed rather than open.

**Regressions.** `tests/test_mcp_payloads.py::TestLineageCountsAreNumbersNotBooleans`
drives the real normalizer with explicit `True`/`False` totals and degrees, plus
missing, negative, float, and malformed cases, and asserts that real integers
still resolve. Twenty-six tests, eleven of which fail against `f1050e2` — the
rest pin behavior that was already correct and must stay that way. One existing
test changed meaning with the rule and also fails against `f1050e2`:
`{"downstreams": {}}` now raises instead of returning `[]`.

**Not verified live.** As with ADR-027: no live run has exercised this code.

---

## Versions

**No live DataHub evidence has been captured.** This session was barred from AWS
access, so no connection to the shared instance was made and no token was
requested or handled. Live versions must be captured during the coordinator's
verification pass.

Pinned and installed locally: `acryl-datahub` 1.6.0.x (matching the coordinator's
DataHub 1.6.0 stack), `mcp` 1.28.1, Python 3.13.2, ruff 0.16.0, pytest 8.x.
