/**
 * Shapes returned by the governance API.
 *
 * Hand-written rather than generated: the console reads a deliberately small
 * slice of each response, and a generated client would couple the demo to a
 * build step a judge would have to run before the page loaded.
 */

export type Verdict = 'contained' | 'residual' | 'escalated' | 'not_started'

export interface Check {
  name: string
  passed: boolean
  detail: string
}

export interface Readiness {
  status: 'ready' | 'degraded'
  project_slug: string
  checks: Check[]
  namespace: Record<string, string>
  simulated: boolean
  mutations_enabled: boolean
  mutation_mode: 'trusted' | 'guarded' | 'disabled'
}

export interface RightsEventEnvelope {
  event: {
    event_id: string
    version: number
    effective_at: string
    source_urn: string
    reason: string
    requester: string
    replacement_source_urn: string | null
    prior: { license_id: string; name: string; permitted_purposes: string[]; state: string }
    new: { license_id: string; name: string; permitted_purposes: string[]; state: string }
  }
  content_hash: string
  revokes: boolean
  lost_purposes: string[]
  note: string
}

export interface LineagePath {
  hops: string[]
  complete: boolean
}

export interface Decision {
  actions: string[]
  rule_ids: string[]
  rationale: string
  priority: number
  missing_evidence: string[]
  paths: LineagePath[]
}

export interface GraphNode {
  urn: string
  label: string
  is_source: boolean
  artifact_class: string | null
  purposes: string[]
  exposure: string | null
  criticality: string | null
  tags: string[]
  domain: string | null
  revocation_status: string | null
  decision: Decision | null
}

export interface GraphEdge {
  upstream: string
  downstream: string
  resolved: boolean
}

export interface ImpactGraph {
  source: string
  simulated: boolean
  nodes: GraphNode[]
  edges: GraphEdge[]
}

export interface PlanStep {
  seq: number
  urn: string
  action: string
  artifact_class: string
}

export interface PlanDecision {
  descendant_urn: string
  artifact_class: string
  actions: string[]
  rule_ids: string[]
  rationale: string
  priority: number
  missing_evidence: string[]
  paths: LineagePath[]
}

export interface Plan {
  plan_hash: string
  generated_at: string
  all_clear: boolean
  requires_approval: boolean
  enforcement_scope: Record<string, string[]>
  decisions: PlanDecision[]
  steps: PlanStep[]
  simulated: boolean
}

export interface Approval {
  approval_id: string
  plan_hash: string
  event_id: string
  decision: string
  approver: string
  note: string
  scope: Record<string, string[]>
  decided_at: string
}

export interface ApprovalState {
  plan_hash: string
  current: Approval | null
  history: Approval[]
}

export interface StepOutcome {
  seq: number
  urn: string
  action: string
  artifact_class: string
  status: string
  changed: boolean
  detail: string
  error: string | null
  evidence: Record<string, unknown>
  resumed: boolean
}

export interface ResidualExposure {
  urn: string
  reason: string
  detail: string
  action: string | null
}

export interface Execution {
  run_id: string
  plan_hash: string
  approval_id: string
  started_at: string
  finished_at: string
  fully_executed: boolean
  summary: string
  steps: StepOutcome[]
  residual_exposure: ResidualExposure[]
}

export interface Probe {
  urn: string
  method: string
  passed: boolean
  expected: string
  observed: string
  kind: 'containment' | 'precision'
}

export interface Verification {
  verified_at: string
  contained: boolean
  summary: string
  probes: Probe[]
  residual_exposure: ResidualExposure[]
}

export interface WritebackReceipt {
  urn: string
  status: string
  tag: string
  aspects: string[]
  properties: Record<string, string>
  verified: boolean
  detail: string
  simulated: boolean
}

export interface WritebackResult {
  verdict: Verdict
  simulated: boolean
  evidence_path: string
  verified: number
  attempted: number
  receipts: WritebackReceipt[]
}

export interface Evidence {
  verdict: Verdict
  simulated: boolean
  generated_at: string
  contained: string[]
  residual_exposure: ResidualExposure[]
  disclaimers: { legal: string; scope: string }
  execution: Execution | null
  verification: Verification | null
}

export interface EstateStatus {
  built: boolean
  root: string
  index: { present: boolean; vector_count: number; source_urns: string[]; holds_partner_rows: boolean }
  model: { active_version: string | null; training_sources: string[]; holds_partner_rows: boolean }
  export: { published: boolean; quarantined: boolean }
  serving: Record<string, string>
}

export interface SearchHit {
  review_id: string
  text: string
  score: number
  source_feed: string
}

/** A probe result rendered in the exposure panel. */
export interface ProbeOutcome {
  label: string
  ok: boolean
  refused: boolean
  summary: string
  detail?: string
}
