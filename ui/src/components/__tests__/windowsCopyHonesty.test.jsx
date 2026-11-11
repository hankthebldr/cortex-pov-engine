/**
 * Console copy that names Windows must say what is proven and what is not.
 *
 * RG-UI-07. The pull path carries "Not yet proven on a customer host". The push
 * path — the one a "no Windows beacon" notice steers a DC towards — said
 * "always ready … PowerShell (.ps1) for Windows PowerShell 5.1" and "Saved
 * cortexsim-<id>.ps1 · SimCore emitted it for windows", with nothing about the
 * fact that no .ps1 bundle has run on any Windows host, or that some bundles
 * stop with exit 2 until CORTEXSIM_ACCEPT_RESIDUE=1 is set.
 *
 * UI-19. Help copy for a Windows target named POSIX things: the legacy hint was
 * `cortexsim-agent --server …` (no .exe, not the installed path).
 *
 * UI-24. The console said the env-var token "stays out of shell history". The
 * pasted line is itself what an interactive shell records. The proxy-log half
 * of the claim is true and stays.
 *
 * jsdom only. Nothing here runs a bundle, and nothing here has run on Windows.
 */
import React from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { installRoutes } from '../../test/mockFetch.js'
import { EnvironmentContext, DEFAULT_ENV } from '../../context/EnvironmentContext.jsx'
import LaunchView from '../console/LaunchView.jsx'
import TargetsView from '../console/TargetsView.jsx'
import HelpOverlay, { HELP_TOPICS } from '../console/HelpOverlay.jsx'
import { DownloadOutcome } from '../console/LaunchErrorNotice.jsx'
import { GLOSSARY } from '../onboarding/glossary.js'
import {
  WINDOWS_BUNDLE_UNPROVEN,
  WINDOWS_BUNDLE_RESIDUE,
  TOKEN_HISTORY_NOTE,
  WINDOWS_BEACON_PATH,
  legacyRunHint,
} from '../console/deployCommands.js'

void React
void HelpOverlay

const WIN_SCENARIO = {
  scenario_id: 'SIM-EDR-013', name: 'Windows credential access', plane: 'EDR',
  push_supported: true, pull_supported: true,
  execution_identity: { default: 'direct', options: ['direct'] },
  external_tools: [], steps: [{ id: 'step-01', identity: 'direct', platforms: ['windows'] }],
  bundle_targets: ['windows'], threshold: null, primary_kpi: null,
}
const POSIX_SCENARIO = {
  ...WIN_SCENARIO, scenario_id: 'SIM-EDR-001', steps: [{ id: 'step-01', identity: 'direct', platforms: ['linux'] }],
  bundle_targets: ['posix'],
}
const PUSH_TARGET = { kind: 'push', id: 'bundle', label: 'offline bundle' }

function launchRoutes() {
  installRoutes({
    'GET /api/tools/adapters': { adapters: [], total: 0 },
    'GET /api/agents': { agents: [], total: 0 },
    'GET /api/credentials/integrations': { integrations: [] },
    'GET /api/shelf/payloads': { payloads: [], dist_dir: '/app/payloads', total: 0, declared: [] },
    'GET /api/shelf/artifacts': { artifacts: null },
    'GET /api/runs': { runs: [], total: 0 },
  })
}

describe('the two sentences themselves', () => {
  it('say authored and not yet run — never that it works', () => {
    expect(WINDOWS_BUNDLE_UNPROVEN).toMatch(/no \.ps1 bundle has run on a Windows host/i)
    expect(WINDOWS_BUNDLE_UNPROVEN).toMatch(/authored/i)
    expect(WINDOWS_BUNDLE_UNPROVEN).not.toMatch(/\b(works|proven on|verified on|tested on) Windows/i)
    expect(WINDOWS_BUNDLE_RESIDUE).toContain('CORTEXSIM_ACCEPT_RESIDUE=1')
    expect(WINDOWS_BUNDLE_RESIDUE).toMatch(/exit 2/)
  })
})

describe('push path — a .ps1 is never presented as ready without the caveat (RG-UI-07)', () => {
  beforeEach(() => { launchRoutes() })

  it('LaunchView shows it when the format is PowerShell', async () => {
    render(<LaunchView scenario={WIN_SCENARIO} selectedTarget={PUSH_TARGET} />)
    await userEvent.selectOptions(await screen.findByLabelText(/Bundle format/i), 'powershell')
    const note = screen.getByTestId('launch-windows-bundle-note')
    expect(note.textContent).toContain(WINDOWS_BUNDLE_UNPROVEN)
    expect(note.textContent).toContain('CORTEXSIM_ACCEPT_RESIDUE=1')
  })

  it('and under auto when SimCore can only emit Windows for this scenario', async () => {
    render(<LaunchView scenario={WIN_SCENARIO} selectedTarget={PUSH_TARGET} />)
    await screen.findByLabelText(/Bundle format/i)
    expect(screen.getByTestId('launch-windows-bundle-note')).toBeTruthy()
  })

  it('a POSIX bundle carries none of it', async () => {
    render(<LaunchView scenario={POSIX_SCENARIO} selectedTarget={PUSH_TARGET} />)
    await screen.findByLabelText(/Bundle format/i)
    expect(screen.queryByTestId('launch-windows-bundle-note')).toBeNull()
  })

  it('the saved-.ps1 line carries it too, by target or by file name', () => {
    const { rerender } = render(
      <DownloadOutcome result={{ status: 'success', filename: 'cortexsim-SIM-EDR-013.ps1', target: 'windows', format: 'auto' }} />,
    )
    expect(screen.getByTestId('download-result').textContent).toContain(WINDOWS_BUNDLE_UNPROVEN)
    expect(screen.getByTestId('download-result').textContent).toContain('CORTEXSIM_ACCEPT_RESIDUE=1')
    rerender(<DownloadOutcome result={{ status: 'success', filename: 'cortexsim-SIM-EDR-013.ps1', target: null, format: 'powershell' }} />)
    expect(screen.getByTestId('download-result').textContent).toContain(WINDOWS_BUNDLE_UNPROVEN)
    rerender(<DownloadOutcome result={{ status: 'success', filename: 'cortexsim-SIM-EDR-001.sh', target: 'posix', format: 'auto' }} />)
    expect(screen.getByTestId('download-result').textContent).not.toContain(WINDOWS_BUNDLE_UNPROVEN)
  })
})

