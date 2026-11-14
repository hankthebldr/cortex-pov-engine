/**
 * "Add to chain" from anywhere, the chain tray, and Build a chain picking the
 * tray up (design v26 §4.4).
 *
 * A TTP card, a package and a data stream each offer Add to chain. It drops
 * the item into the tray and confirms with a toast; the tray carries what was
 * added into Build a chain, where each item becomes a real step — a TTP card a
 * step bound to its detection, a data stream a step on the EAL channel running
 * that emitter, a package a step running that adapter.
 */
import React from 'react'
import { describe, it, expect, vi, beforeAll } from 'vitest'
import { render, screen, fireEvent, waitFor, within, act } from '@testing-library/react'
import { ChainTrayProvider, useChainTray } from '../../../context/ChainTrayContext.jsx'
import ChainTray from '../ChainTray.jsx'
import TtpBrowserView from '../TtpBrowserView.jsx'
import ToolAdapterCatalog from '../ToolAdapterCatalog.jsx'
import DataStreamsView from '../DataStreamsView.jsx'
import ComposerView from '../ComposerView.jsx'
import { EnvironmentProvider } from '../../../context/EnvironmentContext.jsx'
import { installRoutes } from '../../../test/mockFetch.js'

void React

beforeAll(() => {
  window.ResizeObserver = window.ResizeObserver || class {
    observe() {} unobserve() {} disconnect() {}
  }
  Object.defineProperty(HTMLElement.prototype, 'offsetWidth', { configurable: true, value: 1200 })
  Object.defineProperty(HTMLElement.prototype, 'offsetHeight', { configurable: true, value: 800 })
})

const TTP = {
  id: 'TTP-2026-0002', name: 'LSASS Memory Credential Dump', status: 'active', summary: 'Dump LSASS.',
  tags: [], platforms: ['windows'], simulation_class: 'endpoint', technique_ids: ['T1003.001'],
  tactic_ids: ['TA0006'], kill_chain_phase: 'credential-access',
  detection_counts: { iocs: 0, biocs: 4, xql_queries: 2, correlation_rules: 1, analytics_modules: 0 },
}
const ADAPTER = {
  adapter_id: 'TOOL-NMAP', name: 'Nmap', version: '7.94', tier: 4, category: 'network-scan',
  safety_class: 'safe', planes: ['NDR'], expected_techniques: ['T1046'], target_platform: 'linux', license: 'NPSL',
}
const STREAMS = {
  counts: { covered: 1, addressable: 1, partial: 0, gap: 0, with_negative_control: 0, proven: 0 },
  sources: [],
  emitters: [{ name: 'cloud_audit_emitter', sources: [{ key: 'aws', name: 'AWS CloudTrail' }], datasets: ['cloud_audit_logs'],
    detectors: [], supports_negative_control: false, latest_delivery: null }],
  authored_not_proven: 'Authored is not proven.',
}

/** The tray's items, read back for assertions. */
function TrayProbe() {
  const { items } = useChainTray()
  return <output data-testid="tray-probe">{items.map((i) => `${i.kind}:${i.id}`).join(',')}</output>
}

function withTray(ui, notify = vi.fn()) {
  return { notify, ...render(<ChainTrayProvider notify={notify}>{ui}<TrayProbe /></ChainTrayProvider>) }
}

