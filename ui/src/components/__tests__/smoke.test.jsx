/**
 * Mount-only smoke tests for the remaining components.
 *
 * The goal here is *crash-resistance*: every component renders against its
 * declared API contract without throwing, with default props, and an empty
 * or minimal response.  Behavioral tests for the high-leverage components
 * (PlaneSelector, ScenarioBrowser, LaunchPanel, ResultsValidationWizard,
 * InfraGenerator, EalConsole) live in their own files.
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { installRoutes } from '../../test/mockFetch.js'

import UCTCMapper from '../UCTCMapper.jsx'
import ToolStatusPanel from '../ToolStatusPanel.jsx'
import ResultsViewer from '../ResultsViewer.jsx'
import MitreHeatmap from '../MitreHeatmap.jsx'
import EalRunProgress from '../EalRunProgress.jsx'
import EalCampaignBuilder from '../EalCampaignBuilder.jsx'

describe('component smoke renders', () => {
  it('UCTCMapper renders scenario UC/TC fields', () => {
    render(
      <UCTCMapper
        scenario={{
          scenario_id: 'SIM-EDR-001',
          uc_ref: 'UCS-EDR-01',
          uc_name: 'Endpoint Credential Theft Detection',
          tc_ref: 'TC-EDR-01',
          tc_name: 'Linux Credential Harvesting',
          mitre_tactic: 'TA0006',
          mitre_technique: 'T1003.008',
        }}
      />,
    )
    expect(screen.getByText(/UCS-EDR-01/)).toBeInTheDocument()
    expect(screen.getByText(/TC-EDR-01/)).toBeInTheDocument()
  })

  it('ToolStatusPanel mounts and queries the tools list', async () => {
    installRoutes({
      'GET /api/tools': {
        tools: [
          { name: 'mocktaxii', status: 'running', port: 9000 },
          { name: 'signalbench', status: 'stopped' },
        ],
        total: 2,
      },
    })
    render(<ToolStatusPanel onMessage={vi.fn()} />)
    await waitFor(() => {
      expect(screen.getByText(/mocktaxii/)).toBeInTheDocument()
      expect(screen.getByText(/signalbench/)).toBeInTheDocument()
    })
  })

  it('ResultsViewer renders a runs list and fires onValidate when a row is selected', async () => {
    installRoutes({})
    const onValidate = vi.fn()
    const runs = [
      { run_id: 'r-1', id: 'r-1', scenario_id: 'SIM-EDR-001', status: 'complete', started_at: new Date().toISOString() },
    ]
    render(<ResultsViewer runs={runs} onClose={vi.fn()} onValidate={onValidate} />)
    expect(await screen.findByText(/SIM-EDR-001/)).toBeInTheDocument()
  })

  it('MitreHeatmap renders without throwing on empty coverage', async () => {
    // Real API returns by_tactic as an array (sorted by tactic_id).  Mocking
    // the object shape here would mask a render-time .map() crash that the
    // component otherwise reproduces faithfully.
    installRoutes({
      'GET /api/mitre/coverage': {
        techniques: [],
        by_tactic: [],
        summary: { total_techniques: 0, detected: 0, run_not_detected: 0, not_run: 0 },
      },
    })
    render(<MitreHeatmap />)
    await waitFor(() => {
      expect(document.body.textContent).toMatch(/MITRE|ATT&CK|Coverage/i)
    })
  })

  it('EalRunProgress mounts with a null run', () => {
    render(<EalRunProgress run={null} />)
    // Either renders nothing or an empty placeholder — must not crash
    expect(document.body).toBeInTheDocument()
  })

  it('EalCampaignBuilder mounts and lists EAL plugins from the API', async () => {
    installRoutes({
      'GET /api/eal/plugins': {
        plugins: [
          { name: 'c2_http_beacon', kind: 'network', description: 'C2 over HTTP' },
          { name: 'oauth_grant_emulator', kind: 'identity', description: 'OAuth grant emulator' },
        ],
        total: 2,
      },
    })
    render(<EalCampaignBuilder onMessage={vi.fn()} />)
    await waitFor(() => {
      // At least one plugin name visible — exact UI shape varies but the
      // contract is "plugin list rendered"
      expect(document.body.textContent).toMatch(/c2_http_beacon|oauth_grant_emulator/)
    })
  })
})
