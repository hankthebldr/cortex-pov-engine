/**
 * Smoke + interaction tests for the static Tool Adapter catalog browser.
 *
 * Mirrors AdapterRegistryView.test.jsx but exercises the SEPARATE backend
 * surface (`/api/tools/adapters`) and the SEPARATE concept — static tool
 * adapters with tier / safety_class / planes, not EAL plugins.
 */
import React from 'react'
import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import ToolAdapterCatalog from '../console/ToolAdapterCatalog.jsx'
import { installRoutes } from '../../test/mockFetch.js'

void React

/**
 * The catalog became a CONTROLLED surface — filters, selection and the open
 * panel live in URL params so a DC can send a colleague "the twelve tools this
 * POV needs, three of them unstaged". These tests therefore drive it through a
 * stateful params harness, which is what the real router does.
 */
export function Harness({ initialParams = {}, onNavigate = () => {} } = {}) {
  const [params, setParams] = React.useState(initialParams)
  const merge = React.useCallback((next) => {
    setParams((prev) => {
      const merged = { ...prev, ...next }
      Object.keys(merged).forEach((k) => { if (merged[k] == null || merged[k] === '') delete merged[k] })
      return merged
    })
  }, [])
  return <ToolAdapterCatalog params={params} setParams={merge} onNavigate={onNavigate} />
}

/** The shelf endpoints every render touches. Defaults to "nothing staged,
 *  nothing declared", which is this repo's real state today. */
export const shelfRoutes = ({ payloads = [], declared = [], writable = true } = {}) => ({
  'GET /api/shelf/payloads': { payloads, total: payloads.length, dist_dir: '/app/payloads', auth: 'open', writable, declared },
  'GET /api/shelf/artifacts': { artifacts: declared, total: declared.length, staged: payloads.map((p) => p.name), unstaged: [], unpinned: [], dist_dir: '/app/payloads' },
})

const fixtureList = {
  adapters: [
    {
      adapter_id:          'TOOL-NMAP',
      name:                'Nmap',
      version:             '7.94',
      tier:                4,
      category:            'network-scan',
      safety_class:        'safe',
      planes:              ['NDR'],
      expected_techniques: ['T1046'],
      target_platform:     'linux',
      license:             'NPSL',
      tags:                ['portscan', 'discovery'],
    },
    {
      adapter_id:          'TOOL-MIMIKATZ',
      name:                'Mimikatz',
      version:             '2.2.0-20220919',
      tier:                3,
      category:            'identity-credential',
      safety_class:        'dual-use-lab-only',
      planes:              ['EDR', 'ITDR'],
      expected_techniques: ['T1003.001', 'T1003.006'],
      target_platform:     'windows',
      license:             'CC-BY-4.0',
      tags:                ['credential-dump', 'lsass'],
    },
    {
      adapter_id:          'TOOL-SLIVER',
      name:                'Sliver',
      version:             '1.5.42',
      tier:                3,
      category:            'c2-framework',
      safety_class:        'c2-framework',
      planes:              ['EDR', 'NDR'],
      expected_techniques: ['T1071.001'],
      target_platform:     'linux',
      license:             'GPL-3.0',
      tags:                ['c2', 'red-team'],
    },
  ],
  total: 3,
}

const fixtureDetailMimikatz = {
  adapter_id:   'TOOL-MIMIKATZ',
  name:         'Mimikatz',
  version:      '2.2.0-20220919',
  tier:         3,
  category:     'identity-credential',
  safety_class: 'dual-use-lab-only',
  upstream: {
    repo:        'https://github.com/gentilkiwi/mimikatz',
    license:     'CC-BY-4.0',
    attribution: 'Benjamin Delpy (gentilkiwi)',
  },
  cortex_signal: {
    planes:              ['EDR', 'ITDR'],
    expected_techniques: ['T1003.001', 'T1003.006'],
  },
  install: {
    iac_module: 'edr',
    binary:     'C:\\tools\\mimikatz\\x64\\mimikatz.exe',
  },
  invoke: {
    target_platform:   'windows',
    run_template:      '"{binary}" "{commands}" "exit"',
    default_args:      { commands: 'privilege::debug sekurlsa::logonpasswords' },
    identity_required: 'administrator',
  },
  cleanup: {
    commands: ['rm -f C:\\tools\\mimikatz\\*.txt'],
  },
  ttp_refs:    ['TTP-2026-0002', 'TTP-2026-0004'],
  equivalents: ['TOOL-PYPYKATZ', 'TOOL-RUBEUS'],
  tags:        ['credential-dump', 'lsass', 'flagship'],
}

