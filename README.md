# License Circuit Breaker

## Submission title

**License Circuit Breaker: Executable Data-Rights Revocation with DataHub**

## Tagline

Revoke one upstream data right and contain every affected downstream artifact.

## One-sentence pitch

License Circuit Breaker uses DataHub lineage to find every dataset, feature, model, RAG index, endpoint, and export affected by a revoked data license, executes a governed containment plan, verifies removal or replacement, and records the evidence.

## Basic idea

Catalogs and governance tools can display provenance or warn that a license is incompatible. The operational gap begins after the warning: teams must still find and disable every downstream use before the organization violates a contract or license.

License Circuit Breaker turns a rights change into action. An agent reads the DataHub graph, classifies affected descendants by usage, proposes the correct response for each one, obtains approval, invokes safe execution adapters, verifies that prohibited artifacts are no longer serving, and writes an auditable revocation record back to DataHub.

## Why it can win

- **Meaningful DataHub usage:** End-to-end lineage is essential to discover hidden downstream exposure.
- **Action rather than reporting:** The project contains, replaces, rebuilds, and verifies—not merely flags.
- **Production ML relevance:** Training datasets, features, models, deployments, and RAG stores are first-class descendants.
- **Strong visual story:** One upstream license switch causes an explainable cascade of governed actions.
- **Timely real-world problem:** Data and model supply chains frequently lose downstream license obligations.

## Primary user

AI governance teams, data platform engineers, ML platform teams, privacy and compliance engineers, and legal operations.

## Challenge category

Primary: **Production ML Agents**  
Secondary: **Open / Wildcard**

## The memorable demo moment

A provider revokes the right to use a review dataset. The agent traces it into a fine-tuned model, vector index, API, and export; freezes unsafe serving; swaps in an approved replacement where possible; verifies containment; and publishes the evidence in DataHub.

## Name rationale

“License Circuit Breaker” immediately communicates an enforcing safety mechanism. “DataHub-powered” appears in the subtitle and copy so the project remains clearly independent.

## Workspace map

- [Project brief](./PROJECT_BRIEF.md)
- [Build plan](./BUILD_PLAN.md)
- [Demo and submission](./DEMO_AND_SUBMISSION.md)
- [Hackathon rules](./HACKATHON_RULES.md)
- [AI builder instructions](./AGENTS.md)

## First command for the builder

Read `AGENTS.md`, `HACKATHON_RULES.md`, and `PROJECT_BRIEF.md` completely before choosing the implementation stack or writing code.
