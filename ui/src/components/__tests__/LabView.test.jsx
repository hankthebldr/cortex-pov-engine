import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import LabView, { resolveModuleDependencies } from '../console/LabView.jsx'
import { installRoutes } from '../../test/mockFetch.js'

const adapterFixtures = {
  adapters: [
    {
      adapter_id:   'TOOL-MIMIKATZ',
      name:         'Mimikatz',
      version:      '2.2.0',
      tier:         3,
      category:     'identity-credential',
      safety_class: 'dual-use-lab-only',
      planes:       ['EDR', 'ITDR'],
      expected_techniques: ['T1003.001'],
      target_platform: 'windows',
      license:      'CC-BY-4.0',
      tags:         [],
    },
    {
      adapter_id:   'TOOL-RUBEUS',
      name:         'Rubeus',
      version:      '2.3.2',
      tier:         3,
      category:     'identity-credential',
      safety_class: 'dual-use-lab-only',
      planes:       ['ITDR'],
      expected_techniques: ['T1558.003'],
      target_platform: 'windows',
      license:      'BSD-3-Clause',
      tags:         [],
    },
    {
      adapter_id:   'TOOL-GOPHISH',
      name:         'Gophish',
      version:      '0.12',
      tier:         3,
      category:     'social-engineering',
      safety_class: 'dual-use-lab-only',
      planes:       ['CDR'],
      expected_techniques: ['T1566'],
      target_platform: 'linux',
      license:      'MIT',
      tags:         [],
    },
  ],
  total: 3,
}

/**
 * Tests for the console-themed Lab tab.
 *
 * Two layers:
 *   1. resolveModuleDependencies — pure helper; tests the
 *      AUTO-SELECT-on-check + LEAVE-ALONE-on-uncheck policy and verifies the
 *      BFS walk handles transitive deps + cycles + missing modules safely.
 *   2. <LabView /> — smoke render against mocked /api/infra/* endpoints so
 *      the entire module is exercised on import (lifts global coverage).
 */

const moduleFixtures = {
  modules: [
    { name: 'base', provider: 'aws', description: 'VPC + IAM baseline', dependencies: [] },
    { name: 'edr',  provider: 'aws', description: 'Diverse Linux EDR targets', dependencies: ['base'] },
    { name: 'cdr',  provider: 'aws', description: 'EKS cluster',               dependencies: ['base', 'tim'] },
    { name: 'tim',  provider: 'aws', description: 'TAXII server + IOC feed',   dependencies: ['base'] },
    { name: 'itdr', provider: 'aws', description: 'AD lab',                    dependencies: ['base'] },
  ],
  total: 5,
}

describe('resolveModuleDependencies', () => {
  it('adds the toggled module when previously unchecked', () => {
    const next = resolveModuleDependencies(new Set(['base']), 'edr', moduleFixtures.modules)
    expect(next.has('edr')).toBe(true)
    expect(next.has('base')).toBe(true)
  })

  it('removes the toggled module without cascade on uncheck (option Y)', () => {
    // cdr is checked + so are its deps; unchecking cdr should NOT remove base/tim.
    const prev = new Set(['base', 'cdr', 'tim'])
    const next = resolveModuleDependencies(prev, 'cdr', moduleFixtures.modules)
    expect(next.has('cdr')).toBe(false)
    expect(next.has('base')).toBe(true)
    expect(next.has('tim')).toBe(true)
  })

  it('auto-selects direct dependencies on check (option A)', () => {
    const next = resolveModuleDependencies(new Set(), 'edr', moduleFixtures.modules)
    expect(next.has('edr')).toBe(true)
    expect(next.has('base')).toBe(true)
  })

  it('walks transitive dependencies of arbitrary depth', () => {
    // cdr → [base, tim]; tim → [base]; so checking cdr should pull base + tim.
    const next = resolveModuleDependencies(new Set(), 'cdr', moduleFixtures.modules)
    expect(next.has('cdr')).toBe(true)
    expect(next.has('tim')).toBe(true)
    expect(next.has('base')).toBe(true)
  })

  it('survives cyclic dependency declarations without infinite loop', () => {
    // Synthetic cycle: a → b → a
    const cyclic = [
      { name: 'a', dependencies: ['b'] },
      { name: 'b', dependencies: ['a'] },
    ]
    const next = resolveModuleDependencies(new Set(), 'a', cyclic)
    expect(next.has('a')).toBe(true)
    expect(next.has('b')).toBe(true)
  })

  it('tolerates a dependency that is not in allModules (backend validates)', () => {
    const partial = [
      { name: 'foo', dependencies: ['ghost'] },
    ]
    const next = resolveModuleDependencies(new Set(), 'foo', partial)
    expect(next.has('foo')).toBe(true)
    // We still record 'ghost' so the API call surfaces the backend rejection
    // rather than silently omitting it.
    expect(next.has('ghost')).toBe(true)
  })

  it('handles undefined/empty allModules gracefully (during initial load)', () => {
    const next = resolveModuleDependencies(new Set(), 'whatever', undefined)
    expect(next.has('whatever')).toBe(true)
    expect(next.size).toBe(1)
  })
})

