/**
 * A run that never executed must NEVER be reported as a detection failure.
 *
 * This is the console's single most dangerous behaviour: when the simulation
 * dies on the target (`runuser: may not be used by non-root users`), the
 * storyline builder marks every un-fired detection `missed` — because `failed`
 * is a terminal run state — and the presenter surfaces rendered "0% coverage"
 * plus ten red "No alert reconciled — detection gap" rows, on a run still
 * badged LIVE. Projected at a customer, that is a false-negative claim about
 * their own Cortex tenant.
 *
 * These tests pin the distinction between "executed but not detected" and
 * "never executed".
 */
import React from 'react'
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import DetectionStoryline, { isRunUnproven, reconcileStatus } from '../DetectionStoryline.jsx'
import RunDetailView from '../console/RunDetailView.jsx'

void React

/** Builder output for a 2-detection run, parameterised by run outcome. */
function storylineFor(runStatus) {
  return {
    detection_storyline: {
      run_id: '4ee09860-140d-4078-bd20-5179f5dbf7af',
      scenario_id: 'SIM-EDR-001',
      run_status: runStatus,
      steps: [{
        step_id: 's1',
        step_index: 0,
        step_name: 'Credential dump via /proc',
        mitre_technique: 'T1003.007',
        detections: [
          {
            step_id: 's1', step_index: 0, status: 'missed', mttd_seconds: null,
            expected_detection: {
              type: 'BIOC', plane: 'EDR',
              description: 'Credential extraction tool downloaded and executed',
              detection_id: 'det-1',
            },
            observed: null, evidence_refs: [],
          },
          {
            step_id: 's1', step_index: 0, status: 'missed', mttd_seconds: null,
            expected_detection: {
              type: 'XQL', plane: 'EDR',
              description: 'curl-to-bash pattern with /proc memory access',
              detection_id: 'det-2',
            },
            observed: null, evidence_refs: [],
          },
        ],
      }],
      summary: { total: 2, detected: 0, pending: 0, missed: 2, coverage_pct: 0, mttd: {} },
    },
  }
}

function mockStoryline(runStatus) {
  globalThis.fetch = vi.fn(async () => new Response(
    JSON.stringify(storylineFor(runStatus)),
    { status: 200, headers: { 'content-type': 'application/json' } },
  ))
}

describe('reconcileStatus', () => {
  it('treats failed and aborted runs as unproven', () => {
    expect(isRunUnproven('failed')).toBe(true)
    expect(isRunUnproven('aborted')).toBe(true)
    expect(isRunUnproven('complete')).toBe(false)
    expect(isRunUnproven('running')).toBe(false)
    expect(isRunUnproven(null)).toBe(false)
  })

  it('downgrades a "missed" to "not-executed" when the run failed', () => {
    expect(reconcileStatus('missed', 'failed')).toBe('not-executed')
    expect(reconcileStatus('missed', 'aborted')).toBe('not-executed')
  })

  it('keeps a real miss on a run that actually completed', () => {
    expect(reconcileStatus('missed', 'complete')).toBe('missed')
  })

  it('never downgrades a detection that DID fire before the run died', () => {
    // The signal demonstrably reached the tenant — that evidence survives.
    expect(reconcileStatus('detected', 'failed')).toBe('detected')
  })
})

