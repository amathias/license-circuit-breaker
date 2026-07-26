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
| Milestone | Milestone C — judge console, submission documentation, and release gates complete offline; live gate still open |
| Verified commit/artifact | See "Deployment candidate" below |
| Build command | `py -3.13 -m venv .venv && .venv/Scripts/python.exe -m pip install -e ".[dev]"` |
| Console build command | `npm --prefix web install && npm --prefix web run build` — **see the deployment note below** |
| Test command | `.venv/Scripts/python.exe -m pytest tests/` |
| Fast test command | `.venv/Scripts/python.exe -m pytest tests/ -m "not slow"` |
| Lint command | `.venv/Scripts/python.exe -m ruff check .` |
| Coverage command | `.venv/Scripts/python.exe -m pytest tests/ -m "not slow" --cov` (floor 85%) |
| Typecheck command | `npm --prefix web run typecheck` |
| Seed command | `python -m demo.cli seed` (emits full catalog entries, then verifies by reread) |
| Reset command | `python -m demo.cli reset` (soft, exactly-allowlisted) |
| Restore command | `python -m demo.cli restore` (reverses a soft reset) |
| Slice command | `python -m demo.cli slice [--output plan.json]` |
| Demo command | `python -m demo.cli contain [--approve]` (exit 8 refused, 9 verdict short of contained) |
| Receipt check | `python -m demo.cli verify` |
| Console script | `license-circuit-breaker <command>` |
| Run command | `.venv/Scripts/python.exe -m app.main` |
| Health endpoint | `GET /api/health` — verified locally, returns 200 without DataHub |
| Readiness endpoint | `GET /api/readiness` — fail-closed, non-mutating, verified locally |
| Judge console | `GET /` — served from `web/dist` when built; absent without error when not |
| Persistent volumes | `APP_STATE_DIR` only (receipts, manifests, demo artifacts). No hardcoded paths. |
| Long-running workers | None |
| DataHub read | **Not verified live.** Client implemented; exercised only against the in-memory fake. |
| DataHub writeback | **Not verified live.** Durable and reversible writeback both implemented and tested offline. |
| Blockers | Live DataHub gate requires an AWS/SSM session this session was barred from |
| Evidence produced | 557 tests, 88.16% coverage, `examples/` (simulated), `docs/MILESTONE_B.md`, `docs/DECISIONS.md` (23 ADRs) |

### Deployment note: the console is a build step, not a checked-in asset

`web/dist` is gitignored. A deployment that clones the repository and runs
`python -m app.main` will serve the API correctly and **serve no console at all** —
`app/main.py` mounts the static directory only when it exists, so a missing build
is silent rather than fatal. That is deliberate: the API and the CLI must stay
runnable without a Node toolchain.

If the deployed environment should serve the judge console, the deploy needs
Node 20+ and `npm --prefix web install && npm --prefix web run build` before the
process starts. If it should not, nothing needs to change and `GET /` will 404.

This is a new deployment-facing requirement and is flagged for the coordinator
rather than assumed. No port, route, environment variable, or namespace changed:
the console is served from the existing port 8102 under `/`, and the static mount
is added after the API router so it cannot shadow `/api`.

### Evidence status — read before promoting

**No live DataHub evidence was captured in this session.** The task explicitly barred
AWS access and deployment, so every artifact under `examples/` and every receipt
produced carries `"simulated": true` and was generated against the deterministic
in-memory fake.

Integration gates 3 (real context read) and 4 (verified writeback) remain **open**.
They require a live run during the coordinator's verification pass. Nothing in this
handoff should be read as claiming they passed.

This remains true of the judge console. It was exercised against a locally running
instance in `APP_ENV=offline`, which is how the banner it renders comes to say
"Simulated DataHub". The console has never been pointed at a live instance, and
`README.md` states this in the same terms so a judge reading the repository cannot
form the opposite impression.

### Response to the coordinator review of `c116a26`

All seven rejected defects are fixed. Each has regressions.

