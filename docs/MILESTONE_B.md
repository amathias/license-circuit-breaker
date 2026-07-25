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
python -m demo.cli verify        # verify the receipt ledger hash chain
```

Against the shared instance, with an SSM port-forwarding session already
established by the coordinator:

```bash
APP_ENV=live python -m demo.cli seed
APP_ENV=live python -m demo.cli slice
APP_ENV=live python -m demo.cli reset
```

## Evidence status

**No live DataHub evidence has been captured.** This session was explicitly barred
from AWS access, so every artifact in `examples/` and every receipt in
`APP_STATE_DIR` was produced against the in-memory fake and is flagged
`"simulated": true`.

The live gate — real read, real writeback, re-read, restore, receipts — remains
**open** and belongs to the coordinator's verification pass.

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
| Reset sentinel | Reset refuses unless `license.__fixture_sentinel__` exists and carries the fixture marker. |
| Reset scope | Only entities carrying `lcb-demo-fixture` are removed; unmarked entities inside `license.` are left alone. |
| Empty reset | An empty target list is an error, never an implicit "remove everything". |
| Readiness | Fails closed unless token, MCP tools, project domain/tag, and `license.` entities are all positively verified. |
| Receipts | Secrets redacted before write; hash-chained for tamper evidence. |

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
