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
