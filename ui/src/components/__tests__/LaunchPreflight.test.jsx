import React from 'react'
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react'
import '@testing-library/jest-dom'
import { installRoutes } from '../../test/mockFetch.js'
import LaunchView from '../console/LaunchView.jsx'

/**
 * The preflight has to live AT the launch button.
 *
 * A readiness page is visited once, on a good day. These assertions pin the
 * check to the surface the DC is already on when the customer is watching.
 */

const SCENARIO = {
  scenario_id: 'SIM-EDR-022',
  name: 'Shelf-staged privesc enumeration',
  plane: 'EDR',
  push_supported: true,
  pull_supported: true,
  execution_identity: { default: 'www-data', options: ['www-data', 'root'] },
  external_tools: [{ adapter_ref: 'TOOL-LINPEAS' }],
  steps: [{ id: 'step-01', identity: 'www-data', platforms: ['linux'] }],
  primary_kpi: 'MTTD',
  threshold: { kpi: 'MTTD', op: '<=', value: 300 },
}

const AGENT = {
  agent_id: 'jumpbox-01', hostname: 'ip-10-0-1-24', os: 'linux',
  capabilities: ['shell', 'identity-harness', 'artifact-fetch'],
  status: 'online', last_seen_age_seconds: 2,
}

const ADAPTERS = {
  adapters: [{ adapter_id: 'TOOL-LINPEAS', tier: 4, name: 'LinPEAS', license: 'MIT' }],
  total: 1,
}

const ROUTES = (over = {}) => ({
  'GET /api/tools/adapters': ADAPTERS,
  'GET /api/agents': { agents: [AGENT], total: 1 },
  'GET /api/credentials/integrations': { integrations: [] },
  'GET /api/shelf/payloads': {
    payloads: [], dist_dir: '/app/payloads', total: 0,
    declared: [{
      name: 'linpeas.sh', adapter_id: 'TOOL-LINPEAS',
      url: 'https://github.com/carlospolop/PEASS-ng/releases/download/20250601/linpeas.sh',
    }],
  },
  'GET /api/shelf/artifacts': { artifacts: null },
  ...over,
})

const TARGET = { kind: 'agent', id: 'jumpbox-01', label: 'jumpbox-01' }

beforeEach(() => { installRoutes(ROUTES()) })
afterEach(() => { vi.restoreAllMocks() })

