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
| Milestone | Milestone C — judge console, submission documentation, and release gates complete offline. Three live-gate defects fixed: the entity/aspect contract on `03cda1d`, the `mcp` 1.28 transport signature on `c0574cd`, and payload parsing on `0674f3a`. **Live seed, transport, and tool discovery are all confirmed working**; readiness must be re-run |
| Verified commit/artifact | See "Deployment candidate" below |
| Build command | `py -3.13 -m venv .venv && .venv/Scripts/python.exe -m pip install -e ".[dev]"` |
| Console build command | `npm --prefix web install && npm --prefix web run build` — **see the deployment note below** |
| Test command | `.venv/Scripts/python.exe -m pytest tests/` |
| Fast test command | `.venv/Scripts/python.exe -m pytest tests/ -m "not slow"` |
| Lint command | `.venv/Scripts/python.exe -m ruff check .` |
| Coverage command | `.venv/Scripts/python.exe -m pytest tests/ -m "not slow" --cov` (floor 85%) |
| Typecheck command | `npm --prefix web run typecheck` |
| Seed command | `python -m demo.cli seed` (emits full catalog entries, then verifies by reread; **idempotent — re-running completes a partial instance in place**) |
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
| DataHub read | **Partially verified live.** Transport and tool discovery confirmed working on `0674f3a`. `get_entities`/`get_lineage` returned real data, which is how the payload shapes were captured; the parsers that consume them are fixed but not yet re-run live. |
| DataHub writeback | **Seed verified live on `c0574cd`** by the coordinator's read-only DB audit: 12/12 allowlisted `dataset` URNs active, no foreign ML URNs. Slice writeback and durable revocation writeback remain unverified live. |
| Blockers | Live gates require an AWS/SSM session this session was barred from. All three live defects were diagnosed and fixed from artifacts supplied by the coordinator — the DataHub registry, the installed `mcp` signature, then the captured payloads — never from a live run by this session |
| Evidence produced | 701 tests, 90.62% coverage, `examples/` (simulated), `docs/MILESTONE_B.md`, `docs/DECISIONS.md` (27 ADRs) |

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

**One live result has been reported back, by the coordinator rather than produced
here.** The gate on `c0574cd` confirmed by read-only DB audit that the seed
materialized all 12 allowlisted `dataset` URNs active, with no foreign
`mlModel`/`mlFeatureTable` URNs. That is genuine live evidence for the *write*
half of seeding, and it is recorded as the coordinator's, not as this session's.
It does not close gate 4: the reread verification that seed performs never ran,
because MCP was broken in the same run. Gate 3 is untouched by it.

**A second live result was reported back from the gate on `0674f3a`:** the MCP
transport connected and tool discovery passed, and `get_entities` / `get_lineage`
returned real data — which is how the exact payload shapes in
`tests/test_mcp_payloads.py` were captured. That confirms the transport half of
gate 3. It does not close gate 3, because the parsers consuming those payloads
were broken in that same run and their fix has not been re-run live.

Note that shared OpenSearch was down for part of that run. An infrastructure
outage and a parser bug are different failures with different owners, and until
this candidate they rendered identically as "entities missing". They no longer
do: a payload the normalizers cannot read raises `PayloadError` naming the keys
actually received, and readiness reports it as "could not read project entities"
rather than as an inventory of missing ones.

`README.md` still states that no live DataHub evidence has been captured **in
this repository**, which remains exactly true — every artifact under `examples/`
and every receipt here is simulated. The coordinator's audit and captured
payloads are external to the repository; the payloads appear only as test
fixtures, which the file labels as captured rather than produced.

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

> **This decision was documented but not implemented.** `demo/graph.py` minted
> native `mlModel`/`mlFeatureTable` URNs for three nodes while the seed emitted
> dataset aspects for all of them, which is what produced the 422 on the live
> gate. Fixed and enforced — see "Response to the live gate on `03cda1d`" below
> and ADR-024.

