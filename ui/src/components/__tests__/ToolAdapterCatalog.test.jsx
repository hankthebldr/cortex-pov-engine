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
  it('renders intro + total stat from the API payload', async () => {
    installRoutes({ 'GET /api/tools/adapters': fixtureList })
    render(<ToolAdapterCatalog />)
    await waitFor(() => {
      expect(screen.getByText(/Static catalog of every offensive/i)).toBeInTheDocument()
    })
    // adapter count derived from the response
    expect(screen.getAllByText('3').length).toBeGreaterThan(0)
  })

  it('renders one card per adapter with tier · category · platform line', async () => {
    installRoutes({ 'GET /api/tools/adapters': fixtureList })
    render(<ToolAdapterCatalog />)
    await waitFor(() => {
      expect(screen.getByText('Nmap')).toBeInTheDocument()
    })
    expect(screen.getByText('Mimikatz')).toBeInTheDocument()
    expect(screen.getByText('Sliver')).toBeInTheDocument()
    // Tier+category+platform descriptor present on each card
    expect(screen.getByText(/T4 · network-scan · linux/)).toBeInTheDocument()
    expect(screen.getByText(/T3 · identity-credential · windows/)).toBeInTheDocument()
  })

  it('derives filter chips from the loaded corpus', async () => {
    installRoutes({ 'GET /api/tools/adapters': fixtureList })
    render(<ToolAdapterCatalog />)
    await waitFor(() => expect(screen.getByText('Nmap')).toBeInTheDocument())
    // Plane chips
    expect(screen.getByRole('button', { name: 'NDR' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'EDR' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'ITDR' })).toBeInTheDocument()
    // Tier chips render as "T3", "T4"
    expect(screen.getByRole('button', { name: 'T3' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'T4' })).toBeInTheDocument()
    // Safety class chip — "c2-framework" appears in both the safety and
    // category filter rows (vocabulary overlap is intentional), so assert
    // via getAllByRole rather than getByRole.
    expect(screen.getByRole('button', { name: 'safe' })).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'c2-framework' }).length).toBe(2)
  })

  it('plane filter narrows the visible cards client-side', async () => {
    installRoutes({ 'GET /api/tools/adapters': fixtureList })
    render(<ToolAdapterCatalog />)
    await waitFor(() => expect(screen.getByText('Nmap')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: 'ITDR' }))
    expect(screen.getByText('Mimikatz')).toBeInTheDocument()
    expect(screen.queryByText('Nmap')).not.toBeInTheDocument()
    expect(screen.queryByText('Sliver')).not.toBeInTheDocument()
  })

  it('tier filter compounds with safety filter (logical AND)', async () => {
    installRoutes({ 'GET /api/tools/adapters': fixtureList })
    render(<ToolAdapterCatalog />)
    await waitFor(() => expect(screen.getByText('Nmap')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: 'T3' }))
    fireEvent.click(screen.getByRole('button', { name: 'dual-use-lab-only' }))
    // Only mimikatz satisfies T3 AND dual-use
    expect(screen.getByText('Mimikatz')).toBeInTheDocument()
    expect(screen.queryByText('Sliver')).not.toBeInTheDocument()
  })

  it('clicking a card opens the detail panel with invoke template + cleanup', async () => {
    installRoutes({
      'GET /api/tools/adapters':              fixtureList,
      'GET /api/tools/adapters/TOOL-MIMIKATZ': fixtureDetailMimikatz,
    })
    render(<ToolAdapterCatalog />)
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
    installRoutes({ 'GET /api/tools/adapters': fixtureList })
    // No mock for the detail endpoint → 404 → "Load failed" panel
    render(<ToolAdapterCatalog />)
    await waitFor(() => expect(screen.getByText('Nmap')).toBeInTheDocument())
    fireEvent.click(screen.getByText('Nmap'))
    await waitFor(() => {
      expect(screen.getByText(/Load failed/i)).toBeInTheDocument()
    })
  })

  it('handles list load failure with a visible error banner', async () => {
    installRoutes({})
    render(<ToolAdapterCatalog />)
    await waitFor(() => {
      expect(screen.getByText(/no mock for|Failed to load/i)).toBeInTheDocument()
    })
  })

  it('shows "no matches" empty state when filters exclude everything', async () => {
    installRoutes({ 'GET /api/tools/adapters': fixtureList })
    render(<ToolAdapterCatalog />)
    await waitFor(() => expect(screen.getByText('Nmap')).toBeInTheDocument())
    // EDR plane + safe class — no adapter satisfies both in this fixture
    fireEvent.click(screen.getByRole('button', { name: 'EDR' }))
    fireEvent.click(screen.getByRole('button', { name: 'safe' }))
    expect(screen.getByText(/no adapters match the current filters/i)).toBeInTheDocument()
    // Clear filters CTA
    fireEvent.click(screen.getByRole('button', { name: /clear filters/i }))
    expect(screen.getByText('Nmap')).toBeInTheDocument()
  })

  it('detail panel TTP-ref chips render as clickable buttons', async () => {
    installRoutes({
      'GET /api/tools/adapters':              fixtureList,
      'GET /api/tools/adapters/TOOL-MIMIKATZ': fixtureDetailMimikatz,
    })
    render(<ToolAdapterCatalog />)
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
      'GET /api/tools/adapters':              fixtureList,
      'GET /api/tools/adapters/TOOL-MIMIKATZ': fixtureDetailMimikatz,
    })
    render(<ToolAdapterCatalog />)
    await waitFor(() => expect(screen.getByText('Mimikatz')).toBeInTheDocument())
    fireEvent.click(screen.getByText('Mimikatz'))
    await waitFor(() => {
      expect(screen.getByTestId('equivalent-chip-TOOL-PYPYKATZ')).toBeInTheDocument()
    })
    const chip = screen.getByTestId('equivalent-chip-TOOL-PYPYKATZ')
    expect(chip.tagName).toBe('BUTTON')
  })
})
