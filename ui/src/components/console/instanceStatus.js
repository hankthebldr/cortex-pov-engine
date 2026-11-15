/**
 * instanceStatus.js — what the header says about THIS POVengine (design v26 §4.5).
 *
 * Pure. One status (a StatusIcon key), the instance's uptime, and up to four
 * live KPIs — each built only from something SimCore actually reported:
 *
 *   status   GET /api/health, through the one health model: unanswered is
 *            Down, any degraded component is Degraded, not probed yet is Unknown.
 *   uptime   `uptime_seconds` from /api/health — SimCore's own clock. An older
 *            build does not send it, and then no uptime is shown at all.
 *   KPIs     running · agents online · runs · issues, from the run and agent
 *            lists the console already polls and the health model's degraded
 *            set. A list that has not arrived, or failed, contributes no KPI:
 *            "0 agents" from a fetch that failed would be a measurement nobody
 *            took. While SimCore is not answering there are no KPIs at all —
 *            the lists still in memory are no longer live.
 *
 * The header is not about a POV: it describes the running instance.
 */
import { agentIsOnline } from './setupProgress.js'
import { HS } from './readiness/healthModel.js'

/** "3d 4h", "4h 12m", "12m", "45s". Null when SimCore did not say. */
export function formatUptime(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return null
  const s = Math.floor(seconds)
  const d = Math.floor(s / 86400)
  const h = Math.floor((s % 86400) / 3600)
  const m = Math.floor((s % 3600) / 60)
  if (d > 0) return `${d}d ${h}h`
  if (h > 0) return `${h}h ${m}m`
  if (m > 0) return `${m}m`
  return `${s}s`
}

function statusOf(model) {
  if (!model) return 'unknown'
  if (!model.reachable) return 'down'
  if (model.overall === HS.OK) return 'up'
  if (model.overall === HS.DEGRADED || model.overall === HS.ERROR) return 'degraded'
  return 'unknown'
}

/**
 * @param {Object} p
 * @param {Object|null} p.healthModel   buildHealthModel() output, or null before the first probe
 * @param {Array}  p.runs                the polled run list
 * @param {Array}  p.agents              the polled agent list
 * @param {Object} [p.loading]           { runs, agents } — true until the first answer
 * @param {Object} [p.errors]            { agents } — a failed agent fetch
 * @returns {{ status: string, uptime: string|null, kpis: Array<{key,value,label,tone}> }}
 */
export function instanceStatus({ healthModel = null, runs = [], agents = [], loading = {}, errors = {} } = {}) {
  const status = statusOf(healthModel)
  const live = !!healthModel && healthModel.reachable
  const uptime = live ? formatUptime(healthModel.uptimeSeconds) : null

  const kpis = []
  if (live) {
    if (!loading.runs && Array.isArray(runs)) {
      const running = runs.filter((r) => r && String(r.status || '').toLowerCase() === 'running').length
      kpis.push({ key: 'running', value: String(running), label: 'running', tone: running ? 'pos' : null })
    }
    if (!loading.agents && !errors.agents && Array.isArray(agents)) {
      const online = agents.filter(agentIsOnline).length
      kpis.push(agents.length
        ? { key: 'agents', value: `${online}/${agents.length}`, label: 'agents online', tone: null }
        : { key: 'agents', value: '0', label: 'agents', tone: null })
    }
    if (!loading.runs && Array.isArray(runs)) {
      kpis.push({ key: 'runs', value: String(runs.length), label: runs.length === 1 ? 'run' : 'runs', tone: null })
    }
    const issues = Array.isArray(healthModel.degraded) ? healthModel.degraded.length : 0
    kpis.push({ key: 'issues', value: String(issues), label: issues === 1 ? 'issue' : 'issues', tone: issues ? 'warn' : null })
  }

  return { status, uptime, kpis }
}