### Response to the live gate on `03cda1d`

The live gate found a release-blocking defect. `python -m demo.cli seed` failed
closed with **HTTP 422 "Unknown aspect datasetProperties for entity mlModel"**.
Both halves are fixed, with regressions.

| # | Defect | Resolution | ADR |
|---|---|---|---|
| 1 | Entity model contradicted itself: `adapters/catalog.py` and this handoff documented a uniform dataset-URN model, `demo/graph.py` minted native `mlModel`/`mlFeatureTable` URNs for three nodes, and the seed attached `DatasetPropertiesClass` to all of them | Uniform dataset model implemented, not just documented. `DemoNode` derives the entity type from the URN and raises at import on anything but `dataset`; the stale hand-declared `entity_type` field is gone | ADR-024 |
| 2 | Nothing could catch it: the SDK accepts any aspect beside any URN, and the offline seed wrote to the fake with a hardcoded `entity_type="dataset"` | Every proposal is checked against a pinned snapshot of DataHub's server-side registry, at `build_entity_proposals` and again at `LiveCatalog.emit`. Both seed paths now build the same real SDK proposals, so the fake enforces exactly what GMS enforces | ADR-024 |
| 3 | A partial seed reported only that it failed — a half-populated shared instance was indistinguishable from an untouched one | Seed attempts every entity, then fails closed with a `SeedResult` naming what landed, what failed with which error, what was never attempted, and which edges were skipped. Written to `$APP_STATE_DIR/seed-partial.json`; CLI exits 7 | ADR-025 |
| 4 | The only documented cleanup was `reset`, which refuses a partial target set by design — leaving no recovery short of a global cleanup | Seed is the recovery and is idempotent by construction. Both reset refusals now name it and say so | ADR-025 |

**Why the uniform model was preserved rather than replaced.** The pinned registry
shows DataHub 1.6.0 registers on `mlModel` and `mlFeatureTable` neither
`datasetProperties` — which carries `artifact_class`, `purposes`, `exposure` and
`criticality`, the entire input to the policy engine — nor `upstreamLineage`,
which is the edge set the impact analysis walks. So the 422 was the first of at
least two: the lineage proposals would have failed next. Native support means
`mlModelProperties`/`mlFeatureTableProperties` plus a separate ML lineage
mechanism, **verified live**, which this session could not do. Implementing half
of it would have been worse than either end state. The cost is stated in ADR-024
and disclosed in `README.md`: the model and feature table appear as datasets on
the `mlflow` and `feast` platforms.

**The pinned registry.** `adapters/datahub_entity_registry_1_6_0.json` is derived
from `metadata-models/src/main/resources/entity-registry.yml` at DataHub tag
`v1.6.0`, matching the `acryl-datahub==1.6.0.15` pin. It is stored in the shape
`EntityAspectSpecs.to_dict()` produces, so the SDK's own type parses it and a
live server's registry can be diffed against it with no translation layer. A test
asserts the snapshot and the installed SDK describe the same DataHub, so
upgrading one without the other fails the build.

#### Coordinator recovery steps

The shared instance holds a partial fixture set from the failed run. **Do not run
`reset` and do not run any global cleanup.**

1. Deploy the SHA in "Deployment candidate" below.
2. Confirm reset is *not* needed. `python -m demo.cli reset` will refuse: the
   sentinel is deliberately absent after a partial seed. That refusal is correct
   and now names the recovery.
3. Re-run the seed. It is idempotent and completes the instance in place:
   ```bash
   APP_ENV=live python -m demo.cli seed     # expect "Verified: 12 entities, 9 edges", exit 0
   ```
4. If it exits `7` again, read `$APP_STATE_DIR/seed-partial.json`. It names every
   entity that landed, every one that failed with its error, and everything not
   attempted. Attach that file rather than terminal output.
5. Then continue the live sequence under "Deployment candidate".

