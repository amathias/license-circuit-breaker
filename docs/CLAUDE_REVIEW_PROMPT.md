# Claude review prompt

Copy everything below the divider into Claude with access to the repository.

---

Act as a skeptical principal engineer conducting an independent code, security, and architecture review of **License Circuit Breaker**.

Repository: https://github.com/amathias/license-circuit-breaker

My objective is to make this project credible evidence of staff/principal engineer caliber work. Determine whether the implementation earns that assessment today, and identify the highest-value changes needed if it does not. Do not optimize your conclusions for the rating I want. Be candid, specific, and fair. Credit strong engineering decisions only when the implementation supports them.

Assess the work demonstrated by this repository. A repository alone cannot establish its owner's actual job level, authorship, organizational influence, or sustained impact. Distinguish implementation quality from scope and leadership evidence; do not infer either from polished documentation or code volume.

## 1. Establish context and review boundaries

Read `AGENTS.md`, then these documents completely before assessing the design:

- `HACKATHON_RULES.md`
- `PROJECT_BRIEF.md`
- `BUILD_PLAN.md`
- `DEMO_AND_SUBMISSION.md`

Then read `README.md`, `docs/DECISIONS.md`, `IMPLEMENTATION_PLAN.md`, `COORDINATOR_HANDOFF.md`, `SUBMISSION.md`, and `docs/DEMO_RECORDING.md`. Trace superseded decisions, especially ADR-030 and ADR-031. Separate original requirements, historical status, current claims, and current implementation. Documents are claims to verify, not proof of correctness.

Record the reviewed commit, working-tree differences, environment, and available tools. Identify what you can inspect and execute. If you only have browser access, disclose that limitation and do not imply you ran tests. Do not invent results, line numbers, deployment behavior, or access to private evidence.

This is a review, not authorization to remediate or deploy. Preserve existing work. You may create a review report and isolated local reproductions. Do not modify product code, commit, push, reset existing artifacts, or mutate a live DataHub instance or public deployment. Use disposable local state, offline configuration, and no live credentials for execution. Inspect test setup and configuration before running commands that could load `.env`, contact services, or mutate state. Do not expose secret values in findings.

## 2. Reconstruct the actual system

Trace this flow through the implementation, not just the architecture diagram:

**Rights event → DataHub context and lineage → deterministic policy → impact plan → exact-plan approval → ordered execution → independent verification → residual exposure and verdict → evidence ledger → DataHub writeback and reread.**

Start with:

- Contracts and policy: `app/rights.py`, `app/policy.py`, `policy/rules.yaml`.
- Discovery and planning: `app/context.py`, `app/workflow.py`, `app/namespace.py`.
- Governance and execution: `app/approvals.py`, `app/execution.py`, `app/store.py`, `adapters/containment.py`.
- Verification and evidence: `app/verification.py`, `app/evidence.py`, `app/receipts.py`.
- DataHub integration: `app/clients.py`, `adapters/datahub.py`, `adapters/mcp_client.py`, `adapters/catalog.py`, `adapters/entity_registry.py`, and `adapters/fake_datahub.py`.
- Application boundary: `app/config.py`, `app/main.py`, `app/api.py`, `app/demo_guard.py`, `app/readiness.py`.
- Actual artifacts and lifecycle: `demo/estate.py`, `demo/serving.py`, `demo/seed.py`, `demo/cli.py`, and related fixtures.
- Console: `web/src/`, especially `App.tsx`, `api.ts`, `types.ts`, and `LineageGraph.tsx`.
- Tests, packaging, dependencies, configuration examples, Git ignore rules, and `.github/workflows/`.

Verify these paths against the checkout and follow dependencies beyond this starting list. Provide a compact component/trust-boundary diagram and an explicit workflow state model. Identify sources of truth, derived state, persistent state, external dependencies, and where authority changes hands.

## 3. Review correctness and safety invariants

For each important invariant, identify the implementation, existing test evidence, and at least one plausible way it could fail. Prioritize these questions:

### Lineage and policy

- Does traversal handle cycles, diamonds, multiple paths, pagination, truncation, duplicates, unknown counts, malformed MCP payloads, deleted entities, missing edges, and stale catalog indexes without reporting false completeness?
- Is a known empty graph distinguishable from an incomplete or unreadable response? Can fixture assumptions conceal live behavior?
- Does purpose-specific revocation preserve legitimate analytics uses while propagating contamination correctly through intermediate artifacts?
- Are missing classifications, purposes, ownership, tags, and governance evidence handled deliberately? Is actual metadata use consistent with product claims?
- Are policy precedence, fallback escalation, rights versions, effective times, replacement eligibility, and hashes deterministic and semantically correct?
- Evaluate the dataset-URN representation of ML artifacts and the pinned DataHub aspect registry. Explain the compatibility benefit and loss of native ML semantics without assuming this documented compromise is automatically a defect.

