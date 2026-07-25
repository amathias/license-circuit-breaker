# Implementation Plan: License Circuit Breaker

Executable schedule for the License Circuit Breaker project chat.

Authority order: the portfolio `../AGENTS.md` and `../COORDINATOR_PLAN.md` own shared contracts;
this project's `AGENTS.md` and `COORDINATOR_HANDOFF.md` own everything inside the workspace;
`BUILD_PLAN.md` holds delivery strategy. This document assigns days and pins technology.

- **Start:** July 24, 2026
- **Hard deadline:** August 10, 2026, 5:00 p.m. Eastern Time
- **Role:** primary builder, this project only. Build and test locally. Never edit the live EC2 host.

---

## Fixed allocation (coordinator-assigned — do not change unilaterally)

| Setting | Value |
|---|---|
| Project slug | `license-circuit-breaker` |
| Internal port | `8102` |
| DataHub domain | `Demo / License Circuit Breaker` |
| Required DataHub tag | `project-license-circuit-breaker` |
| Entity prefix | `license.` |
| Fixture root | `demo/fixtures/license-circuit-breaker` |
| State root (deployed) | `/var/lib/datahub-hackathon/license-circuit-breaker` |

Changing any of these requires a coordinator proposal.

### Namespace guard — a hard requirement

Every seed, reset, mutation, writeback, and enforcement target must be validated against the
`license.` prefix and **fail closed** when it falls outside. This is not a warning; it is a
blocking error. Reset must never issue a global DataHub delete.

All demo URNs carry the prefix: `license.reviews.partner_feed`, `license.reviews.normalized`,
`license.features.review_sentiment`, and so on. Every entity is tagged
`project-license-circuit-breaker` and placed in the `Demo / License Circuit Breaker` domain.

### Shared environment contract

`PROJECT_SLUG`, `APP_ENV`, `APP_HOST`, `APP_PORT`, `APP_PUBLIC_URL`, `APP_STATE_DIR`,
`DATAHUB_GMS_URL`, `DATAHUB_MCP_URL`, `DATAHUB_TOKEN`, `DATAHUB_DOMAIN`, `DATAHUB_PROJECT_TAG`,
`DATAHUB_URN_PREFIX`, `DEMO_FIXTURE_ROOT`.

`.env.example` carries safe placeholders only. `APP_STATE_DIR` must be honored — never hardcode the
POSIX state root, or local Windows development breaks.

### Required endpoints

- `GET /api/health` — process is alive.
- `GET /api/readiness` — local state and DataHub connectivity verified, **without mutating shared state**.

---

## Pinned technology decisions

Every decision below, and every decision made later, is mirrored into `docs/DECISIONS.md` as it is
validated — required by `AGENTS.md`. Record the exact versions of DataHub, `acryl-datahub`,
`datahub-agent-context`, and the MCP integration there once connectivity is proven.

| Concern | Decision | Rationale |
|---|---|---|
| Python | 3.12 in a local venv | `datahub-agent-context` compatibility with 3.13 unverified; machine has only 3.13. |
| API | FastAPI + Pydantic v2 + uvicorn, default port 8102 | Typed contracts double as rights/impact/evidence schemas. |
| State | SQLite under `APP_STATE_DIR` | Rights events, jobs, approvals, evidence ledger. |
| Graph | NetworkX | Impact traversal, path extraction, unaffected-branch proof. |
| Warehouse | DuckDB | The `partner_feed` → `normalized` → `review_sentiment` chain. |
| Model | scikit-learn `TfidfVectorizer` + `LogisticRegression` | Sub-second training, deterministic, **no model download**. |
| Vector index | Local TF-IDF vectors + cosine, persisted under `APP_STATE_DIR` | **No `sentence-transformers`.** A large download would break clean-checkout tests and slow the demo. |
| UI | React + TypeScript + Vite, `@xyflow/react` + dagre | Chosen for impact-graph polish. Budgeted 3 days, parallel track. |
| Agent surface | MCP for reads, `datahub-agent-context` SDK for writeback | Using both exceeds the one-integration minimum. |
| Tests | pytest | Policy, traversal, approvals, idempotency, adapters, verification, **namespace isolation**. |

---

## Open items requiring coordinator resolution

These are escalations, not decisions this chat may make.

1. **DataHub access for local development.** `COORDINATOR_PLAN.md` mandates one shared pinned
   DataHub stack and calls five stacks on one host an anti-pattern, but also requires project chats
   to build and test locally. Need a ruling: do project chats point `DATAHUB_GMS_URL` at the shared
   instance over an SSH tunnel, or run a disposable local DataHub for development? This determines
   whether Docker is needed on this workstation at all.
2. **`DATAHUB_TOKEN` vs `DATAHUB_GMS_TOKEN`.** The shared contract specifies `DATAHUB_TOKEN`;
   `mcp-server-datahub` reads `DATAHUB_GMS_TOKEN`. Proposal: keep `DATAHUB_TOKEN` as the contract
   name and map it at process launch. Recording the mapping rather than renaming the contract.
3. **`DATAHUB_MCP_URL` implies HTTP MCP**, i.e. the GMS-hosted `/mcp` endpoint rather than a stdio
   `uvx mcp-server-datahub` process. If confirmed, `uv` drops off this project's prerequisites.
4. **Repository strategy.** Submission rules require an independent public repo per project with a
   visible Apache 2.0 `LICENSE`. This workspace is currently a subdirectory of the portfolio git
   repo. Creating a nested repo here changes what the parent tracks, which is portfolio-structural.
   Holding until the coordinator rules on submodules vs. split repos vs. push-mirroring.

