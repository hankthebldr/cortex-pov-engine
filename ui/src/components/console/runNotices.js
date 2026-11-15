/**
 * runNotices.js — what SimCore said about a launch it accepted, as one short
 * line each, for the run's page. Pure.
 *
 * SimCore writes each of these onto the run as one `!! CODE: …` line before
 * any step runs (orchestrator `_gate_advisory_line`), and returns it on the
 * launch response. The run's page states them once, above the chain, instead
 * of leaving them as raw lines in the log:
 *
 *   TARGET_AGENT_*               the target was offline, or said no OS
 *   TARGET_EGRESS_REQUIRED       a tool the target must already carry (one
 *                                line per tool; stated as one notice)
 *   RUNTIME_DEPENDENCY_GAP       the agent lacks an interpreter a step needs
 *   RUNTIME_INSTALL_NOT_ENABLED  a runtime install was asked for and is off
 *   CLEANUP_NOT_RUN              the beacon will not run the scenario cleanup
 *   EAL_RELAY_UNAVAILABLE        the beacon cannot relay the run's EAL steps
 *
 * One more is the console's own: a run that is live on a beacon without the
 * `live-output` capability shows nothing until each step ends.
 *
 * Each notice is `{ code, line, detail }`: `line` is the few words shown,
 * `detail` is SimCore's sentence, for the tooltip.
 */

const LIFTED = /^(?:!!\s*)?((?:TARGET_AGENT_[A-Z0-9_]+)|TARGET_EGRESS_REQUIRED|RUNTIME_DEPENDENCY_GAP|RUNTIME_INSTALL_NOT_ENABLED|CLEANUP_NOT_RUN|EAL_RELAY_UNAVAILABLE):\s*(.*)$/
const EGRESS = 'TARGET_EGRESS_REQUIRED'

export const LIVE_OUTPUT_CAPABILITY = 'live-output'
export const LIVE_OUTPUT_NOTICE = 'Live output needs the new beacon'

/** `"… did not report python3, jq, and the beacon …"` → `['python3', 'jq']`. */
function missingFrom(text) {
  const m = /did not report (.+?), and the beacon/.exec(text || '')
  return m ? m[1].split(',').map((s) => s.trim()).filter(Boolean) : []
}

/** `"TOOL-NMAP — pull mode does not install …"` → `TOOL-NMAP`. */
function egressTool(text) {
  const m = /^(\S+)\s+[—-]\s/.exec(text || '')
  return m ? m[1] : null
}

function egressLine(tools) {
  if (tools.length === 1) return `Target must already carry ${tools[0]}`
  return tools.length ? `Target must already carry ${tools.length} tools` : 'Target must already carry its tools'
}

function gapLine(missing) {
  if (!missing.length) return 'Agent is missing a runtime'
  const shown = missing.slice(0, 3).join(', ')
  return `Agent is missing ${shown}${missing.length > 3 ? ` +${missing.length - 3}` : ''}`
}

const LINES = {
  TARGET_AGENT_OFFLINE: () => 'Agent was offline at launch',
  TARGET_AGENT_OS_UNKNOWN: () => 'Agent OS unknown, POSIX assumed',
  RUNTIME_DEPENDENCY_GAP: (w) => gapLine(Array.isArray(w.missing) && w.missing.length ? w.missing : missingFrom(w.text)),
  RUNTIME_INSTALL_NOT_ENABLED: () => 'Runtime install is off on this SimCore',
  CLEANUP_NOT_RUN: () => 'This beacon does not run cleanup',
  EAL_RELAY_UNAVAILABLE: () => 'This beacon cannot relay EAL steps',
}

/** `TARGET_AGENT_NOT_READY` → `Target agent not ready`, for a code with no line of its own. */
function plain(code) {
  const words = code.toLowerCase().split('_').join(' ')
  return words.charAt(0).toUpperCase() + words.slice(1)
}

function notice(w) {
  const line = LINES[w.code] ? LINES[w.code](w) : plain(w.code)
  return { code: w.code, line, detail: w.text || line }
}

/** Is this transcript line one the run's page states as a notice? */
export function isNoticeLine(line) {
  return LIFTED.test(String(line || '').trim())
}

/**
 * @param {Object}  args
 * @param {string}  [args.output]  the run's transcript
 * @param {Array}   [args.heard]   what this browser heard on the launch
 *        response (`launchWarningsFor(runId)`): `[{ code, text, … }]`
 * @param {boolean} [args.live]    the run is in flight
 * @param {Object}  [args.agent]   the run's target agent, when this console
 *        can see it (`capabilities`)
 * @returns {Array<{code: string, line: string, detail: string}>}
 */
export function runNotices({ output = '', heard = [], live = false, agent = null } = {}) {
  const out = []
  const seen = new Set()
  // One line per tool the target must carry: said as one notice.
  const egress = { tools: [], texts: [], notice: null }
  const add = (w) => {
    if (!w || !w.code) return
    if (w.code === EGRESS) {
      const tool = egressTool(w.text)
      if (w.text && egress.texts.includes(w.text)) return
      if (tool && !egress.tools.includes(tool)) egress.tools.push(tool)
      if (w.text) egress.texts.push(w.text)
      if (!egress.notice) {
        egress.notice = { code: EGRESS, line: '', detail: '' }
        out.push(egress.notice)
      }
      egress.notice.line = egressLine(egress.tools)
      egress.notice.detail = egress.texts.join('\n') || egress.notice.line
      return
    }
    if (seen.has(w.code)) return
    seen.add(w.code)
    out.push(notice(w))
  }
  if (typeof output === 'string' && output) {
    for (const raw of output.split('\n')) {
      const m = LIFTED.exec(raw.replace(/\r$/, '').trim())
      if (m) add({ code: m[1], text: m[2] })
    }
  }
  for (const w of Array.isArray(heard) ? heard : []) {
    if (w && typeof w.code === 'string' && LIFTED.test(`${w.code}: `)) add(w)
  }
  if (live && agent && Array.isArray(agent.capabilities)
      && !agent.capabilities.includes(LIVE_OUTPUT_CAPABILITY)) {
    out.push({
      code: 'LIVE_OUTPUT_UNAVAILABLE',
      line: LIVE_OUTPUT_NOTICE,
      detail: 'This beacon reports each step when it ends. Update it to see output while a step runs.',
    })
  }
  return out
}