### Approval and execution

- Does approval bind every execution-relevant input: event, policy, targets, actions, scope, replacement source, and material lineage/context? Is incidental metadata excluded appropriately?
- What happens when the plan changes after display, approval, or partial execution? Can a client approve a newly regenerated plan it never saw?
- Can stale approvals, rejected approvals, crafted run IDs, replayed requests, partial scopes, reset, or resume authorize unintended actions or reuse unrelated evidence?
- Are action dependencies enforced? Does a failed prerequisite stop unsafe downstream actions, such as replacing a model whose retraining failed?
- Examine crash windows between artifact mutation, journal update, receipt append, verification, and writeback. Does retry recover safely after a side effect succeeds but its acknowledgement is lost?
- Is idempotency checked against actual state or merely an old receipt? Assess concurrent requests, multiple processes, SQLite transactions, locks, atomic file replacement, interrupted resets, and recovery.

### Verification, evidence, and writeback

- Can `contained` be returned when anything remains unverified, failed, skipped, escalated, or exposed? Can an empty probe set accidentally pass?
- Do probes establish the advertised serving behavior, or merely inspect a flag/manifest that could disagree with it? Can shared assumptions between executor and verifier create false confidence?
- Do unaffected branches remain available? Are residuals complete and correctly tied to the current event, approval, run, and artifact state?
- Can evidence become stale after reset, rebuild, replacement, or a new approval? Are historical execution facts distinguished from current observations?
- Verify hash-chain construction, canonicalization, redaction, concurrent appends, truncation detection, and whether the chain is actually checked. State what tampering a local hash chain can and cannot detect without an external trust anchor.
- Does writeback preserve unrelated metadata, respect the exact namespace, survive partial failure, reread the intended values, and restore state when restoration is promised? Can containment and catalog-publication status contradict each other?

## 4. Conduct a threat-model-driven security review

Identify assets, attacker capabilities, entry points, trust boundaries, and consequences. Review at least these actors: an anonymous internet visitor, a caller with a demo confirmation, a compromised or malformed DataHub/MCP response, and a local operator or process with limited filesystem access.

Check:

- Authorization at every mutation boundary, including direct API calls and CLI paths. Separate human approval, authenticated identity, and abuse controls.
- Environment-dependent behavior: offline/local/live, the anonymous `hackathon` demo, and read-only `production`. Examine unset, misspelled, case-varied, and conflicting environment settings and `.env` loading.
- Confirmation expiration, single use, client/operation binding, issuance limits, replay, client-key/proxy trust, cooldowns, memory growth, and single-process versus multi-worker behavior.
- Browser-origin protections, CORS/CSRF where applicable, input bounds, exception leakage, metadata rendering, URLs, exports, and frontend handling of stale responses or failed operations.
- Filesystem traversal, symlinks/junctions, check/use races, arbitrary paths, quarantine access, unsafe deletion, namespace parsing, and shared-instance isolation.
- SQL, shell, template, and deserialization risks where those mechanisms exist; SSRF and credential forwarding where attacker-controlled destinations are reachable.
- Secrets in tracked files/history, logs, receipts, errors, frontend bundles, and configuration. Report locations and redacted evidence, never secret values.
- Expensive unauthenticated reads, graph growth, unbounded input, synchronous I/O, connection lifecycle, timeouts, retries, and denial-of-service amplification.
- Dependency and CI supply-chain exposure, install reproducibility, package contents, permissions, and version compatibility. For vulnerability claims, verify affected versions and advisories using authoritative sources, dated at review time. Separate confirmed installed versions from allowed version ranges and mere theoretical exposure.

The anonymous demo deliberately uses confirmations as abuse controls, not authentication. Evaluate whether its bounded sandbox claim holds. Do not label every absent enterprise feature a critical vulnerability. Conversely, a documented limitation is not a waiver for a reachable safety failure. Label each issue's applicable deployment mode and prerequisites. Do not run intrusive tests against the public service.

## 5. Assess architecture and engineering maturity

Evaluate cohesion, dependency direction, typed contracts, API compatibility, duplicated orchestration, error taxonomy, state ownership, test seams, and maintainability. Determine whether the separation between policy, approval, execution, verification, and presentation is enforced or merely described.

Assess operational readiness: startup/configuration validation, health versus readiness, observability, correlation identifiers, storage growth, migrations, backup/recovery, packaging, clean setup, dependency changes, and failure diagnosis. Distinguish repository guarantees from unverified infrastructure assumptions.

