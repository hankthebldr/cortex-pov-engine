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
 * REPORT some codes and carry on (`cs_report` / `Cs-Report`, not `cs_fail`):
 * CHECKSUM_SKIPPED and SERVICE_START_FAILED. Those read "The installer
 * stopped … re-run once it is fixed" while the install was still running, and
 * Get started dropped its "waiting" line.
 *
 * jsdom only: what a screen reader actually announces is not exercised here.
 */
import React from 'react'
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { LatestAttempt } from '../console/DeployParts.jsx'
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
  it('sorts the installers\' codes into ok / continued / failed', () => {
    expect(attemptKind('OK')).toBe('ok')
    // cs_report / Cs-Report: the installer says it and carries on.
    expect(attemptKind('CHECKSUM_SKIPPED')).toBe('continued')
    expect(attemptKind('SERVICE_START_FAILED')).toBe('continued')
    expect(attemptKind('DEGRADED_NO_SUPERVISOR')).toBe('continued')
    // cs_fail / Cs-Fail: it exits.
    for (const code of ['CHECKSUM_MISMATCH', 'ENROLL_DENIED', 'WINDOWS_AGENT_UNAVAILABLE', 'NOT_ELEVATED', '', undefined, 'SOMETHING_NEW']) {
      expect(attemptKind(code), String(code)).toBe('failed')
    }
  })

  it('only a failure is an alert', () => {
    expect(attemptRole('CHECKSUM_SKIPPED')).toBe('status')
    expect(attemptRole('SERVICE_START_FAILED')).toBe('status')
    expect(attemptRole('CHECKSUM_MISMATCH')).toBe('alert')
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

  it('SERVICE_START_FAILED and DEGRADED_NO_SUPERVISOR say what the beacon will not survive', () => {
    const { unmount } = render(<LatestAttempt attempt={attempt({ stage: 'install', code: 'SERVICE_START_FAILED', detail: 'systemctl enable --now failed; degrading to nohup' })} />)
    expect(screen.getByRole('status').textContent).toMatch(/without a supervisor|unsupervised/i)
    unmount()
    render(<LatestAttempt attempt={attempt({ stage: 'install', code: 'DEGRADED_NO_SUPERVISOR', detail: 'no systemd' })} />)
    expect(screen.getByRole('status').textContent).toMatch(/logoff|reboot/i)
  })

  it('a real failure keeps its alert and its "fix line" pointer', () => {
    render(<LatestAttempt attempt={attempt({ stage: 'verify', code: 'CHECKSUM_MISMATCH' })} />)
    expect(screen.getByRole('alert').textContent).toMatch(/The installer stopped/)
  })
})
