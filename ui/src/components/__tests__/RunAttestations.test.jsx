import React from 'react'
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import RunDetailView from '../console/RunDetailView.jsx'

void React

const BASE = {
  id: 1, run_id: 'run-a', scenario_id: 'SIM-EDR-001', status: 'complete', mode: 'pull',
  agent_profile_mode: 'unknown', bas_mode_attested: null, soakless_attested: null,
  attested_by: null, attested_at: null, paired_with_run_id: null, canary_run_id: null,
  block_mode_status: null, severed_at_step: null,
}

describe('<RunDetailView /> attestations', () => {
  beforeEach(() => {
    globalThis.fetch = vi.fn(async () => new Response(JSON.stringify({}), {
      status: 200, headers: { 'content-type': 'application/json' },
    }))
  })
  afterEach(() => { vi.restoreAllMocks() })

  it('is absent when nothing was declared', () => {
    render(<RunDetailView runId="run-a" run={BASE} subTab="live" />)
    expect(screen.queryByTestId('run-attestations')).toBeNull()
    expect(screen.queryByTestId('run-severed-banner')).toBeNull()
  })

  it('words every line as attested, not verified, with the Z-suffixed time', () => {
    const run = {
      ...BASE, agent_profile_mode: 'block', attested_by: 'henry', attested_at: '2026-10-26T09:30:00',
      bas_mode_attested: true, soakless_attested: false,
      block_mode_status: 'block_mode_unconfirmed', canary_run_id: 'canary-9',
    }
    render(<RunDetailView runId="run-a" run={run} subTab="live" />)
    const panel = screen.getByTestId('run-attestations')
    expect(panel.textContent).toContain('Agent profile mode: block — attested by henry at 2026-10-26T09:30:00Z, not verified')
    expect(panel.textContent).toContain('BAS mode: enabled')
    expect(panel.textContent).toContain('Soakless: disabled')
    expect(panel.textContent).toContain('Canary: block_mode_unconfirmed (run canary-9)')
  })

  it('never prints null / None for absent facts', () => {
    const run = { ...BASE, agent_profile_mode: 'report' }
    render(<RunDetailView runId="run-a" run={run} subTab="live" />)
    const t = screen.getByTestId('run-attestations').textContent
    expect(t).toContain('attested by unknown at unknown, not verified')
    expect(t).toContain('BAS mode: not attested')
    expect(t).toContain('Soakless: not attested')
    expect(t).toContain('Canary: not run')
    expect(t).not.toMatch(/null|None|undefined/)
  })

  it('shows the canary shapes: status only / run only', () => {
    const { rerender } = render(<RunDetailView runId="run-a" run={{ ...BASE, agent_profile_mode: 'block', block_mode_status: 'canary_not_available' }} subTab="live" />)
    expect(screen.getByTestId('run-attestations').textContent).toContain('Canary: canary_not_available')
    rerender(<RunDetailView runId="run-a" run={{ ...BASE, agent_profile_mode: 'block', canary_run_id: 'c1' }} subTab="live" />)
    expect(screen.getByTestId('run-attestations').textContent).toContain('Canary: run c1 (status unknown)')
  })

  it('shows the severed banner when severed_at_step is set', () => {
    render(<RunDetailView runId="run-a" run={{ ...BASE, severed_at_step: 'step-02' }} subTab="live" />)
    expect(screen.getByTestId('run-severed-banner').textContent).toBe('Chain severed at step-02')
  })
})
