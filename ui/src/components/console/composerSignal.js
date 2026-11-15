/**
 * composerSignal.js — a step's SIGNAL PATH: the mechanism that produces its
 * evidence. Pure; no React, no fetch.
 *
 * WHY THIS EXISTS
 * The door badge, the Lanes lens and the Runs topology used to place a step by
 * its PLANE (NDR → "Broker VM relay", CDR → "Cloud"). The plane names the
 * product that should observe the signal, not how the step produces it, and on
 * the 2026-11-10 corpus that put 427 of 796 steps behind a door they are not
 * launched through. A path is read from what the step DOES:
 *
 *   COLLECTOR  an EAL collector-class plugin POSTs records to a collector
 *              (a Broker VM applet or an HTTP collector)
 *   WIRE       an EAL wire-class plugin generates traffic a sensor sees
 *   CLOUD      the command calls a cloud or cluster API (aws, gcloud, gsutil,
 *              az, bq, kubectl); the provider's audit log carries the evidence
 *   NONE       the command only echoes, prints, sleeps or comments
 *   ENDPOINT   anything else: the beacon runs it on the host
 *
 * It names the MECHANISM, not the observer: a plain `curl` C2 beacon is
 * ENDPOINT even though a firewall sees it; the plane chip says that.
 *
 * HONESTY. A step whose path cannot be read is `unplaced` with a reason (an EAL
 * run naming no plugin, an unknown plugin, plugins of both classes, a command
 * that is not known). It is drawn in ENDPOINT so it stays visible, and its badge
 * says `EAL ?` / `?`. It is never guessed into another lane.
 *
 * TWO IMPLEMENTATIONS, ONE FIXTURE. `core/engine/signal_path.py` derives the
 * same thing for the loader's S-28 lint and the corpus tripwire; both assert
 * `spec/signal_path_cases.json`, which also carries the plugin classes a
 * Python test pins to the live EAL registry.
 */

/** Path keys, in lane order. */
export const SIGNAL_PATHS = ['ENDPOINT', 'COLLECTOR', 'WIRE', 'CLOUD', 'NONE']

/** EAL plugins that POST records to a collector (`collector_url` in their params). */
export const COLLECTOR_PLUGINS = new Set([
  'ad_windows_emitter', 'azure_audit_emitter', 'cloud_audit_emitter',
  'cloud_storage_compute_emitter', 'duo_auth_emitter', 'email_emitter',
  'globalprotect_emitter', 'idp_signin_emulator', 'k8s_audit_emitter',
  'm365_activity_emitter', 'ngfw_eal_emitter', 'ngfw_threat_emitter',
  'third_party_alert_emitter', 'third_party_firewall_emitter', 'third_party_vpn_emitter',
])

/** EAL plugins that generate live traffic. */
export const WIRE_PLUGINS = new Set([
  'agentic_egress', 'airs_prompt_attack', 'browser_attack_runner', 'bulk_https_exfil',
  'c2_http_beacon', 'dns_tunnel_exfil', 'ftp_egress', 'llm_provider_egress',
  'oauth_grant_emulator', 'smb_rpc_sweep', 'ssh_egress', 'stratum_tcp_connect',
])

/** `collector` | `wire` for a registered plugin, else null. */
export function pluginClass(name) {
  if (COLLECTOR_PLUGINS.has(name)) return 'collector'
  if (WIRE_PLUGINS.has(name)) return 'wire'
  return null
}

const UNPLACED_REASON = {
  'no-step': 'No step to read.',
  'no-command': 'The step\'s command is not known here, and the signal path is read from it.',
  'no-plugin-named': 'The step runs the EAL simulator but names no plugin.',
  'unknown-plugin': 'The step names an EAL plugin the registry does not know',
  'mixed-plugins': 'The step runs EAL plugins of both classes (collector and wire)',
}

// An EAL campaign run, and the `plugin:` lines of the campaign it runs.
const EAL_RUN = /eal_simulator(?:\.cli)?[ \t]+run\b/
const PLUGIN_LINE = /^[ \t]*-?[ \t]*plugin:[ \t]*["']?([A-Za-z0-9_]+)/gm
// A cloud or cluster CLI as a bare word, outside quotes and comments.
const CLOUD_CLI = /(?<![A-Za-z0-9_./-])(aws|gcloud|gsutil|az|bq|kubectl)(?![A-Za-z0-9_.-])/
// A single- or double-quoted string on one line.
const QUOTED = /'[^']*'|"(?:[^"\\]|\\.)*"/g
const SEPARATOR = /&&|\|\||;/
const QUIET = new Set(['echo', 'printf', 'sleep', 'true', ':', 'date'])

