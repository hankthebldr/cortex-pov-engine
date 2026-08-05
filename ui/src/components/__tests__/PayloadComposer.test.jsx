import React from 'react'
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import PayloadComposer from '../console/PayloadComposer.jsx'
import { SUPPLY } from '../console/supplyState.js'
import { installRoutes } from '../../test/mockFetch.js'

void React

const ADAPTER = {
  adapter_id: 'TOOL-LINPEAS', name: 'LinPEAS', version: '20250601', tier: 4,
  upstream: { repo: 'https://github.com/carlospolop/PEASS-ng', license: 'MIT' },
}

const SUPPLY_STAGED = {
  state: SUPPLY.STAGED, label: 'STAGED', chipClass: 'chip chip--detected',
  egress: 'none', egressLabel: 'egress: none — served from this SimCore',
  payloadName: 'linpeas.sh', sha256: 'a'.repeat(64), sizeBytes: 1106683, pinned: true,
  license: 'MIT', sourceUrl: 'https://github.com/…/linpeas.sh', stagePath: '/tmp/linpeas.sh',
  actionable: false,
}

const SCENARIO = {
  scenario_id: 'SIM-CDR-001',
  name: 'Container enumeration',
  steps: [{
    id: 'step-05',
    name: 'Run LinPEAS',
    command: 'timeout 60 {adapter:TOOL-LINPEAS} -q',
    expected_detections: [
      { ttp_ref: 'TTP-2026-0022', type: 'BIOC', detection_id: 'bioc-cdr-001-linpeas-privilege-escalation-tool-in-container', description: 'LinPEAS execution' },
      { ttp_ref: 'TTP-2026-0022', type: 'BIOC', detection_id: 'bioc-cdr-001-automated-discovery-command-burst-in-container', description: 'discovery burst' },
    ],
  }],
}

const CARD = {
  id: 'TTP-2026-0022',
  detections: {
    biocs: [
      { name: 'CDR-001 LinPEAS Privilege Escalation Tool In Container', logic: '| filter action_process_image_command_line contains "linpeas"' },
      { name: 'CDR-001 Automated Discovery Command Burst In Container', logic: '| filter action_process_image_name in ("id","uname")' },
    ],
  },
}

const COMPOSITION = {
  composition_id: 'cmp_7d41a9c0f1e2b3a4',
  scenario_id: 'SIM-CDR-001',
  artifacts: [{
    name: 'linpeas.sh', sha256: 'a'.repeat(64), size_bytes: 1106683,
    dest_path: '/tmp/.cache/sysinfo.sh', dest_basename: 'sysinfo.sh', mode: '0755',
    kind: 'file', renamed: true, adapter_id: 'TOOL-LINPEAS', origin: 'adapter_ref',
    license: 'MIT', pinned: true,
  }],
  unstaged_adapters: [], air_gapped: true, renamed_count: 1,
  warnings: [{ code: 'FILENAME_KEYED_DETECTIONS_SUPPRESSED', detail: 'linpeas.sh is staged on disk as sysinfo.sh…' }],
}

const routes = (extra = {}) => installRoutes({
  'GET /api/scenarios/SIM-CDR-001': SCENARIO,
  'GET /api/ttps/TTP-2026-0022': CARD,
  'POST /api/shelf/compose': COMPOSITION,
  'GET /api/openapi.json': { components: { schemas: { LaunchRequest: { properties: { scenario_id: {}, mode: {} } } } } },
  ...extra,
})

const renderComposer = (props = {}) => render(
  <PayloadComposer
    adapter={ADAPTER}
    supply={SUPPLY_STAGED}
    candidateScenarioIds={['SIM-CDR-001']}
    {...props}
  />,
)

