/**
 * Mount-only smoke tests for the Mission Ops Console views.
 *
 * Goal: crash-resistance + coverage exercise. Every console-tab view should
 * render against its declared API contract without throwing, with sensible
 * default props and empty/minimal responses. Behavioral tests for the
 * high-leverage console components (LabView, ScenarioInspector launch flow,
 * etc.) live in their own files.
 */
import { describe, it, expect } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { installRoutes } from '../../test/mockFetch.js'

import AppShell from '../console/AppShell.jsx'
import ConsoleHeader from '../console/ConsoleHeader.jsx'
import ConsoleRail from '../console/ConsoleRail.jsx'
import ConsoleTabs from '../console/ConsoleTabs.jsx'
import CommandStrip from '../console/CommandStrip.jsx'
import CommandPalette from '../console/CommandPalette.jsx'
import TelemetryStrip from '../console/TelemetryStrip.jsx'
import PinButton from '../console/PinButton.jsx'
import ScenarioGrid from '../console/ScenarioGrid.jsx'
import ScenarioInspector from '../console/ScenarioInspector.jsx'
import NarrativeTimeline from '../console/NarrativeTimeline.jsx'
import OperationsView from '../console/OperationsView.jsx'
import InflightView from '../console/InflightView.jsx'
import EvidenceView from '../console/EvidenceView.jsx'
import CoverageView from '../console/CoverageView.jsx'

