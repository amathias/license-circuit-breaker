# Independent review remediation

Date: 2026-09-15. Baseline reviewed by Claude: `1a97ca8`.

These are post-deadline repository hardening changes. They are not evidence that the
hosted service has been upgraded or that its existing state is intact. The review
was used as a defect report; no approval from the reviewer is required to fix it.

## Findings and changes

| Finding | Change | Regression evidence / remaining limit |
|---|---|---|
| LCB-01: incomplete purpose metadata | Missing, empty, malformed, and mixed-validity purposes escalate. Missing evidence propagates through intermediate ancestors. Readiness validates values; policy loading rejects unknown conditions and invalid condition types. | `test_review_policy.py` covers the normalized-to-export false negative and preserves the unaffected analytics branch. |
| LCB-02: approval drift | Approval requests must include the displayed plan hash. Identity covers the stable shared CLI/API event, policy, normalized descendant facts, replacement grant, validation, and decisions. Recorded event versions cannot silently change content. | `test_review_authorization.py` rejects stale/missing hashes; policy tests change facts without changing actions; contract tests compare event hashes across process hash seeds. |
| LCB-03: unsafe resume | A run must match the plan, recorded approval, completed step identities, scope, and current estate fingerprint. Action intent is durable before an adapter runs; final outcome and fingerprint commit atomically. Exactly one matching interrupted action may be retried through its idempotent adapter. | Cross-plan and unexplained rebuilt-estate regressions reject resume before journal mutation. A process-death regression resumes after a quarantine move, and quarantine repairs missing metadata. Legacy runs without fingerprints require a fresh run. |
| LCB-04: environment bypass | Environment names are normalized and allowlisted. Unset defaults to read-only production; typos fail configuration validation. Docs use the same settings loader as runtime guards. | Tests cover whitespace, unknown names, and unset configuration. Trusted local modes remain explicitly selectable. |
| LCB-05: false all-clear / over-reach | Failed precision checks become residual exposure. Passing observed checks is distinct from complete coverage. Escalations prevent `contained=true`; UI wording follows the evidence verdict. | Evidence regressions delete the unaffected table/model and cover escalation-only verification. |
| LCB-06: receipt integrity | Receipt append and validation use cooperating thread/process locks. Planning GETs no longer append receipts or remember plans. Evidence reads and execution reject an invalid chain. | Tests exercise 160 threaded appends and 60 appends across three processes. Tail deletion remains undetectable without an external trusted anchor; documentation now says so. |
| LCB-07: dependency/provenance failures | Failed prerequisite steps block dependent rebuild/retrain/replace operations. Independent containment may continue. Rebuilds check actual input provenance; model replacement checks candidate provenance and bytes. Replacement rights must be independently valid. | Tests inject warehouse failure and invalidate replacement grants. Local adapters explicitly support only the implemented approved fixture feed. |
| LCB-08: missing descendants | Discovery expands beyond the initial six-hop window until the boundary closes. Resource exhaustion fails planning explicitly. Foreign and untagged descendants remain visible as escalations. Live ownership parsing preserves owners. | Tests include a nine-hop chain, a foreign descendant, and an untagged target. Coverage remains limited to lineage represented by DataHub. |
| LCB-09: verification trusts manifests | Index probes inspect stored entries even if search is frozen. Index/model manifests include hashes of actual artifact bytes, checked by verification. Unreadable artifacts produce failed probes. | Restoring old vectors/model bytes under a new manifest fails verification. This is a fixture-specific local verifier, not external attestation or proof of model unlearning. |
| LCB-10: console state | Reset preserves governance history and invalidates approval. Refresh applies one matching plan/approval/evidence generation; errors disable mutation until refreshed. Approval clears stale execution/writeback. | API reset/history regression, TypeScript checking, and production build. Interactive browser acceptance is still a deployment check. |
| LCB-11: anonymous resource use | Global pending-confirmation cap, confirmation preservation on capacity retry, planning-read budget, and bounded query/prediction inputs. | Contract tests cover distinct-client exhaustion, retry, budget expiry, and oversized queries. Rate budgets remain per worker; proxy client identity is not verified here. |
| LCB-12: reproducibility/build | Tests isolate environment/state and avoid wall-clock inequality. `uv.lock` freezes runtime/dev dependencies; CI runs archive installation and pins actions by SHA. Removed unused networkx, bounded event semantics, upgraded nanoid, pinned isolated build setuptools, and made artifact/evidence JSON replacement atomic. | The DataHub SDK still constrains runtime setuptools below 82; see the dependency exception below. Archive tests intentionally export the current shippable working tree. |