describe('<ToolAdapterCatalog />', () => {
  it('says what the page is in one line, with no supply statistics', async () => {
    installRoutes({ ...shelfRoutes(), 'GET /api/tools/adapters': fixtureList })
    render(<Harness />)
    await waitFor(() => expect(screen.getByText(/The tools a step can run/i)).toBeInTheDocument())
    expect(screen.queryByTestId('stat-staged')).not.toBeInTheDocument()
  })

  it('renders one card per adapter with its category and platform, and no tier', async () => {
    installRoutes({ ...shelfRoutes(), 'GET /api/tools/adapters': fixtureList })
    render(<Harness />)
    await waitFor(() => {
      expect(screen.getByText('Nmap')).toBeInTheDocument()
    })
    expect(screen.getByText('Mimikatz')).toBeInTheDocument()
    expect(screen.getByText('Sliver')).toBeInTheDocument()
    expect(screen.getByText(/^network-scan · linux$/)).toBeInTheDocument()
    expect(screen.getByText(/^identity-credential · windows$/)).toBeInTheDocument()
    expect(screen.queryByText(/T4 ·/)).not.toBeInTheDocument()
  })

  it('filters by plane and category only: tier and safety are not filters', async () => {
    installRoutes({ ...shelfRoutes(), 'GET /api/tools/adapters': fixtureList })
    render(<Harness />)
    await waitFor(() => expect(screen.getByText('Nmap')).toBeInTheDocument())
    expect(screen.getByRole('button', { name: 'NDR' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'EDR' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'ITDR' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'network-scan' })).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'c2-framework' })).toHaveLength(1)
    expect(screen.queryByRole('button', { name: 'T3' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'safe' })).not.toBeInTheDocument()
  })

  it('plane filter narrows the visible cards client-side', async () => {
    installRoutes({ ...shelfRoutes(), 'GET /api/tools/adapters': fixtureList })
    render(<Harness />)
    await waitFor(() => expect(screen.getByText('Nmap')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: 'ITDR' }))
    expect(screen.getByText('Mimikatz')).toBeInTheDocument()
    expect(screen.queryByText('Nmap')).not.toBeInTheDocument()
    expect(screen.queryByText('Sliver')).not.toBeInTheDocument()
  })

  it('the plane filter compounds with the category filter (logical AND)', async () => {
    installRoutes({ ...shelfRoutes(), 'GET /api/tools/adapters': fixtureList })
    render(<Harness />)
    await waitFor(() => expect(screen.getByText('Nmap')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: 'EDR' }))
    fireEvent.click(screen.getByRole('button', { name: 'c2-framework' }))
    // Only Sliver is on the EDR plane AND a c2-framework.
    expect(screen.getByText('Sliver')).toBeInTheDocument()
    expect(screen.queryByText('Mimikatz')).not.toBeInTheDocument()
    expect(screen.queryByText('Nmap')).not.toBeInTheDocument()
  })

  it('clicking a card opens the detail panel with invoke template + cleanup', async () => {
    installRoutes({
      ...shelfRoutes(),
      'GET /api/tools/adapters':              fixtureList,
      'GET /api/tools/adapters/TOOL-MIMIKATZ': fixtureDetailMimikatz,
    })
    render(<Harness />)
    await waitFor(() => expect(screen.getByText('Mimikatz')).toBeInTheDocument())
    fireEvent.click(screen.getByText('Mimikatz'))
    await waitFor(() => {
      expect(screen.getByTestId('tool-adapter-detail')).toBeInTheDocument()
    })
    // Upstream attribution
    expect(screen.getByText(/Benjamin Delpy/)).toBeInTheDocument()
    // run_template surfaced verbatim
    expect(screen.getByText(/{binary}/)).toBeInTheDocument()
    // Cleanup command — actual rendered text is `rm -f C:\tools\mimikatz\*.txt`
    // so in a JS regex literal each backslash is a single `\\`.
    expect(screen.getByText(/rm -f C:\\tools\\mimikatz/)).toBeInTheDocument()
    // ttp_refs + equivalents
    expect(screen.getByText('TTP-2026-0002')).toBeInTheDocument()
    expect(screen.getByText('TOOL-PYPYKATZ')).toBeInTheDocument()
  })

  it('handles detail load failure gracefully', async () => {
    installRoutes({ ...shelfRoutes(), 'GET /api/tools/adapters': fixtureList })
    // No mock for the detail endpoint → 404 → "Load failed" panel
    render(<Harness />)
    await waitFor(() => expect(screen.getByText('Nmap')).toBeInTheDocument())
    fireEvent.click(screen.getByText('Nmap'))
    await waitFor(() => {
      expect(screen.getByText(/Load failed/i)).toBeInTheDocument()
    })
  })

  it('handles list load failure with a visible error banner', async () => {
    installRoutes({ ...shelfRoutes() })
    render(<Harness />)
    await waitFor(() => {
      expect(screen.getByText(/no mock for|Failed to load/i)).toBeInTheDocument()
    })
  })

  it('shows "no matches" empty state when filters exclude everything', async () => {
    installRoutes({ ...shelfRoutes(), 'GET /api/tools/adapters': fixtureList })
    render(<Harness />)
    await waitFor(() => expect(screen.getByText('Nmap')).toBeInTheDocument())
    // ITDR plane + network-scan category: no adapter satisfies both in this fixture
    fireEvent.click(screen.getByRole('button', { name: 'ITDR' }))
    fireEvent.click(screen.getByRole('button', { name: 'network-scan' }))
    expect(screen.getByText(/no adapters match the current filters/i)).toBeInTheDocument()
    // Clear filters CTA
    fireEvent.click(screen.getByRole('button', { name: /clear filters/i }))
    expect(screen.getByText('Nmap')).toBeInTheDocument()
  })

  it('detail panel TTP-ref chips render as clickable buttons', async () => {
    installRoutes({
      ...shelfRoutes(),
      'GET /api/tools/adapters':              fixtureList,
      'GET /api/tools/adapters/TOOL-MIMIKATZ': fixtureDetailMimikatz,
    })
    render(<Harness />)
    await waitFor(() => expect(screen.getByText('Mimikatz')).toBeInTheDocument())
    fireEvent.click(screen.getByText('Mimikatz'))
    await waitFor(() => {
      expect(screen.getByTestId('ttp-ref-chip-TTP-2026-0002')).toBeInTheDocument()
    })
    // The chip is now a <button>, not a <span>, so it's focusable + clickable
    const chip = screen.getByTestId('ttp-ref-chip-TTP-2026-0002')
    expect(chip.tagName).toBe('BUTTON')
  })

  it('detail panel equivalent chips render as clickable buttons', async () => {
    installRoutes({
      ...shelfRoutes(),
      'GET /api/tools/adapters':              fixtureList,
      'GET /api/tools/adapters/TOOL-MIMIKATZ': fixtureDetailMimikatz,
    })
    render(<Harness />)
    await waitFor(() => expect(screen.getByText('Mimikatz')).toBeInTheDocument())
    fireEvent.click(screen.getByText('Mimikatz'))
    await waitFor(() => {
      expect(screen.getByTestId('equivalent-chip-TOOL-PYPYKATZ')).toBeInTheDocument()
    })
    const chip = screen.getByTestId('equivalent-chip-TOOL-PYPYKATZ')
    expect(chip.tagName).toBe('BUTTON')
  })
})