| # | Defect | Resolution | ADR |
|---|---|---|---|
| 1 | Live seed only called `set_tags` | `adapters/catalog.py` emits `DatasetProperties`, `Status(removed=False)`, `GlobalTags`, `Domains`, `UpstreamLineage` as SDK proposals; seed rereads and verifies every allowlisted entity and edge, raising `VerificationError` on any gap | ADR-015 |
| 2 | Raw MCP transport | `adapters/mcp_client.py` uses `mcp.ClientSession` over `streamable_http_client` with `initialize()`, introspects advertised schemas, batches `get_entities`, sends `upstream=false` with bounded `max_hops` and `max_results` clamped to the advertised maximum | ADR-014 |
| 3 | Readiness mutated; domain check accepted `None` | `.readiness` probe write removed (`os.access` instead); a missing domain now fails; exact entity coverage, required custom properties, and complete fixture lineage all required | ADR-018 |
| 4 | Restoration skipped when the reread raised | Rollback runs in `finally` from the moment the write is attempted; receipt records `started`/`write_failed`/`verified`/`restored`/`residual_risk`; CLI exits 6 unless verified **and** restored | ADR-017 |
| 5 | Reset merely blanked tags | Soft reset via `Status(removed=True)` plus tag clearing, exactly allowlisted, idempotent, with `restore` to reverse; fails closed on partial/extra/empty/foreign sets and retains per-entity failure evidence | ADR-016 |
| 6 | Packaging excluded `demo`, omitted live deps | `packages = ["app", "adapters", "demo", "policy"]`, `policy` is a package shipping `rules.yaml` as package data, `acryl-datahub>=1.6.0,<1.7` and `mcp>=1.13` declared, console script added | ADR-019 |
| 7 | Regressions | `tests/test_catalog.py`, `tests/test_mcp_client.py`, `tests/test_readiness_nonmutating.py`, `tests/test_cli.py`, `tests/test_packaging.py`, plus additions to the isolation and workflow suites | — |

**Entity model decision:** all fixture nodes are `dataset` URNs carrying an
`artifact_class` custom property, including models, feature tables, indexes, APIs,
and exports. Native `mlModel`/`mlFeatureTable` entities are the better semantic fit
but need their own aspect sets and lineage handling; a uniform dataset model is
what can be verified deterministically in one milestone, and `artifact_class`
carries the semantics the policy engine consumes.

### Milestone B contents

- **Integration client** (`adapters/datahub.py`): MCP for reads via
  `DATAHUB_MCP_URL`, DataHub SDK for writeback. No hardcoded deployment port.
- **MCP transport** (`adapters/mcp_client.py`): official SDK session, schema
  introspection, server-advertised limit clamping.
- **Catalog lifecycle** (`adapters/catalog.py`): full aspect emission and soft
  status control, namespace-guarded before any proposal is built.
- **Deterministic fake** (`adapters/fake_datahub.py`): same client surface, same
  namespace guard on writes, so isolation tests are not vacuous.
- **Context validation** (`app/context.py`): entity presence, namespace, project
  tag, domain, lineage-path reconstruction with completeness tracking.
- **Seed/reset/restore** (`demo/seed.py`): sentinel-gated, exactly allowlisted,
  verified by reread, reversible.
- **Sanitized receipt ledger** (`app/receipts.py`): secrets redacted before write,
  hash-chained, tamper-evident.
- **Fail-closed readiness** (`app/readiness.py`): mutates nothing; requires token,
  endpoints, MCP tools, exact domain, tag controls, full entity coverage with
  required properties, and complete fixture lineage.

### Milestone C contents

- **Judge console** (`web/`): React + TypeScript in strict mode, two runtime
  dependencies, eight stages in demo order. Served by FastAPI from `web/dist`
  when built. The console formats and never computes: every verdict, status, and
  residual exposure it shows is read from the API (ADR-020).
- **Console/API contract tests** (`tests/test_console.py`): every `/api` path the
  client fetches must be served, and every field the page renders must appear in
  a real response. Read from the OpenAPI schema rather than from framework
  internals (ADR-021).
- **Public-safety gate** (`tests/test_public_safety.py`): scans the exact
  shippable file set for credential shapes, `.env`, runtime state, build output,
  and absolute home-directory paths (ADR-023).
- **Coverage gate**: `pytest --cov`, floor 85%, `pytest-cov` declared in the dev
  extras.
- **`contain` command regressions** (`tests/test_cli.py`): the exit codes the
  README documents are now asserted in process rather than described.
- **Submission documentation**: `README.md` is the judge-facing entry point with a
  reproducible quickstart; `DEMO_AND_SUBMISSION.md` is a runbook whose every
  number came from an actual run; `examples/containment-report.md` is that run,
  captured verbatim.

### CLI exit codes

`0` success · `2` refused (sentinel missing, or target set not exactly the
allowlist) · `3` refused by the namespace guard · `4` seed emitted but unverifiable
· `5` partial reset/restore failure · `6` slice completed but writeback was not both
verified and restored · `8` `contain` refused by the approval gate, nothing enforced
· `9` `contain` completed with a verdict short of `contained`.

`9` is the expected result of the documented demo. One descendant is reachable
only through a lineage path DataHub cannot complete, so it escalates. A `contain`
run that exited `0` on the demo fixture would mean the escalation had been lost.

### Defects found and fixed this milestone

**The console contract test passed for the wrong reason.** It walked `app.routes`
recursively to collect served paths. This FastAPI version keeps an included router
as an opaque container rather than flattening its routes onto the application, so
the walk found the container and none of the endpoints in it — and whether the
test passed depended on which other module had run first. It now reads
`app.openapi()["paths"]`. Recorded as ADR-021.

