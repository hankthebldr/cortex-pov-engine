/**
 * deployCommands.js — the ONE place the console spells an install or uninstall
 * command, maps an agent's OS to an installer target, reads which prebuilt
 * beacons this SimCore ships, and copies text to the clipboard.
 *
 * Pure (no React). Each of these used to be re-derived per surface, and each
 * copy drifted: the Windows one-liner was spelled in TargetsView and again in
 * GetStartedView, both without the TLS 1.2 opt-in Windows PowerShell 5.1 needs;
 * installerOS used `/win/i`, which reads `darwin` as Windows; and Copy buttons
 * announced success before the clipboard had answered.
 *
 * What this module can NOT prove: that any of these commands work on a real
 * Windows host. No Windows host has executed the beacon or its installer. The
 * strings here are pinned by vitest in jsdom, nothing more.
 */
import { parseServerTime } from '../../api/time.js'

// ── Installer target ─────────────────────────────────────────────────────────

/**
 * Normalise an agent-reported OS to an installer target. Exact matches only —
 * a substring test is how `darwin` became Windows.
 * @param {string|null|undefined} os
 * @returns {'windows'|'darwin'|'linux'}
 */
export function installerOS(os) {
  const v = String(os ?? '').trim().toLowerCase()
  if (v === 'windows' || v.startsWith('win32')) return 'windows'
  if (v === 'darwin' || v === 'macos') return 'darwin'
  return 'linux'
}

// ── Agent id ─────────────────────────────────────────────────────────────────

/**
 * SimCore's rule for an id it will bake into an installer: `_AGENT_ID_RE` in
 * core/api/agents.py. A COPY — the server is the authority; this exists so the
 * console does not hand a DC a command that is certain to answer
 * 400 BAD_AGENT_ID, which `curl -f` reduces to "error: 400" on the target.
 */
const AGENT_ID_RULE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/

/**
 * Why SimCore would refuse `id` as an installer agent id, or null when it
 * would accept it.
 * @returns {string|null}
 */
export function agentIdProblem(id) {
  const v = String(id ?? '')
  if (AGENT_ID_RULE.test(v)) return null
  if (v === '') return 'the id is empty'
  if (v.length > 64) return `it is ${v.length} characters; the limit is 64`
  if (!/^[A-Za-z0-9]/.test(v)) return 'it must start with a letter or digit'
  const bad = Array.from(new Set(v.replace(/[A-Za-z0-9._-]/g, '')))
  const named = bad.map((c) => (c === ' ' ? 'a space' : `"${c}"`)).join(', ')
  return `it contains ${named}; only letters, digits, ".", "_" and "-" are allowed`
}

// ── Pull one-liners ──────────────────────────────────────────────────────────

/**
 * Windows PowerShell 5.1 on a .NET that predates TLS 1.2-by-default refuses an
 * https SimCore before `iwr` sends a byte. `-bor 3072` ADDS Tls12 to whatever
 * the session already allows; assigning Tls12 alone would drop the rest.
 */
export const TLS12_PREFIX =
  '[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072; '

/**
 * The token rides in an env var, not the URL. That keeps it out of the URL and
 * so out of proxy and web-server access logs. It does NOT keep it out of shell
 * history: the pasted line carries the token and an interactive shell records
 * the line (bash unless HISTCONTROL=ignorespace and a leading space; PowerShell
 * through PSReadLine). The console used to say it stayed out of history.
 */
export const TOKEN_HISTORY_NOTE =
  'The token is kept out of the URL, so proxy and web-server logs never see it. It is on the command line, so '
  + 'the host\'s shell history will hold it: mint a single-use, short-lived token (1 use and 1 hour are the '
  + 'defaults here), and clear the history entry on a shared jumpbox.'

export function windowsPullOneLiner({ url, token = null }) {
  return token
    ? `${TLS12_PREFIX}$env:CORTEXSIM_TOKEN='${token}'; iwr -useb "${url}" | iex`
    : `${TLS12_PREFIX}iwr -useb "${url}" | iex`
}