function mountTargets(env = {}) {
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

describe('Agents surface — the push card and the legacy hint', () => {
  it('the push card promises generation, not a working Windows run', async () => {
    mountTargets()
    const card = screen.getByText('Offline bundle').closest('.target-card')
    expect(card.textContent).not.toMatch(/always ready/i)
    expect(card.textContent).toMatch(/no agent needed/i)
    expect(card.textContent).toMatch(/no \.ps1 bundle has run on a Windows host/i)
    await waitFor(() => expect(card).toBeTruthy())
  })

  it('the legacy hint for Windows names the .exe at its installed path (UI-19)', async () => {
    mountTargets()
    fireEvent.click(screen.getAllByRole('button', { name: /Deploy agent/i })[0])
    const dialog = await screen.findByRole('dialog')
    fireEvent.click(within(dialog).getByRole('button', { name: /Windows/ }))
    fireEvent.click(within(dialog).getByRole('button', { name: /Legacy: self-asserted agent id/i }))
    const hint = within(dialog).getByTestId('deploy-legacy-run-hint').textContent
    expect(hint).toContain(`& "${WINDOWS_BEACON_PATH}" --server`)
    expect(hint).toContain('--id jumpbox-01')
    fireEvent.click(within(dialog).getByRole('button', { name: /Linux/ }))
    expect(within(dialog).getByTestId('deploy-legacy-run-hint').textContent).toMatch(/^or, once installed: cortexsim-agent --server/)
  })

  it('legacyRunHint is spelled per OS in one place', () => {
    expect(legacyRunHint({ os: 'windows', origin: 'http://10.0.0.5:8888', id: 'w1' }))
      .toBe(`& "${WINDOWS_BEACON_PATH}" --server http://10.0.0.5:8888 --id w1 --interval 10`)
    expect(legacyRunHint({ os: 'linux', origin: 'http://10.0.0.5:8888', id: 'j1' }))
      .toBe('cortexsim-agent --server http://10.0.0.5:8888 --id j1 --interval 10')
  })
})

describe('the token and shell history (UI-24)', () => {
  it('no console copy claims the token stays out of shell history', () => {
    const agents = HELP_TOPICS.find((t) => /beacon fleet/i.test(t.body))
    expect(agents).toBeTruthy()
    expect(agents.body).not.toMatch(/stays out of shell history/i)
    expect(agents.body).toMatch(/proxy logs/i)
  })

  it('the note says where the token does end up, and what limits the exposure', () => {
    expect(TOKEN_HISTORY_NOTE).toMatch(/shell history/i)
    expect(TOKEN_HISTORY_NOTE).not.toMatch(/stays out of (the )?(jumpbox's )?shell history/i)
    expect(TOKEN_HISTORY_NOTE).toMatch(/URL|proxy/i)
    expect(TOKEN_HISTORY_NOTE).toMatch(/single-use|one use|short/i)
  })

  it('the deploy modal shows that note beside the minted token', async () => {
    installRoutes({
      'GET /api/infra/bundles': { bundles: [] },
      'GET /api/agents/install/attempts': { attempts: [], total: 0, retained_max: 100 },
      'POST /api/agents/enroll/tokens': {
        id: 3, token: 'cxs_HistT0kenAbCdEfGhIjKlMnOpQrStUvWx', created_at: '2026-11-10T10:00:00',
        expires_at: '2026-11-10T11:00:00', max_uses: 1, used_count: 0, remaining_uses: 1, revoked: false,
      },
    })
    render(
      <EnvironmentContext.Provider value={{ ...DEFAULT_ENV, refreshAgents: vi.fn() }}>
        <TargetsView />
      </EnvironmentContext.Provider>,
    )
    fireEvent.click(screen.getAllByRole('button', { name: /Deploy agent/i })[0])
    const dialog = await screen.findByRole('dialog')
    fireEvent.click(within(dialog).getByRole('button', { name: /Mint enrollment token/i }))
    await within(dialog).findByTestId('deploy-token')
    expect(within(dialog).getByTestId('deploy-token-history-note').textContent).toBe(TOKEN_HISTORY_NOTE)
  })
})

describe('glossary', () => {
  it('the push-bundle entry says the .ps1 is authored, not yet run on Windows', () => {
    expect(GLOSSARY['push-bundle'].definition).toMatch(/PowerShell \(\.ps1\)/)
    expect(GLOSSARY['push-bundle'].definition).toMatch(/not yet run on a Windows host/i)
  })
})
