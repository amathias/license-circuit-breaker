/**
 * The judge console.
 *
 * Seven stages in the order the demo walks them: live exposure, rights event,
 * DataHub impact graph, policy evidence, approval, execution, verification and
 * residual exposure, and DataHub writeback.
 *
 * Two rules shape the UI:
 *
 * The gate is not a button state. Execute stays enabled without an approval so
 * a judge can *watch it refuse* -- the server's 409 and its reason are rendered
 * verbatim. Disabling the button would hide the control being demonstrated.
 *
 * Nothing here decides anything. Every verdict, status, and residual exposure
 * shown is read from the API; the console formats, it does not compute. If the
 * page and the report ever disagreed, the report would be the one to trust, so
 * the page is given nothing to disagree with.
 */

import { useCallback, useEffect, useState } from 'react'
import { ApiError, api, shortUrn } from './api'
import { LineageGraph } from './LineageGraph'
import type {
  ApprovalState,
  EstateStatus,
  Evidence,
  Execution,
  ImpactGraph,
  Plan,
  ProbeOutcome,
  Readiness,
  RightsEventEnvelope,
  Verification,
  WritebackResult,
} from './types'

const PROBE_TEXT = 'the battery lasts all weekend and charges fast'
const PROBE_QUERY = 'battery charge'

type Busy =
  | null
  | 'probe'
  | 'approve'
  | 'execute'
  | 'resume'
  | 'verify'
  | 'writeback'
  | 'reset'

