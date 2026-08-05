import React from 'react'
import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
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

describe('Tools & Payloads — supply state', () => {
  it('labels an unstaged declared artifact NOT STAGED and names the host the target reaches', async () => {
    installRoutes({ 'GET /api/tools/adapters': ADAPTERS, ...shelf({ declared: [DECLARED] }) })
    render(<Harness />)
    await waitFor(() => expect(screen.getByTestId('supply-chip-TOOL-LINPEAS')).toHaveTextContent('NOT STAGED'))
    expect(screen.getByText(/egress: target → github\.com, at run time/)).toBeInTheDocument()
  })

  it('labels a staged artifact STAGED with its digest and licence', async () => {
    installRoutes({ 'GET /api/tools/adapters': ADAPTERS, ...shelf({ payloads: [STAGED], declared: [{ ...DECLARED, staged: true }] }) })
    render(<Harness />)
    await waitFor(() => expect(screen.getByTestId('supply-chip-TOOL-LINPEAS')).toHaveTextContent('STAGED'))
    expect(screen.getByText(/sha256 aaaaaaaaaaaa…/)).toBeInTheDocument()
    expect(screen.getByText(/egress: none — served from this SimCore/)).toBeInTheDocument()
  })

  it('labels a tier-4 pack with no artifact FETCHES AT RUN TIME and offers no Stage button', async () => {
    installRoutes({ 'GET /api/tools/adapters': ADAPTERS, ...shelf({ declared: [DECLARED] }) })
    render(<Harness />)
    await waitFor(() => expect(screen.getByTestId('supply-chip-TOOL-HYDRA')).toHaveTextContent('FETCHES AT RUN TIME'))
    expect(screen.queryByTestId('stage-button-TOOL-HYDRA')).not.toBeInTheDocument()
    // …while the declared one does offer it.
    expect(screen.getByTestId('stage-button-TOOL-LINPEAS')).toBeInTheDocument()
  })

  it('renders every chip as UNKNOWN when the shelf endpoint is absent', async () => {
    installRoutes({ 'GET /api/tools/adapters': ADAPTERS })
    render(<Harness />)
    await waitFor(() => expect(screen.getByTestId('supply-chip-TOOL-LINPEAS')).toHaveTextContent('UNKNOWN'))
    expect(screen.getByTestId('supply-chip-TOOL-SIGNALBENCH')).toHaveTextContent('UNKNOWN')
    expect(screen.getByTestId('payload-shelf-banner')).toHaveTextContent(/SHELF UNKNOWN/)
    expect(screen.getByTestId('payload-shelf-banner')).toHaveTextContent(/neither staged nor unstaged/)
  })

  it('warns about target egress in the banner, with a count', async () => {
    installRoutes({ 'GET /api/tools/adapters': ADAPTERS, ...shelf({ declared: [DECLARED] }) })
    render(<Harness />)
    await waitFor(() => expect(screen.getByTestId('payload-shelf-banner')).toBeInTheDocument())
    expect(screen.getByTestId('payload-shelf-banner'))
      .toHaveTextContent(/2 of 3 tools fetch from the public internet/)
  })

  it('names staged-but-UNBOUND artifacts as bytes nothing can compose', async () => {
    installRoutes({
      'GET /api/tools/adapters': ADAPTERS,
      ...shelf({ payloads: [{ ...STAGED, adapter_id: null }], declared: [] }),
    })
    render(<Harness />)
    await waitFor(() => expect(screen.getByTestId('payload-unbound')).toBeInTheDocument())
    expect(screen.getByTestId('payload-unbound')).toHaveTextContent(/UNBOUND/)
    expect(screen.getByTestId('payload-unbound')).toHaveTextContent(/not as coverage/)
  })

  it('?supply=unstaged filters the grid', async () => {
    installRoutes({ 'GET /api/tools/adapters': ADAPTERS, ...shelf({ declared: [DECLARED] }) })
    render(<Harness initialParams={{ supply: 'unstaged' }} />)
    await waitFor(() => expect(screen.getByText('LinPEAS')).toBeInTheDocument())
    expect(screen.queryByText('Hydra')).not.toBeInTheDocument()
    expect(screen.queryByText('signalbench')).not.toBeInTheDocument()
  })

  it('the banner CTA narrows to the unstaged set', async () => {
    installRoutes({ 'GET /api/tools/adapters': ADAPTERS, ...shelf({ declared: [DECLARED] }) })
    render(<Harness />)
    await waitFor(() => expect(screen.getByText('Hydra')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: /Show them/i }))
    await waitFor(() => expect(screen.queryByText('signalbench')).not.toBeInTheDocument())
  })

  it('?tool= opens the detail panel with Supply BEFORE Upstream', async () => {
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
    expect(labels.indexOf('Supply')).toBeLessThan(labels.indexOf('Upstream'))
    expect(screen.getByTestId('supply-detail')).toHaveTextContent('NOT STAGED')
  })

  it('a read-only shelf replaces "Stage on SimCore" with the offline path', async () => {
    installRoutes({
      'GET /api/tools/adapters': ADAPTERS,
      'GET /api/tools/adapters/TOOL-LINPEAS': { ...ADAPTERS.adapters[0], upstream: {}, install: {}, invoke: {} },
      ...shelf({ declared: [DECLARED], writable: false }),
    })
    render(<Harness initialParams={{ tool: 'TOOL-LINPEAS' }} />)
    await waitFor(() => expect(screen.getByTestId('supply-detail')).toBeInTheDocument())
    expect(screen.queryByRole('button', { name: /Stage on SimCore/ })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Stage offline/ })).toBeInTheDocument()
    expect(screen.getByTestId('payload-shelf-banner')).toHaveTextContent(/build-payloads\.sh/)
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
