/**
 * DeployParts — the installer verdict the deploy modal and Get started share.
 *
 * RG-UI-06. LatestAttempt rendered `12s ago` INSIDE its role=alert node, and
 * the attempts poll re-renders it every 5 s. role=alert is aria-live=assertive
 * and aria-atomic, so a screen reader re-read the whole failure every five
 * seconds for the first minute, on top of whatever the DC was doing to fix it.
 *
 * CHECKSUM_SKIPPED wording. The verdict knew two kinds of code: OK /
 * DEGRADED_NO_SUPERVISOR, and "the installer stopped". But the installers
 * REPORT some codes and carry on (`cs_report` / `Cs-Report`, not `cs_fail`).
 * Those read "The installer stopped … re-run once it is fixed" while the
 * install was still running, and Get started dropped its "waiting" line.
 *
 * WHICH codes are carried on from depends on WHICH installer reported them
 * (core/api/agents.py):
 *   CHECKSUM_SKIPPED        both installers report it and carry on
 *   DEGRADED_NO_SUPERVISOR  both installers report it and carry on
 *   SERVICE_START_FAILED    POSIX: `cs_report`, then degrades to nohup
 *                           WINDOWS: `Cs-Fail` — the installer EXITS (the
 *                           service never reached Running, or the SCM killed it
 *                           inside the 35 s window: error 1053)
 * An earlier version of this file pinned SERVICE_START_FAILED as "continued"
 * from the bare code, with no OS. That was wrong for Windows: a dead install
 * read as progress and Get started kept waiting for a report that never comes.
 * The row's `os` (the installer's own word) decides; a row with no readable OS
 * is a stop, because a stop read as progress never corrects itself.
 *
 * jsdom only: what a screen reader actually announces is not exercised here.
 */
import React from 'react'
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import { InstallAttemptsList, LatestAttempt } from '../console/DeployParts.jsx'
import { attemptKind, attemptRole } from '../console/deployCommands.js'

void React

const NOW = Date.UTC(2026, 10, 10, 10, 0, 12)
const attempt = (o = {}) => ({
  stage: 'enroll', code: 'ENROLL_DENIED', detail: 'HTTP 403 Forbidden',
  hostname: 'WIN22', os: 'windows', arch: 'amd64', reported_at: '2026-11-10T10:00:00', ...o,
})

afterEach(() => { vi.useRealTimers() })

describe('LatestAttempt — the live region does not change between polls (RG-UI-06)', () => {
  it('a failure keeps the same announced text while its age ticks', () => {
    vi.useFakeTimers()
    vi.setSystemTime(NOW)
    const a = attempt()
    const { rerender } = render(<LatestAttempt attempt={a} />)
    const live = screen.getByRole('alert')
    const before = live.textContent
    expect(before).toContain('ENROLL_DENIED')
    expect(before).not.toMatch(/\d+s ago/)

    // The next poll: same attempt, a new object, five seconds later.
    vi.setSystemTime(NOW + 5000)
    rerender(<LatestAttempt attempt={{ ...a }} />)
    expect(screen.getByRole('alert')).toBe(live)
    expect(live.textContent).toBe(before)
  })

  it('the ticking age is still shown, outside the live region and hidden from it', () => {
    vi.useFakeTimers()
    vi.setSystemTime(NOW)
    const { rerender } = render(<LatestAttempt attempt={attempt()} />)
    const ago = screen.getByTestId('deploy-attempt-latest-ago')
    expect(ago.textContent).toMatch(/12s ago/)
    expect(ago.getAttribute('aria-hidden')).toBe('true')
    expect(screen.getByRole('alert').contains(ago)).toBe(false)
    vi.setSystemTime(NOW + 5000)
    rerender(<LatestAttempt attempt={attempt()} />)
    expect(screen.getByTestId('deploy-attempt-latest-ago').textContent).toMatch(/17s ago/)
  })

  it('the announced text carries a fixed report time instead, so the when is not lost', () => {
    vi.useFakeTimers()
    vi.setSystemTime(NOW)
    render(<LatestAttempt attempt={attempt()} />)
    expect(screen.getByRole('alert').textContent).toMatch(/10:00:00 UTC/)
  })

  it('a NEW attempt is a new live node, so it is announced once', () => {
    vi.useFakeTimers()
    vi.setSystemTime(NOW)
    const { rerender } = render(<LatestAttempt attempt={attempt()} />)
    const first = screen.getByRole('alert')
    rerender(<LatestAttempt attempt={attempt({ code: 'BINARY_FETCH_FAILED', stage: 'download', reported_at: '2026-11-10T10:00:09' })} />)
    expect(screen.getByRole('alert')).not.toBe(first)
  })

  it('the status variant is held still the same way', () => {
    vi.useFakeTimers()
    vi.setSystemTime(NOW)
    const a = attempt({ code: 'OK', stage: 'install', detail: 'supervised' })
    const { rerender } = render(<LatestAttempt attempt={a} />)
    const before = screen.getByRole('status').textContent
    vi.setSystemTime(NOW + 5000)
    rerender(<LatestAttempt attempt={{ ...a }} />)
    expect(screen.getByRole('status').textContent).toBe(before)
  })
})

