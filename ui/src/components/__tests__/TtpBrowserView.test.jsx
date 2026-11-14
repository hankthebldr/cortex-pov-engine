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

describe('<TtpBrowserView /> — authoring affordances (issue #59)', () => {
  it('renders the Author new button next to search', async () => {
    installRoutes({ 'GET /api/ttps': fixtureList })
    render(<TtpBrowserView />)
    await waitFor(() => {
      expect(screen.getByTestId('ttp-author-new')).toBeInTheDocument()
    })
  })

  it('clicking Author new opens the editor', async () => {
    installRoutes({
      'GET /api/ttps':         fixtureList,
      'GET /api/ttps/_schema': { type: 'object', properties: {} },
    })
    render(<TtpBrowserView />)
    await waitFor(() => expect(screen.getByTestId('ttp-author-new')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('ttp-author-new'))
    await waitFor(() => expect(screen.getByTestId('ttp-editor')).toBeInTheDocument())
  })

  it('detail panel exposes an Edit… button that switches to the editor', async () => {
    installRoutes({
      'GET /api/ttps':                fixtureList,
      'GET /api/ttps/TTP-2026-0004':  fixtureDetailDcsync,
      'GET /api/ttps/_schema':        { type: 'object', properties: {} },
    })
    render(<TtpBrowserView initialTtpId="TTP-2026-0004" />)
    await waitFor(() => expect(screen.getByTestId('ttp-edit')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('ttp-edit'))
    await waitFor(() => expect(screen.getByTestId('ttp-editor')).toBeInTheDocument())
    // Detail panel no longer rendered while the editor is open.
    expect(screen.queryByTestId('ttp-detail')).not.toBeInTheDocument()
  })
})

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
    // tactic / platform chips (status is run status now, offered only when
    // the server sends run_status — this fixture has none)
    expect(screen.queryByRole('button', { name: 'active' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Never run' })).toBeNull()
    expect(screen.getByRole('button', { name: 'TA0006' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'TA0001' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'windows' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'linux' })).toBeInTheDocument()
  })

  describe('run status (design C5)', () => {
    const withRunStatus = {
      ...fixtureList,
      ttps: fixtureList.ttps.map((t, i) => ({ ...t, run_status: ['detected', 'pending', 'never'][i] })),
    }

    it('each card says what runs have shown, as an icon and a word', async () => {
      installRoutes({ 'GET /api/ttps': withRunStatus })
      render(<TtpBrowserView />)
      await waitFor(() => expect(screen.getByTestId('ttp-card-TTP-2026-0002')).toBeInTheDocument())
      const word = (id) => screen.getByTestId(`ttp-card-${id}`).querySelector('[data-testid="ttpb-card-run-status"]')
      expect(word('TTP-2026-0002')).toHaveTextContent('Detected in a run')
      expect(word('TTP-2026-0003')).toHaveTextContent('Pending')
      expect(word('TTP-2026-0004')).toHaveTextContent('Never run')
      expect(word('TTP-2026-0002').querySelector('[role="img"]')).toHaveAttribute('aria-label', 'Detected in a run')
    })

    it('the Status filter narrows by run status', async () => {
      installRoutes({ 'GET /api/ttps': withRunStatus })
      render(<TtpBrowserView />)
      await waitFor(() => expect(screen.getByTestId('ttp-card-TTP-2026-0002')).toBeInTheDocument())
      fireEvent.click(screen.getByRole('button', { name: 'Never run' }))
      expect(screen.queryByTestId('ttp-card-TTP-2026-0002')).toBeNull()
      expect(screen.getByTestId('ttp-card-TTP-2026-0004')).toBeInTheDocument()
    })
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

  // ── Syntax highlighting (issue #57) ────────────────────────────────

  it('renders XQL syntax-highlighted spans in the expanded body', async () => {
    installRoutes({
      'GET /api/ttps':                fixtureList,
      'GET /api/ttps/TTP-2026-0004':  fixtureDetailDcsync,
    })
    render(<TtpBrowserView initialTtpId="TTP-2026-0004" />)
    await waitFor(() => expect(screen.getByTestId('ttp-detail')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('ttp-det-biocs-0'))

    const pre = screen.getByTestId('ttp-det-body-biocs-0')
    // XQL keywords land on syn-keyword (the only ones in this body).
    const keywordSpans = pre.querySelectorAll('.syn-keyword')
    expect(keywordSpans.length).toBeGreaterThan(0)
    const keywords = Array.from(keywordSpans).map((n) => n.textContent.toLowerCase())
    expect(keywords).toContain('preset')
    expect(keywords).toContain('filter')

    // String literals get syn-string.
    const stringSpan = pre.querySelector('.syn-string')
    expect(stringSpan).toBeTruthy()
    expect(stringSpan.textContent).toContain('"e3514235-...')

    // Round-trip: the rendered text equals the corpus body.
    expect(pre.textContent).toBe(
      'preset = xdr_data\n| filter rpc_interface_uuid = "e3514235-..."',
    )
  })

  it('renders YAML syntax-highlighted spans for analytics_modules', async () => {
    // Provide a fixture with an analytics_modules entry carrying a Sigma-shaped body.
    const yamlFixture = {
      ...fixtureDetailDcsync,
      detections: {
        iocs: [], biocs: [], xql_queries: [],
        correlation_rules: [],
        analytics_modules: [
          {
            name: 'Sigma: DCSync',
            description: 'Sigma rule mirroring DCSync',
            logic:
              'title: DCSync detection\n' +
              'logsource:\n  product: windows\n' +
              'detection:\n  selection:\n    EventID: 4662',
          },
        ],
      },
    }
    installRoutes({
      'GET /api/ttps':                fixtureList,
      'GET /api/ttps/TTP-2026-0004':  yamlFixture,
    })
    render(<TtpBrowserView initialTtpId="TTP-2026-0004" />)
    await waitFor(() => expect(screen.getByTestId('ttp-detail')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('ttp-det-analytics_modules-0'))

    const pre = screen.getByTestId('ttp-det-body-analytics_modules-0')
    const keywords = Array.from(pre.querySelectorAll('.syn-keyword')).map(
      (n) => n.textContent,
    )
    // YAML keys colour as syn-keyword.
    expect(keywords).toEqual(expect.arrayContaining(['title', 'product']))

    // Numeric scalar EventID = 4662
    const numbers = Array.from(pre.querySelectorAll('.syn-number')).map((n) => n.textContent)
    expect(numbers).toContain('4662')

    // Body round-trips through the highlighter so copy still works.
    expect(pre.textContent).toBe(
      'title: DCSync detection\nlogsource:\n  product: windows\n' +
        'detection:\n  selection:\n    EventID: 4662',
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

  // M-2 — a bundle-splitting/restyle pass silently changed two section
  // labels in TtpDetail: "Detection coverage" → "Detection logic", and
  // "Summary" was dropped entirely (the paragraph lost its DetailSection
  // wrapper). The brief was explicit: restore both unless the new wording
  // is clearly better — it wasn't argued to be, so both are restored.
  it('keeps the "Summary" section label and "Detection coverage" (not "Detection logic")', async () => {
    installRoutes({
      'GET /api/ttps':                     fixtureList,
      'GET /api/ttps/TTP-2026-0004':       fixtureDetailDcsync,
      'GET /api/ttps/TTP-2026-0004/runs':  { ttp_id: 'TTP-2026-0004', runs: [], total: 0 },
    })
    render(<TtpBrowserView initialTtpId="TTP-2026-0004" />)
    await waitFor(() => expect(screen.getByTestId('ttp-detail')).toBeInTheDocument())
    expect(screen.getByText('Summary')).toBeInTheDocument()
    expect(screen.getByText(fixtureDetailDcsync.identity.summary)).toBeInTheDocument()
    expect(screen.getByText('Detection coverage')).toBeInTheDocument()
    expect(screen.queryByText('Detection logic')).not.toBeInTheDocument()
  })
})

// ── Organize by the FY27 index: group / sort by use case, test case, subdomain ──
//
// A card carries no UC/TC ref; the view joins card → scenario → test case →
// use case → fy27_subdomain (catalogBindings.js). These cases cover the render
// and — the part that matters most — every way the join can be unavailable
// without the view claiming "no scenario cites this card".

const det = (ttp_ref) => ({ type: 'BIOC', plane: 'EDR', ...(ttp_ref ? { ttp_ref } : {}) })

const fixtureScenarios = {
  scenarios: [
    {
      scenario_id: 'SIM-EDR-006', name: 'LSASS dump', plane: 'EDR',
      uc_ref: 'UCS-EDR-03', tc_ref: 'TC-EDR-05', tc_refs: ['TC-EDR-05', 'TC-IR-01'],
      steps: [{ id: 'step-01', expected_detections: [det('TTP-2026-0002')] }],
    },
    {
      scenario_id: 'SIM-ITDR-002', name: 'DCSync chain', plane: 'ITDR',
      uc_ref: 'UCS-ITDR-02', tc_ref: 'TC-ITDR-04', tc_refs: ['TC-ITDR-04'],
      steps: [{ id: 'step-01', expected_detections: [det('TTP-2026-0004')] }],
    },
  ],
  total: 2,
  projection: 'summary',
}

const fixtureTestCases = {
  index_loaded: true,
  index_version: '2.2',
  test_cases: [
    { tc_id: 'TC-EDR-05', title: 'Causality graph shows the full process chain', ucs_id: 'UCS-EDR-03', ucs_name: 'Lack of Endpoint Alert Context', uc_id: 'UC-EDR', use_case: 'Endpoint Detection & Response' },
    { tc_id: 'TC-IR-01', title: 'Auto-triage reduces manual alert review', ucs_id: 'UCS-IR-01', ucs_name: 'Analyst Fatigue', uc_id: 'UC-IR', use_case: 'Incident Response' },
    { tc_id: 'TC-ITDR-04', title: 'Detect DCSync replication abuse', ucs_id: 'UCS-ITDR-02', ucs_name: 'Credential Theft', uc_id: 'UC-ITDR', use_case: 'Identity Threat Detection & Response' },
  ],
  total: 3,
}

const fixtureUseCases = {
  index_loaded: true,
  index_version: '2.2',
  use_cases: [
    { uc_id: 'UC-EDR', use_case: 'Endpoint Detection & Response', fy27_subdomain: 'Endpoint Protection' },
    { uc_id: 'UC-IR', use_case: 'Incident Response', fy27_subdomain: 'SOC Transformation' },
    { uc_id: 'UC-ITDR', use_case: 'Identity Threat Detection & Response', fy27_subdomain: 'Identity Security' },
  ],
  total: 3,
}

const indexRoutes = (over = {}) => ({
  'GET /api/ttps':            fixtureList,
  'GET /api/scenarios':       fixtureScenarios,
  'GET /api/uctc/test-cases': fixtureTestCases,
  'GET /api/uctc/use-cases':  fixtureUseCases,
  ...over,
})

const cardIds = (root) =>
  Array.from(root.querySelectorAll('[data-testid^="ttp-card-TTP"]'))
    .map((c) => c.getAttribute('data-testid').replace('ttp-card-', ''))

async function renderOrganized(props = {}, routes = indexRoutes()) {
  installRoutes(routes)
  const utils = render(<TtpBrowserView {...props} />)
  await waitFor(() => expect(screen.getByTestId('ttp-card-TTP-2026-0002')).toBeInTheDocument())
  return utils
}

describe('<TtpBrowserView /> — organize by use case / test case / subdomain', () => {
  it('each card face names its primary test case and use case once the index loads', async () => {
    await renderOrganized()
    await waitFor(() => expect(screen.getByTestId('ttp-card-uctc-TTP-2026-0002')).toBeInTheDocument())
    const face = screen.getByTestId('ttp-card-uctc-TTP-2026-0002')
    expect(face).toHaveTextContent('TC-EDR-05')
    expect(face).toHaveTextContent('UC-EDR')
    expect(face).toHaveTextContent('Endpoint Protection')
    // One more test case is evidenced beyond the primary.
    expect(face).toHaveTextContent('+1')
    // A card nothing cites says so, rather than showing an empty row.
    expect(screen.getByTestId('ttp-card-uctc-TTP-2026-0003')).toHaveTextContent(/no scenario cites this card/i)
  })

  it('Group by subdomain renders one section per subdomain with its count, uncited last', async () => {
    await renderOrganized()
    await waitFor(() => expect(screen.getByTestId('ttp-group-by-subdomain')).not.toBeDisabled())
    fireEvent.click(screen.getByTestId('ttp-group-by-subdomain'))

    const sections = Array.from(document.querySelectorAll('[data-testid^="ttp-group-section-"]'))
    expect(sections.map((s) => s.getAttribute('data-group-label'))).toEqual([
      'Endpoint Protection', 'Identity Security', 'SOC Transformation',
      'No scenario cites this card',
    ])
    expect(screen.getByTestId('ttp-group-count-Endpoint Protection')).toHaveTextContent('1 card')
    // LSASS evidences TC-IR-01 too, so it is ALSO listed under SOC Transformation.
    const soc = screen.getByTestId('ttp-group-section-SOC Transformation')
    expect(cardIds(soc)).toEqual(['TTP-2026-0002'])
    // …and there its face cites the binding that put it THERE (TC-IR-01, a
    // secondary), not its lead UC-EDR one — or the placement looks like a bug.
    const face = soc.querySelector('[data-testid="ttp-card-uctc-TTP-2026-0002"]')
    expect(face).toHaveTextContent('UC-IR')
    expect(face).toHaveTextContent('TC-IR-01')
    expect(face).toHaveTextContent(/secondary/i)
    expect(face).not.toHaveTextContent('UC-EDR')
    // Under its own subdomain the same card is not marked secondary.
    const own = screen.getByTestId('ttp-group-section-Endpoint Protection')
      .querySelector('[data-testid="ttp-card-uctc-TTP-2026-0002"]')
    expect(own).toHaveTextContent('TC-EDR-05')
    expect(own).not.toHaveTextContent(/secondary/i)
    expect(cardIds(screen.getByTestId('ttp-group-section-__uncited__'))).toEqual(['TTP-2026-0003'])
    // Flat paging is replaced by the outline.
    expect(screen.queryByTestId('ttp-pager')).not.toBeInTheDocument()
  })

  it('says how many cards repeat, so group counts are never read as a card total', async () => {
    await renderOrganized()
    await waitFor(() => expect(screen.getByTestId('ttp-group-by-subdomain')).not.toBeDisabled())
    fireEvent.click(screen.getByTestId('ttp-group-by-subdomain'))
    const note = screen.getByTestId('ttp-organize-note')
    expect(note).toHaveTextContent('3 cards in 4 groups')
    expect(note).toHaveTextContent('1 card is listed more than once')
    expect(note).toHaveTextContent(/authored/i)
  })

  it('"Primary test case only" drops the secondary placement', async () => {
    await renderOrganized()
    await waitFor(() => expect(screen.getByTestId('ttp-group-by-subdomain')).not.toBeDisabled())
    // The include row only exists while grouped.
    expect(screen.queryByTestId('ttp-include-primary')).not.toBeInTheDocument()
    fireEvent.click(screen.getByTestId('ttp-group-by-subdomain'))
    fireEvent.click(screen.getByTestId('ttp-include-primary'))
    expect(screen.queryByTestId('ttp-group-section-SOC Transformation')).not.toBeInTheDocument()
    expect(cardIds(screen.getByTestId('ttp-group-section-Endpoint Protection'))).toEqual(['TTP-2026-0002'])
  })

  it('a group header collapses and reopens its cards', async () => {
    await renderOrganized()
    await waitFor(() => expect(screen.getByTestId('ttp-group-by-subdomain')).not.toBeDisabled())
    fireEvent.click(screen.getByTestId('ttp-group-by-subdomain'))
    const toggle = screen.getByTestId('ttp-group-toggle-Identity Security')
    // Three cards fit one page, so the outline starts open.
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(cardIds(screen.getByTestId('ttp-group-section-Identity Security'))).toEqual(['TTP-2026-0004'])
    fireEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(cardIds(screen.getByTestId('ttp-group-section-Identity Security'))).toEqual([])
    fireEvent.click(toggle)
    expect(cardIds(screen.getByTestId('ttp-group-section-Identity Security'))).toEqual(['TTP-2026-0004'])
  })

  it('Group by use case titles the group and opens it in the UC / TC index', async () => {
    const onNavigate = vi.fn()
    await renderOrganized({ onNavigate })
    await waitFor(() => expect(screen.getByTestId('ttp-group-by-uc')).not.toBeDisabled())
    fireEvent.click(screen.getByTestId('ttp-group-by-uc'))
    const section = screen.getByTestId('ttp-group-section-UC-ITDR')
    expect(section).toHaveTextContent('Identity Threat Detection & Response')
    expect(section).toHaveTextContent('Identity Security')
    fireEvent.click(screen.getByTestId('ttp-group-open-UC-ITDR'))
    expect(onNavigate).toHaveBeenCalledWith('uctc', { uc: 'UC-ITDR' })
  })

  it('Group by test case opens the test case in the UC / TC index', async () => {
    const onNavigate = vi.fn()
    await renderOrganized({ onNavigate })
    await waitFor(() => expect(screen.getByTestId('ttp-group-by-tc')).not.toBeDisabled())
    fireEvent.click(screen.getByTestId('ttp-group-by-tc'))
    expect(screen.getByTestId('ttp-group-section-TC-ITDR-04')).toHaveTextContent('Detect DCSync replication abuse')
    fireEvent.click(screen.getByTestId('ttp-group-open-TC-ITDR-04'))
    expect(onNavigate).toHaveBeenCalledWith('uctc', { tc: 'TC-ITDR-04' })
  })

  it('Sort by name reorders the flat grid; Sort by test case puts the uncited card last', async () => {
    const { container } = await renderOrganized()
    expect(cardIds(container)).toEqual(['TTP-2026-0002', 'TTP-2026-0003', 'TTP-2026-0004'])
    fireEvent.click(screen.getByTestId('ttp-sort-by-name'))
    // AWS IAM… < DCSync… < LSASS…
    expect(cardIds(container)).toEqual(['TTP-2026-0003', 'TTP-2026-0004', 'TTP-2026-0002'])
    await waitFor(() => expect(screen.getByTestId('ttp-sort-by-tc')).not.toBeDisabled())
    fireEvent.click(screen.getByTestId('ttp-sort-by-tc'))
    // TC-EDR-05 (0002) < TC-ITDR-04 (0004) < unbound (0003)
    expect(cardIds(container)).toEqual(['TTP-2026-0002', 'TTP-2026-0004', 'TTP-2026-0003'])
  })

  it('search finds a card by test case id and by subdomain', async () => {
    const { container } = await renderOrganized()
    await waitFor(() => expect(screen.getByTestId('ttp-card-uctc-TTP-2026-0004')).toBeInTheDocument())
    fireEvent.change(screen.getByTestId('ttp-search'), { target: { value: 'TC-ITDR-04' } })
    expect(cardIds(container)).toEqual(['TTP-2026-0004'])
    fireEvent.change(screen.getByTestId('ttp-search'), { target: { value: 'endpoint protection' } })
    expect(cardIds(container)).toEqual(['TTP-2026-0002'])
  })

  it('reads group / sort / include from the URL params and writes changes back', async () => {
    const setParams = vi.fn()
    await renderOrganized({ params: { group: 'uc', sort: 'name', include: 'primary' }, setParams })
    await waitFor(() => expect(screen.getByTestId('ttp-group-section-UC-EDR')).toBeInTheDocument())
    expect(screen.getByTestId('ttp-group-by-uc')).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByTestId('ttp-sort-by-name')).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByTestId('ttp-include-primary')).toHaveAttribute('aria-pressed', 'true')

    fireEvent.click(screen.getByTestId('ttp-group-by-tc'))
    expect(setParams).toHaveBeenLastCalledWith({ group: 'tc' })
    // Defaults are written as null so the router drops the key from the hash.
    fireEvent.click(screen.getByTestId('ttp-group-by-none'))
    expect(setParams).toHaveBeenLastCalledWith({ group: null })
    fireEvent.click(screen.getByTestId('ttp-sort-by-id'))
    expect(setParams).toHaveBeenLastCalledWith({ sort: null })
  })

  it('follows the URL when the hash changes under the mounted view', async () => {
    // An edited address bar or a same-page link changes `params` WITHOUT a
    // remount. Seen in a real browser: the hash said group=uc, the grid stayed
    // on subdomain.
    installRoutes(indexRoutes())
    const { rerender } = render(<TtpBrowserView params={{ group: 'subdomain' }} />)
    await waitFor(() => expect(screen.getByTestId('ttp-group-section-Identity Security')).toBeInTheDocument())
    rerender(<TtpBrowserView params={{ group: 'uc' }} />)
    await waitFor(() => expect(screen.getByTestId('ttp-group-section-UC-ITDR')).toBeInTheDocument())
    expect(screen.queryByTestId('ttp-group-section-Identity Security')).not.toBeInTheDocument()
    expect(screen.getByTestId('ttp-group-by-uc')).toHaveAttribute('aria-pressed', 'true')
  })

  it('a collapsed group stays collapsed across a re-sort and a trip through another grouping', async () => {
    await renderOrganized()
    await waitFor(() => expect(screen.getByTestId('ttp-group-by-subdomain')).not.toBeDisabled())
    fireEvent.click(screen.getByTestId('ttp-group-by-subdomain'))
    fireEvent.click(screen.getByTestId('ttp-group-toggle-Identity Security'))
    const collapsed = () =>
      expect(screen.getByTestId('ttp-group-toggle-Identity Security')).toHaveAttribute('aria-expanded', 'false')
    collapsed()
    fireEvent.click(screen.getByTestId('ttp-sort-by-name'))
    collapsed()
    fireEvent.click(screen.getByTestId('ttp-group-by-uc'))
    fireEvent.click(screen.getByTestId('ttp-group-by-subdomain'))
    collapsed()
    // The other groups were never touched and keep following the default.
    expect(screen.getByTestId('ttp-group-toggle-Endpoint Protection')).toHaveAttribute('aria-expanded', 'true')
  })

  // ── Degraded is not zero ─────────────────────────────────────────────

  it('index not loaded: grouping is disabled with a reason — never "0 use cases", never "uncited"', async () => {
    await renderOrganized({}, indexRoutes({
      'GET /api/uctc/test-cases': { index_loaded: false, index_version: null, test_cases: [], total: 0 },
      'GET /api/uctc/use-cases':  { index_loaded: false, index_version: null, use_cases: [], total: 0 },
    }))
    const degraded = await screen.findByTestId('ttp-organize-degraded')
    expect(degraded).toHaveTextContent(/UC \/ TC index is not loaded/i)
    for (const id of ['subdomain', 'uc', 'tc']) {
      expect(screen.getByTestId(`ttp-group-by-${id}`)).toBeDisabled()
      expect(screen.getByTestId(`ttp-sort-by-${id}`)).toBeDisabled()
    }
    // Non-index sorts still work.
    expect(screen.getByTestId('ttp-sort-by-name')).not.toBeDisabled()
    expect(screen.queryByText(/no scenario cites this card/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/\b0 use cases\b/i)).not.toBeInTheDocument()
  })

  it('a SimCore whose scenario list carries no ttp_ref: degraded, and no card is called uncited', async () => {
    const olderProjection = {
      ...fixtureScenarios,
      scenarios: fixtureScenarios.scenarios.map((s) => ({
        ...s,
        steps: s.steps.map((st) => ({ ...st, expected_detections: [{ type: 'BIOC', plane: 'EDR' }] })),
      })),
    }
    await renderOrganized({}, indexRoutes({ 'GET /api/scenarios': olderProjection }))
    const degraded = await screen.findByTestId('ttp-organize-degraded')
    expect(degraded).toHaveTextContent(/carries no card references/i)
    expect(screen.getByTestId('ttp-group-by-subdomain')).toBeDisabled()
    expect(screen.queryByText(/no scenario cites this card/i)).not.toBeInTheDocument()
    expect(screen.queryByTestId('ttp-card-uctc-TTP-2026-0002')).not.toBeInTheDocument()
  })

  it('a deep link asking for a grouping the backend cannot serve stays flat and says why', async () => {
    const { container } = await renderOrganized({ params: { group: 'subdomain' } }, indexRoutes({
      'GET /api/uctc/test-cases': { index_loaded: false, index_version: null, test_cases: [], total: 0 },
      'GET /api/uctc/use-cases':  { index_loaded: false, index_version: null, use_cases: [], total: 0 },
    }))
    await screen.findByTestId('ttp-organize-degraded')
    expect(document.querySelector('[data-testid^="ttp-group-section-"]')).toBeNull()
    expect(cardIds(container)).toEqual(['TTP-2026-0002', 'TTP-2026-0003', 'TTP-2026-0004'])
    expect(screen.getByTestId('ttp-organize-degraded')).toHaveTextContent(/showing every card ungrouped\./i)
  })

  it('the degraded note says exactly what fell back — a refused sort is not called "ungrouped"', async () => {
    const noIndex = indexRoutes({
      'GET /api/uctc/test-cases': { index_loaded: false, index_version: null, test_cases: [], total: 0 },
      'GET /api/uctc/use-cases':  { index_loaded: false, index_version: null, use_cases: [], total: 0 },
    })
    const { unmount } = await renderOrganized({ params: { sort: 'tc' } }, noIndex)
    const sortOnly = await screen.findByTestId('ttp-organize-degraded')
    expect(sortOnly).toHaveTextContent(/showing cards in card id order\./i)
    expect(sortOnly).not.toHaveTextContent(/ungrouped/i)
    unmount()

    await renderOrganized({ params: { group: 'uc', sort: 'uc' } }, noIndex)
    expect(await screen.findByTestId('ttp-organize-degraded'))
      .toHaveTextContent(/showing every card ungrouped, in card id order\./i)
  })

  it('an index fetch failure degrades the organize bar and leaves the cards browsable', async () => {
    const { container } = await renderOrganized({}, { 'GET /api/ttps': fixtureList })
    const degraded = await screen.findByTestId('ttp-organize-degraded')
    expect(degraded).toHaveTextContent(/could not be loaded/i)
    expect(screen.getByTestId('ttp-group-by-subdomain')).toBeDisabled()
    expect(cardIds(container)).toEqual(['TTP-2026-0002', 'TTP-2026-0003', 'TTP-2026-0004'])
  })
})
