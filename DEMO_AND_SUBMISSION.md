# Demo and Submission Guide: License Circuit Breaker

## Devpost short description

License Circuit Breaker is a DataHub-powered enforcement agent for data and AI supply chains. When an upstream usage right is revoked, it traces every affected descendant, applies deterministic policy, executes approved containment or replacement actions, verifies that prohibited artifacts stopped serving, and writes the evidence back to DataHub.

## Three-minute demo target

Aim for **2 minutes 35 seconds to 2 minutes 45 seconds**.

### 0:00–0:18 — Prove exposure

Query the demo API and vector search so judges see content derived from the licensed source being served.

> A partner has revoked our right to use this review feed. Its data has already reached a model, vector index, endpoint, and export.

### 0:18–0:50 — Trigger and trace

Create the structured rights event. Show DataHub traversal and the affected graph, including an unaffected branch.

> DataHub supplies the end-to-end provenance, owners, entity types, and serving context. The agent cites the exact lineage path for every decision.

### 0:50–1:20 — Policy and approval

Show deterministic rule IDs and typed actions: freeze, purge, quarantine, replace, or retrain. Display missing-evidence warnings and approval.

> The language model can explain the plan, but deterministic rules and human approval control enforcement.

### 1:20–2:05 — Execute

Approve. Show API freeze, vector purge/rebuild, export quarantine, and toy-model replacement or retraining.

> These local actions actually execute. Each adapter emits evidence and can be retried safely.

### 2:05–2:30 — Verify

Repeat the endpoint/index probes, show the approved model manifest, residual-exposure result, and DataHub writeback.

> Verification prevents a false all-clear. The final evidence identifies both contained and unresolved exposure.

### 2:30–2:42 — Close

> License Circuit Breaker turns provenance into an executable data-rights control.

## Submission narrative

### Problem

License and provenance tools can identify risk, but organizations still need to manually disable every derivative dataset, model, RAG system, API, and export.

### Solution

License Circuit Breaker propagates a structured rights event through DataHub lineage, produces an approved containment plan, executes local actions, verifies behavior, and records evidence.

### What makes it original

The differentiator is downstream execution and verification, not license detection or a compliance dashboard.

### DataHub usage to state explicitly

- Reads downstream data and ML lineage.
- Uses schemas, entity types, ownership, tags, and governance context to classify impact.
- Uses the eligible DataHub agent integration during planning.
- Writes supported revocation status and evidence references into DataHub.

## Judging evidence map

| Criterion | What judges should see |
|---|---|
| Use of DataHub | Hidden descendants discovered through live lineage plus visible writeback |
| Technical execution | Structured policy, approval, real containment adapters, probes, residual exposure |
| Originality | Executes and verifies revocation instead of only detecting incompatible licenses |
| Real-world usefulness | Concrete vendor-data and AI supply-chain response workflow |
| Submission quality | Clear before/after demo, runnable fixtures, evidence examples |

## Required repository evidence

- `examples/rights-event.json`
- `examples/impact-plan.json`
- `examples/containment-report.md`
- policy decision table and rule IDs
- before/after endpoint or index results
- model/source manifests
- DataHub before/after screenshots
- architecture and limitations

## Claims to avoid

- “Provides legal advice.”
- “Automatically interprets every license or contract.”
- “Finds all copies, including untracked offline files.”
- “Cryptographically proves universal model unlearning.”

Prefer: “Executes and verifies policy actions for descendants represented in the demonstrated DataHub graph.”

## Recording checklist

- [ ] Video is public and under three minutes.
- [ ] Prohibited content is visibly served before the trigger.
- [ ] DataHub-derived impact is legible.
- [ ] Policy evidence and approval are shown.
- [ ] Post-action probes are shown.
- [ ] No private contract text, credentials, or copyrighted music appears.
- [ ] Real and simulated adapters are labeled.