describe('LaunchView preflight', () => {
  it('renders the preflight card on the launch surface', async () => {
    render(<LaunchView scenario={SCENARIO} selectedTarget={TARGET} />)
    await waitFor(() => expect(screen.getByTestId('scenario-preflight')).toBeInTheDocument())
    for (const id of ['tools', 'target', 'platform', 'identity', 'measurement']) {
      expect(screen.getByTestId(`preflight-${id}`)).toBeInTheDocument()
    }
  })

  it('warns about a tool that needs internet on the TARGET, before the run rather than after', async () => {
    render(<LaunchView scenario={SCENARIO} selectedTarget={TARGET} />)
    await waitFor(() => {
      expect(screen.getByTestId('preflight-tools')).toHaveAttribute('data-status', 'warn')
    })
    expect(screen.getByTestId('preflight-tools')).toHaveTextContent(/need internet access on the target/)
    expect(screen.getByTestId('preflight-tools')).toHaveTextContent(/runs without its tool/)
  })

  it('never claims READY while a precondition is unchecked', async () => {
    render(<LaunchView scenario={SCENARIO} selectedTarget={TARGET} />)
    await waitFor(() => expect(screen.getByTestId('preflight-verdict')).toBeInTheDocument())
    // The identity account has not been probed on this target, so the run is
    // NOT ready — it is degraded-or-unknown, and the card says which.
    const verdict = screen.getByTestId('preflight-verdict')
    expect(verdict).not.toHaveTextContent('Ready')
    expect(within(verdict).getByRole('img').getAttribute('aria-label')).toMatch(/^(Degraded|Unknown)$/)
    expect(screen.getByTestId('preflight-identity')).toHaveAttribute('data-status', 'unknown')
  })

  // The data path is one line: a StatusIcon and one word, the label, the count.
  // What it means and what to do sit behind a collapsed "Why", two short lines.
  it('shows the data path as one line, with its explanation behind a collapsed Why', async () => {
    installRoutes(ROUTES({
      'GET /api/scenarios/:id/doors': {
        readiness: {
          scenario_id: 'SIM-EDR-022', state: 'needs_doors', scorable: 0, total: 10,
          route_blocked: 10, underived: 0, unevaluated: 0, not_open: [],
        },
      },
      'GET /api/doors': { doors: [], registry_loaded: true, tenant_verified: 0, outbound_calls: 0 },
    }))
    render(<LaunchView scenario={SCENARIO} selectedTarget={TARGET} />)
    const row = await screen.findByTestId('preflight-data-path')
    await waitFor(() => expect(row).toHaveAttribute('data-status', 'warn'))
    expect(within(row).getByRole('img', { name: 'Degraded' })).toBeInTheDocument()
    expect(row.querySelector('.chip')).toBeNull()

    const why = row.querySelector('details')
    expect(why).not.toBeNull()
    expect(why.open).toBe(false)
    const summary = why.querySelector('summary')
    expect(summary).toHaveTextContent('Data path')
    expect(summary).toHaveTextContent('0 of 10 detections can fire')
    expect(summary).toHaveTextContent(/Why$/)
    expect(summary).not.toHaveTextContent(/XDM/)

    const lines = [...why.children].filter((el) => el.tagName !== 'SUMMARY')
      .flatMap((el) => [...el.children])
    expect(lines.map((el) => el.textContent)).toEqual([
      'no detection here is confirmed able to fire yet; each one is still scored as today; '
        + '10 of 10 on an unresolved collector route',
      'BIOC and IOC never fire on a collector route; Analytics and ABIOC need an XDM rule, '
        + 'XQL and Correlation the dataset.',
    ])

    fireEvent.click(summary)
    expect(why.open).toBe(true)
  })

  // The card is its checks. Why they are checked first is one line, kept as
  // the Preflight label's tooltip rather than a paragraph above them.
  it('keeps why it checks first in the Preflight tooltip, not a paragraph', async () => {
    render(<LaunchView scenario={SCENARIO} selectedTarget={TARGET} />)
    const card = await screen.findByTestId('scenario-preflight')
    expect(card).not.toHaveTextContent(/every failure below|same artefact/)
    expect(within(card).getByText('Preflight')).toHaveAttribute(
      'title', "A step that did not run as authored reads as a gap in the customer's coverage.")
  })

  // Tool consent is one compact row per gated tool class, like the EAL row:
  // the checkbox and one sentence, and "Required" while the run waits on it.
  it('asks for tool consent in one compact row, Required until it is ticked', async () => {
    installRoutes(ROUTES({
      'GET /api/tools/adapters': {
        adapters: [{ ...ADAPTERS.adapters[0], safety_class: 'dual-use-lab-only' }], total: 1,
      },
    }))
    render(<LaunchView scenario={SCENARIO} selectedTarget={TARGET} />)
    const box = await screen.findByRole('checkbox', { name: 'I authorize lab-only use of dual-use tools (LinPEAS)' })
    const row = box.closest('.launch-consent__row')
    expect(within(row).getByRole('img', { name: 'Required' })).toBeInTheDocument()
    expect(screen.queryByText(/tool consent required|blocking run/i)).toBeNull()
    expect(screen.getByTestId('launch-blockers')).toHaveTextContent(/lab-only consent/)

    fireEvent.click(box)
    expect(box).toBeChecked()
    expect(within(row).queryByRole('img', { name: 'Required' })).toBeNull()
    expect(screen.getByTestId('launch-blockers')).not.toHaveTextContent(/lab-only consent/)
  })

  // Who runs the scenario's cleanup depends on the beacon chosen: one that
  // advertises `cleanup` runs it after the run; an older one does not (#361).
  it('says who runs the teardown for the agent that is chosen', async () => {
    const CURRENT = {
      ...AGENT, agent_id: 'lab-new-01', hostname: 'lab-new-01',
      capabilities: ['shell', 'identity-harness', 'artifact-fetch', 'cleanup'],
    }
    installRoutes(ROUTES({ 'GET /api/agents': { agents: [AGENT, CURRENT], total: 2 } }))
    const withCleanup = { ...SCENARIO, cleanup: { commands: ['rm -f /tmp/x', 'userdel t'] } }
    const teardown = () => screen.getByText('Teardown').closest('.new-run__plan-row')
    const { rerender } = render(
      <LaunchView scenario={withCleanup} selectedTarget={{ kind: 'agent', id: 'lab-new-01', label: 'lab-new-01' }} />)
    await waitFor(() => expect(teardown()).toHaveTextContent('2 commands, run by the agent after the run'))

    rerender(<LaunchView scenario={withCleanup} selectedTarget={TARGET} />)
    await waitFor(() => expect(teardown()).toHaveTextContent('2 commands, run them yourself'))

    rerender(<LaunchView scenario={withCleanup} selectedTarget={{ kind: 'push', id: 'bundle', label: 'Offline bundle' }} />)
    await waitFor(() => expect(teardown()).toHaveTextContent('2 commands, run by the bundle'))
  })

  it('folds a BLOCKING preflight row into the launch blockers list', async () => {
    installRoutes(ROUTES({
      'GET /api/agents': { agents: [{ ...AGENT, status: 'offline', last_seen_age_seconds: 90000 }] },
    }))
    render(<LaunchView scenario={SCENARIO} selectedTarget={TARGET} />)
    await waitFor(() => {
      expect(screen.getByTestId('launch-blockers')).toHaveTextContent(/beacon offline/i)
    })
    // …and the button the DC would have clicked is dead, with the reason next to it.
    expect(screen.getByRole('button', { name: 'Run' })).toBeDisabled()
  })

  it('does NOT disable launch for a mere warning — ceremony is not safety', async () => {
    render(<LaunchView scenario={SCENARIO} selectedTarget={TARGET} />)
    await waitFor(() => expect(screen.getByTestId('preflight-tools')).toHaveAttribute('data-status', 'warn'))
    fireEvent.click(await screen.findByTestId('new-run-authorised'))
    expect(screen.getByRole('button', { name: 'Run' })).not.toBeDisabled()
  })

  it('surfaces a failed door readiness fetch in the Data path check instead of "not reported"', async () => {
    installRoutes(ROUTES({
      'GET /api/scenarios/:id/doors': () => new Response(
        JSON.stringify({ error: 'A detection card has an unrecognised shape', code: 'DOOR_DERIVATION_FAILED', detail: 'x' }),
        { status: 500, headers: { 'content-type': 'application/json' } }),
      'GET /api/doors': { doors: [], registry_loaded: true, tenant_verified: 0, outbound_calls: 0 },
    }))
    render(<LaunchView scenario={SCENARIO} selectedTarget={TARGET} />)
    await waitFor(() => {
      expect(screen.getByTestId('preflight-data-path')).toHaveTextContent(/door readiness unavailable/)
    })
    expect(screen.getByTestId('preflight-data-path')).toHaveAttribute('data-status', 'unknown')
    expect(screen.getByTestId('preflight-data-path')).not.toHaveTextContent(/not reported/)
  })

  it('says a scoreable scenario with no credential will resolve pending, not fail', async () => {
    render(<LaunchView scenario={SCENARIO} selectedTarget={TARGET} />)
    await waitFor(() => {
      expect(screen.getByTestId('preflight-measurement')).toHaveTextContent(/still owed, NOT a failed detection/)
    })
  })
})

