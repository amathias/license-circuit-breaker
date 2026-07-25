# Project Brief: License Circuit Breaker

## Product thesis

Data rights must propagate as executable constraints, not decorative catalog fields. DataHub knows where data traveled. License Circuit Breaker converts a rights change into governed containment across datasets, ML artifacts, RAG systems, services, and exports.

## Problem

When a vendor, contributor, or open-data source changes or revokes usage rights, an organization must answer:

- Which derived datasets contain it?
- Which models or indexes learned from it?
- Which endpoints or exports are still serving it?
- Can each artifact be disabled, rebuilt, or replaced?
- How can the organization prove containment?

Provenance and governance dashboards help identify risk, but the operational response remains manual and fragmented.

## MVP scenario

Create a local product-review supply chain:

1. Licensed source dataset `reviews.partner_feed`.
2. Cleaned dataset `reviews.normalized`.
3. Feature table `features.review_sentiment`.
4. A toy classifier trained from those features.
5. A vector index populated from review text.
6. A local prediction or search API.
7. A CSV export.
8. An unrelated approved dataset and model.

Ingest all entities and lineage into DataHub. Change the source right from “approved for training and retrieval” to “revoked.” The agent must discover, classify, contain, verify, and record every affected descendant.

## Core user journey

1. Governance operator selects a source asset and records a rights-change event.
2. Agent reads its license metadata and DataHub lineage.
3. Agent expands affected descendants and classifies their type and current use.
4. Deterministic policy maps each asset to required actions.
5. Agent explains impact, uncertainty, missing lineage, and proposed containment.
6. Authorized reviewer approves or edits the plan.
7. Executor freezes services, quarantines exports, purges indexes, and rebuilds or retrains approved replacements.
8. Verifier probes every artifact and confirms that prohibited material is no longer accessible.
9. Agent writes revocation status and evidence links back to DataHub.

## Functional requirements

### Rights model

- Represent source license, permitted purposes, territories or environments if needed, effective time, revocation reason, and replacement source as structured data.
- Include a small deterministic compatibility matrix.
- Avoid presenting legal conclusions; surface rules and evidence supplied by the user or fixture.
- Version all rights changes.

### Impact discovery

- Traverse downstream lineage from the revoked source.
- Fetch entity type, schema, platform, ownership, tags, domains, glossary terms, and ML lineage.
- Classify descendants as dataset, transformation, feature, training run/snapshot, model, deployment, vector index, API, cache, or export.
- Flag incomplete lineage and require manual review rather than claiming completeness.

### Containment planning

- Assign one or more typed actions:
  - freeze serving;
  - quarantine;
  - purge;
  - rebuild from an approved source;
  - retrain;
  - replace;
  - document no action with evidence;
  - escalate.
- Calculate priority from exposure, reachability, and business criticality.
- Explain each action with lineage paths and policy rules.
- Require approval before enforcement.

### Execution and verification

- Implement real local adapters for:
  - disabling an API or switching it to a blocked response;
  - deleting and rebuilding a vector index;
  - quarantining an export;
  - retraining a toy model from approved data.
- Verify behavior through endpoint probes, content/source manifests, file checks, and model training manifests.
- Record failures and residual exposure.
- Write supported governance/status metadata and evidence links back to DataHub.

## Suggested architecture

```text
Governance UI
  -> revocation API and job controller
      -> DataHub lineage/context adapter
      -> structured rights registry
      -> deterministic policy evaluator
      -> impact graph and action planner
      -> approval gate
      -> containment adapter registry
      -> verification engine
      -> evidence ledger + DataHub writeback
```

Suggested stack:

- Python 3.12, FastAPI, Pydantic, NetworkX, pytest.
- React, TypeScript, Vite, graph visualization.
- SQLite for rights events, jobs, approvals, and evidence.
- DuckDB, a lightweight vector store or deterministic local index, scikit-learn, and a local API fixture.
- Docker Compose for DataHub and the application.
- Optional LLM for explanations and remediation prose, never for final rights enforcement.

## Core data contracts

### Rights event

- event ID and effective time
- source DataHub URN
- prior and new structured rights
- reason and evidence reference
- replacement source, if any
- requester and approver

### Impact decision

- descendant URN and lineage paths
- artifact classification
- exposure and criticality
- applicable deterministic rules
- required actions
- confidence and missing evidence

### Containment evidence

- action attempted
- adapter receipt
- verification method and result
- remaining exposure
- timestamp and actor
- DataHub writeback receipt

## Safety and governance

- Dry-run by default.
- Explicit approval before disabling or deleting.
- Real execution only against targets marked demo/sandbox.
- Fail closed when lineage or rights data is insufficient.
- Maintain a separation between policy facts, agent suggestions, and approved actions.
- Provide rollback or replacement paths where practical.
- Do not market the application as providing legal advice or universal license interpretation.

## Must-have scope

- One end-to-end data-to-model and data-to-RAG demo graph.
- Structured rights event.
- Real DataHub traversal and context retrieval.
- Deterministic action policy.
- Impact visualization with lineage-path evidence.
- Approval workflow.
- Three real local containment actions.
- Verification of stopped serving or removed material.
- DataHub writeback and downloadable report.

## Stretch scope

- SPDX-compatible license identifiers.
- Multiple input licenses and compatibility joins.
- Pull request generation for replacement datasets.
- Slack or ticket integration.
- Signed evidence bundles.
- A proposed DataHub structured-property template or Skill.

## Out of scope for the MVP

- Legal interpretation of arbitrary contracts.
- Guaranteed identification of untracked offline copies.
- Cryptographic proof of model unlearning.
- Production enforcement in third-party SaaS platforms.

## Acceptance criteria

- [ ] The demo graph contains datasets, ML artifacts, an index/service, an export, and an unaffected branch.
- [ ] A rights change discovers all expected in-scope descendants.
- [ ] Every action cites a lineage path and deterministic rule.
- [ ] Incomplete lineage creates an escalation, not a false all-clear.
- [ ] Approval is enforced.
- [ ] Local API, index, export, or model artifacts genuinely change.
- [ ] Verification detects a deliberately failed containment action.
- [ ] A supported DataHub writeback is visible.
- [ ] The final report clearly states contained and residual exposure.
- [ ] Tests cover policy, traversal, approvals, idempotency, adapters, and verification.

## Competitive positioning

Provenance, model supply-chain security, and license scanning already exist. The defensible claim is:

> License Circuit Breaker is the execution and verification layer that propagates a rights revocation through the DataHub graph.

Do not claim to invent license detection or data provenance. Show the operational gap after detection and close it visibly.