export function posixPullOneLiner({ url, token = null }) {
  return token
    ? `curl -fsSL "${url}" | CORTEXSIM_TOKEN='${token}' bash`
    : `curl -fsSL "${url}" | bash`
}

export function pullOneLiner({ os, url, token = null }) {
  return installerOS(os) === 'windows'
    ? windowsPullOneLiner({ url, token })
    : posixPullOneLiner({ url, token })
}

// The names SimCore's Content-Disposition gives the two installers.
export const WINDOWS_INSTALLER_FILE = 'install-cortexsim-agent.ps1'
export const POSIX_INSTALLER_FILE = 'install-cortexsim-agent.sh'

/**
 * How to run the downloaded .ps1. Without CORTEXSIM_TOKEN in the environment the
 * script falls back to the id baked into the download — the legacy shared
 * `jumpbox-01` — so every host run this way would claim the same agent id.
 */
export function windowsDownloadRunLine({ token }) {
  return `Unblock-File .\\${WINDOWS_INSTALLER_FILE}; $env:CORTEXSIM_TOKEN='${token}'; `
    + `powershell -NoProfile -ExecutionPolicy Bypass -File .\\${WINDOWS_INSTALLER_FILE}`
}

// ── Windows without `iwr | iex`: download, verify, run ───────────────────────
// The one-liner pipes a download into iex. This path brings the installer AND
// the beacon as files: the DC checks the beacon's hash against the digest
// SimCore serves, then runs the installer with $env:CORTEXSIM_BIN pointing at
// that file — an input the installer already honours (and re-checks against
// SimCore's digest). Nothing here is new on the server. Not run on Windows.

/** The name SimCore's Content-Disposition gives the windows/amd64 beacon. */
export const WINDOWS_BEACON_FILE = 'cortexsim-agent-windows-amd64.exe'

/** Prints True when the downloaded beacon is the one SimCore serves. */
export function windowsPrestageVerifyLine({ sha256 }) {
  return `(Get-FileHash .\\${WINDOWS_BEACON_FILE} -Algorithm SHA256).Hash.ToLower() -eq '${sha256}'`
}

/** Run the downloaded installer against the downloaded, checked beacon. */
export function windowsPrestageRunLine({ token }) {
  return `Unblock-File .\\${WINDOWS_INSTALLER_FILE}, .\\${WINDOWS_BEACON_FILE}; $env:CORTEXSIM_TOKEN='${token}'; `
    + `$env:CORTEXSIM_BIN=(Resolve-Path .\\${WINDOWS_BEACON_FILE}).Path; `
    + `powershell -NoProfile -ExecutionPolicy Bypass -File .\\${WINDOWS_INSTALLER_FILE}`
}

/**
 * Starting an already-installed beacon by hand, in the target's own words. The
 * hint used to be a bare `cortexsim-agent --server …` for every OS: no `.exe`
 * and not where the Windows installer puts it.
 */
export function legacyRunHint({ os, origin, id }) {
  const base = String(origin || '').replace(/\/+$/, '')
  const agentId = id || 'jumpbox-01'
  return installerOS(os) === 'windows'
    ? `& "${WINDOWS_BEACON_PATH}" --server ${base} --id ${agentId} --interval 10`
    : `cortexsim-agent --server ${base} --id ${agentId} --interval 10`
}

// ── Push bundles for Windows ─────────────────────────────────────────────────
// The pull path says "not yet proven on a Windows host". The push path said
// "always ready … PowerShell (.ps1)" with nothing beside it. Both sentences are
// statements about the tree as it is: every .ps1 bundle is authored and
// parse-checked, and none has been executed on any Windows host.

export const WINDOWS_BUNDLE_UNPROVEN =
  'Authored, not proven: no .ps1 bundle has run on a Windows host yet. It is parse-checked only, so run it in a '
  + 'lab before a customer is watching.'

export const WINDOWS_BUNDLE_RESIDUE =
  'Some Windows bundles stop with exit 2 before running any step, because their cleanup leaves residue on the '
  + 'host. The bundle prints the reason; it runs once CORTEXSIM_ACCEPT_RESIDUE=1 is set in that PowerShell session.'