describe('Add to chain — from every catalog item', () => {
  it('a TTP card offers Add to chain; it drops the card into the tray and confirms with a toast', async () => {
    installRoutes({ 'GET /api/ttps': { ttps: [TTP] } })
    const { notify } = withTray(<TtpBrowserView />)
    const card = await screen.findByTestId('ttp-card-TTP-2026-0002')
    const article = card.closest('article')
    fireEvent.click(within(article).getByRole('button', { name: 'Add to chain' }))
    expect(screen.getByTestId('tray-probe')).toHaveTextContent('ttp:TTP-2026-0002')
    expect(notify).toHaveBeenCalledWith(expect.stringMatching(/Added .*LSASS Memory Credential Dump.* to your chain/), 'success')
    // Adding it again does not duplicate it, and says so.
    fireEvent.click(within(article).getByRole('button', { name: 'Add to chain' }))
    expect(screen.getByTestId('tray-probe').textContent).toBe('ttp:TTP-2026-0002')
    expect(notify).toHaveBeenLastCalledWith(expect.stringMatching(/already in your chain/), 'info')
    // Pressing Add to chain did not open the card.
    expect(screen.queryByTestId('ttp-detail')).toBeNull()
  })

  it('the TTP card has the v26 anatomy: id and technique, title, tags, a footer verb', async () => {
    installRoutes({ 'GET /api/ttps': { ttps: [TTP] } })
    withTray(<TtpBrowserView />)
    const card = (await screen.findByTestId('ttp-card-TTP-2026-0002')).closest('article')
    const head = within(card).getByTestId('ttpb-card-head')
    expect(head).toHaveTextContent('TTP-2026-0002')
    expect(head).toHaveTextContent('T1003.001')
    expect(within(card).getByText('LSASS Memory Credential Dump')).toBeInTheDocument()
    expect(within(within(card).getByTestId('ttpb-card-foot')).getAllByRole('button').map((b) => b.textContent))
      .toEqual(['Add to chain'])
  })

  it('a package offers Add to chain', async () => {
    installRoutes({
      'GET /api/tools/adapters': { adapters: [ADAPTER] },
      'GET /api/shelf/payloads': { payloads: [], total: 0, writable: true, declared: [] },
      'GET /api/shelf/artifacts': { artifacts: [], total: 0, staged: [], unstaged: [], unpinned: [] },
    })
    const { notify } = withTray(<ToolAdapterCatalog />)
    const card = await screen.findByTestId('tool-adapter-card-TOOL-NMAP')
    fireEvent.click(within(card).getByRole('button', { name: 'Add to chain' }))
    expect(screen.getByTestId('tray-probe')).toHaveTextContent('package:TOOL-NMAP')
    expect(notify).toHaveBeenCalledWith(expect.stringMatching(/Added .*Nmap.* to your chain/), 'success')
  })

  it('a data stream offers Add to chain', async () => {
    installRoutes({ 'GET /api/eal/data-streams': STREAMS })
    const { notify } = withTray(<DataStreamsView onMessage={vi.fn()} />)
    fireEvent.click(await screen.findByRole('tab', { name: /emitters/i }))
    const row = screen.getByText('cloud_audit_emitter').closest('tr')
    fireEvent.click(within(row).getByRole('button', { name: 'Add to chain' }))
    expect(screen.getByTestId('tray-probe')).toHaveTextContent('stream:cloud_audit_emitter')
    expect(notify).toHaveBeenCalledWith(expect.stringMatching(/Added .*cloud_audit_emitter.* to your chain/), 'success')
  })
})

describe('The chain tray', () => {
  function Seed({ items }) {
    const { add } = useChainTray()
    React.useEffect(() => { items.forEach((i) => add(i, { quiet: true })) }, []) // eslint-disable-line react-hooks/exhaustive-deps
    return null
  }

  it('shows what was added, clears, and carries it into Build a chain', () => {
    const onNavigate = vi.fn()
    withTray(<>
      <Seed items={[{ kind: 'ttp', id: 'TTP-2026-0002', label: 'LSASS dump' }, { kind: 'package', id: 'TOOL-NMAP', label: 'Nmap' }]} />
      <ChainTray onNavigate={onNavigate} />
    </>)
    const tray = screen.getByRole('region', { name: 'Your chain' })
    expect(tray).toHaveTextContent('2 added')
    expect(tray).toHaveTextContent('LSASS dump')
    expect(tray).toHaveTextContent('Nmap')
    fireEvent.click(within(tray).getByRole('button', { name: 'Customize' }))
    expect(onNavigate).toHaveBeenCalledWith('composer', { tray: '1' })
    fireEvent.click(within(tray).getByRole('button', { name: 'Clear' }))
    expect(screen.queryByRole('region', { name: 'Your chain' })).toBeNull()
  })

  it('renders nothing while the tray is empty', () => {
    withTray(<ChainTray onNavigate={() => {}} />)
    expect(screen.queryByRole('region', { name: 'Your chain' })).toBeNull()
  })
})

