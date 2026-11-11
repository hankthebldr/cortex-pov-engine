import React from 'react'
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
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
    source: 'tenant_config', ...atOver,
  },
  ai_close: 'shadow', leftover_lists: false, sources_used: ['tenant_config'],
  read_at: '2026-11-11T10:00:00+00:00', ...over,
})

const block = (over = {}, layerOver = {}, atOver = {}) => ({
  layer: layer(layerOver, atOver), state: layerOver.state || 'PRESENT',
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

beforeEach(() => {})
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

  it('renders Auto Triage as "live · 6 h · score ≤ 40"', async () => {
    const row = await renderRow(block())
    expect(within(row).getByTestId('socfw-auto-triage-deathstar'))
      .toHaveTextContent('live · 6 h · score ≤ 40')
  })

  it('uses the stored window and threshold, not hard-coded ones', async () => {
    const row = await renderRow(block({}, {}, { window_hours: 12, score_threshold: 25 }))
    expect(within(row).getByTestId('socfw-auto-triage-deathstar'))
      .toHaveTextContent('live · 12 h · score ≤ 25')
  })

  it('renders "every aged case" when the score is off', async () => {
    const row = await renderRow(block({}, {}, { score_off: true, score_threshold: null }))
    expect(within(row).getByTestId('socfw-auto-triage-deathstar'))
      .toHaveTextContent('live · 6 h · every aged case')
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

  it('ABSENT renders as degraded with the runbook link', async () => {
    const row = await renderRow(block({}, { state: 'ABSENT', code: 'RESPONSE_LAYER_ABSENT', framework_version: null }))
    expect(row).toHaveAttribute('data-degraded', 'true')
    expect(within(row).getByTestId('socfw-state-deathstar')).toHaveTextContent('ABSENT')
    const link = within(row).getByRole('link', { name: /run-alongside-soc-framework/i })
    expect(link).toHaveAttribute('href', expect.stringContaining('docs/lab/2026-11-11-run-alongside-soc-framework.md'))
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
