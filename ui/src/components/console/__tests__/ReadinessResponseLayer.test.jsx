import React from 'react'
import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import '@testing-library/jest-dom'
import { installRoutes } from '../../../test/mockFetch.js'
import ReadinessView from '../ReadinessView.jsx'
import { EnvironmentContext } from '../../../context/EnvironmentContext.jsx'

const HEALTH_OK = {
  status: 'ok', version: '1.0.0', commit: 'abc1234',
  components: { db: { status: 'ok' }, scenario_catalog: { status: 'ok', count: 170 } },
}

const env = {
  tenant: null, tenants: [], agent: null, agents: [], health: {}, apiError: null,
  healthModel: null, scenarios: [], planes: [], runs: [], activeRun: null, lastRun: null,
  pinnedIds: [], loading: {}, isPinned: () => false, togglePin: () => {}, unpin: () => {},
  setTenant: () => {}, setAgent: () => {}, refreshHealth: () => {}, refreshRuns: () => {},
  refreshScenarios: () => {}, refreshAgents: () => {}, refreshTenants: () => {},
}

const PROVES = 'the SOC Framework is configured on this tenant'
const DOES_NOT = 'that the framework handled any CortexSim case'

const layer = (over = {}, atOver = {}) => ({
  state: 'PRESENT', code: 'RESPONSE_LAYER_OK', framework_version: '3.31.4',
  packs: {}, mode: 'pov',
  auto_triage: {
    live: true, window_hours: 6, score_threshold: 40, score_off: false,
    live_source: 'xql_evidence', window_source: 'tenant_config',
    threshold_source: 'tenant_config', ...atOver,
  },
  ai_close: 'shadow', leftover_lists: false, sources_used: ['tenant_config'],
  read_at: '2026-11-11T10:00:00+00:00', ...over,
})

const STAGE_OK = { status: 'ok', code: 'PF_OK', detail: 'SOC Framework 3.31.4 present' }

const block = (over = {}, layerOver = {}, atOver = {}) => ({
  layer: layer(layerOver, atOver), state: layerOver.state || 'PRESENT',
  stage: STAGE_OK,
  read_at: '2026-11-11T10:00:00+00:00', stale: false,
  proves: PROVES, does_not_prove: DOES_NOT, ...over,
})

const install = (responseLayer) => installRoutes({
  'GET /api/health': HEALTH_OK,
  'GET /api/credentials/integrations': {
    integrations: [{ name: 'deathstar', kind: 'xsiam_tenant', config: {}, last_verified_ok: true }],
  },
  'GET /api/connectors': { connectors: [] },
  'GET /api/assertions/runs': { runs: [] },
  'GET /api/assertions': { assertions: [], total: 18 },
  'GET /api/readiness': {
    integrations: [{ name: 'deathstar', kind: 'xsiam_tenant', response_layer: responseLayer }],
  },
})

const renderRow = async (responseLayer) => {
  install(responseLayer)
  render(
    <EnvironmentContext.Provider value={env}>
      <ReadinessView />
    </EnvironmentContext.Provider>,
  )
  return screen.findByTestId('socfw-row-deathstar')
}

afterEach(() => { vi.restoreAllMocks() })

