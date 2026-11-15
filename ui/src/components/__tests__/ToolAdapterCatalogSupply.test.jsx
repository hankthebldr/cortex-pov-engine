import React from 'react'
import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import ToolAdapterCatalog from '../console/ToolAdapterCatalog.jsx'
import { installRoutes } from '../../test/mockFetch.js'

void React

/**
 * Supply state on the catalog surface.
 *
 * Chips are queried BY TEXT, never by class: colour is not the signal, the
 * label is. A DC reading this on a projector in a customer's boardroom sees a
 * flattened hue and the word.
 */

function Harness({ initialParams = {}, onNavigate = () => {} } = {}) {
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

const ADAPTERS = {
  adapters: [
    { adapter_id: 'TOOL-LINPEAS', name: 'LinPEAS', version: '20250601', tier: 4, category: 'privilege-escalation', safety_class: 'dual-use-lab-only', planes: ['EDR', 'CDR'], expected_techniques: ['T1082'], target_platform: 'linux', license: 'MIT', tags: ['privesc'] },
    { adapter_id: 'TOOL-HYDRA', name: 'Hydra', version: '9.5', tier: 4, category: 'credential-access', safety_class: 'dual-use-lab-only', planes: ['ITDR'], expected_techniques: ['T1110'], target_platform: 'linux', license: 'AGPL-3.0', tags: ['bruteforce'] },
    { adapter_id: 'TOOL-SIGNALBENCH', name: 'signalbench', version: '0.4', tier: 2, category: 'telemetry', safety_class: 'safe', planes: ['EDR'], expected_techniques: [], target_platform: 'linux', license: 'MIT', tags: [] },
  ],
  total: 3,
}

const DECLARED = {
  name: 'linpeas.sh',
  url: 'https://github.com/carlospolop/PEASS-ng/releases/download/20250601/linpeas.sh',
  kind: 'file', sha256: 'a'.repeat(64), pinned: true, pin_type: 'release-tag', pin_ref: '20250601',
  license: 'MIT', adapter_id: 'TOOL-LINPEAS', stage_path: '/tmp/linpeas.sh', mode: '0755',
  used_by: 'SIM-CDR-001', staged: false,
}

const STAGED = {
  name: 'linpeas.sh', filename: 'linpeas.sh', size_bytes: 1106683, sha256: 'a'.repeat(64),
  modified_at: '2026-08-05T13:05:00', source_url: DECLARED.url, license: 'MIT', pinned: true,
  adapter_id: 'TOOL-LINPEAS',
}

const shelf = ({ payloads = [], declared = [], writable = true } = {}) => ({
  'GET /api/shelf/payloads': { payloads, total: payloads.length, dist_dir: '/app/payloads', auth: 'open', writable, declared },
  'GET /api/shelf/artifacts': { artifacts: declared, total: declared.length, staged: payloads.map((p) => p.name), unstaged: [], unpinned: [], dist_dir: '/app/payloads' },
})

describe('Packages — readiness, in plain words', () => {
  // How and when a tool reaches the host is the system's business. A card
  // states one readiness fact: a tool this instance cannot supply, because a
  // run that needs it is refused.
  const INTERNAL = /shelf|staged|fetches at run time|egress|sha256|tier/i

  it('marks a tool this instance cannot supply "Not ready", and says nothing about fetching', async () => {
    installRoutes({ 'GET /api/tools/adapters': ADAPTERS, ...shelf({ declared: [DECLARED] }) })
    render(<Harness />)
    await waitFor(() => expect(screen.getByTestId('not-ready-TOOL-LINPEAS')).toHaveTextContent('Not ready'))
    expect(screen.queryByTestId('not-ready-TOOL-HYDRA')).not.toBeInTheDocument()
    expect(screen.queryByTestId('not-ready-TOOL-SIGNALBENCH')).not.toBeInTheDocument()
    expect(screen.getByTestId('tool-adapter-catalog').textContent).not.toMatch(INTERNAL)
  })

  it('a ready tool carries no marker, no digest and no licence line on its card', async () => {
    installRoutes({ 'GET /api/tools/adapters': ADAPTERS, ...shelf({ payloads: [STAGED], declared: [{ ...DECLARED, staged: true }] }) })
    render(<Harness />)
    await waitFor(() => expect(screen.getByText('LinPEAS')).toBeInTheDocument())
    expect(screen.queryByTestId('not-ready-TOOL-LINPEAS')).not.toBeInTheDocument()
    expect(screen.getByTestId('tool-adapter-catalog').textContent).not.toMatch(INTERNAL)
  })

  it('offers no staging control on a card; every card can be added to the chain', async () => {
    installRoutes({ 'GET /api/tools/adapters': ADAPTERS, ...shelf({ declared: [DECLARED] }) })
    render(<Harness />)
    await waitFor(() => expect(screen.getByText('Hydra')).toBeInTheDocument())
    expect(screen.queryByRole('button', { name: /stage/i })).not.toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Add to chain' })).toHaveLength(3)
  })

  it('claims nothing about readiness when this instance does not report it', async () => {
    installRoutes({ 'GET /api/tools/adapters': ADAPTERS })
    render(<Harness />)
    await waitFor(() => expect(screen.getByText('LinPEAS')).toBeInTheDocument())
    expect(screen.queryByText('Not ready')).not.toBeInTheDocument()
    expect(screen.queryByTestId('payload-shelf-banner')).not.toBeInTheDocument()
  })

  it('?supply=unstaged scopes the grid, names the scope in plain words, and clears', async () => {
    installRoutes({ 'GET /api/tools/adapters': ADAPTERS, ...shelf({ declared: [DECLARED] }) })
    render(<Harness initialParams={{ supply: 'unstaged' }} />)
    await waitFor(() => expect(screen.getByText('LinPEAS')).toBeInTheDocument())
    expect(screen.queryByText('Hydra')).not.toBeInTheDocument()
    expect(screen.queryByText('signalbench')).not.toBeInTheDocument()
    const scope = screen.getByTestId('adapter-supply-scope')
    expect(scope).toHaveTextContent('tools not ready on this instance')
    fireEvent.click(within(scope).getByRole('button', { name: 'clear' }))
    expect(screen.getByText('Hydra')).toBeInTheDocument()
  })

  it('?tool= opens the detail with Availability first, in plain words, and no install mechanics', async () => {
    installRoutes({
      'GET /api/tools/adapters': ADAPTERS,
      'GET /api/tools/adapters/TOOL-LINPEAS': {
        ...ADAPTERS.adapters[0],
        upstream: { repo: 'https://github.com/carlospolop/PEASS-ng', license: 'MIT', attribution: 'Carlos Polop' },
        install: { binary: '/tmp/linpeas.sh', runtime_install_command: 'curl -fsSLo /tmp/linpeas.sh https://…' },
        invoke: { target_platform: 'linux', run_template: '{binary} {flags}' },
        cortex_signal: { planes: ['EDR', 'CDR'], expected_techniques: ['T1082'] },
      },
      ...shelf({ declared: [DECLARED] }),
    })
    render(<Harness initialParams={{ tool: 'TOOL-LINPEAS' }} />)
    await waitFor(() => expect(screen.getByTestId('tool-adapter-detail')).toBeInTheDocument())
    const labels = Array.from(document.querySelectorAll('.competitive__detail-label')).map((n) => n.textContent)
    expect(labels.indexOf('Availability')).toBeLessThan(labels.indexOf('Upstream'))
    expect(labels).not.toContain('Install')
    const availability = screen.getByTestId('supply-detail')
    expect(availability).toHaveTextContent('Not ready')
    expect(availability).toHaveTextContent(/A run that needs this tool is refused/)
    expect(within(availability).getByRole('button', { name: /Make ready/ })).toBeInTheDocument()
  })

  it('a tool the target installs for itself says the target needs internet access', async () => {
    installRoutes({
      'GET /api/tools/adapters': ADAPTERS,
      'GET /api/tools/adapters/TOOL-HYDRA': { ...ADAPTERS.adapters[1], upstream: {}, install: {}, invoke: {} },
      ...shelf({ declared: [DECLARED] }),
    })
    render(<Harness initialParams={{ tool: 'TOOL-HYDRA' }} />)
    await waitFor(() => expect(screen.getByTestId('supply-detail')).toBeInTheDocument())
    expect(screen.getByTestId('supply-detail')).toHaveTextContent(/The target needs internet access/)
    expect(within(screen.getByTestId('supply-detail')).queryByRole('button', { name: /Make ready/ })).not.toBeInTheDocument()
  })

  it('?panel= without ?tool= says so instead of silently doing nothing', async () => {
    installRoutes({ 'GET /api/tools/adapters': ADAPTERS, ...shelf({ declared: [DECLARED] }) })
    render(<Harness initialParams={{ panel: 'stage' }} />)
    await waitFor(() => expect(screen.getByText(/needs a \?tool=/)).toBeInTheDocument())
  })

  it('search narrows by name, id and tag', async () => {
    installRoutes({ 'GET /api/tools/adapters': ADAPTERS, ...shelf({ declared: [DECLARED] }) })
    render(<Harness />)
    await waitFor(() => expect(screen.getByText('LinPEAS')).toBeInTheDocument())
    fireEvent.change(screen.getByTestId('adapter-search'), { target: { value: 'linpeas' } })
    await waitFor(() => expect(screen.queryByText('Hydra')).not.toBeInTheDocument())
    expect(screen.getByText('LinPEAS')).toBeInTheDocument()
  })
})
