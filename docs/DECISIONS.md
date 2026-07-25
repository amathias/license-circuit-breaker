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

## Versions

To be recorded once DataHub connectivity is established (Milestone B): DataHub,
`acryl-datahub`, `datahub-agent-context`, and the MCP integration.