describe('Readiness — SOC Framework row', () => {
  it('renders state, version and mode', async () => {
    const row = await renderRow(block())
    expect(within(row).getByTestId('socfw-state-deathstar')).toHaveTextContent('PRESENT')
    expect(row).toHaveTextContent('3.31.4')
    expect(row).toHaveTextContent('pov')
  })

  it('is a row under the tenant, not a fifth rung', async () => {
    await renderRow(block())
    expect(screen.getByTestId('connector-ladder').querySelectorAll('li')).toHaveLength(4)
  })

  it('renders Auto Triage read values with their source and no "not read" caveat', async () => {
    const row = await renderRow(block())
    const at = within(row).getByTestId('socfw-auto-triage-deathstar')
    expect(at).toHaveTextContent('live (seen in the execution log, last 24 h) · 6 h · score ≤ 40')
    expect(at).not.toHaveTextContent('not read')
    expect(row).not.toHaveAttribute('data-degraded', 'true')
    expect(within(row).getByTestId('socfw-state-deathstar')).toHaveClass('chip--detected')
  })

  it('the canonical pov config says live is inferred and 6 h / 40 are shipped defaults', async () => {
    const row = await renderRow(block({}, {}, {
      live_source: 'mode_inferred', window_source: 'shipped_default',
      threshold_source: 'shipped_default',
    }))
    const at = within(row).getByTestId('socfw-auto-triage-deathstar')
    expect(at).toHaveTextContent('live (inferred from mode, not read)')
    expect(at).toHaveTextContent('6 h (shipped default, not read)')
    expect(at).toHaveTextContent('score ≤ 40 (shipped default, not read)')
  })

  it('a value with no recorded source is never shown as read', async () => {
    const row = await renderRow(block({}, {}, {
      live_source: undefined, window_source: undefined, threshold_source: undefined,
    }))
    expect(within(row).getByTestId('socfw-auto-triage-deathstar'))
      .toHaveTextContent('live (source not recorded) · 6 h (source not recorded)')
  })

  it('Auto Triage that is not live reads "off", never "shadow"', async () => {
    const row = await renderRow(block({}, {}, { live: false, live_source: 'mode_inferred' }))
    const at = within(row).getByTestId('socfw-auto-triage-deathstar')
    expect(at).toHaveTextContent(/^off \(inferred from mode, not read\)/)
    expect(at).not.toHaveTextContent('shadow')
  })

  it('a null window or threshold reads "not read", never silently omitted', async () => {
    const row = await renderRow(block({}, { state: 'PRESENT_UNREAD', code: 'RESPONSE_LAYER_UNREAD' }, {
      window_hours: null, score_threshold: null, score_off: false,
      window_source: 'unknown', threshold_source: 'unknown',
    }))
    expect(within(row).getByTestId('socfw-auto-triage-deathstar'))
      .toHaveTextContent('live (seen in the execution log, last 24 h) · window not read · threshold not read')
  })

  it('a degraded stage behind a PRESENT layer is never green', async () => {
    const row = await renderRow(block({
      stage: { status: 'degraded', code: 'PF_RESPONSE_LAYER_UNREAD', detail: 'XQL read failed: boom' },
    }))
    expect(row).toHaveAttribute('data-degraded', 'true')
    const chip = within(row).getByTestId('socfw-state-deathstar')
    expect(chip).not.toHaveClass('chip--detected')
    expect(chip).toHaveTextContent('PRESENT — stage degraded')
    const code = within(row).getByTestId('socfw-code-deathstar')
    expect(code).toHaveTextContent('code: PF_RESPONSE_LAYER_UNREAD')
    expect(code).toHaveTextContent('XQL read failed: boom')
  })

  it('a stored layer with no stage verdict is unknown, not green', async () => {
    const row = await renderRow(block({ stage: null }))
    expect(row).toHaveAttribute('data-degraded', 'true')
    expect(within(row).getByTestId('socfw-state-deathstar')).not.toHaveClass('chip--detected')
    expect(within(row).getByTestId('socfw-state-deathstar'))
      .toHaveTextContent('PRESENT — stage verdict not stored')
  })

  it('a missing stale flag reads as stale, never fresh', async () => {
    const { stale, ...noStale } = block()
    void stale
    const row = await renderRow(noStale)
    expect(within(row).getByTestId('socfw-state-deathstar')).toHaveTextContent('unknown — run preflight')
  })

  it('uses the stored window and threshold, not hard-coded ones', async () => {
    const row = await renderRow(block({}, {}, { window_hours: 12, score_threshold: 25 }))
    expect(within(row).getByTestId('socfw-auto-triage-deathstar'))
      .toHaveTextContent('· 12 h · score ≤ 25')
  })

  it('renders "every aged case" when the score is off', async () => {
    const row = await renderRow(block({}, {}, { score_off: true, score_threshold: null }))
    expect(within(row).getByTestId('socfw-auto-triage-deathstar'))
      .toHaveTextContent('· 6 h · every aged case')
  })

  it('never labels a no-shadow mode as "default"', async () => {
    const row = await renderRow(block({}, { mode: 'no_shadow_overrides' }))
    const mode = within(row).getByTestId('socfw-mode-deathstar')
    expect(mode).toHaveTextContent('no_shadow_overrides')
    expect(mode.textContent.toLowerCase()).not.toMatch(/\bdefault\b/)
  })

  it('renders the proves / does-not-prove lines verbatim', async () => {
    const row = await renderRow(block())
    expect(row).toHaveTextContent(`proves: ${PROVES}`)
    expect(row).toHaveTextContent(`does NOT prove: ${DOES_NOT}`)
  })

  it('ABSENT renders as degraded with the runbook path', async () => {
    const row = await renderRow(block({}, { state: 'ABSENT', code: 'RESPONSE_LAYER_ABSENT', framework_version: null }))
    expect(row).toHaveAttribute('data-degraded', 'true')
    expect(within(row).getByTestId('socfw-state-deathstar')).toHaveTextContent('ABSENT')
    expect(within(row).queryByRole('link')).toBeNull()
    expect(within(row).getByText('docs/lab/2026-11-11-run-alongside-soc-framework.md')).toHaveClass('mono')
    expect(row).toHaveTextContent('RESPONSE_LAYER_ABSENT')
    expect(row).toHaveTextContent('read_at: 2026-11-11T10:00:00+00:00')
    expect(within(row).getByTestId('socfw-absent-deathstar')).not.toHaveTextContent(/left behind/)
  })

  it('leftover-lists ABSENT is worded differently from a clean ABSENT', async () => {
    const row = await renderRow(block({}, { state: 'ABSENT', code: 'RESPONSE_LAYER_ABSENT', leftover_lists: true }))
    expect(within(row).getByTestId('socfw-absent-deathstar')).toHaveTextContent(/lists were left behind/)
  })

  it('shows the degradation code and read_at on a PRESENT_UNREAD row', async () => {
    const row = await renderRow(block({}, { state: 'PRESENT_UNREAD', code: 'RESPONSE_LAYER_UNREAD' }))
    expect(row).toHaveAttribute('data-degraded', 'true')
    expect(within(row).getByTestId('socfw-code-deathstar')).toHaveTextContent('code: RESPONSE_LAYER_UNREAD')
  })

  it('UNKNOWN renders degraded with its code', async () => {
    const row = await renderRow(block({}, { state: 'UNKNOWN', code: 'RESPONSE_LAYER_PACKS_UNREADABLE' }))
    expect(row).toHaveAttribute('data-degraded', 'true')
    expect(within(row).getByTestId('socfw-state-deathstar')).toHaveTextContent('UNKNOWN')
    expect(row).toHaveTextContent('RESPONSE_LAYER_PACKS_UNREADABLE')
  })

  it('an unrecognised state string renders degraded', async () => {
    const row = await renderRow(block({}, { state: 'WEIRD', code: 'X' }))
    expect(row).toHaveAttribute('data-degraded', 'true')
  })

  it('a missing layer never renders a PRESENT chip', async () => {
    const row = await renderRow({ state: 'PRESENT', stale: false, read_at: null })
    expect(row).toHaveAttribute('data-degraded', 'true')
    expect(within(row).getByTestId('socfw-state-deathstar')).not.toHaveTextContent(/^PRESENT$/)
    expect(within(row).getByTestId('socfw-state-deathstar')).toHaveTextContent('unknown')
  })

  it('a rejected /api/readiness leaves the card and ladder and reads "could not be read"', async () => {
    installRoutes({
      'GET /api/health': HEALTH_OK,
      'GET /api/credentials/integrations': {
        integrations: [{ name: 'deathstar', kind: 'xsiam_tenant', config: {}, last_verified_ok: true }],
      },
      'GET /api/connectors': { connectors: [] },
      'GET /api/assertions/runs': { runs: [] },
      'GET /api/assertions': { assertions: [], total: 18 },
    }) // /api/readiness -> 404
    render(<EnvironmentContext.Provider value={env}><ReadinessView /></EnvironmentContext.Provider>)
    const row = await screen.findByTestId('socfw-row-deathstar')
    expect(screen.getByTestId('connector-ladder')).toBeInTheDocument()
    expect(screen.getByTestId('rdy-tenant-deathstar')).toBeInTheDocument()
    expect(within(row).getByTestId('socfw-state-deathstar'))
      .toHaveTextContent('unknown — /api/readiness could not be read')
  })

  it('a tenant missing from the readiness list reads "could not be read", not "not probed"', async () => {
    install(block())
    installRoutes({
      'GET /api/health': HEALTH_OK,
      'GET /api/credentials/integrations': {
        integrations: [{ name: 'deathstar', kind: 'xsiam_tenant', config: {}, last_verified_ok: true }],
      },
      'GET /api/connectors': { connectors: [] },
      'GET /api/assertions/runs': { runs: [] },
      'GET /api/assertions': { assertions: [], total: 18 },
      'GET /api/readiness': { integrations: [{ name: 'other', kind: 'xsiam_tenant', response_layer: null }] },
    })
    render(<EnvironmentContext.Provider value={env}><ReadinessView /></EnvironmentContext.Provider>)
    const row = await screen.findByTestId('socfw-row-deathstar')
    expect(within(row).getByTestId('socfw-state-deathstar'))
      .toHaveTextContent('unknown — /api/readiness could not be read')
  })

  it('stale renders "unknown — run preflight"', async () => {
    const row = await renderRow(block({ stale: true }))
    expect(within(row).getByTestId('socfw-state-deathstar')).toHaveTextContent('unknown — run preflight')
    expect(row).not.toHaveAttribute('data-degraded', 'true')
  })

  it('null response_layer renders "not probed", never ABSENT', async () => {
    const row = await renderRow(null)
    expect(within(row).getByTestId('socfw-state-deathstar')).toHaveTextContent('not probed')
    expect(row).not.toHaveTextContent('ABSENT')
    expect(row).not.toHaveAttribute('data-degraded', 'true')
  })
})
