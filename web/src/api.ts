/**
 * The API client.
 *
 * Failures stay visible. The only automatic retry is one bounded retry for a
 * guarded public mutation after the server's explicit Retry-After interval.
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
  readonly retryAfterSeconds: number | null

  constructor(
    status: number,
    body: unknown,
    message: string,
    retryAfterSeconds: number | null = null,
  ) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.body = body
    this.retryAfterSeconds = retryAfterSeconds
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
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers ?? {}),
    },
  })

  let body: unknown = null
  const responseText = await response.text()
  if (responseText) {
    try {
      body = JSON.parse(responseText)
    } catch {
      body = responseText
    }
  }

  if (!response.ok) {
    const retryAfter = Number.parseInt(response.headers.get('Retry-After') ?? '', 10)
    throw new ApiError(
      response.status,
      body,
      `${init?.method ?? 'GET'} ${path} failed with ${response.status}`,
      Number.isFinite(retryAfter) ? retryAfter : null,
    )
  }
  return body as T
}

type DemoOperation = 'approve' | 'execute' | 'writeback' | 'reset'

async function confirmation(operation: DemoOperation): Promise<string> {
  const result = await request<{ confirmation: string }>('/api/demo/confirmation', {
    method: 'POST',
    body: JSON.stringify({ operation }),
  })
  return result.confirmation
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds))
}

async function mutation<T>(
  path: string,
  operation: DemoOperation,
  guarded: boolean,
  init: RequestInit,
): Promise<T> {
  if (!guarded) return request<T>(path, init)

  for (let attempt = 0; attempt < 2; attempt += 1) {
    const token = await confirmation(operation)
    try {
      return await request<T>(path, {
        ...init,
        headers: {
          ...(init.headers ?? {}),
          'X-Demo-Confirmation': token,
        },
      })
    } catch (error) {
      if (!(error instanceof ApiError) || error.status !== 429 || attempt > 0) throw error
      await delay((error.retryAfterSeconds ?? 1) * 1000)
    }
  }
  throw new Error('public demo mutation retry exhausted')
}

export const api = {
  readiness: () =>
    // Readiness answers 503 when degraded, and that body is exactly what the
    // console needs to render. Fetching it as a normal error would throw away
    // the check list, so it is read directly.
    fetch('/api/readiness')
      .then(async (response) => (await response.json()) as Readiness)
      .catch(() => null),

  health: () => request<{ status: string; uptime_seconds: number }>('/api/health'),
  rightsEvent: () => request<RightsEventEnvelope>('/api/rights-event'),
  graph: () => request<ImpactGraph>('/api/graph'),
  plan: () => request<Plan>('/api/plan'),
  approvals: () => request<ApprovalState>('/api/approvals'),

  approve: (approver: string, note: string, decision = 'approved', guarded = false) =>
    mutation<{ approval: Approval }>('/api/approvals', 'approve', guarded, {
      method: 'POST',
      body: JSON.stringify({ approver, note, decision }),
    }),

  execute: (options: { run_id?: string } = {}, guarded = false) =>
    mutation<{ execution: Execution; approval_id: string }>(
      '/api/execute',
      'execute',
      guarded,
      {
        method: 'POST',
        body: JSON.stringify(options),
      },
    ),

  runs: () => request<{ runs: { run_id: string; status: string }[] }>('/api/runs'),
  verify: () => request<Verification>('/api/verify'),
  writeback: (guarded = false) =>
    mutation<WritebackResult>('/api/writeback', 'writeback', guarded, { method: 'POST' }),
  evidence: () => request<Evidence>('/api/evidence'),
  estate: () => request<EstateStatus>('/api/estate'),

  predict: (text: string) =>
    request<{
      sentiment: string
      confidence: number
      model_version: string
      training_sources: string[]
    }>('/api/demo/predict', {
      method: 'POST',
      body: JSON.stringify({ text }),
    }),

  search: (query: string) =>
    request<{ hits: SearchHit[]; count: number }>(
      `/api/demo/search?q=${encodeURIComponent(query)}`,
    ),

  exportFile: () =>
    request<{ rows: number; header: string; preview: string[] }>('/api/demo/export'),

  reset: (clearGovernance: boolean, guarded = false) =>
    mutation<{ summary: string; governance_cleared: boolean; approval_invalidated: boolean }>(
      '/api/demo/reset',
      'reset',
      guarded,
      {
        method: 'POST',
        body: JSON.stringify({ clear_governance: clearGovernance }),
      },
    ),
}

/** The readable middle of a tuple URN. */
export function shortUrn(urn: string): string {
  const parts = urn.split(',')
  return parts.length >= 3 ? (parts[parts.length - 2] ?? urn) : urn
}
