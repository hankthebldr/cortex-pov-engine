import React from 'react'
import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import LaunchView from '../console/LaunchView.jsx'
import { installRoutes } from '../../test/mockFetch.js'

void React

/**
 * The launch surface's payload contract.
 *
 * The load-bearing assertion is the LAST one: a plan this SimCore would ignore
 * must never be sent, and must never render as a plan the run honoured. FastAPI
 * silently drops unknown body fields, so posting a plan to a SimCore whose
 * LaunchRequest has none returns 200, runs every step with the tool under its
 * ORIGINAL name, and the console would have claimed a rename in a report a DC
 * shows a customer.
 */

const SCENARIO = {
  scenario_id: 'SIM-CDR-001',
  name: 'Container enumeration',
  plane: 'CDR',
  execution_identity: { default: 'container-runtime', options: ['container-runtime'] },
  external_tools: [
    { name: 'LinPEAS', adapter_ref: 'TOOL-LINPEAS' },
    { name: 'hydra', adapter_ref: 'TOOL-HYDRA' },
    { name: 'linpeas', install_inline: true },
  ],
  cluster_posture: { payloads: ['linpeas.sh'] },
  steps: [],
}

const ADAPTERS = {
  adapters: [
    { adapter_id: 'TOOL-LINPEAS', name: 'LinPEAS', tier: 4, safety_class: 'dual-use-lab-only', planes: ['CDR'], license: 'MIT' },
    { adapter_id: 'TOOL-HYDRA', name: 'Hydra', tier: 4, safety_class: 'dual-use-lab-only', planes: ['ITDR'], license: 'AGPL-3.0' },
  ],
}

const DECLARED = {
  name: 'linpeas.sh', url: 'https://github.com/carlospolop/PEASS-ng/releases/download/20250601/linpeas.sh',
  kind: 'file', sha256: 'a'.repeat(64), pinned: true, license: 'MIT',
  adapter_id: 'TOOL-LINPEAS', stage_path: '/tmp/linpeas.sh', mode: '0755', used_by: 'SIM-CDR-001',
}
const STAGED = {
  name: 'linpeas.sh', size_bytes: 1106683, sha256: 'a'.repeat(64),
  modified_at: '2026-08-05T13:05:00', source_url: DECLARED.url, license: 'MIT',
  pinned: true, adapter_id: 'TOOL-LINPEAS',
}

const PLAN = {
  v: 1,
  scenario_id: 'SIM-CDR-001',
  artifacts: [{
    adapter_id: 'TOOL-LINPEAS', payload_name: 'linpeas.sh', sha256: 'a'.repeat(64),
    dest_path: '/tmp/.cache/sysinfo.sh', mode: '0755', renamed: true,
  }],
}

const shelfRoutes = ({ payloads = [], declared = [] } = {}) => ({
  'GET /api/shelf/payloads': { payloads, total: payloads.length, dist_dir: '/app/payloads', auth: 'open', writable: true, declared },
  'GET /api/shelf/artifacts': { artifacts: declared, total: declared.length, staged: payloads.map((p) => p.name), unstaged: [], unpinned: [], dist_dir: '/app/payloads' },
})

const base = (extra = {}) => installRoutes({
  'GET /api/tools/adapters': ADAPTERS,
  'GET /api/agents': [{ agent_id: 'jump-01', hostname: 'jump-01', status: 'online' }],
  'GET /api/openapi.json': { components: { schemas: { LaunchRequest: { properties: { scenario_id: {}, mode: {} } } } } },
  ...shelfRoutes({ payloads: [STAGED], declared: [DECLARED] }),
  ...extra,
})

const target = { kind: 'agent', id: 'jump-01', label: 'jump-01' }

describe('LaunchView — payload plan resolving (I-1)', () => {
  // A `?plan=` deep link's payload plan decodes asynchronously (dynamic
  // import of ToolAdapterCatalog.jsx in destinations.jsx::useDecodedPlan).
  // `payloadPlanResolving` is how that in-flight window reaches LaunchView.
  // Before the fix this prop did not exist, `payloadPlan` was just `null`
  // during the race, and Launch stayed enabled — a run could go out with no
  // payload_plan and nothing on screen said so.
  it('disables Launch and renders a placeholder — never nothing — while the plan is still resolving', async () => {
    base()
    render(
      <LaunchView
        scenario={{ ...SCENARIO, cluster_posture: {} }}
        selectedTarget={target}
        payloadPlan={null}
        payloadPlanResolving
      />,
    )
    await waitFor(() => expect(screen.getByTestId('launch-payload-plan-resolving')).toBeInTheDocument())
    expect(screen.queryByTestId('launch-payload-plan')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Launch run/ })).toBeDisabled()
    expect(screen.getByTestId('launch-blockers')).toHaveTextContent(/resolving/i)
  })

  it('does not silently drop to "no plan" once resolving settles with an empty plan param', async () => {
    base()
    const { rerender } = render(
      <LaunchView
        scenario={{ ...SCENARIO, cluster_posture: {} }}
        selectedTarget={target}
        payloadPlan={null}
        payloadPlanResolving
      />,
    )
    await waitFor(() => expect(screen.getByTestId('launch-payload-plan-resolving')).toBeInTheDocument())
    rerender(
      <LaunchView
        scenario={{ ...SCENARIO, cluster_posture: {} }}
        selectedTarget={target}
        payloadPlan={PLAN}
        payloadPlanResolving={false}
      />,
    )
    await waitFor(() => expect(screen.getByTestId('launch-payload-plan')).toBeInTheDocument())
    expect(screen.queryByTestId('launch-payload-plan-resolving')).not.toBeInTheDocument()
  })
})