// ── Removal ──────────────────────────────────────────────────────────────────

/**
 * Removing the beacon from the HOST. Deleting the agent in the console removes
 * the registry row only; a running beacon re-registers on its next poll.
 * The Windows script is served by `?os=windows&uninstall=1`.
 *
 * POSIX is `| bash`, run as the user who installed — the uninstaller's own
 * contract (core/api/agents.py, _POSIX_UNINSTALLER header). The console's
 * install line has no sudo and the installer never escalates, so the default
 * install is per-user: a `systemctl --user` unit (Restart=always) or a
 * LaunchAgent under that user's $HOME. The uninstaller resolves every per-user
 * path from $HOME and the launchd domain from `id -u`. Under `sudo bash` those
 * are root's, so it missed the per-user unit, killed the process, printed
 * "done -- removed 1 of 1 item(s)" and exited 0 — and the unit restarted the
 * beacon, which re-registered. Without sudo the same script fails LOUDLY on a
 * root install (NEEDS_ROOT, printing the sudo form), which is the safe default.
 */
export function uninstallCommand({ os, origin }) {
  const base = String(origin || '').replace(/\/+$/, '')
  return installerOS(os) === 'windows'
    ? `${TLS12_PREFIX}iwr -useb '${base}/api/agents/install?os=windows&uninstall=1' | iex`
    : `curl -fsSL '${base}/api/agents/install?uninstall=1' | bash`
}

/** Only for a beacon that was installed as root (`sudo bash`, or a root shell). */
export function posixRootUninstallCommand({ origin }) {
  const base = String(origin || '').replace(/\/+$/, '')
  return `curl -fsSL '${base}/api/agents/install?uninstall=1' | sudo bash`
}

export const POSIX_UNINSTALL_NOTE =
  'Run it as the same user who ran the installer. The install line has no sudo, so the beacon is '
  + 'normally a per-user service under that user\'s home. Under sudo the script looks for per-user '
  + 'files in root\'s environment instead, can miss them, and still print "done" while the service '
  + 'restarts the beacon. If the beacon was installed as root, the script stops with NEEDS_ROOT '
  + 'and removes nothing; only then use:'

/** Manual Windows removal, for when the host cannot reach SimCore. */
export const WINDOWS_MANUAL_UNINSTALL =
  // Stop-Service waits for STOPPED (sc.exe stop returns at STOP_PENDING, while
  // the exe is still locked). Removes every tree the uninstall script does:
  // the service install, a per-user install, and the beacon's own cache and
  // staging roots (agent/beacon/artifact.go). Not executed on Windows here.
  'Stop-Service CortexSimAgent -Force -ErrorAction SilentlyContinue; sc.exe delete CortexSimAgent; ' +
  'Get-Process cortexsim-agent -ErrorAction SilentlyContinue | Stop-Process -Force; ' +
  'Remove-Item -Recurse -Force "$env:ProgramData\\CortexSim","$env:LOCALAPPDATA\\CortexSim",' +
  '"$env:SystemDrive\\opt\\cortexsim","$env:SystemRoot\\Temp\\cortexsim" -ErrorAction SilentlyContinue'

// ── Windows facts the DC needs before the session ────────────────────────────

export const WINDOWS_BEACON_PATH = 'C:\\ProgramData\\CortexSim\\cortexsim-agent.exe'
export const WINDOWS_ELEVATION_LABEL = 'Open Windows PowerShell as Administrator, then paste'

/**
 * The true Windows caveats. Every line is a statement about the code as it is,
 * including what has NOT been proven — a service install path no Windows host
 * has run is not described as working.
 */