/**
 * UI-08 / UI-04 — a pull launch the SERVER refuses must read as clearly as a
 * refused download: code AND structured detail, as an alert, next to the button.
 * The run-integrity refusals carry an OBJECT detail (unresolved steps, missing
 * agents); the old path kept only string details, so the step ids the DC has to
 * fix were dropped on the floor.
 */
describe('LaunchView — refused pull launch renders code and detail', () => {
  const refuse = (body) => () => new Response(JSON.stringify(body), {
    status: 409, headers: { 'content-type': 'application/json' },
  })

  it('PULL_TARGET_UNSATISFIABLE names every unresolved step and its reason', async () => {
    installRoutes(ROUTES({
      'POST /api/run': refuse({
        detail: {
          error: "PULL_TARGET_UNSATISFIABLE: agent 'jumpbox-01' is posix and cannot run step(s) "
            + '[step-01 (POSIX_COMMAND_UNAVAILABLE)] — declare platform_variants.linux on those steps',
          code: 'PULL_TARGET_UNSATISFIABLE',
          detail: {
            target: 'posix', agent_id: 'jumpbox-01', agent_os: 'linux',
            unresolved: [
              { step_id: 'step-01', reason: 'POSIX_COMMAND_UNAVAILABLE' },
              { step_id: 'step-04', reason: 'POSIX_COMMAND_UNAVAILABLE' },
            ],
            hint: 'declare platform_variants.linux on those steps, or launch against a Windows agent',
          },
        },
      }),
    }))
    render(<LaunchView scenario={SCENARIO} selectedTarget={TARGET} />)
    fireEvent.click(await screen.findByTestId('new-run-authorised'))
    const btn = await screen.findByRole('button', { name: 'Run' })
    await waitFor(() => expect(btn).not.toBeDisabled())
    fireEvent.click(btn)
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('PULL_TARGET_UNSATISFIABLE')
    // step-04 appears ONLY in the structured detail, never in the message.
    expect(alert).toHaveTextContent('step-04')
    expect(alert).toHaveTextContent(/launch against a Windows agent/)
  })

  it('TARGET_AGENT_NOT_ENROLLED names the steps that reference the missing agent', async () => {
    installRoutes(ROUTES({
      'POST /api/run': refuse({
        detail: {
          error: "TARGET_AGENT_NOT_ENROLLED: composed step target(s) ['ghost-02'] are not enrolled",
          code: 'TARGET_AGENT_NOT_ENROLLED',
          detail: { missing_agents: ['ghost-02'], referencing_steps: { 'ghost-02': ['step-03'] } },
        },
      }),
    }))
    render(<LaunchView scenario={SCENARIO} selectedTarget={TARGET} />)
    fireEvent.click(await screen.findByTestId('new-run-authorised'))
    const btn = await screen.findByRole('button', { name: 'Run' })
    await waitFor(() => expect(btn).not.toBeDisabled())
    fireEvent.click(btn)
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('TARGET_AGENT_NOT_ENROLLED')
    expect(alert).toHaveTextContent('step-03')
  })
})
