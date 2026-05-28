/**
 * Smoke + interaction tests for the TTP browser view.
 *
 * Mirrors the ToolAdapterCatalog test shape — fixture API responses
 * for list + detail, then render + filter + select.
 */
import React from 'react'
import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import TtpBrowserView from '../console/TtpBrowserView.jsx'
import { installRoutes } from '../../test/mockFetch.js'

void React

// NOTE: The backend's /api/ttps list endpoint sorts by `id` ascending
// before returning — see ``core/api/ttps.py``'s list_ttps. The fixture
// matches that contract so test assertions exercise the production
// render order, not an arbitrary insertion order.
const fixtureList = {
  ttps: [
    {
      id:                'TTP-2026-0002',
      name:              'LSASS Memory Credential Dump',
      status:            'active',
      summary:           'Dump credentials from LSASS process memory using mimikatz, procdump, or comsvcs.dll.',
      tags:              ['actor-multiple', 'malware-mimikatz', 'severity-critical'],
      platforms:         ['windows'],
      simulation_class:  'endpoint',
      destructive:       true,
      technique_ids:     ['T1003.001'],
      tactic_ids:        ['TA0006'],
      kill_chain_phase:  'credential-access',
      actor_names:       ['Ignoble Scorpius'],
      detection_counts:  { iocs: 0, biocs: 4, xql_queries: 2, correlation_rules: 1, analytics_modules: 0 },
      panw_products:     ['cortex-xdr'],
    },
    {
      id:                'TTP-2026-0003',
      name:              'AWS IAM Key Abuse → S3 Exfil',
      status:            'active',
      summary:           'Compromised IAM key drives enumeration + bulk S3 GetObject.',
      tags:              [],
      platforms:         ['linux'],
      simulation_class:  'cloud',
      destructive:       false,
      technique_ids:     ['T1078.004', 'T1580'],
      tactic_ids:        ['TA0001', 'TA0007'],
      kill_chain_phase:  'actions-on-objectives',
      actor_names:       [],
      detection_counts:  { iocs: 0, biocs: 2, xql_queries: 2, correlation_rules: 1, analytics_modules: 0 },
      panw_products:     ['cortex-cloud'],
    },
    {
      id:                'TTP-2026-0004',
      name:              'DCSync — Domain Replication Abuse',
      status:            'active',
      summary:           'Replicate Active Directory password hashes via MS-DRSR GetNCChanges.',
      tags:              ['vector-domain-replication'],
      platforms:         ['windows'],
      simulation_class:  'identity',
      destructive:       true,
      technique_ids:     ['T1003.006'],
      tactic_ids:        ['TA0006'],
      kill_chain_phase:  'actions-on-objectives',
      actor_names:       [],
      detection_counts:  { iocs: 0, biocs: 3, xql_queries: 3, correlation_rules: 2, analytics_modules: 0 },
      panw_products:     ['cortex-xdr'],
    },
  ],
  total: 3,
}