describe('<PayloadComposer /> — the rename control', () => {
  it('defaults to VERBATIM so ignoring the control changes nothing', async () => {
    routes()
    renderComposer()
    expect(screen.getByTestId('compose-preset-verbatim')).toBeChecked()
    await waitFor(() => expect(screen.getByTestId('impact-verbatim')).toBeInTheDocument())
    expect(screen.getByTestId('impact-verbatim')).toHaveTextContent(/No detections go dark/)
  })

  it('partitions detections once a neutral alias is chosen', async () => {
    routes()
    renderComposer()
    fireEvent.click(screen.getByTestId('compose-preset-alias'))
    // Assert the SETTLED partition. The card fetch is async, and a mid-flight
    // assertion would be asserting the loading state, not the answer.
    await waitFor(() => expect(screen.getByTestId('impact-panel'))
      .toHaveTextContent(/Not keyed on the filename \(1\)/))
    expect(
      screen.getByTestId('impact-namekeyed-bioc-cdr-001-linpeas-privilege-escalation-tool-in-container'),
    ).toBeInTheDocument()
    expect(screen.getByTestId('impact-panel')).toHaveTextContent(/which is the finding/i)
  })

  it('reports UNKNOWN rather than "behavioural" when a TTP card cannot be read', async () => {
    routes({ 'GET /api/ttps/TTP-2026-0022': () => new Response('{}', { status: 500 }) })
    renderComposer()
    fireEvent.click(screen.getByTestId('compose-preset-alias'))
    await waitFor(() => expect(
      screen.getByTestId('impact-unknown-bioc-cdr-001-automated-discovery-command-burst-in-container'),
    ).toBeInTheDocument())
    expect(screen.getByTestId('impact-panel')).toHaveTextContent(/query text NOT scanned/)
    expect(screen.getByTestId('impact-panel')).toHaveTextContent(/not.*counted as behavioural/i)
  })

  it('validates a custom path against the staging allowlist', async () => {
    routes()
    renderComposer()
    fireEvent.change(screen.getByTestId('compose-custom-path'), { target: { value: '/usr/bin/x' } })
    await waitFor(() => expect(screen.getByTestId('compose-path-error')).toBeInTheDocument())
    fireEvent.change(screen.getByTestId('compose-custom-path'), { target: { value: 'a; rm -rf /' } })
    await waitFor(() => expect(screen.getByTestId('compose-path-error')).toHaveTextContent(/absolute/))
    fireEvent.change(screen.getByTestId('compose-custom-path'), { target: { value: '/tmp/.cache/sysinfo.sh' } })
    await waitFor(() => expect(screen.queryByTestId('compose-path-error')).not.toBeInTheDocument())
  })

  it('renders the resolved plan with its digest and the suppression warning', async () => {
    routes()
    renderComposer()
    fireEvent.click(screen.getByTestId('compose-preset-alias'))
    await waitFor(() => expect(screen.getByTestId('compose-artifact-linpeas.sh')).toBeInTheDocument())
    expect(screen.getByTestId('compose-artifact-linpeas.sh')).toHaveTextContent('/tmp/.cache/sysinfo.sh')
    expect(screen.getByTestId('compose-warning-FILENAME_KEYED_DETECTIONS_SUPPRESSED')).toBeInTheDocument()
  })

  it('surfaces a 409 from /compose verbatim instead of an empty plan', async () => {
    routes({
      'POST /api/shelf/compose': () => new Response(
        JSON.stringify({ detail: { error: 'Payload not staged', code: 'PAYLOAD_NOT_STAGED', detail: 'linpeas.sh is not on the shelf' } }),
        { status: 409, headers: { 'content-type': 'application/json' } },
      ),
    })
    renderComposer()
    await waitFor(() => expect(screen.getByTestId('compose-plan-error')).toBeInTheDocument())
    expect(screen.getByTestId('compose-plan-error')).toHaveTextContent('PAYLOAD_NOT_STAGED')
  })

  it('refuses to offer "arm" when this SimCore would ignore a payload_plan', async () => {
    routes()
    renderComposer()
    await waitFor(() => expect(screen.getByTestId('compose-no-plan-support')).toBeInTheDocument())
    expect(screen.queryByRole('button', { name: /Compose & arm/ })).not.toBeInTheDocument()
    expect(screen.getByTestId('compose-no-plan-support'))
      .toHaveTextContent(/silently ignored/)
  })

  it('offers "arm" when the schema does carry payload_plan', async () => {
    routes({
      'GET /api/openapi.json': {
        components: { schemas: { LaunchRequest: { properties: { scenario_id: {}, payload_plan: {} } } } },
      },
    })
    const onArm = vi.fn()
    renderComposer({ onArm })
    await waitFor(() => expect(screen.getByRole('button', { name: /Compose & arm/ })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: /Compose & arm/ }))
    expect(onArm).toHaveBeenCalledWith('SIM-CDR-001', expect.objectContaining({ composition_id: 'cmp_7d41a9c0f1e2b3a4' }))
  })

  it('flags a step that downloads the tool itself as one a rename cannot change', async () => {
    routes({
      'GET /api/scenarios/SIM-CDR-001': {
        ...SCENARIO,
        steps: [{
          ...SCENARIO.steps[0],
          command: 'curl -sSL https://github.com/carlospolop/PEASS-ng/releases/latest/download/linpeas.sh -o /tmp/linpeas.sh && bash /tmp/linpeas.sh -q',
        }],
      },
    })
    renderComposer()
    fireEvent.click(screen.getByTestId('compose-preset-alias'))
    await waitFor(() => expect(screen.getByTestId('compose-inline-step-05')).toBeInTheDocument())
    expect(screen.getByTestId('compose-inline-step-05')).toHaveTextContent(/still reaches the internet/)
  })

  it('says so plainly when no scenario references the adapter', () => {
    routes()
    renderComposer({ candidateScenarioIds: [] })
    expect(screen.getByText(/No scenario references/)).toBeInTheDocument()
  })
})