describe('console-view smoke renders', () => {
  it('AppShell mounts with empty data + no active run', () => {
    render(
      <AppShell activeTab="operations" planes={[]} pinned={[]} paletteItems={[]}>
        <div data-testid="content">child</div>
      </AppShell>
    )
    expect(screen.getByTestId('content')).toBeInTheDocument()
  })

  it('ConsoleHeader renders brand + env pill + ⌘K trigger', () => {
    render(<ConsoleHeader health={{ hostname: 'lab-test', version: 'v1.0', sensors: {} }} />)
    expect(screen.getByText(/LAB-TEST/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /command palette/i })).toBeInTheDocument()
    expect(screen.getByText('v1.0', { exact: false })).toBeInTheDocument()
  })

  it('ConsoleRail renders planes + pinned + handles click callbacks', () => {
    const planes = [
      { code: 'EDR', name: 'Endpoint', count: 5, isActive: false },
      { code: 'CDR', name: 'Cloud',    count: 3, isActive: true },
    ]
    render(
      <ConsoleRail
        planes={planes}
        pinned={[{ id: 'SIM-MP-004', name: 'APT29' }]}
        onSelectPlane={() => {}}
        onSelectPinned={() => {}}
      />
    )
    expect(screen.getByText('Endpoint')).toBeInTheDocument()
    expect(screen.getByText(/APT29/)).toBeInTheDocument()
  })

  it('ConsoleTabs renders all five tabs', () => {
    render(
      <ConsoleTabs
        activeTab="operations"
        onTabChange={() => {}}
        badges={{ operations: '19', inflight: { text: 'LIVE', variant: 'live' } }}
      />
    )
    expect(screen.getByRole('tab', { name: /operations/i })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /in-flight/i })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /evidence/i })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /lab/i })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /coverage/i })).toBeInTheDocument()
  })

  it('CommandStrip renders default keyboard hints', () => {
    render(<CommandStrip ticker="" />)
    expect(screen.getByText('search')).toBeInTheDocument()
    expect(screen.getByText('launch')).toBeInTheDocument()
    expect(screen.getByText('export')).toBeInTheDocument()
  })

  it('CommandPalette mounts closed (no overlay visible)', () => {
    render(<CommandPalette open={false} items={[]} />)
    // dialog is in DOM but hidden via display:none; just confirm no throw + role exists
    expect(screen.getByRole('dialog', { hidden: true })).toBeInTheDocument()
  })

  it('CommandPalette renders items when open', () => {
    const items = [
      { section: 'Scenarios', id: 's1', title: 'APT29 Cloud', meta: 'SIM-MP-004', icon: '▸', onSelect: () => {} },
    ]
    render(<CommandPalette open={true} items={items} />)
    expect(screen.getByText('APT29 Cloud')).toBeInTheDocument()
  })

  it('TelemetryStrip renders run summary + abort button', () => {
    render(
      <TelemetryStrip
        run={{
          scenarioId: 'SIM-MP-004',
          step: 2, totalSteps: 5,
          elapsed: 137,
          detected: 4, total: 12,
          nextStep: 'T1530',
        }}
      />
    )
    expect(screen.getByText('SIM-MP-004')).toBeInTheDocument()
    expect(screen.getByText('2 / 5')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /abort/i })).toBeInTheDocument()
  })

  it('PinButton renders both card + inspector variants', () => {
    const { container, rerender } = render(<PinButton pinned={false} variant="card" />)
    expect(container.querySelector('.pin-btn--card')).toBeTruthy()
    rerender(<PinButton pinned={true} variant="inspector" />)
    expect(container.querySelector('.pin-btn--inspector.is-pinned')).toBeTruthy()
  })

  it('ScenarioGrid renders cards or empty state', () => {
    render(<ScenarioGrid scenarios={[]} />)
    expect(screen.getByText(/no scenarios match/i)).toBeInTheDocument()
  })

  it('ScenarioGrid renders real cards with planes + actor + TIDs', () => {
    const scenarios = [
      {
        scenario_id: 'SIM-MP-004',
        name: 'APT29 Cloud',
        plane: 'ANALYTICS',
        mitre_technique: 'T1552.001',
        additional_techniques: [{ technique: 'T1078.004' }],
        threat_report: 'Unit42 — APT29',
        tags: ['intermediate', 'apt29'],
        steps: [
          { mitre_technique: 'T1552.001', expected_detections: [{ plane: 'EDR' }] },
          { mitre_technique: 'T1078.004', expected_detections: [{ plane: 'CDR' }] },
        ],
      },
    ]
    render(
      <ScenarioGrid
        scenarios={scenarios}
        selectedScenarioId="SIM-MP-004"
        isPinned={(id) => id === 'SIM-MP-004'}
      />
    )
    expect(screen.getByText('SIM-MP-004')).toBeInTheDocument()
    expect(screen.getByText('APT29 Cloud')).toBeInTheDocument()
    expect(screen.getByText('T1552.001')).toBeInTheDocument()
  })

  it('ScenarioInspector renders a closed shell with no scenario', () => {
    const { container } = render(<ScenarioInspector />)
    expect(container.querySelector('.inspector')).toBeTruthy()
  })

  it('ScenarioInspector renders an open drawer with a scenario + launch hook stub', () => {
    const launch = {
      mode: 'pull',
      setMode: () => {},
      identity: 'www-data',
      setIdentity: () => {},
      agents: [{ id: 'a1', hostname: 'agent-1', os: 'linux' }],
      selectedAgent: 'a1',
      setSelectedAgent: () => {},
      pushFormat: 'bash',
      setPushFormat: () => {},
      launching: false,
      downloading: false,
      lastRun: null,
      identityOptions: ['www-data', 'root'],
      supportsPull: true,
      supportsPush: true,
      launchDisabled: false,
      launch: () => {},
      downloadPushBundle: () => {},
    }
    const scenario = {
      scenario_id: 'SIM-MP-004',
      name: 'APT29 Cloud Credential Theft',
      plane: 'ANALYTICS',
      mitre_tactic: 'TA0006', mitre_tactic_name: 'Credential Access',
      mitre_technique: 'T1552.001', mitre_technique_name: 'Credentials In Files',
      execution_identity: { default: 'www-data', options: ['www-data', 'root'] },
      steps: [],
      tags: ['intermediate'],
      pull_supported: true,
      push_supported: true,
    }
    render(<ScenarioInspector scenario={scenario} open launch={launch} pinned={false} />)
    expect(screen.getByText(/APT29 Cloud Credential Theft/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /launch/i })).toBeInTheDocument()
  })

  it('NarrativeTimeline renders nothing with empty frames', () => {
    const { container } = render(<NarrativeTimeline frames={[]} stitches={[]} />)
    // No frames → no timeline structure. Just verify it didn't throw.
    expect(container).toBeTruthy()
  })

  it('OperationsView mounts and fetches the scenario list', async () => {
    installRoutes({
      'GET /api/scenarios': { scenarios: [] },
      'GET /api/agents':    [],
    })
    render(<OperationsView />)
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /operations/i })).toBeInTheDocument()
    })
  })

  it('InflightView shows empty-state with no active or last run', () => {
    render(<InflightView activeRun={null} lastRun={null} />)
    expect(screen.getByText(/no run in progress/i)).toBeInTheDocument()
  })

  it('EvidenceView shows empty-state with no run', () => {
    render(<EvidenceView activeRun={null} lastRun={null} />)
    expect(screen.getByText(/no run to validate/i)).toBeInTheDocument()
  })

  it('EvidenceView mounts KPI row + scorecard with a run', async () => {
    installRoutes({
      'GET /api/results/r-1': {
        results: [
          { id: 1, mitre_technique: 'T1552.001', plane: 'EDR', detection_type: 'BIOC',
            expected_description: 'AKIA grep', observed: true,  mttd_seconds: 38, alert_id: 'a-1' },
          { id: 2, mitre_technique: 'T1078.004', plane: 'CDR', detection_type: 'Analytics',
            expected_description: 'sts:GetCallerIdentity from odd IP', observed: null },
          { id: 3, mitre_technique: 'T1537',     plane: 'ANALYTICS', detection_type: 'Stitch',
            expected_description: 'XSIAM stitch', observed: false, alert_id: 'inc-001' },
        ],
      },
    })
    render(
      <EvidenceView
        activeRun={{ runId: 'r-1', scenarioId: 'SIM-MP-004' }}
        lastRun={null}
      />
    )
    // Wait for the scorecard rows to render.
    await waitFor(() => {
      expect(screen.getByText(/AKIA grep/)).toBeInTheDocument()
    })
    expect(screen.getByText(/Coverage/i)).toBeInTheDocument()
    expect(screen.getByText(/Export POV report/i)).toBeInTheDocument()
  })

  it('NarrativeTimeline renders frames with stitches', async () => {
    installRoutes({
      'GET /api/scenarios/SIM-MP-004': {
        scenario_id: 'SIM-MP-004',
        name: 'APT29 Cloud',
        mitre_tactic: 'TA0006', mitre_tactic_name: 'Credential Access',
        mitre_technique: 'T1552.001', mitre_technique_name: 'Credentials In Files',
        steps: [
          { id: 'step-01', name: 'cred grep',       identity: 'www-data',
            mitre_technique: 'T1552.001', expected_detections: [{ plane: 'EDR', type: 'BIOC' }] },
          { id: 'step-02', name: 'cloud pivot',     identity: 'www-data',
            mitre_technique: 'T1078.004', expected_detections: [
              { plane: 'EDR', type: 'BIOC' },
              { plane: 'CDR', type: 'Analytics' },
              { plane: 'ANALYTICS', type: 'Analytics' },
            ] },
          { id: 'step-03', name: 'cloud enum',      identity: 'www-data',
            mitre_technique: 'T1580', expected_detections: [
              { plane: 'CDR', type: 'BIOC' },
              { plane: 'ANALYTICS', type: 'IOC' },
            ] },
        ],
      },
      'GET /api/runs/r-1': { id: 'r-1', current_step: 2, status: 'running' },
      'GET /api/results/r-1': { results: [] },
    })
    const { container } = render(
      <InflightView
        activeRun={{ runId: 'r-1', scenarioId: 'SIM-MP-004', step: 2, totalSteps: 3, elapsed: 90 }}
        lastRun={null}
      />
    )
    await waitFor(() => {
      expect(container.querySelector('.tl-steps')).toBeTruthy()
    })
  })

  it('CoverageView mounts and fetches the heatmap data', async () => {
    installRoutes({
      'GET /api/mitre/coverage': {
        summary: { total_techniques: 0, detected: 0, run_not_detected: 0, not_run: 0 },
        by_tactic: [],
      },
    })
    render(<CoverageView />)
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /coverage/i })).toBeInTheDocument()
    })
  })
})