export const WINDOWS_CAVEATS = Object.freeze([
  {
    id: 'unproven',
    title: 'Not yet proven on a Windows host.',
    body: 'The Windows beacon and this installer are built and served, but no Windows host has run them yet. '
      + 'Treat the first Windows enrolment as the test, before a customer is watching.',
  },
  {
    id: 'identity',
    title: 'Identity collapses to the beacon account.',
    body: 'Windows has no credential-free impersonation, so every step runs as the account the beacon runs as. '
      + 'Steps that declare another identity are marked IDENTITY NOT HONOURED in the run record.',
  },
  {
    id: 'unsigned',
    title: 'The beacon is unsigned.',
    body: `Get an AV/XDR allow for ${WINDOWS_BEACON_PATH} before the session. Otherwise the endpoint agent `
      + 'may quarantine the beacon itself, and the scenario never runs.',
  },
  {
    id: 'elevation',
    title: 'Run it elevated.',
    body: 'Elevated, the installer registers the CortexSimAgent service so the beacon survives logoff and reboot. '
      + 'That service path is new and not yet proven on a Windows host. Not elevated, the installer refuses unless '
      + 'CORTEXSIM_ALLOW_UNSUPERVISED=1 is set; the beacon then runs detached, dies at logoff, and cannot run '
      + 'admin-only TTPs.',
  },
])

// ── Which beacons this SimCore ships ─────────────────────────────────────────

/**
 * Read `agent_binaries` from the console's health model for ONE target.
 *
 * The component's colour is not the answer: it is `degraded` when ANY target is
 * missing, so reading the colour told a DC picking Linux that Windows was
 * missing, and one picking Windows nothing they could act on. The per-target
 * `missing[]` list is the answer. Absence of the component, or of the list, is
 * `unknown` — never `present`.
 *
 * EVERY ARCHITECTURE, NOT ONLY amd64. The beacon matrix is five targets
 * (linux and darwin on amd64 + arm64, windows on amd64). This used to ask about
 * `<goos>/amd64` alone — and SimCore's `missing[]` is computed against amd64
 * alone as well — so a shelf with no darwin/arm64 read "present" for macOS and
 * an Apple-silicon Mac fell through to a source build with no warning.
 * `available[]` is the complete list of what is on the shelf, so it answers
 * for arm64; without it (an older SimCore) arm64 is `unconfirmed`, never
 * assumed present.
 *
 * @param {Object|null} healthModel  buildHealthModel() output
 * @param {string} os
 * @returns {{state: 'present'|'partial'|'missing'|'unknown', target: string, os: string,
 *            reason: string|null, code?: string|null, targets: string[],
 *            missingTargets: string[], presentTargets: string[], unconfirmedTargets: string[]}}
 *   `target` is the amd64 target (the one `missing[]` speaks for). `partial`
 *   means some architectures of this OS are served and some are not.
 */
export const BEACON_ARCHES = Object.freeze({
  linux: ['amd64', 'arm64'],
  darwin: ['arm64', 'amd64'], // Apple silicon first: it is the Mac a DC is most likely holding
  windows: ['amd64'],
})

export function beaconShipState(healthModel, os) {
  const goos = installerOS(os)
  const target = `${goos}/amd64`
  const targets = BEACON_ARCHES[goos].map((arch) => `${goos}/${arch}`)
  const out = (state, reason = null, extra = {}) => ({
    state, target, os: goos, reason, targets,
    missingTargets: [], presentTargets: [], unconfirmedTargets: [], ...extra,
  })
  if (!healthModel || healthModel.reachable === false) {
    return out('unknown', 'SimCore health has not been read')
  }
  const comps = healthModel.components
  const row = Array.isArray(comps)
    ? comps.find((c) => c && c.key === 'agent_binaries')
    : comps && typeof comps === 'object' ? comps.agent_binaries : null
  if (!row || row.status === 'unreported') return out('unknown', 'this SimCore does not report agent_binaries')
  const missing = row.extra && row.extra.missing
  if (!Array.isArray(missing)) return out('unknown', 'agent_binaries carries no missing[] list')
  const available = Array.isArray(row.extra.available) ? row.extra.available : null

  const missingTargets = []
  const presentTargets = []
  const unconfirmedTargets = []
  for (const t of targets) {
    if (t === target) {
      // `missing[]` is authoritative for the amd64 target.
      ;(missing.includes(t) ? missingTargets : presentTargets).push(t)
    } else if (missing.includes(t)) {
      missingTargets.push(t)
    } else if (available) {
      ;(available.includes(t) ? presentTargets : missingTargets).push(t)
    } else {
      unconfirmedTargets.push(t)
    }
  }
  const state = missingTargets.length === 0
    ? 'present'
    : presentTargets.length === 0 && unconfirmedTargets.length === 0 ? 'missing' : 'partial'
  return out(state, null, { code: row.code || null, missingTargets, presentTargets, unconfirmedTargets })
}