Consider realistic evolution: more descendants, multiple sources, concurrent operators, overlapping revocations, new adapters, and a DataHub outage during containment. Use concrete scenarios to identify which boundaries would hold and which would require change. Do not recommend microservices, Kubernetes, event buses, or generic abstractions without a specific problem and proportionate benefit. Explicitly identify simplifications worth preserving.

Compare README and submission claims with code and reproducible evidence, especially live versus simulated integration, completeness, resumability, reversibility, verification strength, native ML entities, and compliance limitations.

## 6. Validate rather than speculate

Inspect tests for behavioral assertions, negative cases, real artifact checks, and mocks that reproduce the implementation's assumptions. Coverage percentages and test counts alone are not evidence of safety.

Where the isolated environment permits, run the documented checks using the platform-appropriate interpreter:

```text
python -m ruff check .
python -m pytest tests/ -m "not slow" --cov
python -m pytest tests/ -m slow
npm --prefix web ci
npm --prefix web run typecheck
npm --prefix web run build
```

Report exact commands, outcomes, and skipped/blocked checks. Check whether archive-based packaging tests exercise committed HEAD or the reviewed working tree. Treat dependency/network failures separately from product failures. Run a disposable offline end-to-end scenario if feasible; verify the documented approval refusal and escalated exit status rather than assuming every nonzero exit is a failure.

For important suspected defects, create minimal local reproductions or temporary regression tests where safe. If reproduction is unavailable, provide a precise code trace and label the finding unconfirmed. Before finalizing, challenge your strongest findings: look for guards in callers/callees, relevant tests, superseding decisions, and deployment prerequisites that could invalidate them.

## 7. Deliver one evidence-based report

Use this structure:

### A. Executive assessment

State the supported engineering-quality level, confidence, the three strongest signals, and the three most consequential gaps. Separately assess suitability as a reproducible local demo, an anonymous hosted demo, and a production governance service.

### B. Ranked findings

Group confirmed defects, unconfirmed risks, and future production requirements separately. Rank by actual impact and likelihood; do not invent a finding quota. For each consequential finding include:

- Stable ID, severity, confidence, and affected deployment modes.
- Exact file/line references from the reviewed version.
- Trigger or attacker prerequisites and reproducible steps/code trace.
- Expected versus actual behavior and concrete consequence.
- Existing protections and why they are insufficient in this case.
- Smallest effective fix, trade-offs, and a regression-test acceptance criterion.

Deduplicate shared root causes. Separate stylistic preferences from defects. Do not imply speculative issues are demonstrated exploits.

### C. Architecture and invariant assessment

Include the diagram, workflow state model, major trade-offs, and an invariant table with implementation evidence, tests, and remaining uncertainty. Identify strengths with the same evidentiary rigor as weaknesses.

### D. Staff/principal calibration

Use a table covering correctness, security boundaries, architecture, operational maturity, testing, maintainability, technical judgment, and scope/impact. Rate each as below senior, senior, staff, principal-caliber, or insufficient evidence, with concrete justification. Define these labels as review criteria, not universal company leveling standards:

- **Senior:** independently delivers a reliable, maintainable system within a defined scope.
- **Staff:** establishes coherent boundaries and invariants across subsystems, anticipates failures, makes proportionate trade-offs, and creates patterns others can operate and extend.
- **Principal:** additionally demonstrates durable strategy across systems or teams, resolution of broad constraints, and validated organizational or business impact. Identify evidence unavailable from this repository rather than manufacturing it.

Do not average away a severe correctness or security defect. Distinguish “contains staff/principal-quality design decisions” from “the project establishes its owner's level.” Explain what additional technical or impact evidence would materially change your assessment.

### E. Prioritized improvement plan

Give the smallest coherent set of improvements that would materially strengthen the staff/principal assessment. Separate current safety blockers, near-term engineering improvements, and requirements conditional on production adoption. For each include affected components, expected benefit, dependencies, rough effort, and observable acceptance criteria. Explain what to defer and why. Do not implement the plan in this review.

### F. Evidence and limits

List reviewed areas, commands/results, local reproductions, unverified live-integration claims, unavailable evidence, and residual uncertainty. Finish with a short, defensible statement of how you would describe this project's engineering quality to a skeptical hiring committee.

Lead with consequential findings. Be thorough without filling the report with generic checklists, praise, speculative vulnerabilities, or cosmetic refactors. The goal is stronger engineering and credible evidence, not a favorable label.

After completing the review, append a short timestamped completion entry to
`D:\project-coordination\CURRENT_WORK.md` with the reviewed revision, outcome, and report
location. Include no secrets or private evidence. If that local path is unavailable,
report that the completion entry could not be written.
