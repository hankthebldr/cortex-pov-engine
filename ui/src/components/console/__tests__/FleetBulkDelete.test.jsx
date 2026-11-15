/**
 * Components › Agents › Fleet — removing agent rows, one at a time and in bulk
 * (#339).
 *
 * What was observably wrong on base (46efd54b): every agent card had one
 * "✕ delete" button that opened a panel of host-side uninstall instructions,
 * with the "Delete row" button under them. Removing N agents took 2N clicks
 * plus scrolling; a lab SimCore with 22 offline rows (21 left behind by the
 * Tier-D e2e modules) took 44. There was no bulk action at all.
 *
 * DELETE /api/agents/{id} is unchanged. These tests pin what the console does
 * with it: one inline confirm per row, "Delete all offline (N)" and a select
 * mode, sequential calls, every refusal reported, one refresh at the end.
 *
 * jsdom only. Layout is checked in a real browser, not here.
 */
import React from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, within, waitFor, act } from '@testing-library/react'
import { installRoutes } from '../../../test/mockFetch.js'
import {
  EnvironmentContext, EnvironmentProvider, DEFAULT_ENV,
} from '../../../context/EnvironmentContext.jsx'
import TargetsView from '../TargetsView.jsx'

void React

const naiveUtc = (msAgo) => new Date(Date.now() - msAgo).toISOString().replace('Z', '')

function row(agent_id, status = 'online') {
  const offline = status !== 'online'
  return {
    agent_id,
    hostname: `${agent_id}.lab`,
    os: 'linux',
    status,
    capabilities: ['shell', 'identity-harness'],
    registered_at: naiveUtc(86_400_000),
    last_seen: naiveUtc(offline ? 7_200_000 : 3_000),
    last_seen_age_seconds: offline ? 7200 : 3,
  }
}

// Online, the active agent (offline), stale, then three plain offline rows.
const FLEET = [
  row('on-1', 'online'),
  row('off-active', 'offline'),
  row('stale-1', 'stale'),
  row('off-1', 'offline'),
  row('off-2', 'offline'),
  row('off-3', 'offline'),
]

const REFUSAL_409 = {
  detail: {
    error: 'Agent has runs that are not finished',
    code: 'AGENT_HAS_OPEN_RUNS',
    detail: "agent 'off-2' is still owed, or is running, 1 run(s): run-abc. Let each one finish or abort it (POST /api/runs/run-abc/abort), then delete the agent.",
    run_ids: ['run-abc'],
  },
}

const json = (body, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { 'content-type': 'application/json' },
})

/**
 * A DELETE route that records the order of calls and how many were in flight
 * at once. `answer(id)` decides the response; it may return a promise.
 */
function deleteRecorder(answer = () => ({ status: 'deleted' })) {
  const rec = { calls: [], inFlight: 0, maxInFlight: 0 }
  rec.route = async (url) => {
    const id = decodeURIComponent(new URL(url, 'http://localhost').pathname.split('/').pop())
    rec.calls.push(id)
    rec.inFlight += 1
    rec.maxInFlight = Math.max(rec.maxInFlight, rec.inFlight)
    try {
      // A real hop to the event loop, so a parallel caller would overlap.
      await new Promise((r) => setTimeout(r, 2))
      const out = await answer(id)
      return out instanceof Response ? out : json({ status: 'deleted', agent_id: id, ...out })
    } finally {
      rec.inFlight -= 1
    }
  }
  return rec
}

function mount({ agents = FLEET, active = null, routes = {} } = {}) {
  installRoutes({
    'GET /api/infra/bundles': { bundles: [] },
    'GET /api/agents/install/attempts': { attempts: [], total: 0, retained_max: 100 },
    ...routes,
  })
  const refreshAgents = vi.fn(async () => {})
  render(
    <EnvironmentContext.Provider value={{ ...DEFAULT_ENV, agents, agent: active, setAgent: vi.fn(), refreshAgents }}>
      <TargetsView embedded />
    </EnvironmentContext.Provider>,
  )
  return { refreshAgents }
}

