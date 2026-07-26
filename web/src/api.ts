/**
 * The API client.
 *
 * Two things it deliberately does not do: retry, and hide failures. A judge
 * clicking Execute needs to see the refusal that comes back, not a spinner that
 * quietly tries again. `ApiError` carries the status and the parsed body so the
 * console can show the *reason* a gate refused, which is the whole point of
 * having a gate.
 */

import type {
  ApprovalState,
  Approval,
  EstateStatus,
  Evidence,
  Execution,
  ImpactGraph,
  Plan,
  Readiness,
  RightsEventEnvelope,
  SearchHit,
  Verification,
  WritebackResult,
} from './types'

export class ApiError extends Error {
  readonly status: number
  readonly body: unknown

  constructor(status: number, body: unknown, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.body = body
  }

  /** A governed refusal, as opposed to a fault. */
  get isRefusal(): boolean {
    return this.status === 451 || this.status === 409
  }

  /** The human-readable reason, dug out of whichever shape the detail took. */
  get reason(): string {
    const detail = (this.body as { detail?: unknown })?.detail
    if (typeof detail === 'string') return detail
    if (detail && typeof detail === 'object') {
      const record = detail as Record<string, unknown>
      if (typeof record.reason === 'string') return record.reason
      if (typeof record.message === 'string') return record.message
    }
    return this.message
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })

  let body: unknown = null
  const text = await response.text()
  if (text) {
    try {
      body = JSON.parse(text)
    } catch {
      body = text
    }
  }

  if (!response.ok) {
    throw new ApiError(response.status, body, `${init?.method ?? 'GET'} ${path} → ${response.status}`)
  }
  return body as T
}

export const api = {
  readiness: () =>
    // Readiness answers 503 when degraded, and that body is exactly what the
    // console needs to render. Fetching it as a normal error would throw away
    // the check list, so it is read directly.
    fetch('/api/readiness')
      .then(async (r) => (await r.json()) as Readiness)
      .catch(() => null),

  health: () => request<{ status: string; uptime_seconds: number }>('/api/health'),
  rightsEvent: () => request<RightsEventEnvelope>('/api/rights-event'),
  graph: () => request<ImpactGraph>('/api/graph'),
  plan: () => request<Plan>('/api/plan'),
  approvals: () => request<ApprovalState>('/api/approvals'),

  approve: (approver: string, note: string, decision = 'approved') =>
    request<{ approval: Approval }>('/api/approvals', {
      method: 'POST',
      body: JSON.stringify({ approver, note, decision }),
    }),

  execute: (options: { run_id?: string; fail_adapter?: string } = {}) =>
    request<{ execution: Execution; approval_id: string }>('/api/execute', {
      method: 'POST',
      body: JSON.stringify(options),
    }),

  runs: () => request<{ runs: { run_id: string; status: string }[] }>('/api/runs'),
  verify: () => request<Verification>('/api/verify'),
  writeback: () => request<WritebackResult>('/api/writeback', { method: 'POST' }),
  evidence: () => request<Evidence>('/api/evidence'),
  estate: () => request<EstateStatus>('/api/estate'),

  predict: (text: string) =>
    request<{ sentiment: string; confidence: number; model_version: string; training_sources: string[] }>(
      '/api/demo/predict',
      { method: 'POST', body: JSON.stringify({ text }) },
    ),

  search: (q: string) =>
    request<{ hits: SearchHit[]; count: number }>(`/api/demo/search?q=${encodeURIComponent(q)}`),

  exportFile: () => request<{ rows: number; header: string; preview: string[] }>('/api/demo/export'),

  reset: (clearGovernance: boolean) =>
    request<{ summary: string; governance_cleared: boolean }>('/api/demo/reset', {
      method: 'POST',
      body: JSON.stringify({ clear_governance: clearGovernance }),
    }),
}

/** The readable middle of a tuple URN. */
export function shortUrn(urn: string): string {
  const parts = urn.split(',')
  return parts.length >= 3 ? (parts[parts.length - 2] ?? urn) : urn
}
