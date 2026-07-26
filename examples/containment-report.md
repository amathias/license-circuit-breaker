# Containment report

**Verdict:** `escalated`  
**Generated:** 2026-07-26T01:43:56.063276+00:00  
**Plan:** `fd7fcb6fdc8d1cf6ae603368b6c9f42c42acb48fc5881656e4c85f5a6031f0a2`

> **SIMULATED DATAHUB RUN.** The catalog reads and the writeback in this report ran against the deterministic in-memory DataHub substitute, not a live instance. The local artifact changes below are real and were probed directly.

## Rights event

- **Event:** `evt-lcb-demo-001` v1
- **Source:** `urn:li:dataset:(urn:li:dataPlatform:duckdb,license.reviews.partner_feed,PROD)`
- **Effective:** 2026-07-26T01:43:44.087014+00:00
- **Reason:** Partner revoked training and retrieval rights effective immediately
- **Purposes lost:** retrieval, training
- **Replacement source:** `urn:li:dataset:(urn:li:dataPlatform:duckdb,license.reviews.approved_feed,PROD)`
- **Requested by:** governance@example.com

## Decisions

| Priority | Artifact | Class | Actions | Rules |
|---:|---|---|---|---|
| 90 | `license.services.predict_api` | api | freeze | LCB-R050 |
| 65 | `license.models.review_sentiment` | model | retrain, replace | LCB-R030 |
| 60 | `license.reviews.legacy_snapshot` | dataset | escalate | LCB-R001 |
| 50 | `license.reviews.normalized` | dataset | rebuild | LCB-R020 |
| 50 | `license.indexes.review_search` | vector_index | purge, rebuild | LCB-R040 |
| 50 | `license.features.review_sentiment` | feature | rebuild | LCB-R022 |
| 40 | `license.reports.review_volume` | dataset | no_action | LCB-R010 |
| 25 | `license.exports.reviews_extract` | export | quarantine | LCB-R051 |

## Approval

- **Decision:** approved
- **Approver:** governance@example.com
- **At:** 2026-07-26T01:43:44.280255+00:00
- **Approval:** `apr-6c38bf503226`
- **Bound to plan:** `fd7fcb6fdc8d1cf6ae603368b6c9f42c42acb48fc5881656e4c85f5a6031f0a2`
- **Note:** Reviewed the lineage evidence and the cited rules.

## Execution

8/8 steps completed, 1 residual exposure(s)

| # | Artifact | Action | Status | Changed | Detail |
|---:|---|---|---|---|---|
| 0 | `license.services.predict_api` | freeze | completed | yes | serving state set to blocked |
| 1 | `license.exports.reviews_extract` | quarantine | completed | yes | moved the published export to reviews_extract.csv under quarantine |
| 2 | `license.indexes.review_search` | purge | completed | yes | purged 24 vectors |
| 3 | `license.reviews.normalized` | rebuild | completed | yes | rebuilt normalized with 20 rows from urn:li:dataset:(urn:li:dataPlatform:duckdb,license.reviews.approved_feed,PROD) |
| 4 | `license.features.review_sentiment` | rebuild | completed | no | review_sentiment already holds the 20 rebuilt rows; no change made |
| 5 | `license.indexes.review_search` | rebuild | completed | yes | rebuilt 20 vectors from urn:li:dataset:(urn:li:dataPlatform:duckdb,license.reviews.approved_feed,PROD) |
| 6 | `license.models.review_sentiment` | retrain | completed | yes | trained review_sentiment/v2-approved on 20 approved rows; the served version is still v1 |
| 7 | `license.models.review_sentiment` | replace | completed | yes | serving review_sentiment/v2-approved |

## Verification

8/8 probes passed (6 containment, 2 precision)

| Artifact | Probe | Result | Observed |
|---|---|---|---|
| `license.reviews.normalized` | table_scan | PASS | 20 rows, none from the partner feed |
| `license.exports.reviews_extract` | export_access_probe | PASS | refused: the published export has been quarantined and is no longer retrievable at its published path |
| `license.services.predict_api` | endpoint_probe | PASS | refused: frozen by approved containment action, actor=governance@example.com |
| `license.indexes.review_search` | index_manifest_scan | PASS | 20 documents indexed, none from the partner feed |
| `license.features.review_sentiment` | table_scan | PASS | 20 rows, none from the partner feed |
| `license.models.review_sentiment` | training_manifest_scan | PASS | serving v2-approved trained on 20 rows from ['urn:li:dataset:(urn:li:dataPlatform:duckdb,license.reviews.approved_feed,PROD)'] |
| `license.reports.review_volume` | availability_probe | PASS | 4 rows still queryable |
| `license.models.approved_sentiment` | availability_probe | PASS | serving v1 from ['urn:li:dataset:(urn:li:dataPlatform:duckdb,license.reviews.approved_feed,PROD)'] |

## Residual exposure

| Artifact | Reason | Action | Detail |
|---|---|---|---|
| `license.reviews.legacy_snapshot` | escalated | escalate | DataHub lineage has a gap on at least one path to this artifact; Manual confirmation required that no untracked copies exist |

## DataHub writeback

- **attempted:** `8`
- **verified:** `8`

| Artifact | Status | Tag | Aspects | Verified |
|---|---|---|---|---|
| `license.reviews.partner_feed` | escalated | `license-revocation-escalated` | globalTags, datasetProperties | yes |
| `license.services.predict_api` | contained | `license-revocation-contained` | globalTags, datasetProperties | yes |
| `license.models.review_sentiment` | contained | `license-revocation-contained` | globalTags, datasetProperties | yes |
| `license.reviews.legacy_snapshot` | escalated | `license-revocation-escalated` | globalTags, datasetProperties | yes |
| `license.reviews.normalized` | contained | `license-revocation-contained` | globalTags, datasetProperties | yes |
| `license.indexes.review_search` | contained | `license-revocation-contained` | globalTags, datasetProperties | yes |
| `license.features.review_sentiment` | contained | `license-revocation-contained` | globalTags, datasetProperties | yes |
| `license.exports.reviews_extract` | contained | `license-revocation-contained` | globalTags, datasetProperties | yes |

## Limitations

- License Circuit Breaker supports compliance operations. It does not provide legal advice, does not interpret contract text, and makes no determination about whether any obligation has been met. The rights asserted here are the ones an operator recorded.
- Containment covers descendants represented in the demonstrated DataHub graph. Untracked copies, offline extracts, and systems outside the graph are not addressed. Stopping a model serving is not proof that it has unlearned its training data.

## How this example was produced

Captured verbatim from an offline run, then committed unedited apart from this
section. To reproduce it:

```bash
APP_ENV=offline APP_STATE_DIR=.state/example-run python -m demo.cli estate build
APP_ENV=offline APP_STATE_DIR=.state/example-run python -m demo.cli contain --approve
```

The command exits `9`, not `0`. That is the correct outcome: one descendant
reaches the estate through a lineage path DataHub cannot complete, so the plan
escalates it instead of claiming containment, and a verdict short of
`contained` is a non-zero exit. Exit `0` here would mean the run had found
nothing left unresolved.

Run identifiers, hashes, and timestamps differ on every run. The decisions, the
rule IDs, the probe results, and the verdict do not.