/** The card whose title is `id` (ids can appear again in a confirm list). */
function cardOf(id) {
  const title = screen.getAllByText(id).find((el) => el.classList.contains('target-card__title'))
  return title.closest('.target-card')
}

const listed = (list) => within(list).getAllByRole('listitem').map((li) => li.textContent.trim())

beforeEach(() => { window.localStorage.clear() })

describe('Fleet · per row: one click, then an inline confirm on the same row', () => {
  it('Delete shows "Delete <id>?" with Cancel and Delete; the uninstall steps are behind a disclosure', () => {
    mount({ agents: [row('jb-1')] })
    const c = cardOf('jb-1')
    fireEvent.click(within(c).getByRole('button', { name: /delete/i }))

    const confirm = within(c).getByRole('group', { name: 'Delete jb-1?' })
    expect(confirm.textContent).toMatch(/Delete jb-1\?/)
    expect(within(confirm).getByRole('button', { name: 'Cancel' })).toHaveFocus()
    expect(within(confirm).getByRole('button', { name: 'Delete' })).toBeEnabled()

    // Nothing sits between the click and the confirm.
    expect(within(c).queryByTestId('agent-uninstall-cmd')).toBeNull()
    const how = within(c).getByRole('button', { name: /How to remove the beacon from the host/ })
    expect(how).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(how)
    expect(how).toHaveAttribute('aria-expanded', 'true')
    expect(within(c).getByTestId('agent-uninstall-cmd')).toBeTruthy()
    expect(c.textContent).toMatch(/removes the registry row only/i)
  })

  it('Delete on the confirm calls DELETE once for that agent and refreshes', async () => {
    const del = deleteRecorder()
    const { refreshAgents } = mount({ agents: [row('jb-1'), row('jb-2')], routes: { 'DELETE /api/agents/:id': del.route } })
    const c = cardOf('jb-1')
    fireEvent.click(within(c).getByRole('button', { name: /delete/i }))
    fireEvent.click(within(c).getByRole('button', { name: 'Delete' }))
    await waitFor(() => expect(refreshAgents).toHaveBeenCalledTimes(1))
    expect(del.calls).toEqual(['jb-1'])
  })

  it('Escape closes the confirm and gives focus back to the row\'s Delete', () => {
    mount({ agents: [row('jb-1')] })
    const c = cardOf('jb-1')
    fireEvent.click(within(c).getByRole('button', { name: /delete/i }))
    const confirm = within(c).getByRole('group', { name: 'Delete jb-1?' })
    fireEvent.keyDown(within(confirm).getByRole('button', { name: 'Cancel' }), { key: 'Escape' })
    expect(within(c).queryByRole('group', { name: 'Delete jb-1?' })).toBeNull()
    expect(within(c).getByRole('button', { name: /delete/i })).toHaveFocus()
  })

  it('Escape from the opened disclosure cancels too', () => {
    mount({ agents: [row('jb-1')] })
    const c = cardOf('jb-1')
    fireEvent.click(within(c).getByRole('button', { name: /delete/i }))
    const how = within(c).getByRole('button', { name: /How to remove the beacon from the host/ })
    fireEvent.click(how)
    fireEvent.keyDown(within(c).getByRole('button', { name: 'Copy removal command for jb-1' }), { key: 'Escape' })
    expect(within(c).queryByRole('group', { name: 'Delete jb-1?' })).toBeNull()
    expect(within(c).queryByTestId('agent-uninstall-cmd')).toBeNull()
  })
})

