/**
 * checks.js — read-only check reports turned into Configure results and
 * Configuration rows.
 *
 * The tenant and firewall preflights (`POST /api/connectors/{kind}/preflight`)
 * answer with staged reports: `{ overall, stages: [{ id, status, code, detail,
 * remediation }], queries_issued }`. One mapping, so a stage cannot read
 * green in a step and amber in a row.
 *
 * A report nothing answered (`queries_issued` 0 or missing) proves nothing:
 * it reads Can't check whatever its stages say — the rule CheckStep.jsx
 * already applies to the firewall ladder.
 */

const STAGE_STATE = { ok: 'passed', degraded: 'degraded', blocked: 'failed', skipped: 'cant_check' }
const RANK = { failed: 3, degraded: 2, cant_check: 1, passed: 0 }

export function answered(report) {
  return !!report && typeof report.queries_issued === 'number' && report.queries_issued > 0
}

export function stageRow(stage, label = stage.id) {
  const detail = [stage.detail, stage.remediation].filter(Boolean).join(' — ')
  return {
    label: stage.code && stage.status !== 'ok' ? `${label} · ${stage.code}` : label,
    state: STAGE_STATE[stage.status] || 'cant_check',
    detail: detail || undefined,
  }
}

/**
 * A step result from a report, over the stages named in `ids` (all when
 * omitted). `step` is the Setup step a failure should open.
 */
export function reportResult(report, { ids = null, step = null, okDetail = null } = {}) {
  if (!report || report._unavailable) {
    return { state: 'cant_check', detail: 'This POVengine has no read-only check for this — it predates the connector preflight.' }
  }
  const stages = Array.isArray(report.stages) ? report.stages : []
  const picked = ids ? stages.filter((s) => ids.includes(s.id)) : stages
  const rows = picked.map((s) => stageRow(s))
  if (!answered(report)) {
    return {
      state: 'cant_check',
      detail: 'Nothing answered this check (queries issued: 0), so it proves nothing about the connection.',
      rows,
    }
  }
  if (!picked.length) return { state: 'cant_check', detail: 'The check did not report this stage.', rows }
  const state = rows.reduce((acc, r) => (RANK[r.state] > RANK[acc] ? r.state : acc), 'passed')
  const bad = picked.find((s) => s.status === 'blocked') || picked.find((s) => s.status === 'degraded')
  return {
    state,
    issue: bad ? (bad.code || bad.id) : undefined,
    detail: state === 'passed' ? (okDetail || 'Every stage reported ok.') : (bad?.detail || undefined),
    rows,
    step,
  }
}

/** A Configuration row state from the stages named in `ids`, or Can't check. */
export function configState(report, ids) {
  if (!answered(report)) return { state: 'cant_check', detail: 'Run Test to read it' }
  const picked = (report.stages || []).filter((s) => ids.includes(s.id))
  if (!picked.length || picked.every((s) => s.status === 'skipped')) {
    return { state: 'cant_check', detail: 'The last check did not reach this stage' }
  }
  const blocked = picked.find((s) => s.status === 'blocked')
  if (blocked) return { state: 'missing', detail: blocked.detail || blocked.code }
  const degraded = picked.find((s) => s.status === 'degraded')
  if (degraded) return { state: 'changed', detail: degraded.detail || degraded.code }
  return { state: 'ok', detail: picked.map((s) => s.detail).filter(Boolean).join(' · ') || undefined }
}

/** The page-header Test result for a whole report. */
export function headerResult(report, { step = null } = {}) {
  const r = reportResult(report, { step })
  return { state: r.state, issue: r.issue || (r.state === 'cant_check' ? "Can't check" : undefined), detail: r.detail, step }
}
