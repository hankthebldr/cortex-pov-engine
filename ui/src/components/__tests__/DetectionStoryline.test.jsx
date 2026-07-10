/**
 * Tests for <DetectionStoryline /> — the presenter-grade vertical
 * kill-chain timeline. fetch is mocked; jsdom has no EventSource so the
 * component falls back to interval polling (guarded internally).
 *
 * NOTE: placed under __tests__/ so vitest's `src/ ** /__tests__/ ** ` include
 * glob actually collects it (the scope named a bare src/components/ path,
 * which the runner would skip). Behaviour is identical.
 */
import React from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import DetectionStoryline from '../DetectionStoryline.jsx'

void React

// The exact shape core/engine/detection_storyline.py → build_storyline emits,
// wrapped in the { detection_storyline } envelope core/api/storyline.py returns.
// getStoryline() normalizes this into the flat view-model the component reads —
// so this fixture exercises the real client↔server seam (previously untested).
const STORYLINE_RAW = {
  detection_storyline: {
    run_id: 'run-abc',
    scenario_id: 'SIM-EDR-001',
    run_status: 'complete',
    steps: [
      {
        step_id: 's1',
        step_index: 0,
        step_name: 'Credential dump via LSASS',
        mitre_technique: 'T1003.001',
        detections: [
          {
            step_id: 's1',
            step_index: 0,
            mitre_technique: 'T1003.001',
            ttp_ref: 'TTP-2026-0006',
            expected_detection: {
              type: 'ABIOC',
              plane: 'EDR',
              description: 'BIOC on procdump against lsass',
              detection_id: 'abioc-lsass-procdump',
            },
            observed: {
              alert_name: 'LSASS credential dump',
              alert_id: 'a9f4-21c8-deadbeef',
              technique: 'T1003.001',
              observed_at: '2026-05-20T12:41:45Z',
              deep_link: 'https://tenant.xdr.example.com/alerts/a9f4-21c8-deadbeef',
              severity: 'high',
              host: 'web-01',
            },
            status: 'detected',
            mttd_seconds: 38,
            evidence_refs: [1, 2],
            result_id: 1,
            detection_kind: 'abioc',
          },
        ],
      },
      {
        step_id: 's2',
        step_index: 1,
        step_name: 'Reverse shell callback',
        mitre_technique: 'T1059.004',
        detections: [
          {
            step_id: 's2',
            step_index: 1,
            mitre_technique: 'T1059.004',
            ttp_ref: 'TTP-2026-0011',
            expected_detection: {
              type: 'Correlation',
              plane: 'NDR',
              description: 'Correlation on outbound C2 beacon',
              detection_id: 'CR-C2-0001',
            },
            observed: null,
            status: 'pending',
            mttd_seconds: null,
            evidence_refs: [],
            result_id: 2,
            detection_kind: 'correlation',
          },
        ],
      },
    ],
    summary: {
      total: 2,
      detected: 1,
      pending: 1,
      missed: 0,
      coverage_pct: 50,
      mttd: { count: 1, min: 38, median: 38, p90: 92, max: 38 },
    },
  },
}

function mockFetchOk(payload) {
  return vi.fn(async () => ({
    ok: true,
    status: 200,
    statusText: 'OK',
    json: async () => payload,
  }))
}

function mockFetchErr() {
  return vi.fn(async () => ({
    ok: false,
    status: 500,
    statusText: 'Server Error',
    json: async () => ({ detail: 'boom' }),
  }))
}

describe('<DetectionStoryline />', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('renders an empty state when no runId is supplied', () => {
    render(<DetectionStoryline runId={null} />)
    expect(screen.getByText(/No run selected/i)).toBeInTheDocument()
  })

  it('fetches the storyline and renders header KPIs + step TTPs', async () => {
    vi.stubGlobal('fetch', mockFetchOk(STORYLINE_RAW))
    render(<DetectionStoryline runId="run-abc" scenarioId="SIM-EDR-001" />)

    expect(await screen.findByText('Detection Storyline')).toBeInTheDocument()
    // Coverage KPI
    expect(screen.getByText('50%')).toBeInTheDocument()
    expect(screen.getByText('1/2')).toBeInTheDocument()
    // MTTD p50 formatted
    expect(screen.getByText('38s')).toBeInTheDocument()
    // Step TTPs + names
    expect(screen.getByText('T1003.001')).toBeInTheDocument()
    expect(screen.getByText('Credential dump via LSASS')).toBeInTheDocument()
    expect(screen.getByText('Reverse shell callback')).toBeInTheDocument()
  })

  it('renders the observed Cortex alert with engine badge, MTTD, and tenant deep-link when detected', async () => {
    vi.stubGlobal('fetch', mockFetchOk(STORYLINE_RAW))
    render(<DetectionStoryline runId="run-abc" />)

    // The six-value vocabulary — ABIOC engine badge is surfaced.
    expect(await screen.findByText('ABIOC')).toBeInTheDocument()
    expect(screen.getByText('a9f4-21c8-deadbeef')).toBeInTheDocument()
    expect(screen.getByText(/MTTD 38s/)).toBeInTheDocument()
    expect(screen.getByText('Detected')).toBeInTheDocument()

    const link = screen.getByRole('link', { name: /view in tenant/i })
    expect(link).toHaveAttribute(
      'href',
      'https://tenant.xdr.example.com/alerts/a9f4-21c8-deadbeef',
    )
  })

  it('shows an "awaiting detection" state for pending detections', async () => {
    vi.stubGlobal('fetch', mockFetchOk(STORYLINE_RAW))
    render(<DetectionStoryline runId="run-abc" />)

    expect(await screen.findByText(/awaiting detection/i)).toBeInTheDocument()
    expect(screen.getByText('Awaiting')).toBeInTheDocument()
    // Correlation engine (strongest XSIAM differentiator) is badged.
    expect(screen.getByText('Correlation')).toBeInTheDocument()
  })

  it('fires onOpenEvidence when the evidence affordance is clicked', async () => {
    vi.stubGlobal('fetch', mockFetchOk(STORYLINE_RAW))
    const onOpenEvidence = vi.fn()
    render(<DetectionStoryline runId="run-abc" onOpenEvidence={onOpenEvidence} />)

    const btn = await screen.findByRole('button', { name: /evidence \(2\)/i })
    fireEvent.click(btn)
    expect(onOpenEvidence).toHaveBeenCalledTimes(1)
    expect(onOpenEvidence.mock.calls[0][0].alert_external_id).toBe('a9f4-21c8-deadbeef')
  })

  it('surfaces an error state and calls onError when the fetch fails', async () => {
    vi.stubGlobal('fetch', mockFetchErr())
    const onError = vi.fn()
    render(<DetectionStoryline runId="run-abc" onError={onError} />)

    expect(await screen.findByText(/Could not load storyline/i)).toBeInTheDocument()
    await waitFor(() => expect(onError).toHaveBeenCalled())
    expect(onError.mock.calls[0][0]).toMatch(/boom/)
  })
})
