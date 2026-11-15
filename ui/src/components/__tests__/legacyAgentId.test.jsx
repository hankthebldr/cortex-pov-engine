/**
 * The legacy agent id is checked before it reaches an install command
 * (RG-INSTALL-07, console half).
 *
 * Round 1 made GET /api/agents/install validate every value it interpolates:
 * an id that is not `[A-Za-z0-9][A-Za-z0-9._-]{0,63}` now answers 400
 * BAD_AGENT_ID (it used to be baked, unquoted, into the shell and the unit
 * file). That is right. But the console still built the legacy one-liner from
 * a free-text field, and offered "↓ installer" for any id a beacon had
 * registered with. On the target a refusal is `curl: (22) … error: 400` with
 * the code and the fix discarded, and bash exits 0 on the empty pipe.
 *
 * The rule below is the server's (`_AGENT_ID_RE` in core/api/agents.py). It is
 * a copy: if the server's changes, this one is wrong until it follows.
 */
import React from 'react'
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, within } from '@testing-library/react'
import { installRoutes } from '../../test/mockFetch.js'
import { EnvironmentContext, DEFAULT_ENV } from '../../context/EnvironmentContext.jsx'
import TargetsView from '../console/TargetsView.jsx'
import { agentIdProblem } from '../console/deployCommands.js'

void React

describe('agentIdProblem — the server\'s rule, said in words', () => {
  it.each([
    'jumpbox-01', 'web01.corp', 'a', 'A_b-c.9', 'x'.repeat(64),
  ])('accepts %j', (id) => {
    expect(agentIdProblem(id)).toBeNull()
  })

  it.each([
    ['my host', /space/i],
    ['aa:bb:cc:dd:ee:ff', /":"/],
    ["Henry's-Mac", /"'"/],
    ['-host', /start with a letter or digit/i],
    ['.hidden', /start with a letter or digit/i],
    ['x'.repeat(65), /64/],
    ['WIN_HOST$', /"\$"/],
    ['', /empty/i],
  ])('refuses %j and says why', (id, why) => {
    const p = agentIdProblem(id)
    expect(p).toMatch(why)
  })
})

function mount(env = {}) {
  installRoutes({
    'GET /api/infra/bundles': { bundles: [] },
    'GET /api/agents/install/attempts': { attempts: [], total: 0, retained_max: 100 },
  })
  return render(
    <EnvironmentContext.Provider value={{ ...DEFAULT_ENV, refreshAgents: vi.fn(), ...env }}>
      <TargetsView />
    </EnvironmentContext.Provider>,
  )
}

describe('Deploy modal — the legacy id field', () => {
  async function legacy() {
    mount()
    fireEvent.click(screen.getAllByRole('button', { name: /Deploy agent/i })[0])
    const dialog = await screen.findByRole('dialog')
    fireEvent.click(within(dialog).getByRole('button', { name: /Legacy: self-asserted agent id/i }))
    return dialog
  }

  it('an id the server would refuse yields no command to copy, and names the refusal', async () => {
    const dialog = await legacy()
    fireEvent.change(within(dialog).getByLabelText(/Agent ID/i), { target: { value: 'my host' } })
    const alert = within(dialog).getByTestId('deploy-legacy-id-error')
    expect(alert).toHaveAttribute('role', 'alert')
    expect(alert.textContent).toContain('BAD_AGENT_ID')
    expect(alert.textContent).toMatch(/space/i)
    expect(alert.textContent).toMatch(/error: 400/)
    expect(within(dialog).queryByTestId('deploy-legacy-one-liner')).toBeNull()
    expect(within(dialog).queryByRole('button', { name: /Copy legacy install command/ })).toBeNull()
    expect(within(dialog).getByLabelText(/Agent ID/i)).toHaveAttribute('aria-invalid', 'true')
  })

  it('a valid id restores the command', async () => {
    const dialog = await legacy()
    const input = within(dialog).getByLabelText(/Agent ID/i)
    fireEvent.change(input, { target: { value: 'aa:bb' } })
    expect(within(dialog).queryByTestId('deploy-legacy-one-liner')).toBeNull()
    fireEvent.change(input, { target: { value: 'web01.corp' } })
    expect(within(dialog).queryByTestId('deploy-legacy-id-error')).toBeNull()
    expect(within(dialog).getByTestId('deploy-legacy-one-liner').textContent).toContain('id=web01.corp')
    expect(input).toHaveAttribute('aria-invalid', 'false')
  })

  it('an empty field still falls back to the documented default id', async () => {
    const dialog = await legacy()
    fireEvent.change(within(dialog).getByLabelText(/Agent ID/i), { target: { value: '' } })
    expect(within(dialog).queryByTestId('deploy-legacy-id-error')).toBeNull()
    expect(within(dialog).getByTestId('deploy-legacy-one-liner').textContent).toContain('id=jumpbox-01')
  })
})

describe('Agent cards — "↓ installer" is offered only for an id the server will build one for', () => {
  const card = (id) => screen.getByText(id).closest('.target-card')

  it('a row registered with an id the installer route refuses has no dead download link', () => {
    mount({ agents: [
      { agent_id: 'aa:bb:cc:dd:ee:ff', hostname: 'old', os: 'linux', status: 'online', last_seen_age_seconds: 1 },
      { agent_id: 'jb-ok', hostname: 'new', os: 'linux', status: 'online', last_seen_age_seconds: 1 },
    ] })
    expect(within(card('aa:bb:cc:dd:ee:ff')).queryByRole('link', { name: /installer/ })).toBeNull()
    const why = within(card('aa:bb:cc:dd:ee:ff')).getByTestId('agent-installer-unavailable')
    expect(why.textContent).toMatch(/no installer/i)
    expect(why.getAttribute('title')).toContain('BAD_AGENT_ID')
    expect(within(card('jb-ok')).getByRole('link', { name: /installer/ })).toBeTruthy()
  })
})