const fixtureDetailDcsync = {
  id:           'TTP-2026-0004',
  status:       'active',
  metadata: {
    tags: ['vector-domain-replication', 'malware-mimikatz'],
  },
  identity: {
    name:    'DCSync — Domain Replication Abuse',
    summary: 'Replicate AD password hashes via MS-DRSR GetNCChanges.',
  },
  threat_context: {
    actors: [
      { name: 'Muddled Libra', aliases: ['Scattered Spider'], mitre_group_id: 'G1015' },
    ],
  },
  mitre_attack: {
    techniques: [
      { technique_id: 'T1003', subtechnique_id: 'T1003.006',
        name: 'OS Credential Dumping: DCSync',
        tactic_ids: ['TA0006'] },
    ],
    kill_chain_phase: 'actions-on-objectives',
  },
  detections: {
    iocs: [],
    biocs: [
      {
        name:         'DRSUAPI Replication From Non-DC Host',
        description:  'A non-DC host issues a DRSUAPI RPC call to a DC.',
        severity:     'high',
        detection_id: 'BIOC-CRED-DCSYNC-001',
        logic:        'preset = xdr_data\n| filter rpc_interface_uuid = "e3514235-..."',
      },
      { name: 'b' },
      { name: 'c' },
    ],
    xql_queries: [
      {
        name:    'Confirm DRSUAPI replication seen from non-DC',
        purpose: 'validation',
        query:   'preset = xdr_data\n| filter event_type = ENUM.NETWORK',
      },
    ],
    correlation_rules: [],
    analytics_modules: [],
  },
  panw_mapping: {
    products: [{ module: 'cortex-xdr', submodule: 'identity-threat-module' }],
  },
  references: [],
  referenced_by_adapters: [
    { adapter_id: 'TOOL-MIMIKATZ',  name: 'Mimikatz',   tier: 3,
      category: 'identity-credential', safety_class: 'dual-use-lab-only' },
    { adapter_id: 'TOOL-RUBEUS',    name: 'Rubeus',     tier: 3,
      category: 'identity-credential', safety_class: 'dual-use-lab-only' },
    { adapter_id: 'TOOL-BLOODHOUND', name: 'BloodHound', tier: 3,
      category: 'identity-credential', safety_class: 'dual-use-lab-only' },
  ],
}

