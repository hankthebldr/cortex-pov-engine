/**
 * launchWarnings.js — what SimCore said when it accepted a launch with a
 * reservation, kept long enough to reach the run it belongs to.
 *
 * Pure plus a small session store. `POST /api/run` answers 200 with
 * `warnings[]` for an offline target, an agent that registered no OS, or a step
 * running its platform variant. Every launch surface navigates to the run on
 * success, so a notice on the launch form alone is on screen for an instant.
 * The run view therefore reads them too: from the transcript when SimCore wrote
 * the marker there, and from here when it did not.
 *
 * The store is per browser session (sessionStorage, with an in-memory copy for
 * when storage is blocked). It is a convenience, not a record: another
 * browser, or this one after the tab closes, sees only what Run.output holds.
 */

const KEY = 'cortexsim.launchWarnings.v1'
const KEEP = 50
// `TARGET_AGENT_OFFLINE: …`, `!! TARGET_AGENT_OS_UNKNOWN: …`, `!! PLATFORM VARIANT: …`
const CODED = /^(?:!!\s*)?([A-Z][A-Z0-9_]*(?: [A-Z][A-Z0-9_]*)*):\s*(.*)$/s

let memory = null

/** One warning line → `{code, text}`; `code` is null when the line names none. */
export function parseWarning(raw) {
  const line = typeof raw === 'string' ? raw.trim() : JSON.stringify(raw)
  const m = CODED.exec(line)
  return m ? { code: m[1], text: m[2] } : { code: null, text: line.replace(/^!!\s*/, '') }
}

/**
 * The `warnings` of a launch response as `[{code, text}]`.
 * An unrecognised shape is shown as text, never read as "no warnings".
 */
export function normalizeLaunchWarnings(response) {
  const raw = response && typeof response === 'object' ? response.warnings : undefined
  if (raw === undefined || raw === null) return []
  const list = Array.isArray(raw) ? raw : [raw]
  return list.filter((w) => w !== null && w !== undefined && w !== '').map(parseWarning)
}

function load() {
  if (memory) return memory
  memory = {}
  try {
    const raw = window.sessionStorage.getItem(KEY)
    const v = raw ? JSON.parse(raw) : null
    if (v && typeof v === 'object' && !Array.isArray(v)) memory = v
  } catch { /* storage blocked or corrupt — the in-memory copy still serves this page */ }
  return memory
}

function save() {
  try { window.sessionStorage.setItem(KEY, JSON.stringify(memory)) } catch { /* storage blocked */ }
}

/** Remember what the launch response said about `runId`. No warnings stores nothing. */
export function rememberLaunchWarnings(runId, warnings) {
  if (!runId || !Array.isArray(warnings) || warnings.length === 0) return
  const store = load()
  store[String(runId)] = warnings
  const ids = Object.keys(store)
  for (const id of ids.slice(0, Math.max(0, ids.length - KEEP))) delete store[id]
  save()
}

/** @returns {Array<{code: string|null, text: string}>} */
export function launchWarningsFor(runId) {
  if (!runId) return []
  const v = load()[String(runId)]
  return Array.isArray(v) ? v : []
}

/** Test seam: drop everything remembered. */
export function forgetLaunchWarnings() {
  memory = {}
  try { window.sessionStorage.removeItem(KEY) } catch { /* storage blocked */ }
}