**On the entities already present.** The failed run materialized the fixture
nodes preceding the first ML URN — `license.reviews.partner_feed`,
`license.reviews.approved_feed`, and `license.reviews.normalized`. All three are
`dataset` URNs that this fix does not rename, so the reseed simply re-upserts
them. This project emitted 13 aspect proposals across those three entities before
the 422; DataHub adds key and browse-path aspects of its own, so the row count
you observed will be higher and is expected — we are not able to reconcile the
exact figure from here and are not claiming to.

**One thing to verify.** GMS rejected the *first* proposal for each ML URN, so no
`mlModel` or `mlFeatureTable` entity should exist. This could not be confirmed
from this session. If any do exist, they fall outside the new allowlist, which
means this project's `reset` will not touch them by design — they would need
coordinator removal. Checking is a read: search the instance for
`urn:li:mlModel:` and `urn:li:mlFeatureTable:` under the `license.` prefix.

### Response to the live gate on `c0574cd`

The entity/aspect fix held. **The live gate confirmed the seed succeeded**: the
read-only DB audit found all 12 allowlisted `dataset` URNs active, and no foreign
`mlModel`/`mlFeatureTable` URNs — closing the open question left in the previous
handoff. The uniform-dataset model works against a live DataHub 1.6.0.

The gate then failed at MCP verification and readiness:

```
TypeError: streamable_http_client() got an unexpected keyword argument 'headers'
  adapters/mcp_client.py:_session
```

| # | Defect | Resolution | ADR |
|---|---|---|---|
| 1 | `mcp` 1.28 changed the transport to `(url, *, http_client=None, terminate_on_close=True)`; `headers=` was removed, and the call site still passed it, so every session raised before issuing a request | The transport is handed an `httpx.AsyncClient` we build, carrying the bearer header, a split request/SSE timeout, and `follow_redirects=True` to match `mcp`'s own client | ADR-026 |
| 2 | The suite asserted on *source text* (`assert "streamable_http_client" in source`), which is true of a call that cannot execute — a pinned-dependency signature change was invisible to it | Tests bind against `inspect.signature` of the installed `mcp`; the transport spy validates every call against that same real signature; and one test drives the genuine unpatched transport and asserts the failure is a connection failure, not an unexpected-keyword `TypeError` | ADR-026 |
| 3 | The client is never closed by the transport when it is passed in, and this transport opens one session per call — a connection pool leaked per MCP request | The client is opened in an `async with` that unwinds on success, on handshake failure, and on an exception thrown back into the session | ADR-026 |
| 4 | A stored `timeout` was passed nowhere, so every request used library defaults | Request budget and SSE read budget are applied as an `httpx.Timeout`, and both defaults are asserted equal to `mcp`'s own | ADR-026 |
| 5 | A refused connection surfaced as `unhandled errors in a TaskGroup (1 sub-exception)` — true and useless in a readiness report | Exception groups are flattened to leaf causes, so readiness names `ConnectTimeout` / `ConnectionRefusedError` | ADR-026 |

Secret handling is unchanged in intent and stronger in practice: the token still
only travels to the transport, and anything raised out of a session is now
scrubbed of both the literal token and any `Bearer <value>` sequence before it
can reach a receipt or a readiness report.

#### Coordinator recovery steps

The fixtures are already correct on the shared instance. **Do not reset and do
not reseed** unless step 2 says otherwise.

1. Deploy the SHA in "Deployment candidate" below. This is a client-side
   transport fix; it changes nothing in the catalog.
2. Re-run verification and readiness only:
   ```bash
   curl -s $APP_PUBLIC_URL/api/readiness    # expect "ready", 10/10 checks passed
   ```
   `entity_coverage` and `fixture_lineage` should pass against the fixtures
   already in place. If either fails, *then* re-run `seed` — it is idempotent and
   completes in place; still no reset.
3. Then the remaining live sequence under "Deployment candidate". Note that
   `slice` performs a reversible writeback and `reset`/`restore` are a matched
   pair, so the instance ends where it started.

