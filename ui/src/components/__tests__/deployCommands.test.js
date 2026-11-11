/**
 * deployCommands — the ONE place the console spells an install / uninstall
 * command, maps an agent's OS to an installer target, reads which beacons this
 * SimCore ships, and copies text.
 *
 * Before this module the Windows one-liner was spelled twice (TargetsView and
 * GetStartedView) and both copies lacked the TLS 1.2 opt-in that Windows
 * PowerShell 5.1 on an older .NET needs before `iwr` can talk to an https
 * SimCore; installerOS used `/win/i`, which reads `darwin` as Windows; and every
 * Copy button said "copied" before — or without — the clipboard agreeing.
 *
 * jsdom only. Real browser clipboard permission prompts and http:// behaviour
 * are NOT exercised here; these tests pin the decision logic around them.
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import {
  TLS12_PREFIX,
  installerOS,
  windowsPullOneLiner,
  posixPullOneLiner,
  pullOneLiner,
  windowsDownloadRunLine,
  uninstallCommand,
  posixRootUninstallCommand,
  POSIX_UNINSTALL_NOTE,
  WINDOWS_MANUAL_UNINSTALL,
  beaconShipState,
  copyToClipboard,
  attemptRole,
  attemptsSince,
} from '../console/deployCommands.js'
import { buildHealthModel } from '../console/readiness/healthModel.js'

const URL_WIN = 'https://simcore.lab:8888/api/agents/install?os=windows&interval=10'
const URL_LNX = 'https://simcore.lab:8888/api/agents/install?os=linux&interval=10'

describe('installerOS — exact match, never a substring (UI-12)', () => {
  it.each([
    ['windows', 'windows'],
    ['Windows', 'windows'],
    ['win32', 'windows'],
    ['win32_nt', 'windows'],
    ['darwin', 'darwin'], // the /win/i bug: dar-WIN-n
    ['Darwin', 'darwin'],
    ['macos', 'darwin'],
    ['macOS', 'darwin'],
    ['linux', 'linux'],
    ['cygwin', 'linux'], // contains "win", is not Windows
    ['freebsd', 'linux'],
    ['', 'linux'],
    [null, 'linux'],
    [undefined, 'linux'],
  ])('%j → %s', (input, expected) => {
    expect(installerOS(input)).toBe(expected)
  })
})

describe('pull one-liners (INST-12)', () => {
  it('the Windows line opts into TLS 1.2 before iwr, then carries the token in an env var', () => {
    expect(windowsPullOneLiner({ url: URL_WIN, token: 'cxs_abc' })).toBe(
      "[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072; "
      + "$env:CORTEXSIM_TOKEN='cxs_abc'; iwr -useb \"" + URL_WIN + "\" | iex",
    )
  })

  it('the tokenless (legacy) Windows line keeps the TLS opt-in', () => {
    expect(windowsPullOneLiner({ url: URL_WIN })).toBe(`${TLS12_PREFIX}iwr -useb "${URL_WIN}" | iex`)
  })

  it('TLS12_PREFIX is the exact additive opt-in, never an overwrite of other protocols', () => {
    expect(TLS12_PREFIX).toBe(
      '[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072; ',
    )
  })

  it('the POSIX line is unchanged', () => {
    expect(posixPullOneLiner({ url: URL_LNX, token: 'cxs_abc' }))
      .toBe(`curl -fsSL "${URL_LNX}" | CORTEXSIM_TOKEN='cxs_abc' bash`)
    expect(posixPullOneLiner({ url: URL_LNX })).toBe(`curl -fsSL "${URL_LNX}" | bash`)
  })

  it('pullOneLiner dispatches on the exact OS, so macOS gets bash', () => {
    expect(pullOneLiner({ os: 'macos', url: URL_LNX, token: 't' })).toMatch(/^curl /)
    expect(pullOneLiner({ os: 'darwin', url: URL_LNX, token: 't' })).toMatch(/^curl /)
    expect(pullOneLiner({ os: 'windows', url: URL_WIN, token: 't' })).toMatch(/^\[Net\.ServicePointManager\]/)
  })
})

describe('downloaded-installer run line (UI-15)', () => {
  it('unblocks, sets the token and runs the .ps1 under a bypassed policy', () => {
    expect(windowsDownloadRunLine({ token: 'cxs_abc' })).toBe(
      "Unblock-File .\\install-cortexsim-agent.ps1; $env:CORTEXSIM_TOKEN='cxs_abc'; "
      + 'powershell -NoProfile -ExecutionPolicy Bypass -File .\\install-cortexsim-agent.ps1',
    )
  })
})

describe('uninstall commands (UI-10)', () => {
  // RG-UI-02. The console's install line has no sudo and the installer never
  // escalates, so the default install is per-user: a `systemctl --user` unit
  // with Restart=always under the installing user's $HOME. The uninstaller
  // resolves every per-user path from $HOME. Under `sudo bash` that is root's,
  // so it found nothing per-user, killed the process, printed "done -- removed
  // 1 of 1 item(s)", and the user unit restarted the beacon ten seconds later.
  // Its own header gives the contract: `| bash`, and `| sudo bash` only for a
  // root install — which a non-root run refuses loudly with NEEDS_ROOT.
  it('Linux and macOS pipe the removal script to bash, as the installing user — never sudo by default', () => {
    expect(uninstallCommand({ os: 'linux', origin: 'https://x:8888' }))
      .toBe("curl -fsSL 'https://x:8888/api/agents/install?uninstall=1' | bash")
    expect(uninstallCommand({ os: 'darwin', origin: 'https://x:8888' }))
      .toBe("curl -fsSL 'https://x:8888/api/agents/install?uninstall=1' | bash")
  })

  it('the root-install form is separate and named for what it is', () => {
    expect(posixRootUninstallCommand({ origin: 'https://x:8888/' }))
      .toBe("curl -fsSL 'https://x:8888/api/agents/install?uninstall=1' | sudo bash")
    expect(POSIX_UNINSTALL_NOTE).toMatch(/same user who ran the installer/i)
    expect(POSIX_UNINSTALL_NOTE).toContain('NEEDS_ROOT')
    expect(POSIX_UNINSTALL_NOTE).toMatch(/installed as root/i)
  })

  it('Windows asks for the PowerShell removal script (TLS opt-in first)', () => {
    expect(uninstallCommand({ os: 'windows', origin: 'https://x:8888' }))
      .toBe(`${TLS12_PREFIX}iwr -useb 'https://x:8888/api/agents/install?os=windows&uninstall=1' | iex`)
  })

  it('the manual Windows fallback waits for the stop, then removes every tree', () => {
    // sc.exe stop returns at STOP_PENDING with the exe still locked.
    expect(WINDOWS_MANUAL_UNINSTALL).not.toMatch(/sc\.exe stop/)
    expect(WINDOWS_MANUAL_UNINSTALL.indexOf('Stop-Service CortexSimAgent -Force'))
      .toBeLessThan(WINDOWS_MANUAL_UNINSTALL.indexOf('sc.exe delete CortexSimAgent'))
    for (const tree of ['$env:ProgramData\\CortexSim', '$env:LOCALAPPDATA\\CortexSim',
      '$env:SystemDrive\\opt\\cortexsim', '$env:SystemRoot\\Temp\\cortexsim']) {
      expect(WINDOWS_MANUAL_UNINSTALL).toContain(tree)
    }
  })
})

function health(agentBinaries) {
  return buildHealthModel({
    status: 'degraded',
    components: agentBinaries === undefined ? {} : { agent_binaries: agentBinaries },
  })
}

describe('beaconShipState — read the per-target missing list, not the component colour (UI-02 / UI-16)', () => {
  const incompleteWin = {
    status: 'degraded', code: 'AGENT_DIST_INCOMPLETE', detail: 'missing windows/amd64',
    missing: ['windows/amd64'], available: ['darwin/amd64', 'linux/amd64'],
  }

  it('Windows missing reads missing; Linux on the same SimCore reads present', () => {
    expect(beaconShipState(health(incompleteWin), 'windows')).toMatchObject({ state: 'missing', target: 'windows/amd64' })
    expect(beaconShipState(health(incompleteWin), 'linux')).toMatchObject({ state: 'present', target: 'linux/amd64' })
    expect(beaconShipState(health(incompleteWin), 'macos')).toMatchObject({ state: 'present', target: 'darwin/amd64' })
  })

  it('an empty dist reads missing for every target', () => {
    const empty = { status: 'degraded', code: 'AGENT_DIST_EMPTY', detail: 'none', missing: ['darwin/amd64', 'linux/amd64', 'windows/amd64'], available: [] }
    for (const os of ['linux', 'darwin', 'windows']) {
      expect(beaconShipState(health(empty), os).state).toBe('missing')
    }
  })

  it('no health model, or a SimCore that does not report agent_binaries, is unknown — never present', () => {
    expect(beaconShipState(null, 'windows').state).toBe('unknown')
    expect(beaconShipState(health(undefined), 'windows').state).toBe('unknown')
  })

  it('a reported component with no missing[] list is unknown, not "nothing missing"', () => {
    expect(beaconShipState(health({ status: 'ok', code: 'OK' }), 'windows').state).toBe('unknown')
  })
})

describe('copyToClipboard — success only when something really copied (UI-14)', () => {
  const restore = []
  function setEnv({ secure, clipboard, execCommand }) {
    Object.defineProperty(window, 'isSecureContext', { configurable: true, value: secure })
    Object.defineProperty(window.navigator, 'clipboard', { configurable: true, value: clipboard })
    document.execCommand = execCommand
    restore.push(() => {
      delete window.isSecureContext
      delete window.navigator.clipboard
      delete document.execCommand
    })
  }
  afterEach(() => { while (restore.length) restore.pop()() })

  it('awaits writeText in a secure context and does not fall back when it succeeds', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    const execCommand = vi.fn(() => true)
    setEnv({ secure: true, clipboard: { writeText }, execCommand })
    await expect(copyToClipboard('hello')).resolves.toBe('copied')
    expect(writeText).toHaveBeenCalledWith('hello')
    expect(execCommand).not.toHaveBeenCalled()
  })

  it('a rejected writeText falls back to execCommand, and only its success counts', async () => {
    setEnv({ secure: true, clipboard: { writeText: vi.fn().mockRejectedValue(new Error('denied')) }, execCommand: vi.fn(() => true) })
    await expect(copyToClipboard('x')).resolves.toBe('copied')
  })

  it('outside a secure context the Clipboard API is not even tried', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    const execCommand = vi.fn(() => true)
    setEnv({ secure: false, clipboard: { writeText }, execCommand })
    await expect(copyToClipboard('x')).resolves.toBe('copied')
    expect(writeText).not.toHaveBeenCalled()
    expect(execCommand).toHaveBeenCalledWith('copy')
  })

  it('both paths failing is a failure, and leaves no stray textarea behind', async () => {
    setEnv({ secure: false, clipboard: undefined, execCommand: vi.fn(() => false) })
    await expect(copyToClipboard('x')).resolves.toBe('failed')
    expect(document.querySelectorAll('textarea').length).toBe(0)
  })

  it('an execCommand that throws is a failure, not an exception', async () => {
    setEnv({ secure: false, clipboard: undefined, execCommand: vi.fn(() => { throw new Error('nope') }) })
    await expect(copyToClipboard('x')).resolves.toBe('failed')
  })

  it('no text is a failure', async () => {
    setEnv({ secure: true, clipboard: { writeText: vi.fn() }, execCommand: vi.fn(() => true) })
    await expect(copyToClipboard('')).resolves.toBe('failed')
  })
})

describe('install attempts (UI-06)', () => {
  it('OK and DEGRADED_NO_SUPERVISOR are status; every other code is an alert', () => {
    expect(attemptRole('OK')).toBe('status')
    expect(attemptRole('DEGRADED_NO_SUPERVISOR')).toBe('status')
    expect(attemptRole('ENROLL_DENIED')).toBe('alert')
    expect(attemptRole('WINDOWS_AGENT_UNAVAILABLE')).toBe('alert')
    expect(attemptRole('')).toBe('alert')
    expect(attemptRole(undefined)).toBe('alert')
  })

  it('keeps only reports after the mint time, newest first, and drops unplaceable ones', () => {
    const mint = Date.UTC(2026, 10, 5, 10, 0, 0)
    const list = [
      { code: 'OK', stage: 'detect', reported_at: '2026-11-05T09:59:59' },
      { code: 'OK', stage: 'enroll', reported_at: '2026-11-05T10:00:03' },
      { code: 'CHECKSUM_MISMATCH', stage: 'verify', reported_at: '2026-11-05T10:00:09' },
      { code: 'OK', stage: 'run', reported_at: 'not-a-time' },
    ]
    expect(attemptsSince(list, mint).map((a) => a.stage)).toEqual(['verify', 'enroll'])
    expect(attemptsSince(null, mint)).toEqual([])
    expect(attemptsSince(list, NaN)).toEqual([])
  })
})