/** `darwin/arm64` → "Apple silicon"; the words a DC knows a host by. */
export function archLabel(target) {
  const [goos, arch] = String(target || '').split('/')
  if (goos === 'darwin') return arch === 'arm64' ? 'Apple silicon' : 'Intel'
  return arch === 'arm64' ? 'ARM64 (aarch64)' : 'x86-64'
}

// ── Will SimCore answer to this address? ─────────────────────────────────────

const LOOPBACK_HOSTS = new Set(['localhost', '127.0.0.1', '::1'])
const IPV4 = /^(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}$/

const normHost = (h) => String(h ?? '').trim().toLowerCase().replace(/^\[|\]$/g, '').replace(/\.$/, '')

/** The server's own rule (security/host_guard.py::_matches): `*.x` is subdomains only. */
function hostMatches(host, patterns) {
  return patterns.some((raw) => {
    const p = normHost(raw)
    if (p.startsWith('*.')) return host.endsWith(p.slice(1)) && host !== p.slice(2)
    return host === p
  })
}

/**
 * Would SimCore's Host guard answer a request addressed to `serverUrl`?
 *
 * SimCore answers 400 HOST_NOT_ALLOWED unless `Host` is localhost, an IP
 * literal, the machine's own host name / FQDN, or a name in
 * CORTEXSIM_ALLOWED_HOSTS. A DC who types a DNS name into an install line gets
 * `curl: (22) ... error: 400` on the target, before the installer body is
 * served — so nothing is reported back and the console keeps waiting.
 *
 * `/api/health` publishes the guard's configuration on `api_auth`
 * (`host_guard`, `allowed_hosts`). It does NOT publish the machine's own host
 * name, and inside a container that is the container id anyway. So the honest
 * states are:
 *   admitted   — loopback, an IP literal, the name THIS console was reached by
 *                (the page loading proves SimCore answers to it), a listed
 *                name, or a disabled guard
 *   not_listed — a name the published allowlist does not carry; SimCore refuses
 *                it unless it is the machine's own name
 *   unknown    — SimCore did not report the guard in a shape this reads; never
 *                collapsed into admitted
 *   invalid    — not an http(s) URL at all
 *
 * @param {Object|null} healthModel  buildHealthModel() output
 * @param {string} serverUrl         what the DC typed
 * @param {string} [consoleHost]     window.location.hostname
 * @returns {{state: 'admitted'|'not_listed'|'unknown'|'invalid', host: string|null,
 *            why: string|null, allowed: string[]|null}}
 */
export function serverHostVerdict(healthModel, serverUrl, consoleHost = '') {
  const out = (state, host, why = null, allowed = null) => ({ state, host, why, allowed })
  let parsed
  try { parsed = new URL(String(serverUrl ?? '').trim()) } catch { return out('invalid', null, 'not a URL') }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return out('invalid', null, 'not http(s)')
  const host = normHost(parsed.hostname)
  if (!host) return out('invalid', null, 'no host')

  if (LOOPBACK_HOSTS.has(host)) return out('admitted', host, 'loopback')
  if (IPV4.test(host) || host.includes(':')) return out('admitted', host, 'ip')
  if (consoleHost && host === normHost(consoleHost)) return out('admitted', host, 'console-host')

  const comps = healthModel && healthModel.reachable !== false ? healthModel.components : null
  const row = Array.isArray(comps)
    ? comps.find((c) => c && c.key === 'api_auth')
    : comps && typeof comps === 'object' ? comps.api_auth : null
  const extra = row && row.status !== 'unreported' ? row.extra : null
  if (!extra) return out('unknown', host, 'this SimCore does not report its Host guard')
  if (extra.host_guard === 'disabled') return out('admitted', host, 'guard-disabled')
  if (extra.host_guard !== 'enforced' || !Array.isArray(extra.allowed_hosts)) {
    return out('unknown', host, 'this SimCore reported its Host guard in a shape this console does not read')
  }
  const allowed = extra.allowed_hosts.map(String)
  if (allowed.includes('*')) return out('admitted', host, 'guard-disabled', allowed)
  return hostMatches(host, allowed)
    ? out('admitted', host, 'listed', allowed)
    : out('not_listed', host, 'not in CORTEXSIM_ALLOWED_HOSTS', allowed)
}