describe('Fleet · Delete all offline (N)', () => {
  it('counts offline rows only: never the active agent, an online or a stale one', () => {
    mount({ active: FLEET[1] })
    fireEvent.click(screen.getByRole('button', { name: 'Delete all offline (3)' }))
    const confirm = screen.getByRole('group', { name: 'Delete 3 offline agents?' })
    expect(listed(within(confirm).getByRole('list', { name: 'Agents to delete' })))
      .toEqual(['off-1', 'off-2', 'off-3'])
    expect(within(confirm).getByRole('button', { name: 'Cancel' })).toHaveFocus()
  })

  it('is not offered when no row qualifies', () => {
    mount({ agents: [row('on-1'), row('off-active', 'offline')], active: row('off-active', 'offline') })
    expect(screen.queryByRole('button', { name: /Delete all offline/ })).toBeNull()
    // Select mode still exists for the rows that remain.
    expect(screen.getByRole('button', { name: 'Select' })).toBeTruthy()
  })

  it('deletes one agent at a time and refreshes the list once, at the end', async () => {
    const del = deleteRecorder()
    const { refreshAgents } = mount({ active: FLEET[1], routes: { 'DELETE /api/agents/:id': del.route } })
    fireEvent.click(screen.getByRole('button', { name: 'Delete all offline (3)' }))
    fireEvent.click(screen.getByRole('button', { name: 'Delete 3 agents' }))
    await screen.findByTestId('fleet-bulk-summary')
    expect(del.calls).toEqual(['off-1', 'off-2', 'off-3'])
    expect(del.maxInFlight).toBe(1)
    expect(refreshAgents).toHaveBeenCalledTimes(1)
    expect(screen.getByTestId('fleet-bulk-summary').textContent).toMatch(/Deleted 3/)
  })

  it('reports progress in a polite live region', async () => {
    const gates = {}
    const del = deleteRecorder((id) => new Promise((resolve) => { gates[id] = resolve }))
    mount({ active: FLEET[1], routes: { 'DELETE /api/agents/:id': del.route } })
    const live = screen.getByTestId('fleet-bulk-live')
    expect(live).toHaveAttribute('role', 'status')
    expect(live).toHaveAttribute('aria-live', 'polite')

    fireEvent.click(screen.getByRole('button', { name: 'Delete all offline (3)' }))
    fireEvent.click(screen.getByRole('button', { name: 'Delete 3 agents' }))
    await waitFor(() => expect(live).toHaveTextContent('Deleting 1 of 3…'))
    await waitFor(() => expect(gates['off-1']).toBeTypeOf('function'))
    await act(async () => { gates['off-1']({}) })
    await waitFor(() => expect(live).toHaveTextContent('Deleting 2 of 3…'))
    await waitFor(() => expect(gates['off-2']).toBeTypeOf('function'))
    await act(async () => { gates['off-2']({}) })
    await waitFor(() => expect(gates['off-3']).toBeTypeOf('function'))
    await act(async () => { gates['off-3']({}) })
    await waitFor(() => expect(live).toHaveTextContent('Deleted 3 of 3.'))
  })

  it('a 409 mid-batch does not stop the rest and is in the summary with its code and run ids', async () => {
    const del = deleteRecorder((id) => (id === 'off-2' ? json(REFUSAL_409, 409) : {}))
    const { refreshAgents } = mount({ active: FLEET[1], routes: { 'DELETE /api/agents/:id': del.route } })
    fireEvent.click(screen.getByRole('button', { name: 'Delete all offline (3)' }))
    fireEvent.click(screen.getByRole('button', { name: 'Delete 3 agents' }))

    const summary = await screen.findByTestId('fleet-bulk-summary')
    expect(del.calls).toEqual(['off-1', 'off-2', 'off-3'])
    expect(refreshAgents).toHaveBeenCalledTimes(1)
    expect(within(summary).getByRole('img', { name: 'Deleted 2' })).toBeTruthy()
    expect(within(summary).getByRole('img', { name: 'Refused 1' })).toBeTruthy()
    const refused = within(summary).getByRole('list', { name: 'Refused deletes' })
    expect(refused.textContent).toContain('off-2')
    expect(refused.textContent).toContain('AGENT_HAS_OPEN_RUNS')
    expect(within(refused).getByRole('link', { name: 'run-abc' })).toHaveAttribute('href', '#/runs?run=run-abc')
    expect(screen.getByTestId('fleet-bulk-live')).toHaveTextContent('Deleted 2 of 3. Refused 1.')

    // The refused row keeps the single-delete refusal, abort control included.
    const alert = within(cardOf('off-2')).getByTestId('agent-delete-refused')
    expect(within(alert).getByRole('button', { name: 'Abort run-abc' })).toBeTruthy()
  })

  it('a request that throws is a refusal too, and the batch carries on', async () => {
    const del = deleteRecorder((id) => {
      if (id === 'off-1') throw new TypeError('Failed to fetch')
      return {}
    })
    mount({ active: FLEET[1], routes: { 'DELETE /api/agents/:id': del.route } })
    fireEvent.click(screen.getByRole('button', { name: 'Delete all offline (3)' }))
    fireEvent.click(screen.getByRole('button', { name: 'Delete 3 agents' }))
    const summary = await screen.findByTestId('fleet-bulk-summary')
    expect(del.calls).toEqual(['off-1', 'off-2', 'off-3'])
    expect(within(summary).getByRole('img', { name: 'Refused 1' })).toBeTruthy()
  })
})