If MCP still fails, the readiness detail now names the leaf cause rather than a
task-group wrapper — attach that line.

### Response to the live gate on `0674f3a`

The MCP 1.28 transport fix worked and tool discovery passed. The gate then
surfaced a parser-compatibility defect — and, importantly, separated it from an
infrastructure fault.

**The OpenSearch distinction.** Shared OpenSearch was down for part of the run,
which makes `get_entities` genuinely fail. When it recovered and the same call
returned data that readiness still could not use, the remaining fault was proven
to be ours. That separation is the diagnosis, and the old code actively obscured
it: an unreadable response and an empty catalog both rendered as "entities
missing." They are opposite conditions and now report differently — a payload the
normalizers do not recognize raises `PayloadError` naming the keys it did get,
rather than returning an empty list.

| # | Defect | Resolution | ADR |
|---|---|---|---|
| 1 | `_iter_entities` looked for `entities`/`results`/`data`; the envelope is `{"result": [...]}`, so it returned `[]` and readiness reported 12/12 unusable against a correctly seeded instance | Strict unwrap of `result`; any other shape raises `PayloadError` | ADR-027 |
| 2 | `customProperties` is a list of `{key, value}` pairs, not a mapping — `dict()` on it gave `{}`, failing the `artifact_class`/`purposes` check for every entity | Pairs normalized to a mapping; a mapping in that position now raises rather than being accepted | ADR-027 |
| 3 | `tags` is `{"tags": [{"tag": {"urn": ...}}]}`; the old code iterated the outer object and produced the literal string `"tags"` as the only tag, so every project-tag check failed | Nested association parsed to bare tag names | ADR-027 |
| 4 | `domain` is `{"domain": {"urn": ...}}`; the old code read `name`/`urn`/`id` off the outer object and returned `None`, reported as "no domain" | Nested domain URN extracted, which is what readiness compares against `domain_urn()` | ADR-027 |
| 5 | `_to_lineage_edges` did not parse `{"downstreams": {"total": N, "searchResults": [{"entity": {"urn": ...}, "degree": D}]}}` at all, and would have used `str(dict)` as a URN if it had | Exact parse; `degree == 1` is a provable edge, deeper descendants are emitted `resolved=False` rather than as fabricated one-hop paths | ADR-027 |
| 6 | `check_fixture_lineage` verified 9 declared edges from one walk out of the source, which this envelope can never satisfy — the check could not pass live regardless of the parser | `LiveDataHubClient.has_edge` asks about each specific upstream, one MCP call per edge | ADR-027 |
| 7 | With `isError=true`, a non-JSON text block and no `structuredContent`, the message rendered as `returned an error: None`, discarding the server's only explanation — which during the outage would have said so | `extract_error_text` falls back to the raw text blocks, still token-scrubbed, and says so explicitly when the server sent nothing | ADR-027 |

Every payload in `tests/test_mcp_payloads.py` is the exact captured shape, not an
invention. Two further strictness rules fall out of the same principle: a missing
`searchResults` is accepted only when the server also reports `total` 0 or absent,
so a dropped key cannot read as an empty graph; and `total > len(searchResults)`
raises, because a truncated descendant set is a smaller blast radius than the
real one.

#### Coordinator recovery steps

The fixtures are correct and active on the shared instance. **Do not seed first,
do not reset.** This is a client-side parser fix and changes nothing in the
catalog.

1. Deploy the SHA in "Deployment candidate" below.
2. Re-run readiness only:
   ```bash
   curl -s $APP_PUBLIC_URL/api/readiness    # expect "ready", 10/10 checks passed
   ```
   `entity_coverage` should now report 12 entities active, tagged, domained, with
   `['artifact_class', 'purposes']`. `fixture_lineage` verifies the 9 declared
   edges with one MCP call each.
3. Only if `entity_coverage` reports genuinely missing entities — as opposed to a
   `PayloadError` — is a reseed warranted; `seed` remains idempotent.