// ── Install telemetry ────────────────────────────────────────────────────────

// Codes an installer REPORTS and then carries on from (`cs_report` / `Cs-Report`
// in core/api/agents.py, as opposed to `cs_fail` / `Cs-Fail`, which exit). Each
// one is a degraded install, not a stopped one. A code not listed here is
// treated as a stop — an unknown code is never read as progress.
//
// The two installers do NOT agree on every code, so the verdict is read from
// the telemetry ROW (its `os` is the installer's own word: `linux` | `darwin`
// from the bash script, `windows` from the PowerShell one), never from the bare
// code:
//
//   code                    POSIX installer            Windows installer
//   CHECKSUM_SKIPPED        cs_report, carries on      Cs-Report, carries on
//   DEGRADED_NO_SUPERVISOR  cs_report, carries on      Cs-Report (its verdict)
//   SERVICE_START_FAILED    cs_report, then degrades   Cs-Fail — EXITS: the
//                           to nohup                   service never reached
//                                                      Running, or the SCM
//                                                      killed it within 35 s
//                                                      (error 1053)
//
// SERVICE_START_FAILED used to sit in one set for every OS. On Windows that
// rendered a dead install as "carried on past a warning … a later report says
// whether it checked in", and no later report ever comes.
const CONTINUED_BY_BOTH = new Set(['DEGRADED_NO_SUPERVISOR', 'CHECKSUM_SKIPPED'])
const CONTINUED_BY_POSIX_ONLY = new Set(['SERVICE_START_FAILED'])

/**
 * Which installer wrote a telemetry row, from the row's own `os`. Exact words
 * only: a row with a missing or unrecognised OS is `unknown`, and an
 * OS-dependent code on it is a stop — a stop read as progress never corrects
 * itself, while a warning read as a stop is replaced by the next report.
 * (installerOS() is not used here: it maps everything unrecognised to linux.)
 * @returns {'posix'|'windows'|'unknown'}
 */
function reportingInstaller(os) {
  const v = String(os ?? '').trim().toLowerCase()
  if (v === 'windows') return 'windows'
  if (v === 'linux' || v === 'darwin') return 'posix'
  return 'unknown'
}

/**
 * What an installer report MEANS for the install.
 *   ok        — the stage succeeded
 *   continued — the installer said this and carried on, degraded
 *   failed    — the installer exited here
 * @param {{code?: string, os?: string}|null|undefined} attempt  the telemetry
 *   row. A bare code string is read as a row with no OS.
 * @returns {'ok'|'continued'|'failed'}
 */
export function attemptKind(attempt) {
  const row = attempt && typeof attempt === 'object' ? attempt : { code: attempt }
  const c = String(row.code ?? '').trim().toUpperCase()
  if (c === 'OK') return 'ok'
  if (CONTINUED_BY_BOTH.has(c)) return 'continued'
  if (CONTINUED_BY_POSIX_ONLY.has(c) && reportingInstaller(row.os) === 'posix') return 'continued'
  return 'failed'
}

/** Only a stop is an alert; ok and continued reports are status. */
export function attemptRole(attempt) {
  return attemptKind(attempt) === 'failed' ? 'alert' : 'status'
}

/**
 * What a reported-and-continued code leaves the DC with. Looked up only when
 * attemptKind() said `continued`, so the SERVICE_START_FAILED line is reached
 * for a POSIX row alone — it describes the bash installer's nohup fallback,
 * which the Windows installer does not have.
 */
