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
| Milestone | Milestone B (guarded vertical slice) — complete offline; live gate open |
| Verified commit/artifact | See "Deployment candidate" below |
| Build command | `py -3.13 -m venv .venv && .venv/Scripts/python.exe -m pip install -e ".[dev]"` |
| Test command | `.venv/Scripts/python.exe -m pytest tests/` — **189 passing** |
| Lint command | `.venv/Scripts/python.exe -m ruff check .` — **clean** |
| Seed command | `python -m demo.cli seed` |
| Reset command | `python -m demo.cli reset` |
| Slice command | `python -m demo.cli slice [--output plan.json]` |
| Receipt check | `python -m demo.cli verify` |
| Run command | `.venv/Scripts/python.exe -m app.main` |
| Health endpoint | `GET /api/health` — verified locally, returns 200 without DataHub |
| Readiness endpoint | `GET /api/readiness` — fail-closed, non-mutating, verified locally |
| Persistent volumes | `APP_STATE_DIR` only (receipts, manifests, demo artifacts). No hardcoded paths. |
| Long-running workers | None |
| DataHub read | **Not verified live.** Client implemented; exercised only against the in-memory fake. |
| DataHub writeback | **Not verified live.** Reversible writeback implemented and tested offline. |
| Blockers | Live DataHub gate requires an AWS/SSM session this session was barred from |
| Evidence produced | 189 tests, `examples/` (simulated), `docs/MILESTONE_B.md`, `docs/DECISIONS.md` (13 ADRs) |

### Evidence status — read before promoting

**No live DataHub evidence was captured in this session.** The task explicitly barred
AWS access and deployment, so every artifact under `examples/` and every receipt
produced carries `"simulated": true` and was generated against the deterministic
in-memory fake.

Integration gates 3 (real context read) and 4 (verified writeback) remain **open**.
They require a live run during the coordinator's verification pass. Nothing in this
handoff should be read as claiming they passed.

### Milestone B contents

- **Integration client** (`adapters/datahub.py`): MCP over Streamable HTTP for reads
  via `DATAHUB_MCP_URL`, GMS for writeback. No hardcoded deployment port. Handles
  both plain-JSON and SSE-framed MCP responses.
- **Deterministic fake** (`adapters/fake_datahub.py`): same client surface, same
  namespace guard on writes, so isolation tests are not vacuous.
- **Reversible writeback**: capture prior aspect → write → immediate re-read →
  restore. `verified` and `restored` recorded separately; `clean` requires both.
- **Context validation** (`app/context.py`): entity presence, namespace, project
  tag, domain, lineage-path reconstruction with completeness tracking.
- **Marker-guarded seed/reset** (`demo/seed.py`): sentinel-gated, marker-scoped.
- **Sanitized receipt ledger** (`app/receipts.py`): secrets redacted before write,
  hash-chained, tamper-evident.
- **Fail-closed readiness** (`app/readiness.py`): requires token, MCP tools, project
  domain/tag, and readable `license.` entities. A reachable GMS is not sufficient.

### Defect found and fixed this milestone

The first offline slice run reported `no_action` for the prediction API and the CSV
export. `is_affected` compared only each descendant's own declared purposes against
the revoked set, so an API declaring `serving` looked clean despite serving output
from a model trained on revoked data — a false all-clear.

Contamination now propagates downstream from any descendant using a revoked purpose,
while deliberately not propagating from the source itself, which preserves the
unaffected analytics branch. Four regression tests cover it. Recorded as ADR-013.

### Required environment variables

`PROJECT_SLUG`, `APP_ENV`, `APP_HOST`, `APP_PORT`, `APP_PUBLIC_URL`, `APP_STATE_DIR`,
`DATAHUB_GMS_URL`, `DATAHUB_MCP_URL`, `DATAHUB_TOKEN`, `DATAHUB_DOMAIN`,
`DATAHUB_PROJECT_TAG`, `DATAHUB_URN_PREFIX`, `DEMO_FIXTURE_ROOT`.

`APP_ENV=offline` selects the in-memory fake. Any other value requires live DataHub
configuration. No secret values appear in `.env.example` or this document.

### DataHub operations this project performs

| Operation | Aspect | Scope |
|---|---|---|
| Read entity context | `get_entities` via MCP | `license.` only |
| Read downstream lineage | `get_lineage` via MCP | from `license.` sources |
| Reversible tag writeback | `globalTags` via GMS `ingestProposal` | `license.` only, restored immediately |
| Seed / reset | `globalTags` | `license.` + fixture marker only |

Entities created: 12 (11 graph nodes + 1 sentinel), all prefixed `license.`, all
tagged `project-license-circuit-breaker` and `lcb-demo-fixture`, all in domain
`Demo / License Circuit Breaker`.

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

Slice runtime offline is under a second. Live runtime is dominated by MCP round trips;
the slice issues roughly 2N + 4 calls for N descendants.

### Deployment candidate

| Field | Value |
|---|---|
| Branch | `main` |
| Product candidate | `c116a26c223ea65f120c86ff5486dd3fd634773e` |
| Tests | 189 passing |
| Lint | ruff clean |
| Working tree | clean |
| Local `main` == `origin/main` | yes |

**Promotion caveat.** This candidate is verified *offline only*. Integration gates 3
and 4 require a live DataHub run that this session could not perform. Recommend
promoting to a non-judged environment first, running
`APP_ENV=live python -m demo.cli seed && python -m demo.cli slice`, and confirming
the receipt shows `verified=True restored=True` before treating the writeback gate
as passed.

### Known limitations

- Containment adapters are not implemented; no local artifact is disabled yet.
- The receipt ledger is tamper-**evident**, not tamper-proof.
- Purpose metadata is read from custom properties this project seeds.
- Reversible writeback proves capability without leaving state; durable revocation
  status is a later milestone.
- Demo concurrency: seed and reset are not mutually exclusive across processes. Two
  concurrent resets are safe (idempotent, sentinel-gated) but a concurrent seed and
  reset against the same instance would interleave. Single-operator assumption
  holds for the demo.

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



