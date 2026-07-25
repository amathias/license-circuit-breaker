# Coordinator Handoff: License Circuit Breaker

## Relationship to the portfolio coordinator

This project chat owns License Circuit Breaker's product, code, tests, demo, evidence, and
submission. The portfolio coordinator at `../COORDINATOR_PLAN.md` owns the shared DataHub and AWS
deployment contracts.

Before changing a port, public route, shared environment variable, DataHub namespace, deployment
topology, or global reset behavior, submit the proposed change to the coordinator. Do not edit the
live EC2 host from this project chat.

## Fixed project allocation

| Setting | Value |
|---|---|
| Project slug | `license-circuit-breaker` |
| Internal port | `8102` |
| DataHub domain | `Demo / License Circuit Breaker` |
| Required DataHub tag | `project-license-circuit-breaker` |
| Entity prefix | `license.` |
| Fixture root | `demo/fixtures/license-circuit-breaker` |
| State root | `/var/lib/datahub-hackathon/license-circuit-breaker` |

## Project-chat obligations

- Build only License Circuit Breaker business behavior.
- Keep rights events, containment, verification, and reset operations inside this allocation.
- Fail closed if an enforcement or reset target falls outside the `license.` namespace.
- Implement `GET /api/health` and `GET /api/readiness`.
- Keep the project independently runnable without the other four submissions.
- Update the milestone handoff below whenever deployment-facing behavior changes.

## Milestone handoff

| Field | Current value |
|---|---|
| Status | `in progress` |
| Milestone | Milestone A (contracts and skeleton) â€” partially complete |
| Verified commit/artifact | Pending local baseline commit; coordinator records exact hash before promotion |
| Build command | `py -3.13 -m venv .venv && .venv/Scripts/python.exe -m pip install -e ".[dev]"` |
| Test command | `.venv/Scripts/python.exe -m pytest tests/` â€” **86 passing** |
| Seed command | Not yet implemented (blocked on Milestone B fixtures) |
| Reset command | Not yet implemented (guard `assert_scoped_reset` ready and tested) |
| Run command | `.venv/Scripts/python.exe -m app.main` |
| Health endpoint | `GET /api/health` â€” **verified**, returns 200 without DataHub |
| Readiness endpoint | `GET /api/readiness` â€” **verified**, read-only, returns `degraded` until DataHub configured |
| Persistent volumes | `APP_STATE_DIR` only (SQLite, manifests, demo artifacts). No hardcoded paths. |
| Long-running workers | None |
| DataHub read | Not yet attempted â€” blocked on access ruling |
| DataHub writeback | Not yet attempted â€” blocked on access ruling |
| Blockers | Deterministic seed/reset and live shared DataHub read/write remain incomplete |
| Evidence produced | Namespace guard + tests, deterministic rule table, `docs/DECISIONS.md` (9 ADRs) |

### Milestone A progress

Complete:

- Namespace guard (`app/namespace.py`), fail-closed, 35 tests. Rejects foreign URNs,
  global selectors, empty reset target lists, path traversal, and the fixture root itself.
- Rights model (`app/rights.py`): typed licenses, versioned append-only events,
  lineage paths, impact decisions, containment evidence.
- Deterministic policy table (`policy/rules.yaml`, 16 rules) + evaluator, 40 tests
  covering allowed / revoked / replacement-available / incomplete-evidence / unaffected.
- FastAPI app on port 8102 with health, readiness, and `GET /api/policy/rules`.
- Shared environment contract implemented; `.env.example` with placeholders only.

Outstanding for Milestone A: deterministic seed/reset (needs Milestone B fixtures).

### Coordinator rulings

1. **Local DataHub access:** use AWS Systems Manager port forwarding to the shared EC2 host. No
   inbound SSH, GMS, or MCP ports will be opened.
2. **Token name:** retain `DATAHUB_TOKEN` in every project. Deployment maps it to
   `DATAHUB_GMS_TOKEN` for the official MCP process.
3. **MCP transport:** use the coordinator-hosted private Streamable HTTP endpoint at
   `http://127.0.0.1:8000/mcp` during an SSM session and the equivalent loopback endpoint on EC2.
4. **Repository strategy:** this submission remains an independent Git repository on `main`.
   Its public repository is `https://github.com/amathias/license-circuit-breaker`, and local
   `origin` uses the repository-scoped
   `github-datahub-license-circuit-breaker` SSH alias. The primary project writer may push verified
   milestones under the no-force rules in `AGENTS.md`; remotes and deploy keys remain
   coordinator-owned.

These decisions unblock local fixture work. Live integration remains blocked until the shared
DataHub stack is healthy.

### Resource expectations

Application process is light: FastAPI + SQLite + DuckDB + a TF-IDF index, no GPU, no
model downloads. Startup under two seconds. Memory well under 512 MB. The DataHub stack
itself is the only significant consumer and is coordinator-owned.

## Required deployment handoff format

When requesting deployment, replace all placeholder values and include:

1. Exact commit or immutable artifact identifier.
2. Required environment variables without secret values.
3. Build, test, seed, reset, run, and rollback commands.
4. Health/readiness results.
5. DataHub entities, reads, writes, and receipts.
6. Filesystem volumes and disposable paths.
7. Expected CPU, memory, startup time, and job duration.
8. Known limitations and demo concurrency behavior.