describe('<DetectionStoryline /> on a failed run', () => {
  afterEach(() => { vi.restoreAllMocks() })

  it('does not accuse the tenant of a detection gap', async () => {
    mockStoryline('failed')
    render(<DetectionStoryline runId="4ee09860" pollIntervalMs={100000} />)
    await screen.findByText(/Credential extraction tool/)

    expect(screen.queryByText(/detection gap/i)).toBeNull()
    expect(screen.getAllByText(/Signal never generated/i)).toHaveLength(2)
  })

  it('refuses to quote a coverage percentage', async () => {
    mockStoryline('failed')
    render(<DetectionStoryline runId="4ee09860" pollIntervalMs={100000} />)
    const kpi = await screen.findByTestId('storyline-coverage-unproven')
    expect(kpi.textContent).toContain('n/a')
    expect(kpi.textContent).toContain('failed')
    expect(kpi.textContent).not.toContain('0%')
  })

  it('explains up front that this is not a verdict on the tenant', async () => {
    mockStoryline('failed')
    render(<DetectionStoryline runId="4ee09860" pollIntervalMs={100000} />)
    const banner = await screen.findByTestId('storyline-unproven-banner')
    expect(banner.textContent).toMatch(/did not complete/i)
    expect(banner.textContent).toMatch(/RUN FAILED/)
  })

  it('labels every un-fired detection "Not executed", not "Missed"', async () => {
    mockStoryline('failed')
    render(<DetectionStoryline runId="4ee09860" pollIntervalMs={100000} />)
    await screen.findByText(/Credential extraction tool/)
    expect(screen.getAllByText('Not executed')).toHaveLength(2)
    expect(screen.queryByText('Missed')).toBeNull()
  })
})

describe('<DetectionStoryline /> on a completed run', () => {
  afterEach(() => { vi.restoreAllMocks() })

  it('still reports a real detection gap as a gap', async () => {
    mockStoryline('complete')
    render(<DetectionStoryline runId="4ee09860" pollIntervalMs={100000} />)
    await screen.findByText(/Credential extraction tool/)

    expect(screen.getAllByText(/No alert reconciled — detection gap/)).toHaveLength(2)
    expect(screen.getAllByText('Missed')).toHaveLength(2)
    expect(screen.queryByTestId('storyline-unproven-banner')).toBeNull()
    expect(screen.queryByTestId('storyline-coverage-unproven')).toBeNull()
  })
})

describe('<RunDetailView /> failure banner', () => {
  beforeEach(() => {
    // The sub-tabs self-fetch; keep them inert so the banner is what we assert on.
    globalThis.fetch = vi.fn(async () => new Response(JSON.stringify({}), {
      status: 200, headers: { 'content-type': 'application/json' },
    }))
  })
  afterEach(() => { vi.restoreAllMocks() })

  const FAILED_RUN = {
    id: 7,
    run_id: '4ee09860-140d-4078-bd20-5179f5dbf7af',
    scenario_id: 'SIM-EDR-001',
    status: 'failed',
    mode: 'pull',
    output: 'step-01 start\nrunuser: may not be used by non-root users\n--- exit_code=1',
  }

  it('surfaces the actual failure reason — which appeared on no surface before', async () => {
    render(<RunDetailView runId={FAILED_RUN.run_id} run={FAILED_RUN} subTab="live" />)
    const banner = await screen.findByTestId('run-failure-banner')
    expect(banner.textContent).toContain('runuser: may not be used by non-root users')
    expect(banner.textContent).toContain('exit_code=1')
    expect(banner.textContent).toMatch(/RUN FAILED/)
  })

  it('says plainly that coverage on this run is not a measurement', async () => {
    render(<RunDetailView runId={FAILED_RUN.run_id} run={FAILED_RUN} subTab="live" />)
    const banner = await screen.findByTestId('run-failure-banner')
    expect(banner.textContent).toMatch(/not a measurement/i)
  })

  it('shows no failure banner for a healthy run', () => {
    render(<RunDetailView runId="r1" run={{ ...FAILED_RUN, status: 'complete' }} subTab="live" />)
    expect(screen.queryByTestId('run-failure-banner')).toBeNull()
  })

  it('handles a failed run that captured no output', async () => {
    render(<RunDetailView runId="r1" run={{ ...FAILED_RUN, output: null }} subTab="live" />)
    const banner = await screen.findByTestId('run-failure-banner')
    expect(banner.textContent).toMatch(/no output captured/i)
  })
})
