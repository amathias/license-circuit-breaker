# Build Plan: License Circuit Breaker

## Delivery strategy

Build the enforcement loop before expanding the license vocabulary. The critical proof is:

> A structured rights event traverses live DataHub lineage, causes real containment actions, verifies that unsafe serving stopped, and leaves auditable DataHub context.

## Recommended repository shape

```text
/
  app/                  # API, policy, planning, jobs
  web/                  # impact graph and approval UI
  adapters/             # DataHub and containment adapters
  policy/               # structured demo rights and decision tables
  demo/                 # data, model, index, API, export, ingestion/reset
  examples/             # rights events and evidence reports
  tests/
  docs/
  docker-compose.yml
  .env.example
  LICENSE
  README.md
```

## Phase 0: Prove DataHub connectivity

- Pin and start open-source DataHub.
- Ingest a minimal source-to-model lineage chain.
- Read it through the MCP Server or Agent Context Kit.
- Perform and verify one harmless supported writeback.
- Record exact API, SDK, and DataHub versions.

Exit condition: automated smoke test proves read and write.

## Phase 1: Build structured rights and policy

- Define typed license/right, revocation event, artifact class, action, and evidence schemas.
- Implement a small explicit rule table for the demo.
- Make policy output deterministic and independently testable.
- Add tests for allowed, revoked, replacement-available, incomplete-evidence, and unaffected cases.

Exit condition: policy decisions require no LLM and cite rule IDs.

## Phase 2: Build the local supply chain

- Create source, transformations, features, model, index, API, and export.
- Add approved replacement data for one branch.
- Generate manifests linking local artifacts to DataHub URNs.
- Add reliable seed and reset commands.

Exit condition: the API and index demonstrably serve content derived from the soon-to-be-revoked source.

## Phase 3: Impact graph and action planning

- Integrate live DataHub traversal.
- Classify each descendant.
- Map policy decisions to typed containment actions.
- Include lineage paths, missing evidence, priority, owner, and validation.
- Add an unaffected branch to prove precision.

Exit condition: expected affected and unaffected artifacts are classified correctly.

## Phase 4: Enforcement adapters

- Implement API freeze, index purge/rebuild, export quarantine, and toy-model retrain/replace.
- Add approval and idempotency.
- Emit machine-readable receipts.
- Inject one controlled adapter failure to test residual-exposure reporting.

Exit condition: local artifact behavior changes for real and can be reset.

## Phase 5: Verification and writeback

- Probe service endpoints.
- Inspect index/source manifests.
- Confirm quarantined export access.
- Verify the active model's approved training manifest.
- Generate JSON and Markdown evidence.
- Write supported status and evidence references to DataHub.

Exit condition: a deliberately skipped action prevents an all-clear.

## Phase 6: Judge-facing UI and hardening

Required UI states:

1. Rights event.
2. DataHub-derived impact graph.
3. Policy decision and lineage evidence per artifact.
4. Approval.
5. Live containment actions.
6. Verification and residual exposure.
7. DataHub writeback.

Then:

- add examples;
- test clean setup;
- add Apache 2.0 license;
- document legal and technical limitations;
- record a demo under 2:45.

## Test plan

### Unit

- Rights parsing and versioning.
- Policy matrix.
- Graph traversal and unaffected branches.
- Action ordering.
- Approval and idempotency.
- Verification aggregation.

### Integration

- DataHub read/write.
- API freeze and restore.
- Index purge/rebuild.
- Export quarantine.
- Toy-model retrain and manifest switch.

### End to end

- Seed graph and serving fixtures.
- Revoke source.
- Approve plan.
- Execute containment.
- Verify zero in-scope serving or accurately report residual exposure.
- Confirm DataHub update and evidence bundle.

## Scope cuts if behind

Cut in this order:

1. Territory or jurisdiction dimensions.
2. Multiple replacement strategies.
3. Notifications and ticketing.
4. Cryptographic signing.
5. More than four adapters.

Never cut deterministic policy, real enforcement, verification, DataHub writeback, or limitation disclosure.

## Evidence to preserve

- Rights event JSON and rule IDs.
- Impact graph with lineage paths.
- Endpoint responses before and after.
- Index and model manifests.
- Quarantine and retraining receipts.
- Residual-exposure calculation.
- DataHub before/after screenshots.

## Final engineering checklist

- [ ] No raw contracts are treated as automatically interpreted legal advice.
- [ ] Deterministic policy owns enforcement.
- [ ] Every destructive action requires approval.
- [ ] All demo targets are isolated.
- [ ] Reset and rerun are reliable.
- [ ] No committed secrets.
- [ ] CI covers critical policy and enforcement paths.
- [ ] README maps proof to judging criteria.