describe('<LabView />', () => {
  it('lists modules from /api/infra/modules with locked-on base', async () => {
    installRoutes({
      'GET /api/infra/modules':  moduleFixtures,
      'GET /api/infra/bundles':  { bundles: [], total: 0 },
    })
    const { container } = render(<LabView />)
    // Wait for at least one module to render so the async fetch settles.
    expect(await screen.findByText(/edr target count/i)).toBeInTheDocument()
    // Module names are short and collide with param labels ("edr" vs "EDR target count").
    // Scope the assertion to the module grid where they appear as bold mono names.
    const grid = container.querySelector('.lab__module-grid')
    expect(grid).toBeTruthy()
    for (const name of ['base', 'edr', 'cdr', 'tim']) {
      expect(grid.textContent).toContain(name)
    }
    // Provider segmented control + Generate button are always present.
    expect(screen.getByRole('button', { name: /generate bundle/i })).toBeInTheDocument()
  })

  it('disables Generate when required fields are empty', async () => {
    installRoutes({
      'GET /api/infra/modules':  moduleFixtures,
      'GET /api/infra/bundles':  { bundles: [], total: 0 },
    })
    render(<LabView />)
    const btn = await screen.findByRole('button', { name: /generate bundle/i })
    expect(btn).toBeDisabled()
  })

  it('renders the bundle history table when bundles exist', async () => {
    installRoutes({
      'GET /api/infra/modules': moduleFixtures,
      'GET /api/infra/bundles': {
        bundles: [
          {
            bundle_id: 'b-abc12345-deadbeef',
            provider: 'aws',
            modules: ['base', 'edr'],
            created_at: '2026-05-20T12:34:56Z',
            size_bytes: 18432,
          },
        ],
        total: 1,
      },
    })
    render(<LabView />)
    expect(await screen.findByText(/recent bundles/i)).toBeInTheDocument()
    // Bundle ID is truncated to ~12 chars + ellipsis in the table cell.
    expect(screen.getByText(/b-abc12345/)).toBeInTheDocument()
  })
})

describe('<LabView /> adapter auto-pull picker', () => {
  it('renders an adapter chip per tier-3 adapter returned by the API', async () => {
    installRoutes({
      'GET /api/infra/modules': moduleFixtures,
      'GET /api/infra/bundles': { bundles: [], total: 0 },
      'GET /api/tools/adapters': adapterFixtures,
    })
    render(<LabView />)
    await waitFor(() => {
      expect(screen.getByTestId('adapter-auto-pull')).toBeInTheDocument()
    })
    expect(screen.getByTestId('adapter-toggle-TOOL-MIMIKATZ')).toBeInTheDocument()
    expect(screen.getByTestId('adapter-toggle-TOOL-RUBEUS')).toBeInTheDocument()
    expect(screen.getByTestId('adapter-toggle-TOOL-GOPHISH')).toBeInTheDocument()
  })

  it('groups adapters by category (identity-credential, social-engineering)', async () => {
    installRoutes({
      'GET /api/infra/modules': moduleFixtures,
      'GET /api/infra/bundles': { bundles: [], total: 0 },
      'GET /api/tools/adapters': adapterFixtures,
    })
    render(<LabView />)
    await waitFor(() => {
      expect(screen.getByText(/identity-credential/i)).toBeInTheDocument()
    })
    expect(screen.getByText(/social-engineering/i)).toBeInTheDocument()
  })

  it('chip starts unticked', async () => {
    installRoutes({
      'GET /api/infra/modules': moduleFixtures,
      'GET /api/infra/bundles': { bundles: [], total: 0 },
      'GET /api/tools/adapters': adapterFixtures,
    })
    render(<LabView />)
    const chip = await screen.findByTestId('adapter-toggle-TOOL-RUBEUS')
    expect(chip.className).not.toMatch(/is-active/)
  })

  it('hides the picker entirely when the catalog returns no adapters', async () => {
    installRoutes({
      'GET /api/infra/modules': moduleFixtures,
      'GET /api/infra/bundles': { bundles: [], total: 0 },
      'GET /api/tools/adapters': { adapters: [], total: 0 },
    })
    render(<LabView />)
    // Wait for the modules grid to confirm render, then assert the picker
    // is absent. Using a unique-fixture module name avoids the ambiguous-
    // text matcher trap.
    await waitFor(() => {
      expect(screen.getByText('itdr')).toBeInTheDocument()
    })
    expect(screen.queryByTestId('adapter-auto-pull')).not.toBeInTheDocument()
  })

  it('survives /api/tools/adapters failing — section stays hidden, no crash', async () => {
    // No mock for /api/tools/adapters → 404 from the mockFetch fallback
    installRoutes({
      'GET /api/infra/modules': moduleFixtures,
      'GET /api/infra/bundles': { bundles: [], total: 0 },
    })
    render(<LabView />)
    await waitFor(() => {
      expect(screen.getByText('itdr')).toBeInTheDocument()
    })
    // No adapter section, no error banner, no React crash
    expect(screen.queryByTestId('adapter-auto-pull')).not.toBeInTheDocument()
  })

})
