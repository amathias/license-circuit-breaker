# Builder Instructions: License Circuit Breaker

## Mission

Build a working, judge-ready vertical slice of License Circuit Breaker: a DataHub-powered agent that turns an upstream rights revocation into verified downstream containment.

## Read first

Before modifying code, read these files completely:

1. `HACKATHON_RULES.md`
2. `PROJECT_BRIEF.md`
3. `BUILD_PLAN.md`
4. `DEMO_AND_SUBMISSION.md`

## Non-negotiable product behavior

- Read real lineage, ML entities, ownership, tags, and governance context from open-source DataHub through an eligible integration.
- Demonstrate a real DataHub writeback using a supported API or SDK.
- Trace all in-scope descendants and explain why each is affected.
- Produce a typed action for each descendant: freeze, purge, rebuild, replace, retrain, quarantine, or document no action.
- Require approval before enforcement and preserve an immutable-looking local evidence log.
- Execute real actions on disposable local artifacts; label mock enterprise connectors as simulations.
- Verify that prohibited artifacts are no longer available after execution.
- State clearly that the tool supports compliance operations but is not legal advice.

## Engineering principles

- Use deterministic license-policy rules for the verdict; the LLM may explain or propose but cannot make the final enforcement decision alone.
- Model licenses and rights as structured data, not prose-only prompts.
- Make execution idempotent, resumable, and reversible where possible.
- Deny unsafe actions when lineage is incomplete and surface the missing evidence.
- Keep secrets in environment variables and provide `.env.example`.
- Test lineage traversal, policy evaluation, approval gates, adapter failures, and verification.
- Maintain `docs/DECISIONS.md` as architectural decisions are made.

## GitHub publishing

- Canonical repository: `https://github.com/amathias/license-circuit-breaker`.
- Configured origin: `git@github-datahub-license-circuit-breaker:amathias/license-circuit-breaker.git`.
- While this chat is the project's primary writer, it may commit and intermittently push verified
  milestone changes to `origin/main`.
- Inspect the complete diff, run relevant checks, stage only intended paths, and keep
  `COORDINATOR_HANDOFF.md` current before pushing.
- Never change the remote, force push, delete remote refs, use another project's SSH alias, or add
  secrets, private keys, `.env` files, runtime receipts, or private evidence to Git.
- If `origin` is absent or differs from the exact value above, stop and escalate to the portfolio
  coordinator.

## Definition of done

A reviewer can ingest the demo graph, revoke a source license, inspect the complete impact analysis, approve a containment plan, watch real local descendants be disabled or rebuilt, verify that prohibited serving has stopped, inspect DataHub writeback, and reproduce the demo from the README.

## Submission guardrails

- The repository must be public and contain an Apache 2.0 `LICENSE`.
- The work must be newly built during the submission period.
- Disclose any meaningful pre-existing code or assets.
- Keep the title independent: “License Circuit Breaker,” described as DataHub-powered.
