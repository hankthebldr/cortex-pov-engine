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

// ── Pull one-liners ──────────────────────────────────────────────────────────

/**
 * Windows PowerShell 5.1 on a .NET that predates TLS 1.2-by-default refuses an
 * https SimCore before `iwr` sends a byte. `-bor 3072` ADDS Tls12 to whatever
 * the session already allows; assigning Tls12 alone would drop the rest.
 */
export const TLS12_PREFIX =
  '[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072; '

/** The token rides in an env var, not the URL: out of shell history and proxy logs. */
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

// ── Removal ──────────────────────────────────────────────────────────────────

/**
 * Removing the beacon from the HOST. Deleting the agent in the console removes
 * the registry row only; a running beacon re-registers on its next poll.
 * The Windows script is served by `?os=windows&uninstall=1`.
 */
export function uninstallCommand({ os, origin }) {
  const base = String(origin || '').replace(/\/+$/, '')
  return installerOS(os) === 'windows'
    ? `${TLS12_PREFIX}iwr -useb '${base}/api/agents/install?os=windows&uninstall=1' | iex`
    : `curl -fsSL '${base}/api/agents/install?uninstall=1' | sudo bash`
}

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
 * @param {Object|null} healthModel  buildHealthModel() output
 * @param {string} os
 * @returns {{state: 'present'|'missing'|'unknown', target: string, os: string,
 *            reason: string|null, code?: string|null}}
 */
export function beaconShipState(healthModel, os) {
  const goos = installerOS(os)
  const target = `${goos}/amd64`
  const out = (state, reason = null, extra = {}) => ({ state, target, os: goos, reason, ...extra })
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
  return out(missing.includes(target) ? 'missing' : 'present', null, { code: row.code || null })
}

// ── Install telemetry ────────────────────────────────────────────────────────

const STATUS_CODES = new Set(['OK', 'DEGRADED_NO_SUPERVISOR'])

/** OK and DEGRADED_NO_SUPERVISOR are progress; every other code is a failure. */
export function attemptRole(code) {
  return STATUS_CODES.has(String(code ?? '').trim().toUpperCase()) ? 'status' : 'alert'
}

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