describe('install codes — reported-and-continued is not "the installer stopped"', () => {
  const row = (os, code, o = {}) => ({ stage: 'install', code, os, ...o })

  it('sorts codes every installer treats the same way into ok / continued / failed', () => {
    for (const os of ['linux', 'darwin', 'windows']) {
      expect(attemptKind(row(os, 'OK')), os).toBe('ok')
      // cs_report / Cs-Report: the installer says it and carries on.
      expect(attemptKind(row(os, 'CHECKSUM_SKIPPED')), os).toBe('continued')
      expect(attemptKind(row(os, 'DEGRADED_NO_SUPERVISOR')), os).toBe('continued')
      // cs_fail / Cs-Fail: it exits.
      for (const code of ['CHECKSUM_MISMATCH', 'ENROLL_DENIED', 'WINDOWS_AGENT_UNAVAILABLE', 'NOT_ELEVATED', '', undefined, 'SOMETHING_NEW']) {
        expect(attemptKind(row(os, code)), `${os} ${String(code)}`).toBe('failed')
      }
    }
  })

  it('SERVICE_START_FAILED is a stop on Windows (Cs-Fail) and a warning on POSIX (cs_report)', () => {
    expect(attemptKind(row('windows', 'SERVICE_START_FAILED'))).toBe('failed')
    expect(attemptRole(row('windows', 'SERVICE_START_FAILED'))).toBe('alert')
    expect(attemptKind(row('Windows', 'service_start_failed'))).toBe('failed')
    for (const os of ['linux', 'darwin']) {
      expect(attemptKind(row(os, 'SERVICE_START_FAILED')), os).toBe('continued')
      expect(attemptRole(row(os, 'SERVICE_START_FAILED')), os).toBe('status')
    }
  })

  it('a row that does not say which installer wrote it is never read as progress on that code', () => {
    for (const os of ['', undefined, null, 'freebsd', 'win']) {
      expect(attemptKind(row(os, 'SERVICE_START_FAILED')), String(os)).toBe('failed')
    }
    // A bare code carries no OS at all.
    expect(attemptKind('SERVICE_START_FAILED')).toBe('failed')
    expect(attemptRole('SERVICE_START_FAILED')).toBe('alert')
    // Nothing at all is a stop, not a pass.
    expect(attemptKind(null)).toBe('failed')
    expect(attemptKind(undefined)).toBe('failed')
  })

  it('only a failure is an alert', () => {
    expect(attemptRole(row('linux', 'CHECKSUM_SKIPPED'))).toBe('status')
    expect(attemptRole(row('windows', 'CHECKSUM_SKIPPED'))).toBe('status')
    expect(attemptRole(row('windows', 'DEGRADED_NO_SUPERVISOR'))).toBe('status')
    expect(attemptRole(row('linux', 'CHECKSUM_MISMATCH'))).toBe('alert')
  })

  it('CHECKSUM_SKIPPED says the binary was NOT verified and that the install went on', () => {
    render(<LatestAttempt attempt={attempt({ stage: 'verify', code: 'CHECKSUM_SKIPPED', detail: "expect='' actual_tool_present=no" })} />)
    const live = screen.getByRole('status')
    expect(live.textContent).not.toMatch(/The installer stopped/)
    expect(live.textContent).not.toMatch(/re-run once it is fixed/)
    expect(live.textContent).toMatch(/not verified/i)
    expect(live.textContent).toMatch(/carried on/i)
    // It must not read as a clean pass either.
    expect(live.closest('.install-attempt').className).toContain('install-attempt--warn')
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('a Windows SERVICE_START_FAILED is an alert that says the installer stopped', () => {
    render(<LatestAttempt attempt={{
      os: 'windows', stage: 'install', code: 'SERVICE_START_FAILED',
      detail: "CortexSimAgent was Running but is 'Stopped' 35 s later: it is crashing or being stopped",
      hostname: 'WIN22', arch: 'amd64', reported_at: '2026-11-10T10:00:00',
    }} />)
    const live = screen.getByRole('alert')
    expect(live.textContent).toMatch(/The installer stopped/)
    expect(live.textContent).toMatch(/SERVICE_START_FAILED/)
    expect(live.textContent).toMatch(/re-run once it is fixed/)
    // Nothing here may say a later report is coming: none is.
    expect(live.textContent).not.toMatch(/carried on/i)
    expect(live.textContent).not.toMatch(/later report/i)
    expect(live.closest('.install-attempt').className).toContain('install-attempt--fail')
    expect(screen.queryByRole('status')).toBeNull()
  })

  it('a POSIX SERVICE_START_FAILED and DEGRADED_NO_SUPERVISOR say what the beacon will not survive', () => {
    const { unmount } = render(<LatestAttempt attempt={attempt({ os: 'linux', hostname: 'jb', stage: 'install', code: 'SERVICE_START_FAILED', detail: 'systemctl enable --now failed; degrading to nohup' })} />)
    const live = screen.getByRole('status')
    expect(live.textContent).toMatch(/without a supervisor|unsupervised/i)
    expect(live.textContent).not.toMatch(/The installer stopped/)
    expect(live.closest('.install-attempt').className).toContain('install-attempt--warn')
    unmount()
    render(<LatestAttempt attempt={attempt({ stage: 'install', code: 'DEGRADED_NO_SUPERVISOR', detail: 'per-user process pid 4312, not a service; checked in after 12 s' })} />)
    expect(screen.getByRole('status').textContent).toMatch(/logoff|reboot/i)
  })

  it('a real failure keeps its alert and its "fix line" pointer', () => {
    render(<LatestAttempt attempt={attempt({ stage: 'verify', code: 'CHECKSUM_MISMATCH' })} />)
    expect(screen.getByRole('alert').textContent).toMatch(/The installer stopped/)
  })

  it('the attempts list words the same code per installer: stopped for Windows, warning for Linux', () => {
    render(<InstallAttemptsList state={{
      attempts: [
        { os: 'windows', arch: 'amd64', hostname: 'WIN22', stage: 'install', code: 'SERVICE_START_FAILED', reported_at: '2026-11-10T10:00:09' },
        { os: 'linux', arch: 'amd64', hostname: 'jb', stage: 'install', code: 'SERVICE_START_FAILED', reported_at: '2026-11-10T10:00:05' },
      ],
      error: null, unsupported: false,
    }} />)
    const rows = within(screen.getByTestId('deploy-attempts-list')).getAllByRole('listitem')
    expect(rows[0].className).toContain('install-attempts__row--fail')
    expect(rows[0].textContent).toMatch(/^stopped\b/)
    expect(rows[1].className).toContain('install-attempts__row--warn')
    expect(rows[1].textContent).toMatch(/^warning\b/)
  })
})