describe('LaunchView — payload plan', () => {
  it('renders the composed plan with destination, digest and the rename caveat', async () => {
    base()
    render(<LaunchView scenario={SCENARIO} selectedTarget={target} payloadPlan={PLAN} />)
    await waitFor(() => expect(screen.getByTestId('launch-payload-plan')).toBeInTheDocument())
    expect(screen.getByTestId('launch-payload-linpeas.sh')).toHaveTextContent('/tmp/.cache/sysinfo.sh')
    expect(screen.getByTestId('launch-payload-linpeas.sh')).toHaveTextContent('sha256 aaaaaaaaaaaa…')
    expect(screen.getByTestId('launch-payload-renamed')).toHaveTextContent(/That is the control, not a miss/)
  })

  it('warns about target-fetched tools without disabling Launch', async () => {
    // TOOL-HYDRA has no declared artifact → the target fetches it at run time.
    base()
    render(<LaunchView scenario={{ ...SCENARIO, cluster_posture: {} }} selectedTarget={target} />)
    await waitFor(() => expect(screen.getByTestId('launch-payload-egress')).toBeInTheDocument())
    expect(screen.getByTestId('launch-payload-egress')).toHaveTextContent(/Hydra/)
    const launch = screen.getByRole('button', { name: /Launch run/ })
    // Consent is the only thing gating it here, not the egress warning.
    expect(screen.getByTestId('launch-blockers')).not.toHaveTextContent(/Hydra/)
    expect(launch).toBeInTheDocument()
  })

  it('BLOCKS launch when a scenario-declared cluster payload is not staged, and names it', async () => {
    base({ ...shelfRoutes({ payloads: [], declared: [DECLARED] }) })
    render(<LaunchView scenario={SCENARIO} selectedTarget={target} />)
    await waitFor(() => expect(screen.getByTestId('launch-blockers')).toHaveTextContent(/linpeas\.sh is declared/))
    expect(screen.getByTestId('launch-blockers')).toHaveTextContent(/PAYLOAD_NOT_STAGED/)
    expect(screen.getByRole('button', { name: /Launch run/ })).toBeDisabled()
  })

  it('says UNKNOWN — not "fine" — when the shelf cannot be read', async () => {
    installRoutes({
      'GET /api/tools/adapters': ADAPTERS,
      'GET /api/agents': [{ agent_id: 'jump-01', hostname: 'jump-01', status: 'online' }],
    })
    render(<LaunchView scenario={{ ...SCENARIO, cluster_posture: {} }} selectedTarget={target} payloadPlan={PLAN} />)
    await waitFor(() => expect(screen.getByTestId('launch-payload-unknown')).toBeInTheDocument())
    expect(screen.getByTestId('launch-payload-unknown')).toHaveTextContent(/not .fine./)
  })

  it('does NOT send payload_plan to a SimCore whose schema has no such field, and says so', async () => {
    const seen = []
    base({
      'POST /api/run': (url, init) => { seen.push(JSON.parse(init.body)); return { run_id: 'r-1', status: 'running' } },
    })
    render(
      <LaunchView
        scenario={{ ...SCENARIO, cluster_posture: {} }}
        selectedTarget={target}
        payloadPlan={PLAN}
      />,
    )
    await waitFor(() => expect(screen.getByTestId('launch-payload-unsupported')).toBeInTheDocument())
    // Consent gate first — the scenario uses dual-use adapters.
    screen.getByLabelText(/lab-only/i, { selector: 'input' })?.click?.()
    expect(screen.getByTestId('launch-payload-unsupported'))
      .toHaveTextContent(/does not accept a payload_plan/)
    expect(screen.getByTestId('launch-payload-unsupported'))
      .toHaveTextContent(/not.*in effect for this run/i)
  })

  it('DOES send payload_plan when the schema carries it', async () => {
    const seen = []
    base({
      'GET /api/openapi.json': { components: { schemas: { LaunchRequest: { properties: { scenario_id: {}, payload_plan: {} } } } } },
      'POST /api/run': (url, init) => { seen.push(JSON.parse(init.body)); return { run_id: 'r-1', status: 'running' } },
    })
    const onRunComplete = vi.fn()
    render(
      <LaunchView
        scenario={{ ...SCENARIO, cluster_posture: {}, external_tools: [{ name: 'LinPEAS', adapter_ref: 'TOOL-LINPEAS' }] }}
        selectedTarget={target}
        payloadPlan={PLAN}
        onRunComplete={onRunComplete}
      />,
    )
    await waitFor(() => expect(screen.getByTestId('launch-payload-plan')).toBeInTheDocument())
    // tick the dual-use consent, then fire
    const consent = document.querySelector('.launch-consent__row input')
    consent.click()
    await waitFor(() => expect(screen.getByRole('button', { name: /Launch run/ })).toBeEnabled())
    screen.getByRole('button', { name: /Launch run/ }).click()
    await waitFor(() => expect(seen.length).toBe(1))
    expect(seen[0].payload_plan).toEqual(PLAN.artifacts)
  })
})