4. Then the remaining live sequence under "Deployment candidate".

If a `PayloadError` appears, the envelope has changed again: the message names
the keys actually received, and that message is what to attach.

**Two differences to expect on the live run, both correct.** The fixture's
deliberately unresolvable edge (`SOURCE -> ORPHAN`) is materialized live as a
real `UpstreamLineage` aspect, so DataHub resolves it — unlike offline. And
descendants beyond the first hop are reported `resolved=False`, because
`get_lineage` gives a degree, never a parent. The live verdict should still be
`escalated`, but for a different reason than the offline demo. Reconstructing
exact multi-hop parentage needs one `get_lineage` call per node; that changes the
request pattern and is flagged here rather than adopted unilaterally.

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
- **Entity/aspect contract gate** (`tests/test_entity_aspect_contract.py`): builds
  every proposal the seed emits as a real SDK object, round-trips each through
  the emitter's own `to_obj`/`from_obj`, and checks every `(entityType,
  aspectName)` pair against the pinned registry. No fake participates, so no fake
  can satisfy it (ADR-024).
- **Seed recovery gate** (`tests/test_seed_recovery.py`): partial-seed evidence,
  sentinel withholding, edge skipping, and idempotent reseed from partial and
  soft-removed states (ADR-025).
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
verified and restored · `7` **partial seed: some entities materialized and others
did not; evidence written to `$APP_STATE_DIR/seed-partial.json`** · `8` `contain`
refused by the approval gate, nothing enforced · `9` `contain` completed with a
verdict short of `contained`.

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

Every proposal in this table is checked against the pinned DataHub 1.6.0
entity/aspect registry before it is built and again before it is emitted. An
aspect an entity type does not register raises `AspectContractError` offline
instead of returning 422 from GMS.

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
| Product candidate | the product commit this handoff ships with; its exact SHA is recorded by the `docs:` commit that immediately follows it |
| Supersedes | `0674f3a985ea400aa6c45385982b37f8adbd517e` (**rejected** by the live gate: payload parsers could not read the real envelopes; its transport fix was confirmed good) |
| Tests | 699 fast + 2 slow archive-install = **701 passing** |
| Coverage | **90.62%** (floor 85%) |
| Lint | ruff clean |
| Console typecheck | `tsc --noEmit` clean; `vite build` succeeds |
| Packaging gate | passing, including the registry snapshot in the installed archive |
| Public-safety gate | passing |
| Working tree | clean |
| Local `main` == `origin/main` | yes |

**Promotion caveat.** This candidate is verified *offline only*. Integration gates 3
and 4 require a live DataHub run that this session could not perform. The 422 that
rejected `03cda1d` was diagnosed against the pinned registry snapshot, not against
the shared instance: **no live run has confirmed the fix.** Re-run the live gate
using "Coordinator recovery steps" above before promoting.

Suggested live verification sequence, in a non-judged environment first:

```bash
APP_ENV=live python -m demo.cli seed      # expect "Verified: 12 entities, 9 edges"
curl -s $APP_PUBLIC_URL/api/readiness     # expect status "ready", 10/10 checks passed
APP_ENV=live python -m demo.cli slice     # expect exit 0 and verified=True restored=True
APP_ENV=live python -m demo.cli reset     # expect "12 soft-removed"
APP_ENV=live python -m demo.cli restore   # expect "Restored 12 entities"
curl -s -o /dev/null -w '%{http_code}' $APP_PUBLIC_URL/   # 200 if the console was
                                                          # built, 404 if not
```

A non-zero exit from `slice` means the writeback was not both verified and
restored; treat the writeback gate as failed and check the receipt's
`residual_risk` before rerunning.

Exit `7` from `seed` means a partial seed. Read
`$APP_STATE_DIR/seed-partial.json` and re-run `seed`; it is idempotent and needs
no cleanup first. Do not run `reset` to recover — see "Coordinator recovery
steps" above.

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