export default function App() {
  const [readiness, setReadiness] = useState<Readiness | null>(null)
  const [rights, setRights] = useState<RightsEventEnvelope | null>(null)
  const [graph, setGraph] = useState<ImpactGraph | null>(null)
  const [plan, setPlan] = useState<Plan | null>(null)
  const [approvals, setApprovals] = useState<ApprovalState | null>(null)
  const [execution, setExecution] = useState<Execution | null>(null)
  const [verification, setVerification] = useState<Verification | null>(null)
  const [writeback, setWriteback] = useState<WritebackResult | null>(null)
  const [evidence, setEvidence] = useState<Evidence | null>(null)
  const [estate, setEstate] = useState<EstateStatus | null>(null)
  const [probes, setProbes] = useState<ProbeOutcome[]>([])

  const [approver, setApprover] = useState('governance@example.com')
  const [note, setNote] = useState('Reviewed the lineage evidence and the cited rules.')
  const [selected, setSelected] = useState<string | null>(null)
  const [busy, setBusy] = useState<Busy>(null)
  const [error, setError] = useState<string | null>(null)
  const [gateRefusal, setGateRefusal] = useState<string | null>(null)

  /** Re-read everything derived from server state. */
  const refresh = useCallback(async () => {
    const [r, ev, g, p, a, es, e] = await Promise.allSettled([
      api.readiness(),
      api.rightsEvent(),
      api.graph(),
      api.plan(),
      api.approvals(),
      api.estate(),
      api.evidence(),
    ])
    if (r.status === 'fulfilled') setReadiness(r.value)
    if (ev.status === 'fulfilled') setRights(ev.value)
    if (g.status === 'fulfilled') setGraph(g.value)
    if (p.status === 'fulfilled') setPlan(p.value)
    if (a.status === 'fulfilled') setApprovals(a.value)
    if (es.status === 'fulfilled') setEstate(es.value)
    if (e.status === 'fulfilled') {
      setEvidence(e.value)
      setExecution(e.value.execution)
      setVerification(e.value.verification)
    }
  }, [])

  const runProbes = useCallback(async () => {
    setBusy('probe')
    const results: ProbeOutcome[] = []

    try {
      const prediction = await api.predict(PROBE_TEXT)
      results.push({
        label: 'Prediction API',
        ok: true,
        refused: false,
        summary: `serving "${prediction.sentiment}" at ${(prediction.confidence * 100).toFixed(0)}% confidence`,
        detail: `model ${prediction.model_version}, trained on ${prediction.training_sources
          .map(shortUrn)
          .join(', ')}`,
      })
    } catch (exc) {
      const refusal = exc instanceof ApiError && exc.status === 451
      results.push({
        label: 'Prediction API',
        ok: false,
        refused: refusal,
        summary: refusal ? 'refused — 451 Unavailable For Legal Reasons' : 'unavailable',
        detail: exc instanceof ApiError ? exc.reason : String(exc),
      })
    }

    try {
      const found = await api.search(PROBE_QUERY)
      const partner = found.hits.filter((h) => h.review_id.startsWith('P-'))
      results.push({
        label: 'Vector search',
        ok: true,
        refused: false,
        summary:
          found.count === 0
            ? 'index returns nothing — purged'
            : `${found.count} result(s), ${partner.length} from the revoked partner feed`,
        detail: found.hits.map((h) => `${h.review_id} · ${h.text}`).join('\n'),
      })
    } catch (exc) {
      const refusal = exc instanceof ApiError && exc.status === 451
      results.push({
        label: 'Vector search',
        ok: false,
        refused: refusal,
        summary: refusal ? 'refused — 451 Unavailable For Legal Reasons' : 'unavailable',
        detail: exc instanceof ApiError ? exc.reason : String(exc),
      })
    }

    try {
      const file = await api.exportFile()
      results.push({
        label: 'CSV export',
        ok: true,
        refused: false,
        summary: `${file.rows} rows readable at the published path`,
        detail: file.preview.slice(0, 2).join('\n'),
      })
    } catch (exc) {
      const refusal = exc instanceof ApiError && exc.status === 451
      results.push({
        label: 'CSV export',
        ok: false,
        refused: refusal,
        summary: refusal ? 'refused — 451 Unavailable For Legal Reasons' : 'unavailable',
        detail: exc instanceof ApiError ? exc.reason : String(exc),
      })
    }

    setProbes(results)
    setBusy(null)
  }, [])

  useEffect(() => {
    void refresh().then(runProbes)
  }, [refresh, runProbes])

  async function guarded(state: Busy, action: () => Promise<void>) {
    setBusy(state)
    setError(null)
    try {
      await action()
    } catch (exc) {
      setError(exc instanceof ApiError ? `${exc.message} — ${exc.reason}` : String(exc))
    } finally {
      setBusy(null)
    }
  }

  const doApprove = (decision: 'approved' | 'rejected') =>
    guarded('approve', async () => {
      setGateRefusal(null)
      await api.approve(approver, note, decision)
      setApprovals(await api.approvals())
    })

  const doExecute = () =>
    guarded('execute', async () => {
      setGateRefusal(null)
      try {
        const result = await api.execute()
        setExecution(result.execution)
      } catch (exc) {
        if (exc instanceof ApiError && exc.status === 409) {
          // The refusal is the feature. Surface it and stop, without treating
          // it as an application error.
          setGateRefusal(exc.reason)
          return
        }
        throw exc
      }
      setVerification(await api.verify())
      await runProbes()
      await refresh()
    })

  const doResume = () =>
    guarded('resume', async () => {
      if (!execution) return
      const result = await api.execute({ run_id: execution.run_id })
      setExecution(result.execution)
      setVerification(await api.verify())
      await runProbes()
      await refresh()
    })

  const doVerify = () =>
    guarded('verify', async () => {
      setVerification(await api.verify())
      await runProbes()
    })

  const doWriteback = () =>
    guarded('writeback', async () => {
      setWriteback(await api.writeback())
      await refresh()
    })

  const doReset = () =>
    guarded('reset', async () => {
      await api.reset(true)
      setExecution(null)
      setVerification(null)
      setWriteback(null)
      setGateRefusal(null)
      await refresh()
      await runProbes()
    })

  const approval = approvals?.current ?? null
  const approved = approval?.decision === 'approved'
  const verdict = evidence?.verdict ?? 'not_started'
  const selectedNode = graph?.nodes.find((n) => n.urn === selected) ?? null
  const mutationsEnabled = readiness?.mutations_enabled ?? false

  return (
    <div className="app">
      <header className="masthead">
        <div>
          <h1>License Circuit Breaker</h1>
          <p className="tagline">
            Executable data-rights revocation with DataHub. One upstream right is revoked;
            every affected descendant is traced, contained under approval, and verified.
          </p>
        </div>
        <div className="status-row">
          {readiness ? (
            <span className={`pill ${readiness.status === 'ready' ? 'ok' : 'warn'}`}>
              {readiness.status === 'ready' ? 'ready' : 'degraded (503)'}
            </span>
          ) : (
            <span className="pill muted">readiness unknown</span>
          )}
          {readiness?.simulated ? <span className="pill warn">DataHub: simulated</span> : null}
          <span className={`pill ${
            verdict === 'contained' ? 'ok'
              : verdict === 'not_started' ? 'muted'
              : verdict === 'escalated' ? 'info'
              : 'danger'
          }`}>
            verdict: {verdict}
          </span>
          <button onClick={doReset} disabled={busy !== null || !mutationsEnabled} className="danger">
            {busy === 'reset' ? <span className="spin" /> : 'Reset demo'}
          </button>
        </div>
      </header>

      <aside className="demo-banner" role="note" aria-label="Public demo notice">
        <strong>PUBLIC DEMO</strong>
        <span>
          This judge console operates a disposable license-containment estate. The workflow is
          restricted to the isolated <code>license.*</code> DataHub allocation and project-owned
          demo artifacts—never production or personal data.
        </span>
        <span className="demo-links">
          <a
            href="https://github.com/amathias/license-circuit-breaker#verification"
            target="_blank"
            rel="noreferrer"
          >
            Source, API &amp; self-hosting
          </a>
        </span>
      </aside>

      {!mutationsEnabled && readiness ? (
        <div className="notice info">
          <strong>Hosted workflow is read-only.</strong> Approval, containment, writeback, and
          reset are disabled on the anonymous public deployment. Use the repository&apos;s
          documented <code>APP_ENV=offline</code> setup to run the complete executable workflow.
        </div>
      ) : null}

      {readiness?.simulated ? (
        <div className="notice warn">
          <strong>Simulated DataHub.</strong> Catalog reads and writeback are running against the
          deterministic in-memory substitute, so nothing here is evidence of a live DataHub
          integration. Every local artifact change below is real and is probed directly.
        </div>
      ) : null}

      {error ? <div className="notice danger">{error}</div> : null}

      {/* --- 1. exposure ------------------------------------------------ */}
      <Stage
        index={1}
        done={probes.some((p) => p.refused)}
        title="Live exposure"
        subtitle="What the local artifacts are serving right now"
      >
        <div className="actions" style={{ marginBottom: 12 }}>
          <button onClick={runProbes} disabled={busy !== null}>
            {busy === 'probe' ? <span className="spin" /> : 'Re-probe every artifact'}
          </button>
          <span className="muted">
            Probes read the artifacts directly, not the receipts.
          </span>
        </div>

        <div className="grid-3">
          {probes.map((probe) => (
            <div
              key={probe.label}
              className={`probe ${probe.refused ? 'refused' : probe.ok ? 'exposed' : ''}`}
            >
              <header>
                <span className="name">{probe.label}</span>
                <span className={`pill ${probe.refused ? 'ok' : probe.ok ? 'warn' : 'danger'}`}>
                  {probe.refused ? 'contained' : probe.ok ? 'exposed' : 'error'}
                </span>
              </header>
              <div className="result">{probe.summary}</div>
              {probe.detail ? (
                <ul className="hits">
                  {probe.detail.split('\n').filter(Boolean).slice(0, 3).map((line, i) => (
                    <li
                      key={i}
                      className={line.startsWith('P-') ? 'partner' : line.startsWith('A-') ? 'approved' : ''}
                    >
                      {line}
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
          ))}
        </div>

        {estate ? (
          <div className="grid-3" style={{ marginTop: 12 }}>
            <Card
              title="Vector index"
              value={`${estate.index.vector_count} documents`}
              note={
                estate.index.holds_partner_rows
                  ? 'holds revoked partner content'
                  : 'no partner content indexed'
              }
              tone={estate.index.holds_partner_rows ? 'warn' : 'ok'}
            />
            <Card
              title="Served model"
              value={estate.model.active_version ?? 'none'}
              note={
                estate.model.holds_partner_rows
                  ? 'trained on revoked partner rows'
                  : 'trained only on approved rows'
              }
              tone={estate.model.holds_partner_rows ? 'warn' : 'ok'}
            />
            <Card
              title="CSV export"
              value={estate.export.published ? 'published' : 'quarantined'}
              note={
                estate.export.published
                  ? 'retrievable at its published path'
                  : 'moved out of reach'
              }
              tone={estate.export.published ? 'warn' : 'ok'}
            />
          </div>
        ) : null}
      </Stage>

      {/* --- 2. rights event -------------------------------------------- */}
      <Stage
        index={2}
        done={!!rights}
        title="Rights event"
        subtitle={rights ? `${rights.event.event_id} · v${rights.event.version}` : ''}
      >
        {rights ? (
          <>
            <dl className="kv">
              <dt>Source</dt>
              <dd className="mono">{shortUrn(rights.event.source_urn)}</dd>
              <dt>Reason</dt>
              <dd>{rights.event.reason}</dd>
              <dt>Purposes before</dt>
              <dd>{rights.event.prior.permitted_purposes.join(', ')}</dd>
              <dt>Purposes after</dt>
              <dd>{rights.event.new.permitted_purposes.join(', ')}</dd>
              <dt>Purposes lost</dt>
              <dd>
                {rights.lost_purposes.map((p) => (
                  <span key={p} className="pill danger" style={{ marginRight: 5 }}>
                    {p}
                  </span>
                ))}
              </dd>
              <dt>Replacement</dt>
              <dd className="mono">
                {rights.event.replacement_source_urn
                  ? shortUrn(rights.event.replacement_source_urn)
                  : 'none'}
              </dd>
              <dt>Recorded by</dt>
              <dd>{rights.event.requester}</dd>
              <dt>Content hash</dt>
              <dd className="mono dim">{rights.content_hash.slice(0, 32)}…</dd>
            </dl>
            <div className="notice info" style={{ marginTop: 12, marginBottom: 0 }}>
              {rights.note}
            </div>
          </>
        ) : (
          <p className="muted">Loading…</p>
        )}
      </Stage>

      {/* --- 3. impact graph -------------------------------------------- */}
      <Stage
        index={3}
        done={!!graph}
        title="DataHub impact"
        subtitle={graph ? `${graph.nodes.length} entities · ${graph.edges.length} lineage edges` : ''}
      >
        {graph ? (
          <>
            <LineageGraph
              nodes={graph.nodes}
              edges={graph.edges}
              source={graph.source}
              selected={selected}
              onSelect={(urn) => setSelected(urn === selected ? null : urn)}
            />
            {selectedNode ? (
              <div className="card" style={{ marginTop: 12 }}>
                <h3>{shortUrn(selectedNode.urn)}</h3>
                <dl className="kv">
                  <dt>Class</dt>
                  <dd>{selectedNode.artifact_class ?? 'unclassified'}</dd>
                  <dt>Declared purposes</dt>
                  <dd>{selectedNode.purposes.join(', ') || '—'}</dd>
                  <dt>Exposure</dt>
                  <dd>
                    {selectedNode.exposure ?? '—'} · {selectedNode.criticality ?? '—'} criticality
                  </dd>
                  <dt>DataHub tags</dt>
                  <dd className="mono dim">{selectedNode.tags.join(', ') || '—'}</dd>
                  {selectedNode.decision ? (
                    <>
                      <dt>Lineage path</dt>
                      <dd className="mono">
                        {selectedNode.decision.paths.length
                          ? selectedNode.decision.paths
                              .map((p) => p.hops.map(shortUrn).join(' → ') + (p.complete ? '' : '  [incomplete]'))
                              .join(' | ')
                          : 'none could be cited'}
                      </dd>
                      <dt>Rule</dt>
                      <dd>
                        {selectedNode.decision.rule_ids.join(', ')} — {selectedNode.decision.rationale}
                      </dd>
                    </>
                  ) : null}
                </dl>
              </div>
            ) : (
              <p className="muted" style={{ marginTop: 10, marginBottom: 0 }}>
                Select a node to see its lineage path, declared purposes, and the rule that
                decided its fate.
              </p>
            )}
          </>
        ) : (
          <p className="muted">Loading…</p>
        )}
      </Stage>

      {/* --- 4. policy -------------------------------------------------- */}
      <Stage
        index={4}
        done={!!plan}
        title="Deterministic policy"
        subtitle={plan ? `plan ${plan.plan_hash.slice(0, 12)}…` : ''}
      >
        {plan ? (
          <>
            <div className="notice info">
              Every verdict below comes from the rule table, not from a language model. The same
              graph and the same event always produce the same plan hash — which is what an
              approval binds to.
            </div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Priority</th>
                    <th>Artifact</th>
                    <th>Class</th>
                    <th>Actions</th>
                    <th>Rule</th>
                    <th>Evidence gaps</th>
                  </tr>
                </thead>
                <tbody>
                  {plan.decisions.map((d) => {
                    const escalated = d.actions.includes('escalate')
                    const clear = d.actions.includes('no_action')
                    return (
                      <tr
                        key={d.descendant_urn}
                        className={escalated ? 'escalated' : clear ? 'clear' : 'affected'}
                      >
                        <td>{d.priority}</td>
                        <td className="mono">{shortUrn(d.descendant_urn)}</td>
                        <td>{d.artifact_class}</td>
                        <td>
                          {d.actions.map((a) => (
                            <span
                              key={a}
                              className={`pill ${escalated ? 'info' : clear ? 'ok' : 'danger'}`}
                              style={{ marginRight: 4 }}
                            >
                              {a}
                            </span>
                          ))}
                        </td>
                        <td className="mono">{d.rule_ids.join(', ')}</td>
                        <td className="muted">{d.missing_evidence.join('; ') || '—'}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </>
        ) : (
          <p className="muted">Loading…</p>
        )}
      </Stage>

      {/* --- 5. approval ------------------------------------------------ */}
      <Stage
        index={5}
        done={approved}
        title="Approval"
        subtitle={approval ? `${approval.decision} by ${approval.approver}` : 'not yet reviewed'}
      >
        {approval ? (
          <div className={`notice ${approved ? 'info' : 'danger'}`}>
            <strong>{approved ? 'Approved' : 'Rejected'}</strong> by {approval.approver} ·{' '}
            <span className="mono">{approval.approval_id}</span>
            <br />
            Bound to plan <span className="mono">{approval.plan_hash.slice(0, 24)}…</span>
            {approval.note ? (
              <>
                <br />
                {approval.note}
              </>
            ) : null}
          </div>
        ) : (
          <div className="notice warn">
            No decision has been recorded. Enforcement will refuse until one is.
          </div>
        )}

        <div className="grid-2">
          <div className="field">
            <label htmlFor="approver">Approver</label>
            <input
              id="approver"
              type="text"
              value={approver}
              onChange={(e) => setApprover(e.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="note">Note</label>
            <input id="note" type="text" value={note} onChange={(e) => setNote(e.target.value)} />
          </div>
        </div>

        <div className="actions" style={{ marginTop: 12 }}>
          <button
            className="primary"
            onClick={() => doApprove('approved')}
            disabled={busy !== null || !approver.trim() || !mutationsEnabled}
          >
            {busy === 'approve' ? <span className="spin" /> : 'Approve this exact plan'}
          </button>
          <button
            onClick={() => doApprove('rejected')}
            disabled={busy !== null || !approver.trim() || !mutationsEnabled}
          >
            Reject
          </button>
        </div>

        {approvals && approvals.history.length > 1 ? (
          <p className="muted" style={{ marginTop: 10, marginBottom: 0 }}>
            {approvals.history.length} decisions recorded. Approvals are append-only; a change of
            mind is a new decision, never an edit.
          </p>
        ) : null}
      </Stage>

      {/* --- 6. execution ----------------------------------------------- */}
      <Stage
        index={6}
        done={!!execution}
        title="Containment"
        subtitle={execution ? execution.summary : 'nothing has run'}
      >
        {gateRefusal ? (
          <div className="notice danger">
            <strong>Refused by the approval gate.</strong> {gateRefusal}
          </div>
        ) : null}

        <div className="actions" style={{ marginBottom: 12 }}>
          {/* Deliberately enabled without an approval, so the refusal can be
              demonstrated rather than merely described. */}
          <button
            className="primary"
            onClick={() => doExecute()}
            disabled={busy !== null || !mutationsEnabled}
          >
            {busy === 'execute' ? <span className="spin" /> : 'Execute containment'}
          </button>
          {execution && !execution.fully_executed ? (
            <button onClick={doResume} disabled={busy !== null || !mutationsEnabled}>
              {busy === 'resume' ? <span className="spin" /> : `Resume ${execution.run_id}`}
            </button>
          ) : null}
        </div>

        {execution ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>#</th>
                  <th>Artifact</th>
                  <th>Action</th>
                  <th>Status</th>
                  <th>Changed</th>
                  <th>Detail</th>
                </tr>
              </thead>
              <tbody>
                {execution.steps.map((step) => (
                  <tr
                    key={step.seq}
                    className={step.status === 'completed' ? 'clear' : 'failed'}
                  >
                    <td>{step.seq}</td>
                    <td className="mono">{shortUrn(step.urn)}</td>
                    <td>{step.action}</td>
                    <td>
                      <span className={`pill ${step.status === 'completed' ? 'ok' : 'danger'}`}>
                        {step.status}
                      </span>
                    </td>
                    <td className="muted">{step.changed ? 'yes' : 'no change'}</td>
                    <td className="muted">{step.error ?? step.detail}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="muted">
            No containment has run. Approve the plan above, or press Execute to watch the gate
            refuse.
          </p>
        )}
      </Stage>

      {/* --- 7. verification -------------------------------------------- */}
      <Stage
        index={7}
        done={verification?.contained === true}
        title="Verification and residual exposure"
        subtitle={verification ? verification.summary : 'not verified'}
      >
        <div className="actions" style={{ marginBottom: 12 }}>
          <button onClick={doVerify} disabled={busy !== null}>
            {busy === 'verify' ? <span className="spin" /> : 'Re-run every probe'}
          </button>
          <span className="muted">
            Probes read the artifacts, never the receipts — a skipped action cannot pass.
          </span>
        </div>

        {verification ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Artifact</th>
                  <th>Probe</th>
                  <th>Checks</th>
                  <th>Result</th>
                  <th>Observed</th>
                </tr>
              </thead>
              <tbody>
                {verification.probes.map((probe) => (
                  <tr key={`${probe.urn}-${probe.method}`} className={probe.passed ? 'clear' : 'failed'}>
                    <td className="mono">{shortUrn(probe.urn)}</td>
                    <td className="muted">{probe.method}</td>
                    <td className="muted">{probe.kind}</td>
                    <td>
                      <span className={`pill ${probe.passed ? 'ok' : 'danger'}`}>
                        {probe.passed ? 'pass' : 'fail'}
                      </span>
                    </td>
                    <td className="muted">{probe.observed}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="muted">Run containment first.</p>
        )}

        {evidence && evidence.residual_exposure.length > 0 ? (
          <>
            <h3 style={{ marginTop: 16, marginBottom: 8, fontSize: 13 }}>Residual exposure</h3>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Artifact</th>
                    <th>Reason</th>
                    <th>Action</th>
                    <th>Detail</th>
                  </tr>
                </thead>
                <tbody>
                  {evidence.residual_exposure.map((entry, i) => (
                    <tr key={i} className="escalated">
                      <td className="mono">{shortUrn(entry.urn)}</td>
                      <td>
                        <span className="pill warn">{entry.reason}</span>
                      </td>
                      <td className="muted">{entry.action ?? '—'}</td>
                      <td className="muted">{entry.detail}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="muted" style={{ marginTop: 8, marginBottom: 0 }}>
              Residual exposure is why the verdict is <strong>{verdict}</strong> rather than
              contained. An escalation is never reported as an all-clear.
            </p>
          </>
        ) : evidence && evidence.execution ? (
          <p className="muted" style={{ marginTop: 12, marginBottom: 0 }}>
            No residual exposure. Every probed artifact was confirmed contained.
          </p>
        ) : null}
      </Stage>

      {/* --- 8. writeback ----------------------------------------------- */}
      <Stage
        index={8}
        done={!!writeback}
        title="DataHub writeback"
        subtitle={writeback ? `${writeback.verified}/${writeback.attempted} statuses verified` : ''}
      >
        <div className="actions" style={{ marginBottom: 12 }}>
          <button
            className="primary"
            onClick={doWriteback}
            disabled={busy !== null || !execution || !mutationsEnabled}
          >
            {busy === 'writeback' ? <span className="spin" /> : 'Write the outcome back to DataHub'}
          </button>
          <span className="muted">
            Each artifact receives the status it earned, verified by re-read.
          </span>
        </div>

        {writeback ? (
          <>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Artifact</th>
                    <th>Status</th>
                    <th>Tag</th>
                    <th>Aspects</th>
                    <th>Verified</th>
                  </tr>
                </thead>
                <tbody>
                  {writeback.receipts.map((receipt) => (
                    <tr key={receipt.urn} className={receipt.verified ? 'clear' : 'failed'}>
                      <td className="mono">{shortUrn(receipt.urn)}</td>
                      <td>
                        <span
                          className={`pill ${
                            receipt.status === 'contained'
                              ? 'ok'
                              : receipt.status === 'escalated'
                                ? 'info'
                                : 'danger'
                          }`}
                        >
                          {receipt.status}
                        </span>
                      </td>
                      <td className="mono dim">{receipt.tag}</td>
                      <td className="mono dim">{receipt.aspects.join(', ')}</td>
                      <td>{receipt.verified ? 'yes' : 'NO'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="muted" style={{ marginTop: 9, marginBottom: 0 }}>
              Evidence bundle written to <span className="mono">{writeback.evidence_path}</span>
              {writeback.simulated ? ' — against the in-memory substitute.' : '.'}
            </p>
          </>
        ) : (
          <p className="muted">
            Run containment first. Writing a governance status for a containment that never
            happened would put a claim in the catalog with nothing behind it.
          </p>
        )}
      </Stage>

      <footer className="footer">
        {evidence ? (
          <>
            <span>{evidence.disclaimers.legal}</span>
            <span>{evidence.disclaimers.scope}</span>
          </>
        ) : null}
        <span>
          Namespace: <span className="mono">{readiness?.namespace.urn_prefix ?? 'license.'}</span> ·
          every enforcement target is checked against it and fails closed.
        </span>
      </footer>
    </div>
  )
}

function Stage(props: {
  index: number
  done: boolean
  title: string
  subtitle?: string
  children: React.ReactNode
}) {
  return (
    <section className="stage">
      <div className="stage-head">
        <span className={`stage-index${props.done ? ' done' : ''}`}>
          {props.done ? '✓' : props.index}
        </span>
        <span className="stage-title">{props.title}</span>
        {props.subtitle ? <span className="stage-sub">{props.subtitle}</span> : null}
      </div>
      <div className="stage-body">{props.children}</div>
    </section>
  )
}

function Card(props: { title: string; value: string; note: string; tone: 'ok' | 'warn' }) {
  return (
    <div className="card">
      <h3>{props.title}</h3>
      <div className="value">{props.value}</div>
      <div className={`note ${props.tone === 'warn' ? '' : ''}`}>
        <span className={`pill ${props.tone}`}>{props.note}</span>
      </div>
    </div>
  )
}