Items 1–3 gate Milestone B. Item 4 gates the first public push, not local work.

---

## Schedule, mapped to portfolio milestones

The backend track is sequential. The **UI track runs in parallel from Aug 2**, once Milestone B
freezes API contracts — building against each landed endpoint rather than one late block, which is
how a hackathon project ends with nothing filmable.

### Milestone A — contracts and skeleton (Jul 24–28)

Almost none of this needs DataHub, so it proceeds while escalations 1–3 are resolved.

- [ ] FastAPI skeleton on port 8102 with `/api/health` and `/api/readiness`.
- [ ] Config module loading the full shared env contract; `.env.example` with placeholders.
- [ ] Namespace guard module + fail-closed tests. **Build this before anything that writes.**
- [ ] Deterministic `seed` and `reset` commands, namespace-scoped.
- [ ] Rights model: Pydantic `License`, `RightsEvent`, `ArtifactClass`, `Action`, `Evidence`, `ImpactDecision`.
- [ ] Deterministic policy rule table with stable IDs (`LCB-R001`…) and no LLM in the verdict path.
- [ ] Tests: approved, revoked, replacement-available, incomplete-evidence, unaffected.
- [ ] Create `docs/DECISIONS.md` and record decisions made so far.
- [ ] Update `COORDINATOR_HANDOFF.md`.

**Exit:** health/readiness respond, policy decisions cite rule IDs, namespace guard rejects
out-of-prefix targets, handoff current.

### Milestone B — first vertical slice (Jul 29 – Aug 2)

- [ ] Local supply chain: DuckDB chain, sklearn classifier + training manifest, TF-IDF vector index
      + source manifest, FastAPI predict/search endpoints, CSV export.
- [ ] An **unaffected** approved branch, an **approved replacement** source, and one deliberately
      incomplete lineage edge to force escalation.
- [ ] Ingest into DataHub under the `license.` prefix, project tag, and domain.
- [ ] Live downstream traversal; classify descendants; map policy to typed actions with priority.
- [ ] Real DataHub read **and** supported writeback, both verified with retained receipts.
- [ ] **Freeze API contracts here** for the UI track.

**Exit:** affected and unaffected artifacts both classified correctly; read and writeback verified.

### Enforcement and verification (Aug 3–6)

- [ ] Adapters: API freeze, index purge/rebuild, export quarantine, model retrain/replace.
- [ ] Dry-run default; approval gate before destructive actions; idempotent re-runs; receipts.
- [ ] One controlled adapter failure exercising residual-exposure reporting.
- [ ] Verification: endpoint probes, manifest inspection, quarantine access check, training-manifest check.
- [ ] Residual-exposure calculation; JSON + Markdown evidence bundle.
- [ ] Writeback of status and evidence references, namespace-scoped.

**Exit:** local artifacts genuinely change and reset cleanly; a deliberately skipped action
prevents an all-clear.

### UI track (Aug 2–8, parallel)

Seven screens: rights event → impact graph → per-artifact policy evidence → approval → live
execution → verification and residual exposure → DataHub writeback.

### Milestone C/D readiness — hardening (Aug 7–8)

- [ ] Clean setup and full test suite pass from a fresh checkout.
- [ ] Namespace and reset isolation tests pass.
- [ ] Demo runs without any other submission present.
- [ ] `examples/`: `rights-event.json`, `impact-plan.json`, `containment-report.md`.
- [ ] README maps each judging criterion to concrete proof.
- [ ] Legal-advice disclaimer and technical limitations visible; real vs. simulated adapters labeled.
- [ ] Handoff updated with commit, commands, resources, and limitations for deployment promotion.

### Submission (Aug 9), buffer (Aug 10)

- [ ] Demo video 2:35–2:45, hard limit 3:00, per `DEMO_AND_SUBMISSION.md`.
- [ ] DataHub before/after screenshots.
- [ ] Repo public with Apache 2.0 `LICENSE` visible; Devpost submission complete.
- [ ] Disclose any meaningful pre-existing code or assets.
- [ ] Title kept independent: "License Circuit Breaker," described as DataHub-powered.

Aug 10 is overrun buffer only. Submit before 5:00 p.m. ET.

---

## Integration gates

From `../AGENTS.md`. Not deployable until all eight pass:

1. Clean setup and tests pass. 2. Seed and reset deterministic. 3. Real context read from shared
DataHub via an eligible integration. 4. Supported writeback performed and verified. 5. Namespace
and reset isolation tests pass. 6. Health endpoint and judge workflow work behind the reverse
proxy. 7. Demo independent of other submissions. 8. Handoff record current.

## Risk register

| Risk | Impact | Mitigation |
|---|---|---|
| Escalations 1–3 unresolved past Jul 28 | High | Milestone A is DataHub-free by design; escalate now, not at Milestone B. |
| Namespace guard added late | High | Build it before the first writing code path, not after. |
| `datahub-agent-context` incompatible with Python 3.12/3.13 | High | Verify during Milestone A. |
| Custom platform URNs do not render lineage as expected | High | Validate before the supply chain is built on the assumption. |
| Shared DataHub contention with four other projects | Medium | Namespace isolation plus coordinator-owned backups before material ingestion changes. |
| UI slips, nothing to film | High | Parallel track from Aug 2; contracts frozen at Milestone B. |
| Demo video exceeds 3:00 | Fatal to scoring | Script and rehearse against documented timings. |

## Scope-cut order

Territory dimensions → multiple replacement strategies → notifications → cryptographic signing →
adapters beyond four.

**Never cut:** deterministic policy, real enforcement, verification, DataHub writeback, namespace
isolation, or limitation disclosure.