const isComment = (line) => line.replace(/^[ \t]+/, '').startsWith('#')

function callsCloud(command) {
  for (const line of command.split('\n')) {
    if (isComment(line)) continue
    if (CLOUD_CLI.test(line.replace(QUOTED, 'Q'))) return true
  }
  return false
}

/**
 * True when every line only echoes, prints, sleeps or is a comment. Anything
 * that could act — a redirect, a pipe, a command substitution (which runs even
 * inside double quotes) — makes it an action. Certain or ENDPOINT, never a
 * guessed NONE.
 */
function doesNothing(command) {
  for (const line of command.split('\n')) {
    if (isComment(line)) continue
    if (line.includes('$(') || line.includes('`')) return false
    const bare = line.replace(QUOTED, 'Q')
    for (const segment of bare.split(SEPARATOR)) {
      const t = segment.trim()
      if (!t || t.startsWith('#')) continue
      if (!QUIET.has(t.split(/\s+/)[0])) return false
      if (/[<>|]/.test(t)) return false
    }
  }
  return true
}

function pluginPath(plugins, runsOn) {
  if (!plugins.length) return { path: 'ENDPOINT', runsOn, plugins, unplaced: 'no-plugin-named' }
  const unknown = plugins.filter((p) => !pluginClass(p))
  if (unknown.length) return { path: 'ENDPOINT', runsOn, plugins, unplaced: 'unknown-plugin', unknown }
  const classes = new Set(plugins.map(pluginClass))
  if (classes.size > 1) return { path: 'ENDPOINT', runsOn, plugins, unplaced: 'mixed-plugins' }
  return { path: classes.has('collector') ? 'COLLECTOR' : 'WIRE', runsOn, plugins, unplaced: null }
}

function withReason(sig) {
  let reason = sig.unplaced ? UNPLACED_REASON[sig.unplaced] : null
  if (sig.unplaced === 'unknown-plugin') reason += `: ${sig.unknown.join(', ')}.`
  if (sig.unplaced === 'mixed-plugins') reason += `: ${sig.plugins.join(', ')}.`
  const { unknown: _u, ...rest } = sig
  return { ...rest, reason }
}

/**
 * The signal path of one step, in the Composer's step model or the corpus /
 * API shape (both carry `channel`, `eal` and `command`).
 *
 * @returns {{path:string, runsOn:'AGT'|'SIM', plugins:string[],
 *            unplaced:string|null, reason:string|null}}
 */
export function signalOf(step) {
  if (!step || typeof step !== 'object') {
    return withReason({ path: 'ENDPOINT', runsOn: 'AGT', plugins: [], unplaced: 'no-step' })
  }
  if (step.channel === 'eal') {
    const p = step.eal && typeof step.eal.plugin === 'string' ? step.eal.plugin.trim() : ''
    return withReason(pluginPath(p ? [p] : [], 'SIM'))
  }
  const command = step.command
  if (typeof command !== 'string') {
    return withReason({ path: 'ENDPOINT', runsOn: 'AGT', plugins: [], unplaced: 'no-command' })
  }
  if (EAL_RUN.test(command)) {
    const names = [...new Set([...command.matchAll(PLUGIN_LINE)].map((m) => m[1]))].sort()
    return withReason(pluginPath(names, 'AGT'))
  }
  let path = 'ENDPOINT'
  if (callsCloud(command)) path = 'CLOUD'
  else if (doesNothing(command)) path = 'NONE'
  return withReason({ path, runsOn: 'AGT', plugins: [], unplaced: null })
}

/**
 * The door badge: where the step runs, then the path when it is not the
 * endpoint. An unplaced step says so (`EAL ?`, `?`) instead of naming a door.
 */
export function signalBadge(sig) {
  const on = sig?.runsOn || 'AGT'
  if (sig?.unplaced === 'no-command' || sig?.unplaced === 'no-step') return `${on} ?`
  if (sig?.unplaced) return `${on} → EAL ?`
  if (sig?.path === 'NONE') return `${on} · no action`
  if (!sig || sig.path === 'ENDPOINT') return on
  return `${on} → ${sig.path}`
}
