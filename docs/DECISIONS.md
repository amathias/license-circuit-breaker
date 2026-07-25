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

## Versions

**No live DataHub evidence has been captured.** This session was barred from AWS
access, so no connection to the shared instance was made and no token was
requested or handled. Live versions must be captured during the coordinator's
verification pass.

Pinned and installed locally: `acryl-datahub` 1.6.0.x (matching the coordinator's
DataHub 1.6.0 stack), `mcp` 1.28.1, Python 3.13.2, ruff 0.16.0, pytest 8.x.