describe('<TtpBrowserView />', () => {
  it('renders intro + stats derived from the API payload', async () => {
    installRoutes({ 'GET /api/ttps': fixtureList })
    render(<TtpBrowserView />)
    await waitFor(() => {
      expect(screen.getByText(/Browser over the/)).toBeInTheDocument()
    })
    // 3 TTPs in the fixture
    expect(screen.getAllByText('3').length).toBeGreaterThan(0)
  })

  it('renders one card per TTP with id + name + summary', async () => {
    installRoutes({ 'GET /api/ttps': fixtureList })
    render(<TtpBrowserView />)
    await waitFor(() => {
      expect(screen.getByTestId('ttp-card-TTP-2026-0002')).toBeInTheDocument()
    })
    expect(screen.getByTestId('ttp-card-TTP-2026-0004')).toBeInTheDocument()
    expect(screen.getByTestId('ttp-card-TTP-2026-0003')).toBeInTheDocument()
    // Identity name surfaces in each card
    expect(screen.getByText('LSASS Memory Credential Dump')).toBeInTheDocument()
    expect(screen.getByText(/DCSync.*Domain Replication/)).toBeInTheDocument()
  })

  it('cards are sorted by id alphabetically (stable rendering)', async () => {
    installRoutes({ 'GET /api/ttps': fixtureList })
    const { container } = render(<TtpBrowserView />)
    await waitFor(() => {
      expect(screen.getByTestId('ttp-card-TTP-2026-0002')).toBeInTheDocument()
    })
    const cards = Array.from(container.querySelectorAll('[data-testid^="ttp-card-"]'))
    const ids = cards.map((c) => c.getAttribute('data-testid').replace('ttp-card-', ''))
    expect(ids).toEqual([...ids].sort())
  })

  it('derives filter chips from the loaded corpus', async () => {
    installRoutes({ 'GET /api/ttps': fixtureList })
    render(<TtpBrowserView />)
    await waitFor(() => {
      expect(screen.getByTestId('ttp-card-TTP-2026-0002')).toBeInTheDocument()
    })
    // status / tactic / platform chips
    expect(screen.getByRole('button', { name: 'active' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'TA0006' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'TA0001' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'windows' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'linux' })).toBeInTheDocument()
  })

  it('hides the card grid when filters exclude everything', async () => {
    installRoutes({ 'GET /api/ttps': fixtureList })
    render(<TtpBrowserView />)
    await waitFor(() => expect(screen.getByTestId('ttp-card-TTP-2026-0002')).toBeInTheDocument())
    // No card has both TA0001 (initial-access) AND windows in the fixture
    fireEvent.click(screen.getByRole('button', { name: 'TA0001' }))
    fireEvent.click(screen.getByRole('button', { name: 'windows' }))
    expect(screen.getByText(/no TTPs match the current filters/i)).toBeInTheDocument()
  })

  it('free-text search narrows the grid by name', async () => {
    installRoutes({ 'GET /api/ttps': fixtureList })
    render(<TtpBrowserView />)
    await waitFor(() => expect(screen.getByTestId('ttp-card-TTP-2026-0002')).toBeInTheDocument())
    fireEvent.change(screen.getByTestId('ttp-search'), { target: { value: 'dcsync' } })
    // Only TTP-2026-0004 (DCSync) matches
    expect(screen.getByTestId('ttp-card-TTP-2026-0004')).toBeInTheDocument()
    expect(screen.queryByTestId('ttp-card-TTP-2026-0002')).not.toBeInTheDocument()
    expect(screen.queryByTestId('ttp-card-TTP-2026-0003')).not.toBeInTheDocument()
  })

  it('search matches tags and is tokenised with AND semantics', async () => {
    installRoutes({ 'GET /api/ttps': fixtureList })
    render(<TtpBrowserView />)
    await waitFor(() => expect(screen.getByTestId('ttp-card-TTP-2026-0002')).toBeInTheDocument())
    // 'mimikatz' tag lives on TTP-2026-0002
    fireEvent.change(screen.getByTestId('ttp-search'), { target: { value: 'mimikatz' } })
    expect(screen.getByTestId('ttp-card-TTP-2026-0002')).toBeInTheDocument()
    expect(screen.queryByTestId('ttp-card-TTP-2026-0004')).not.toBeInTheDocument()
    // Two tokens that don't co-occur on any card → empty
    fireEvent.change(screen.getByTestId('ttp-search'), { target: { value: 'mimikatz dcsync' } })
    expect(screen.getByText(/no TTPs match the current filters/i)).toBeInTheDocument()
  })

  it('search matches technique ids', async () => {
    installRoutes({ 'GET /api/ttps': fixtureList })
    render(<TtpBrowserView />)
    await waitFor(() => expect(screen.getByTestId('ttp-card-TTP-2026-0002')).toBeInTheDocument())
    fireEvent.change(screen.getByTestId('ttp-search'), { target: { value: 'T1003.006' } })
    expect(screen.getByTestId('ttp-card-TTP-2026-0004')).toBeInTheDocument()
    expect(screen.queryByTestId('ttp-card-TTP-2026-0002')).not.toBeInTheDocument()
  })

  it('Clear button resets search + chip filters', async () => {
    installRoutes({ 'GET /api/ttps': fixtureList })
    render(<TtpBrowserView />)
    await waitFor(() => expect(screen.getByTestId('ttp-card-TTP-2026-0002')).toBeInTheDocument())
    fireEvent.change(screen.getByTestId('ttp-search'), { target: { value: 'dcsync' } })
    expect(screen.queryByTestId('ttp-card-TTP-2026-0002')).not.toBeInTheDocument()
    fireEvent.click(screen.getByTestId('ttp-clear-filters'))
    // All cards visible again
    expect(screen.getByTestId('ttp-card-TTP-2026-0002')).toBeInTheDocument()
    expect(screen.getByTestId('ttp-card-TTP-2026-0003')).toBeInTheDocument()
    expect(screen.getByTestId('ttp-card-TTP-2026-0004')).toBeInTheDocument()
  })

  it('Clear button is hidden when no filters are active', async () => {
    installRoutes({ 'GET /api/ttps': fixtureList })
    render(<TtpBrowserView />)
    await waitFor(() => expect(screen.getByTestId('ttp-card-TTP-2026-0002')).toBeInTheDocument())
    expect(screen.queryByTestId('ttp-clear-filters')).not.toBeInTheDocument()
  })

  it('clicking a card opens detail with summary + adapter cross-refs', async () => {
    installRoutes({
      'GET /api/ttps':                fixtureList,
      'GET /api/ttps/TTP-2026-0004':  fixtureDetailDcsync,
    })
    render(<TtpBrowserView />)
    await waitFor(() => expect(screen.getByTestId('ttp-card-TTP-2026-0004')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('ttp-card-TTP-2026-0004'))
    await waitFor(() => expect(screen.getByTestId('ttp-detail')).toBeInTheDocument())
    // Reverse cross-link surfaces each adapter
    expect(screen.getByTestId('ttp-adapter-ref-TOOL-MIMIKATZ')).toBeInTheDocument()
    expect(screen.getByTestId('ttp-adapter-ref-TOOL-RUBEUS')).toBeInTheDocument()
    expect(screen.getByTestId('ttp-adapter-ref-TOOL-BLOODHOUND')).toBeInTheDocument()
  })

  it('opens detail automatically when initialTtpId is supplied', async () => {
    // This is the path CoverageView triggers when the cortex:navigate-ttp
    // event fires from a Tool Adapter detail chip click.
    installRoutes({
      'GET /api/ttps':                fixtureList,
      'GET /api/ttps/TTP-2026-0004':  fixtureDetailDcsync,
    })
    render(<TtpBrowserView initialTtpId="TTP-2026-0004" />)
    await waitFor(() => expect(screen.getByTestId('ttp-detail')).toBeInTheDocument())
    expect(screen.getByTestId('ttp-adapter-ref-TOOL-MIMIKATZ')).toBeInTheDocument()
  })

  it('handles list load failure with a visible error banner', async () => {
    installRoutes({})
    render(<TtpBrowserView />)
    await waitFor(() => {
      expect(screen.getByText(/no mock for|Failed to load/i)).toBeInTheDocument()
    })
  })

  // ── Detection accordion (XQL/BIOC body reveal + copy) ──────────────

  it('detection accordion is collapsed by default and reveals body on click', async () => {
    installRoutes({
      'GET /api/ttps':                fixtureList,
      'GET /api/ttps/TTP-2026-0004':  fixtureDetailDcsync,
    })
    render(<TtpBrowserView initialTtpId="TTP-2026-0004" />)
    await waitFor(() => expect(screen.getByTestId('ttp-detail')).toBeInTheDocument())

    // Body is not visible before expand
    expect(screen.queryByText(/rpc_interface_uuid/)).not.toBeInTheDocument()

    // Expand the first BIOC
    fireEvent.click(screen.getByTestId('ttp-det-biocs-0'))

    // XQL body now visible
    expect(screen.getByText(/rpc_interface_uuid/)).toBeInTheDocument()
  })

  it('renders detection name + severity + detection_id chip in the head', async () => {
    installRoutes({
      'GET /api/ttps':                fixtureList,
      'GET /api/ttps/TTP-2026-0004':  fixtureDetailDcsync,
    })
    render(<TtpBrowserView initialTtpId="TTP-2026-0004" />)
    await waitFor(() => expect(screen.getByTestId('ttp-detail')).toBeInTheDocument())
    expect(screen.getByText('DRSUAPI Replication From Non-DC Host')).toBeInTheDocument()
    expect(screen.getByText('high')).toBeInTheDocument()
    expect(screen.getByText('BIOC-CRED-DCSYNC-001')).toBeInTheDocument()
  })

  it('copy button writes the detection body to clipboard', async () => {
    const writeText = vi.fn().mockResolvedValue()
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    })

    installRoutes({
      'GET /api/ttps':                fixtureList,
      'GET /api/ttps/TTP-2026-0004':  fixtureDetailDcsync,
    })
    render(<TtpBrowserView initialTtpId="TTP-2026-0004" />)
    await waitFor(() => expect(screen.getByTestId('ttp-detail')).toBeInTheDocument())

    // Expand the first XQL query and copy
    fireEvent.click(screen.getByTestId('ttp-det-xql_queries-0'))
    fireEvent.click(screen.getByTestId('ttp-det-copy-xql_queries-0'))

    expect(writeText).toHaveBeenCalledTimes(1)
    expect(writeText).toHaveBeenCalledWith(
      'preset = xdr_data\n| filter event_type = ENUM.NETWORK',
    )
  })

  // ── Run history (per-TTP run rollup) ───────────────────────────────

  it('renders the run history table with coverage chips + MTTD', async () => {
    installRoutes({
      'GET /api/ttps':                       fixtureList,
      'GET /api/ttps/TTP-2026-0004':         fixtureDetailDcsync,
      'GET /api/ttps/TTP-2026-0004/runs': {
        ttp_id: 'TTP-2026-0004',
        runs: [
          {
            run_id: 'r-dcsync-1', scenario_id: 'SIM-ITDR-002',
            run_status: 'complete', started_at: '2026-05-23T15:00:00Z',
            expected: 2, observed: 2, min_mttd_seconds: 30,
            detection_ids: ['BIOC-CRED-DCSYNC-001'],
          },
          {
            run_id: 'r-dcsync-2', scenario_id: 'SIM-ITDR-002',
            run_status: 'complete', started_at: '2026-05-22T10:00:00Z',
            expected: 2, observed: 1, min_mttd_seconds: 600,
            detection_ids: ['BIOC-CRED-DCSYNC-001'],
          },
        ],
        total: 2,
      },
    })
    render(<TtpBrowserView initialTtpId="TTP-2026-0004" />)
    await waitFor(() => expect(screen.getByTestId('ttp-runs-table')).toBeInTheDocument())
    expect(screen.getByTestId('ttp-run-r-dcsync-1')).toBeInTheDocument()
    expect(screen.getByTestId('ttp-run-r-dcsync-2')).toBeInTheDocument()
    // Coverage chip — full hit on run 1, partial on run 2
    expect(screen.getByText('2/2')).toBeInTheDocument()
    expect(screen.getByText('1/2')).toBeInTheDocument()
    // MTTD — 30s formatted as seconds, 600s formatted as 10m
    expect(screen.getByText('30s')).toBeInTheDocument()
    expect(screen.getByText('10m')).toBeInTheDocument()
  })

  it('clicking a run row dispatches cortex:navigate-run with the run_id', async () => {
    installRoutes({
      'GET /api/ttps':                       fixtureList,
      'GET /api/ttps/TTP-2026-0004':         fixtureDetailDcsync,
      'GET /api/ttps/TTP-2026-0004/runs': {
        ttp_id: 'TTP-2026-0004',
        runs: [
          {
            run_id: 'r-dcsync-1', scenario_id: 'SIM-ITDR-002',
            run_status: 'complete', started_at: '2026-05-23T15:00:00Z',
            expected: 2, observed: 2, min_mttd_seconds: 30,
            detection_ids: [],
          },
        ],
        total: 1,
      },
    })

    const received = []
    const listener = (e) => received.push(e.detail?.runId)
    window.addEventListener('cortex:navigate-run', listener)

    try {
      render(<TtpBrowserView initialTtpId="TTP-2026-0004" />)
      await waitFor(() => expect(screen.getByTestId('ttp-run-r-dcsync-1')).toBeInTheDocument())
      fireEvent.click(screen.getByTestId('ttp-run-r-dcsync-1'))
      expect(received).toEqual(['r-dcsync-1'])
    } finally {
      window.removeEventListener('cortex:navigate-run', listener)
    }
  })

  it('detail panel exposes an Export ATT&CK layer button when techniques exist', async () => {
    installRoutes({
      'GET /api/ttps':                fixtureList,
      'GET /api/ttps/TTP-2026-0004':  fixtureDetailDcsync,
    })
    render(<TtpBrowserView initialTtpId="TTP-2026-0004" />)
    await waitFor(() => expect(screen.getByTestId('ttp-detail')).toBeInTheDocument())
    expect(screen.getByTestId('ttp-export-navigator')).toBeInTheDocument()
  })

  it('clicking Export ATT&CK layer triggers a layer download', async () => {
    installRoutes({
      'GET /api/ttps':                fixtureList,
      'GET /api/ttps/TTP-2026-0004':  fixtureDetailDcsync,
    })
    // Stub the download plumbing — jsdom has no real anchor download.
    const createEl = document.createElement.bind(document)
    const clicks = []
    vi.spyOn(document, 'createElement').mockImplementation((tag) => {
      const el = createEl(tag)
      if (tag === 'a') {
        el.click = () => clicks.push(el.download)
      }
      return el
    })
    const origCreate = URL.createObjectURL
    const origRevoke = URL.revokeObjectURL
    URL.createObjectURL = vi.fn(() => 'blob:stub')
    URL.revokeObjectURL = vi.fn()

    try {
      render(<TtpBrowserView initialTtpId="TTP-2026-0004" />)
      await waitFor(() => expect(screen.getByTestId('ttp-export-navigator')).toBeInTheDocument())
      fireEvent.click(screen.getByTestId('ttp-export-navigator'))
      expect(clicks).toHaveLength(1)
      expect(clicks[0]).toBe('cortexsim-ttp-2026-0004-navigator.json')
    } finally {
      document.createElement.mockRestore()
      URL.createObjectURL = origCreate
      URL.revokeObjectURL = origRevoke
    }
  })

  // ── "Launch all" — scenarios-by-TTP action loop (issue #56) ──────

  it('detail panel exposes a Launch all… button', async () => {
    installRoutes({
      'GET /api/ttps':                fixtureList,
      'GET /api/ttps/TTP-2026-0004':  fixtureDetailDcsync,
    })
    render(<TtpBrowserView initialTtpId="TTP-2026-0004" />)
    await waitFor(() => expect(screen.getByTestId('ttp-detail')).toBeInTheDocument())
    expect(screen.getByTestId('ttp-launch-all')).toBeInTheDocument()
  })

  it('clicking Launch all… opens the modal and loads matching scenarios', async () => {
    installRoutes({
      'GET /api/ttps':                fixtureList,
      'GET /api/ttps/TTP-2026-0004':  fixtureDetailDcsync,
      'GET /api/scenarios': [
        { scenario_id: 'SIM-ITDR-002', name: 'DCSync chain', plane: 'ITDR', mitre_technique: 'T1003.006' },
        { scenario_id: 'SIM-EDR-007',  name: 'mimikatz dcsync', plane: 'EDR',  mitre_technique: 'T1003.006' },
      ],
    })
    render(<TtpBrowserView initialTtpId="TTP-2026-0004" />)
    await waitFor(() => expect(screen.getByTestId('ttp-launch-all')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('ttp-launch-all'))
    await waitFor(() => expect(screen.getByTestId('ttp-launcher-modal')).toBeInTheDocument())
    expect(screen.getByTestId('ttp-launcher-row-SIM-ITDR-002')).toBeInTheDocument()
    expect(screen.getByTestId('ttp-launcher-row-SIM-EDR-007')).toBeInTheDocument()
    expect(screen.getByText('2 of 2 selected')).toBeInTheDocument()
  })

  it('confirming Launch fires N POSTs and navigates to the first run', async () => {
    const runCalls = []
    installRoutes({
      'GET /api/ttps':                fixtureList,
      'GET /api/ttps/TTP-2026-0004':  fixtureDetailDcsync,
      'GET /api/scenarios': [
        { scenario_id: 'SIM-ITDR-002', name: 'DCSync chain', plane: 'ITDR', mitre_technique: 'T1003.006' },
        { scenario_id: 'SIM-EDR-007',  name: 'mimikatz dcsync', plane: 'EDR',  mitre_technique: 'T1003.006' },
      ],
      'POST /api/run': (_url, init) => {
        const body = JSON.parse(init.body || '{}')
        runCalls.push(body)
        return { run_id: `r-${body.scenario_id}` }
      },
    })
    const navEvents = []
    const listener = (e) => navEvents.push(e.detail?.runId)
    window.addEventListener('cortex:navigate-run', listener)

    try {
      render(<TtpBrowserView initialTtpId="TTP-2026-0004" />)
      await waitFor(() => expect(screen.getByTestId('ttp-launch-all')).toBeInTheDocument())
      fireEvent.click(screen.getByTestId('ttp-launch-all'))
      await waitFor(() => expect(screen.getByTestId('ttp-launcher-modal')).toBeInTheDocument())
      // Default mode = push, all scenarios pre-selected
      fireEvent.click(screen.getByTestId('ttp-launcher-confirm'))
      await waitFor(() => expect(screen.getByTestId('ttp-launcher-summary')).toBeInTheDocument())
      expect(runCalls).toHaveLength(2)
      const scenarioIds = runCalls.map((c) => c.scenario_id).sort()
      expect(scenarioIds).toEqual(['SIM-EDR-007', 'SIM-ITDR-002'])
      // First successful launch's run_id was dispatched
      expect(navEvents.length).toBeGreaterThan(0)
      expect(navEvents[0]).toMatch(/^r-SIM-/)
      expect(screen.getByText(/launched/)).toBeInTheDocument()
    } finally {
      window.removeEventListener('cortex:navigate-run', listener)
    }
  })

  it('unchecking scenarios narrows the launch set', async () => {
    const runCalls = []
    installRoutes({
      'GET /api/ttps':                fixtureList,
      'GET /api/ttps/TTP-2026-0004':  fixtureDetailDcsync,
      'GET /api/scenarios': [
        { scenario_id: 'SIM-ITDR-002', name: 'DCSync chain', plane: 'ITDR', mitre_technique: 'T1003.006' },
        { scenario_id: 'SIM-EDR-007',  name: 'mimikatz dcsync', plane: 'EDR',  mitre_technique: 'T1003.006' },
      ],
      'POST /api/run': (_url, init) => {
        const body = JSON.parse(init.body || '{}')
        runCalls.push(body)
        return { run_id: `r-${body.scenario_id}` }
      },
    })
    render(<TtpBrowserView initialTtpId="TTP-2026-0004" />)
    await waitFor(() => expect(screen.getByTestId('ttp-launch-all')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('ttp-launch-all'))
    await waitFor(() => expect(screen.getByTestId('ttp-launcher-modal')).toBeInTheDocument())
    // Uncheck the EDR scenario
    fireEvent.click(screen.getByLabelText(/Include SIM-EDR-007/))
    expect(screen.getByText('1 of 2 selected')).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('ttp-launcher-confirm'))
    await waitFor(() => expect(screen.getByTestId('ttp-launcher-summary')).toBeInTheDocument())
    expect(runCalls).toHaveLength(1)
    expect(runCalls[0].scenario_id).toBe('SIM-ITDR-002')
  })

  it('renders the no-scenarios placeholder when no scenarios cite this TTP', async () => {
    installRoutes({
      'GET /api/ttps':                fixtureList,
      'GET /api/ttps/TTP-2026-0004':  fixtureDetailDcsync,
      'GET /api/scenarios': [],
    })
    render(<TtpBrowserView initialTtpId="TTP-2026-0004" />)
    await waitFor(() => expect(screen.getByTestId('ttp-launch-all')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('ttp-launch-all'))
    await waitFor(() => expect(screen.getByTestId('ttp-launcher-empty')).toBeInTheDocument())
    expect(screen.queryByTestId('ttp-launcher-confirm')).not.toBeInTheDocument()
  })

  it('empty run history renders the no-runs placeholder', async () => {
    installRoutes({
      'GET /api/ttps':                       fixtureList,
      'GET /api/ttps/TTP-2026-0004':         fixtureDetailDcsync,
      'GET /api/ttps/TTP-2026-0004/runs':    { ttp_id: 'TTP-2026-0004', runs: [], total: 0 },
    })
    render(<TtpBrowserView initialTtpId="TTP-2026-0004" />)
    await waitFor(() => expect(screen.getByTestId('ttp-runs-empty')).toBeInTheDocument())
    expect(screen.getByText(/no runs have exercised this TTP yet/i)).toBeInTheDocument()
  })

  it('empty detections renders a friendly no-detections message', async () => {
    installRoutes({
      'GET /api/ttps': fixtureList,
      'GET /api/ttps/TTP-2026-0004': {
        ...fixtureDetailDcsync,
        detections: {
          iocs: [], biocs: [], xql_queries: [], correlation_rules: [], analytics_modules: [],
        },
      },
    })
    render(<TtpBrowserView initialTtpId="TTP-2026-0004" />)
    await waitFor(() => expect(screen.getByTestId('ttp-detail')).toBeInTheDocument())
    expect(screen.getByText(/no detections shipped/i)).toBeInTheDocument()
  })
})