**The evidence report leaked an absolute path, and nothing caught it.** The
writeback section of `EvidenceBundle.to_markdown` interpolated the receipt list
into an f-string, emitting a multi-thousand-character Python dict repr that
carried the absolute evidence path once per receipt — including the developer's
home directory name, on an artifact bound for a public repository. Receipts now
render as a table, the committed example is generated with a relative
`APP_STATE_DIR`, and `tests/test_public_safety.py` fails the build on any
home-directory path in a shippable file. Recorded as ADR-022 and ADR-023.

Neither defect affected deployment behavior, enforcement, or the namespace guard.

### Defect found in the previous milestone

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

| Operation | Aspect(s) | Scope |
|---|---|---|
| Read entity context | `get_entities` via MCP, batched | `license.` only |
| Read downstream lineage | `get_lineage` via MCP, `upstream=false`, bounded hops | from `license.` sources |
| Read advertised tools | `tools/list` via MCP | read-only introspection |
| Seed | `DatasetProperties`, `Status`, `GlobalTags`, `Domains`, `UpstreamLineage` via SDK proposals | `license.` only |
| Reversible tag writeback | `GlobalTags` via SDK proposal | `license.` only, restored immediately |
| Soft reset | `GlobalTags` (cleared) + `Status(removed=True)` | `license.` + fixture marker only |
| Restore | full seed aspect set | `license.` + fixture marker only |

Entities created: 12 (11 graph nodes + 1 sentinel), all `dataset` URNs prefixed
`license.` carrying an `artifact_class` custom property, all tagged
`project-license-circuit-breaker` and `lcb-demo-fixture`, all assigned the
`Demo / License Circuit Breaker` domain. Lineage: 9 declared edges, one
deliberately unresolvable to exercise fail-closed escalation.

**Never touched:** the domain entity and the tag entities themselves. Those are
shared coordinator-owned controls; this project references them and never creates,
mutates, or removes them. `tests/test_isolation.py` asserts this.

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

Slice runtime offline is under a second. Live runtime is dominated by MCP round
trips. With batched `get_entities` the slice issues roughly 6 MCP calls regardless
of descendant count, plus 4 GMS proposals for the reversible writeback. Seed issues
5 proposals per entity (60 for the demo graph) followed by one batched verification
read.

Each MCP call opens its own session, since the application is synchronous. That is
acceptable at demo volume but is the first thing to change if the graph grows.

The `slow`-marked packaging tests take roughly 12 minutes, dominated by two isolated
`acryl-datahub` installs. Run the fast suite (`-m "not slow"`) during development and
the full suite before proposing a candidate.

### Deployment candidate

| Field | Value |
|---|---|
| Branch | `main` |
| Product candidate | `3f92117751baee105c0563078d0f0d6c9311d4df` |
| Supersedes | `47b3df6db857c8e441a8c72bac810659006ef9b2` (superseded, not rejected) |
| Tests | 555 fast + 2 slow archive-install = **557 passing** |
| Coverage | **88.16%** (floor 85%) |
| Lint | ruff clean |
| Console typecheck | `tsc --noEmit` clean; `vite build` succeeds |
| Public-safety gate | passing |
| Working tree | clean |
| Local `main` == `origin/main` | yes |

**Promotion caveat.** This candidate is verified *offline only*. Integration gates 3
and 4 require a live DataHub run that this session could not perform.

Suggested live verification sequence, in a non-judged environment first:

```bash
APP_ENV=live python -m demo.cli seed      # expect "Verified: 12 entities, 9 edges"
curl -s $APP_PUBLIC_URL/api/readiness     # expect status "ready", 9/9 checks passed
APP_ENV=live python -m demo.cli slice     # expect exit 0 and verified=True restored=True
APP_ENV=live python -m demo.cli reset     # expect "12 soft-removed"
APP_ENV=live python -m demo.cli restore   # expect "Restored 12 entities"
curl -s -o /dev/null -w '%{http_code}' $APP_PUBLIC_URL/   # 200 if the console was
                                                          # built, 404 if not
```

A non-zero exit from `slice` means the writeback was not both verified and
restored; treat the writeback gate as failed and check the receipt's
`residual_risk` before rerunning.

### Known limitations

- The receipt ledger is tamper-**evident**, not tamper-proof.
- Purpose metadata is read from custom properties this project seeds.
- Durable revocation writeback is implemented (`POST /api/writeback`, and the
  writeback stage of `demo.cli contain`): each artifact receives the status it
  earned plus the plan hash and an evidence reference, confirmed by re-read. The
  separate *reversible* writeback used by `demo.cli slice` remains the mechanism
  for proving catalog-write capability without leaving state behind.
- The console is a build artifact, not a checked-in one. See the deployment note
  under "Milestone handoff".
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