describe('Build a chain — picks up the tray', () => {
  function routes() {
    return installRoutes({
      'GET /api/health': { status: 'ok', components: {} },
      'GET /api/scenarios': { scenarios: [] },
      'GET /api/runs': [],
      'GET /api/agents': [],
      'GET /api/credentials/integrations': [],
      'GET /api/shelf/payloads': { payloads: [] },
      'GET /api/shelf/artifacts': { artifacts: [] },
      'GET /api/ttps': { ttps: [] },
      'GET /api/tools/adapters': { adapters: [] },
      'GET /api/eal/data-streams': STREAMS,
    })
  }

  it('turns each tray item into a real step, then empties the tray', async () => {
    routes()
    let api
    function Grab() { api = useChainTray(); return null }
    const setParams = vi.fn()
    // StrictMode, as main.jsx mounts the console: its double effect pass must
    // not add the items and then wipe them.
    const tree = (composer) => (
      <React.StrictMode>
        <EnvironmentProvider>
          <ChainTrayProvider notify={vi.fn()}>
            <Grab />
            <TrayProbe />
            {composer && <ComposerView params={{ tray: '1' }} setParams={setParams} />}
          </ChainTrayProvider>
        </EnvironmentProvider>
      </React.StrictMode>
    )
    const { rerender } = render(tree(false))
    // The items are in the tray before Build a chain opens, as in the console.
    act(() => {
      api.add({ kind: 'ttp', id: 'TTP-2026-0002', label: 'LSASS Memory Credential Dump', technique: 'T1003.001', plane: 'EDR' }, { quiet: true })
      api.add({ kind: 'stream', id: 'cloud_audit_emitter', label: 'cloud_audit_emitter' }, { quiet: true })
      api.add({ kind: 'package', id: 'TOOL-NMAP', label: 'Nmap' }, { quiet: true })
    })
    rerender(tree(true))
    await waitFor(() => expect(screen.getByTestId('chain-step-step-03')).toBeInTheDocument())
    expect(screen.getByTestId('chain-step-step-01')).toHaveTextContent('LSASS Memory Credential Dump')
    // The stream runs its emitter on the EAL channel; the door says so.
    expect(screen.getByTestId('chain-step-channel-step-02')).toHaveTextContent('EAL')
    expect(screen.getByTestId('chain-door-step-02')).toHaveTextContent('SIM → COLLECTOR')
    // The YAML is what SimCore would run: the TTP binding, the emitter, the adapter.
    fireEvent.click(screen.getByRole('button', { name: 'YAML' }))
    const yaml = screen.getByTestId('composer-yaml').textContent
    expect(yaml).toContain('TTP-2026-0002')
    expect(yaml).toContain('cloud_audit_emitter')
    expect(yaml).toContain('{adapter:TOOL-NMAP}')
    expect(screen.getByTestId('tray-probe').textContent).toBe('')
    expect(setParams).toHaveBeenCalledWith({ tray: null }, { replace: true })
  })

  it('the palette is tabbed Scenarios · TTP cards · Data streams · Packages', async () => {
    routes()
    render(
      <EnvironmentProvider>
        <ChainTrayProvider notify={vi.fn()}>
          <ComposerView params={{}} />
        </ChainTrayProvider>
      </EnvironmentProvider>,
    )
    fireEvent.click(await screen.findByRole('button', { name: 'Add to chain' }))
    const tabs = within(screen.getByRole('tablist', { name: 'Palette surfaces' })).getAllByRole('tab')
    expect(tabs.map((t) => t.textContent)).toEqual(['Scenarios', 'TTP cards', 'Data streams', 'Packages'])
    fireEvent.click(tabs[2])
    await waitFor(() => expect(within(screen.getByTestId('composer-palette')).getByText('cloud_audit_emitter')).toBeInTheDocument())
  })
})