export const CONTINUED_NOTE = Object.freeze({
  CHECKSUM_SKIPPED:
    'The beacon binary was NOT verified: the host had no digest tool, or SimCore served no digest to compare '
    + 'with. The installer carried on with that binary, so its integrity is unchecked. A later report says '
    + 'whether the install finished.',
  SERVICE_START_FAILED:
    'systemd would not start the service, so the installer carried on without a supervisor (nohup). The beacon '
    + 'will not survive a logoff or a reboot. A later report says whether it checked in.',
  DEGRADED_NO_SUPERVISOR:
    'The installer carried on without a supervisor: the beacon is running, and it will not survive a logoff '
    + 'or a reboot. The agent counts as online only after the beacon polls.',
})

/**
 * Attempts reported strictly after `sinceMs`, newest first. Both sides are
 * SimCore timestamps when the caller passes a token's `created_at`, so no
 * browser clock is involved. An attempt whose time cannot be read cannot be
 * placed after the mint and is left out of the since-mint verdict.
 */
export function attemptsSince(list, sinceMs) {
  if (!Array.isArray(list) || !Number.isFinite(sinceMs)) return []
  return list
    .map((a) => ({ a, t: parseServerTime(a && a.reported_at) }))
    .filter(({ t }) => Number.isFinite(t) && t > sinceMs)
    .sort((x, y) => y.t - x.t)
    .map(({ a }) => a)
}

// ── Clipboard ────────────────────────────────────────────────────────────────

export const COPY_UNAVAILABLE_HTTP = 'Copy is unavailable on http:// — select the text'

/** What to tell the operator when nothing could copy. */
export function copyFailureMessage() {
  const secure = typeof window !== 'undefined' && window.isSecureContext === true
  return secure
    ? 'Copy failed — the text is selected; press Ctrl+C (⌘C on a Mac)'
    : COPY_UNAVAILABLE_HTTP
}

function execCommandCopy(text) {
  if (typeof document === 'undefined' || typeof document.execCommand !== 'function' || !document.body) return false
  const ta = document.createElement('textarea')
  ta.value = text
  ta.setAttribute('readonly', '')
  ta.setAttribute('aria-hidden', 'true')
  ta.style.position = 'fixed'
  ta.style.top = '0'
  ta.style.left = '-9999px'
  ta.style.opacity = '0'
  const prevFocus = document.activeElement
  document.body.appendChild(ta)
  try {
    ta.focus()
    ta.select()
    return document.execCommand('copy') === true
  } catch {
    return false
  } finally {
    ta.remove()
    if (prevFocus && typeof prevFocus.focus === 'function') prevFocus.focus()
  }
}

/**
 * Copy `text`. Resolves 'copied' only when something really copied.
 * The Clipboard API exists only in a secure context, and a DC on a lab
 * SimCore is usually on plain http://, so the hidden-textarea execCommand path
 * is the one that most often does the work.
 * @returns {Promise<'copied'|'failed'>}
 */
export async function copyToClipboard(text) {
  if (text == null || text === '') return 'failed'
  const s = String(text)
  const secure = typeof window !== 'undefined' && window.isSecureContext === true
  const clip = typeof navigator !== 'undefined' ? navigator.clipboard : undefined
  if (secure && clip && typeof clip.writeText === 'function') {
    try {
      await clip.writeText(s)
      return 'copied'
    } catch { /* permission denied or document not focused — try the fallback */ }
  }
  return execCommandCopy(s) ? 'copied' : 'failed'
}

/** Select an element's text so the operator can copy it by hand. */
export function selectNodeText(el) {
  if (!el || typeof window === 'undefined' || !window.getSelection || typeof document.createRange !== 'function') {
    return false
  }
  try {
    const range = document.createRange()
    range.selectNodeContents(el)
    const sel = window.getSelection()
    sel.removeAllRanges()
    sel.addRange(range)
    return true
  } catch {
    return false
  }
}