## Additional review observations

- **Historical evidence:** the API saves verification immediately after execution and serves that
  snapshot for a requested run. Reset does not rewrite it. Execution and snapshotting share the
  estate lock. The default evidence view explicitly describes current state.
- **DataHub metadata updates:** outcome properties and status tags use the pinned SDK's dataset
  PATCH builder. Offline contract tests inspect the real SDK proposals and prove that neither
  path replaces an aspect from a stale indexed read. A concurrent unrelated tag survives status
  writeback. Live server acceptance of these PATCH operations has not been checked in this
  remediation.
- **Partial writeback:** failed writes return an unverified receipt stating that partial application
  is possible. Tags and properties remain separate, non-transactional writes. A concurrent status
  writer can still race; verification rereads and rejects conflicting status tags.
- **Crash recovery:** atomic JSON writes prevent partial JSON documents. The journal records intent
  before a filesystem action and commits the outcome with its estate fingerprint. A single matching
  interrupted action is retried through an idempotent adapter; quarantine recreates missing metadata
  after a completed move. Unexplained drift, multiple interrupted actions, or an ambiguous state with
  both published and quarantined copies still fail closed for operator review.
- **Local trust:** the ledger and fingerprints do not defend against an operator who can rewrite
  both artifacts and their evidence. No hosted ledger audit was performed. Do not erase a failed
  chain to make verification pass.

## Dependency exception

The project build backend is isolated and pinned to setuptools 83.0.0, the fix listed for
[PYSEC-2026-3447](https://osv.dev/vulnerability/PYSEC-2026-3447). The pinned
`acryl-datahub==1.6.0.15` runtime requires `setuptools<82.0.0`, so the runtime lock retains 81.0.0.
Forcing 83 into that environment would violate the SDK dependency contract. The advisory concerns
source-distribution manifest exclusions on macOS; this is a documented dependency exception,
not a claim that the entire Python environment is free of advisories. A compatible DataHub SDK
upgrade and live integration test are required to remove it.

The console lock updates the dev dependency nanoid to the compatible patched release for
[GHSA-2v37-7h3g-55p8](https://github.com/advisories/GHSA-2v37-7h3g-55p8).

## Upgrade and review procedure

1. Preserve existing governance databases, receipts, and artifacts before upgrading. An existing
   broken ledger is evidence to investigate, not a file to replace silently.
2. Install the committed lock with `uv sync --frozen --extra dev`. Select `APP_ENV` explicitly;
   unknown values now fail startup.
3. Rebuild disposable local artifacts so manifests have byte hashes. Schema initialization adds
   the run fingerprint column. Historical runs without fingerprints cannot resume.
4. Refresh and review the new plan. Previous approval hashes are intentionally invalidated.
5. Existing live replacement entities need the structured rights grant now present in the seed
   specification. Missing grants safely escalate. Coordinate a scoped catalog migration before
   claiming live rebuild behavior; this remediation does not seed or mutate the shared catalog.
6. Re-review the remediation revision and its tests. Assess remaining architectural limits
   separately from fixed reproductions. Repository quality alone cannot establish an owner's
   organizational staff/principal scope or impact.

## Validation

The local validation record is in [COORDINATOR_HANDOFF.md](../COORDINATOR_HANDOFF.md).
Checks include the full fast suite with the unchanged 85% coverage gate, both clean
archive-install tests, Ruff, TypeScript, Vite production build, lock consistency,
installed dependency compatibility, and an isolated offline CLI walkthrough.

The walkthrough verifies exit 8 without approval, exit 9 after approved containment
with the expected unresolved-lineage escalation, eight passing artifact probes, and
an intact receipt chain. All new adversarial tests use disposable local state. No
live catalog, hosted browser, or hosted ledger result is implied by these checks.