describe('Fleet · select mode', () => {
  it('Select shows a checkbox per row and Delete selected (N) deletes exactly those, in one confirm', async () => {
    const del = deleteRecorder()
    const { refreshAgents } = mount({ active: FLEET[1], routes: { 'DELETE /api/agents/:id': del.route } })
    expect(screen.queryAllByRole('checkbox')).toHaveLength(0)

    const toggle = screen.getByRole('button', { name: 'Select' })
    fireEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getAllByRole('checkbox')).toHaveLength(FLEET.length)
    expect(screen.getByRole('button', { name: 'Delete selected (0)' })).toBeDisabled()

    fireEvent.click(screen.getByRole('checkbox', { name: 'Select off-active' }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select on-1' }))
    fireEvent.click(screen.getByRole('button', { name: 'Delete selected (2)' }))

    const confirm = screen.getByRole('group', { name: 'Delete 2 selected agents?' })
    // Fleet order, not click order.
    expect(listed(within(confirm).getByRole('list', { name: 'Agents to delete' }))).toEqual(['on-1', 'off-active'])
    expect(confirm.textContent).toMatch(/active agent/i)
    expect(confirm.textContent).toMatch(/re-registers/i)

    fireEvent.click(within(confirm).getByRole('button', { name: 'Delete 2 agents' }))
    await screen.findByTestId('fleet-bulk-summary')
    expect(del.calls).toEqual(['on-1', 'off-active'])
    expect(del.maxInFlight).toBe(1)
    expect(refreshAgents).toHaveBeenCalledTimes(1)
    expect(screen.queryAllByRole('checkbox')).toHaveLength(0)
  })

  it('deleting the active agent in bulk falls back the same way a single delete does', async () => {
    // Real provider: the stale-pointer guard is what clears the active agent.
    let roster = [row('jb-online'), row('jb-active', 'offline')]
    window.localStorage.setItem('cortexsim.activeAgent', 'jb-active')
    installRoutes({
      'GET /api/agents': () => ({ agents: roster }),
      'DELETE /api/agents/:id': (url) => {
        const id = new URL(url, 'http://localhost').pathname.split('/').pop()
        roster = roster.filter((a) => a.agent_id !== id)
        return { status: 'deleted', agent_id: id }
      },
      'GET /api/scenarios': { scenarios: [] },
      'GET /api/runs': { runs: [] },
      'GET /api/health': { status: 'ok' },
      'GET /api/infra/bundles': { bundles: [] },
      'GET /api/agents/install/attempts': { attempts: [], total: 0, retained_max: 100 },
    })
    render(<EnvironmentProvider runPollMs={60_000}><TargetsView embedded /></EnvironmentProvider>)
    const summary = () => screen.getByTestId('active-agent-summary')
    await waitFor(() => expect(summary()).toHaveTextContent('active agent: jb-active'))

    fireEvent.click(screen.getByRole('button', { name: 'Select' }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select jb-active' }))
    fireEvent.click(screen.getByRole('button', { name: 'Delete selected (1)' }))
    fireEvent.click(screen.getByRole('button', { name: 'Delete 1 agent' }))

    await waitFor(() => expect(summary()).toHaveTextContent('active agent: jb-online'))
    expect(window.localStorage.getItem('cortexsim.activeAgent')).toBe('jb-online')
  })
})
