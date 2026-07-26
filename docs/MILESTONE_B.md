# Milestone B: guarded vertical slice

What this milestone proves, how to run it, and — importantly — what it does **not**
yet prove.

## Scope

One rights event travels end to end: read license and lineage context, validate it,
produce a deterministic containment decision, perform one reversible
`license.`-scoped writeback, immediately re-read and restore it, and persist
sanitized receipts.

Local containment adapters (freezing the API, purging the index, quarantining the
export, retraining the model) are **not** in this milestone. They arrive with their
own approval gate next.

## Running it

Offline, against the deterministic in-memory fake — no DataHub required:

```bash
APP_ENV=offline python -m demo.cli slice
APP_ENV=offline python -m demo.cli seed
APP_ENV=offline python -m demo.cli reset
APP_ENV=offline python -m demo.cli restore
python -m demo.cli verify        # verify the receipt ledger hash chain
```

Against the shared instance, with an SSM port-forwarding session already
established by the coordinator:

```bash
APP_ENV=live python -m demo.cli seed     # emits full catalog entries, then verifies by reread
APP_ENV=live python -m demo.cli slice    # exits non-zero unless verified AND restored
APP_ENV=live python -m demo.cli reset    # soft, exactly-allowlisted
APP_ENV=live python -m demo.cli restore  # reverses the soft reset
```

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 2 | Refused: sentinel missing, or target set not exactly the allowlist |
| 3 | Refused by the namespace guard |
| 4 | Seed emitted but could not be verified by reread |
| 5 | Partial failure during reset or restore |
| 6 | Slice completed but the writeback was not both verified and restored |

Code 6 matters most. A good plan with a dirty writeback is not a passing run, and
exiting zero would let CI treat residue on the shared instance as success.

## Evidence status

**No live DataHub evidence has been captured.** This session was explicitly barred
from AWS access, so every artifact in `examples/` and every receipt in
`APP_STATE_DIR` was produced against the in-memory fake and is flagged
`"simulated": true`.

The live gate — real read, real writeback, re-read, restore, receipts — remains
**open** and belongs to the coordinator's verification pass.

> **Since closed.** This section records the position as of Milestone B and is
> left as written. The coordinator's live closeout on `eb81588` subsequently
> passed that gate; see "Live closeout on `eb81588`" in `COORDINATOR_HANDOFF.md`.
> The first paragraph above still holds — no live artifact is committed to this
> repository.

## What the offline slice produces

```text
[ 90] freeze             LCB-R050   license.services.predict_api
[ 65] retrain, replace   LCB-R030   license.models.review_sentiment
[ 60] escalate           LCB-R001   license.reviews.legacy_snapshot
[ 50] rebuild            LCB-R020   license.reviews.normalized
[ 50] purge, rebuild     LCB-R040   license.indexes.review_search
[ 50] rebuild            LCB-R022   license.features.review_sentiment
[ 40] no_action          LCB-R010   license.reports.review_volume
[ 25] quarantine         LCB-R051   license.exports.reviews_extract
```

Four things are load-bearing in that output:

- **`legacy_snapshot` escalates** rather than reporting clean. Its upstream edge does
  not resolve, so impact cannot be bounded.
- **`review_volume` is the only `no_action`.** It uses `analytics`, a purpose this
  event retains. Precision, not volume, is what makes the plan credible.
- **`predict_api` freezes and `reviews_extract` quarantines** even though neither
  declares a revoked purpose — see contamination propagation below.
- **The plan is not an all-clear** while an escalation is outstanding.

## Guards

| Guard | Behavior |
|---|---|
| Namespace | Every read, write, seed, reset, and enforcement target must resolve under `license.`; anything else raises `NamespaceViolation`. |
| Global selectors | `*`, `all`, `%`, empty string rejected outright. |
| Reset sentinel | Reset refuses unless `license.__fixture_sentinel__` exists and carries both the fixture marker and the project tag. |
| Reset exactness | The target set must match the allowlist exactly. Missing, extra, unmarked, or empty all fail closed. |
| Reset blast radius | Soft status change plus tag clearing only. Shared domain and tag control entities are never created, mutated, or removed. |
| Seed verification | Every allowlisted entity and edge is reread after emission; a failed reread fails the seed. |
| Readiness | Mutates nothing. Requires token, MCP tools, exact project domain, both tag controls, every allowlisted entity active with required custom properties, and complete fixture lineage. |
| Writeback | Restoration runs in `finally` from the moment the write is attempted, including when the verifying reread raises. |
| Receipts | Secrets redacted before write; hash-chained for tamper evidence. |

## Live-path corrections

The coordinator rejected the first candidate (`c116a26`) on seven live-path
defects. All are fixed; the ones worth knowing about:

- **Seed only set tags.** A live instance got tags and nothing else — no
  properties, no `artifact_class`, no domain, no active status, no lineage. The
  graph looked seeded and was unusable. Now emits full catalog entries via SDK
  proposals and verifies by reread. (ADR-015)
- **MCP skipped `initialize`.** Hand-rolled JSON-RPC worked only by the server's
  leniency. Now uses `ClientSession` over `streamable_http_client` with schema
  introspection, batching, and `upstream=false` stated explicitly. (ADR-014)
- **Readiness wrote a probe file** on every poll, and its domain check was
  conditional on a domain existing — so `domain=None` passed the check designed to
  catch it. (ADR-018)
- **Rollback was skipped when the verifying reread raised**, leaving a stray tag
  on shared state. Now runs in `finally`. (ADR-017)
- **Packaging omitted `demo` and `policy`**, so an installed archive could not
  seed, slice, or load a rule. No source-tree test could have caught it. (ADR-019)

## Contamination propagation

The first offline run reported `no_action` for both the prediction API and the CSV
export. Both were wrong, and wrong in the product's most dangerous direction.

`is_affected` compared only a descendant's *own* declared purposes against the
revoked set. The API declares `serving`; the export declares `export`. This event
revokes `training` and `retrieval`, so neither matched and both looked clean —
despite the API serving predictions from a model trained on revoked data and the
export containing revoked-derived rows.

An artifact is now in scope when its own purposes are revoked **or** an ancestor on
a lineage path uses a revoked purpose. Propagation deliberately starts at the
descendants rather than the source: treating the revoked source itself as
contaminating would sweep in the analytics branch and destroy the precision that
makes the unaffected result meaningful.

Regression tests: `tests/test_workflow.py::TestImpactPlan::test_api_is_frozen`,
`test_export_is_quarantined`, `test_contamination_propagates_downstream`, and
`test_contamination_does_not_reach_the_retained_purpose_branch`.

## Limitations

- Containment adapters are not implemented; nothing local is disabled yet.
- The receipt ledger is tamper-**evident**, not tamper-proof. It detects edits,
  reordering, and truncation; it cannot survive an attacker who rewrites the file.
- Purpose metadata is read from DataHub custom properties seeded by this project.
  Real-world artifacts would need that metadata supplied or inferred.
- Reversible writeback proves capability without leaving state behind. It is not a
  substitute for the durable revocation-status writeback due in a later milestone.
