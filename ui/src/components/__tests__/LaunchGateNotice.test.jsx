/**
 * The container-agent launch gate, as the console shows it.
 *
 * (a) advisory `warnings[]` from a successful launch are rendered, not dropped;
 * (b) an AGENT_TOOL_ABSENT refusal names the missing tools per step and offers an
 *     explicit "Launch anyway" that re-sends the SAME launch with
 *     allow_missing_tools: true;
 * (c) an AGENT_POSTURE_INSUFFICIENT refusal names what is missing and the
 *     satisfying profile, with no override.
 */
import React from 'react'
import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import LaunchView from '../console/LaunchView.jsx'
import { installRoutes } from '../../test/mockFetch.js'

void React

const SCENARIO = {
  scenario_id: 'SIM-CDR-009',
  name: 'Container probe',
  plane: 'CDR',
  execution_identity: { default: 'direct', options: ['direct'] },
  steps: [],
}
const target = { kind: 'agent', id: 'pod-1', label: 'pod-1' }

function json(status, body) {
  return new Response(JSON.stringify(body), {
    status, headers: { 'content-type': 'application/json' },
  })
}

function routes(post) {
  const posted = []
  installRoutes({
    'GET /api/tools/adapters': { adapters: [] },
    'GET /api/agents': [{ agent_id: 'pod-1', hostname: 'pod-1', status: 'online' }],
    'GET /api/runs': [],
    'GET /api/openapi.json': { components: { schemas: { LaunchRequest: { properties: {} } } } },
    'GET /api/shelf/payloads': { payloads: [], total: 0, declared: [] },
    'GET /api/shelf/artifacts': { artifacts: [], total: 0, staged: [], unstaged: [], unpinned: [] },
    'GET /api/credentials/integrations': { integrations: [] },
    'POST /api/run': (_u, init) => {
      const body = JSON.parse(init.body)
      posted.push(body)
      return post(body, posted.length)
    },
  })
  return posted
}

async function fire() {
  const btn = await screen.findByRole('button', { name: /Launch run/ })
  await waitFor(() => expect(btn).toBeEnabled())
  await userEvent.click(btn)
}

const TOOL_ABSENT = {
  detail: {
    error: "AGENT_TOOL_ABSENT: agent 'pod-1': the agent pod lacks tools the scenario's steps call: step-02: jq",
    code: 'AGENT_TOOL_ABSENT',
    detail: {
      missing: [{ step: 'step-02', tools: ['jq', 'nmap'] }],
      prepare_status: 'PREPARE_DEGRADED',
      override: 'allow_missing_tools',
      agent_id: 'pod-1',
    },
  },
}

describe('launch gate notice', () => {
  it('shows launch warnings and holds on the launcher until the operator continues', async () => {
    const onRunComplete = vi.fn()
    routes(() => ({
      run_id: 'r-1', mode: 'pull',
      warnings: [
        { code: 'AGENT_NO_RBAC_FOR_KUBECTL', steps: ['step-03'], agent_id: 'pod-1' },
        { code: 'AGENT_TOOL_ROSTER_UNKNOWN', detail: 'the agent did not report its tool roster; tools were not checked', agent_id: 'pod-1' },
      ],
    }))
    render(<LaunchView scenario={SCENARIO} selectedTarget={target} onRunComplete={onRunComplete} />)
    await fire()
    const notice = await screen.findByTestId('launch-gate-warnings')
    expect(notice).toHaveTextContent('AGENT_NO_RBAC_FOR_KUBECTL')
    expect(notice).toHaveTextContent('step-03')
    expect(notice).toHaveTextContent('AGENT_TOOL_ROSTER_UNKNOWN')
    expect(notice).toHaveTextContent('tools were not checked')
    expect(notice).toHaveTextContent('pod-1')
    // The run started; the notice does not block it, it only waits to be read.
    expect(onRunComplete).not.toHaveBeenCalled()
    await userEvent.click(within(notice).getByRole('button', { name: /View run/ }))
    expect(onRunComplete).toHaveBeenCalledWith(expect.objectContaining({ run_id: 'r-1' }))
  })

  it('a launch without warnings navigates exactly as before', async () => {
    const onRunComplete = vi.fn()
    routes(() => ({ run_id: 'r-2', mode: 'pull' }))
    render(<LaunchView scenario={SCENARIO} selectedTarget={target} onRunComplete={onRunComplete} />)
    await fire()
    await waitFor(() => expect(onRunComplete).toHaveBeenCalledTimes(1))
    expect(screen.queryByTestId('launch-gate-warnings')).not.toBeInTheDocument()
  })

  it('AGENT_TOOL_ABSENT lists missing tools and "Launch anyway" re-sends the same launch with the override', async () => {
    const onRunComplete = vi.fn()
    const posted = routes((_b, n) => (n === 1 ? json(409, TOOL_ABSENT) : { run_id: 'r-3', mode: 'pull' }))
    render(<LaunchView scenario={SCENARIO} selectedTarget={target} onRunComplete={onRunComplete} />)
    await fire()
    const refusal = await screen.findByTestId('launch-gate-refusal')
    expect(refusal).toHaveTextContent('step-02')
    expect(refusal).toHaveTextContent('jq')
    expect(refusal).toHaveTextContent('nmap')
    expect(refusal).toHaveTextContent('PREPARE_DEGRADED')
    expect(refusal).not.toHaveTextContent('allow_missing_tools=true')
    const anyway = within(refusal).getByRole('button', {
      name: 'Launch anyway (steps needing a missing tool will stop with TOOL_ABSENT)',
    })
    await userEvent.click(anyway)
    await waitFor(() => expect(posted).toHaveLength(2))
    expect(posted[0].allow_missing_tools).toBeUndefined()
    const { allow_missing_tools: override, ...rest } = posted[1]
    expect(override).toBe(true)
    expect(rest).toEqual(posted[0])
    await waitFor(() => expect(onRunComplete).toHaveBeenCalledTimes(1))
  })

  it('AGENT_POSTURE_INSUFFICIENT names what is missing and the satisfying profile, with no override', async () => {
    routes(() => json(409, {
      detail: {
        error: "AGENT_POSTURE_INSUFFICIENT: agent 'pod-1': the scenario needs cluster posture a 'baseline' agent pod does not have",
        code: 'AGENT_POSTURE_INSUFFICIENT',
        detail: {
          missing_findings: ['clusterrole-wildcard-verbs'],
          missing_rbac_rules: [{ scope: 'namespace', api_groups: [''], resources: ['secrets'], verbs: ['get', 'list'] }],
          agent_profile: 'baseline',
          satisfying_profile: 'cluster-privilege',
          agent_id: 'pod-1',
        },
      },
    }))
    render(<LaunchView scenario={SCENARIO} selectedTarget={target} />)
    await fire()
    const refusal = await screen.findByTestId('launch-gate-refusal')
    expect(refusal).toHaveTextContent('clusterrole-wildcard-verbs')
    expect(refusal).toHaveTextContent('secrets')
    expect(refusal).toHaveTextContent('get, list')
    expect(refusal).toHaveTextContent('cluster-privilege')
    expect(within(refusal).queryByRole('button')).not.toBeInTheDocument()
  })
})
