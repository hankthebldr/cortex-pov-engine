/**
 * PUSH-09 / DOCS-05 / UI-03 / UI-04 — the console can hand a DC a Windows push
 * bundle, and a refused download is visible where they clicked.
 *
 * Before this guard the console offered only bash and k8s. 71 scenarios declare
 * a Windows step and SimCore has served `format=powershell` since the push
 * generator went platform-aware, but nothing in the UI could request it, every
 * download was saved as `.sh`, and a 409 BUNDLE_TARGET_UNSATISFIABLE reached a
 * no-op `onError` — the button did nothing and nothing said why.
 *
 * jsdom only: this proves the request the console sends and the name it saves
 * under. Nothing here executes a .ps1, and nothing here has run on Windows.
 */
import React, { useEffect } from 'react'
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { installRoutes } from '../../test/mockFetch.js'
import LaunchView from '../console/LaunchView.jsx'
import ScenarioInspector from '../console/ScenarioInspector.jsx'
import useLaunchScenario from '../console/useLaunchScenario.js'
import { downloadScenario } from '../../api/client.js'

void React

const WIN_SCENARIO = {
  scenario_id: 'SIM-EDR-013',
  name: 'Windows credential access',
  plane: 'EDR',
  push_supported: true,
  pull_supported: true,
  execution_identity: { default: 'direct', options: ['direct'] },
  external_tools: [],
  steps: [
    { id: 'step-01', identity: 'direct', platforms: ['windows'] },
    { id: 'step-02', identity: 'direct', platforms: ['windows'] },
  ],
  threshold: null,
  primary_kpi: null,
}

const PUSH_TARGET = { kind: 'push', id: 'bundle', label: 'offline bundle' }

const UNSATISFIABLE_POSIX = {
  detail: {
    error: 'Scenario cannot emit a bundle for this target',
    code: 'BUNDLE_TARGET_UNSATISFIABLE',
    detail: 'target=posix scenario=SIM-EDR-013 unresolved=[step-01 (POSIX_COMMAND_UNAVAILABLE), '
      + 'step-02 (POSIX_COMMAND_UNAVAILABLE)] — declare platform_variants.linux on those steps',
  },
}

function ps1Response(id, { disposition = true, target = 'windows' } = {}) {
  const headers = { 'content-type': 'text/plain; charset=utf-8', 'X-CortexSim-Bundle-Target': target }
  if (disposition) {
    headers['Content-Disposition'] = `attachment; filename="cortexsim-${id}.${target === 'windows' ? 'ps1' : 'sh'}"`
  }
  return new Response('# bundle\n', { status: 200, headers })
}

let downloadUrls
let savedAs

function routes(downloadHandler) {
  return {
    'GET /api/tools/adapters': { adapters: [], total: 0 },
    'GET /api/agents': { agents: [], total: 0 },
    'GET /api/credentials/integrations': { integrations: [] },
    'GET /api/shelf/payloads': { payloads: [], dist_dir: '/app/payloads', total: 0, declared: [] },
    'GET /api/shelf/artifacts': { artifacts: null },
    'GET /api/runs': { runs: [], total: 0 },
    'GET /api/scenarios/SIM-EDR-013/download': (url) => {
      downloadUrls.push(url)
      return downloadHandler(url)
    },
  }
}

beforeEach(() => {
  downloadUrls = []
  savedAs = []
  URL.createObjectURL = vi.fn(() => 'blob:mock')
  URL.revokeObjectURL = vi.fn()
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function click() {
    savedAs.push(this.download)
  })
})

const formatOf = (url) => new URL(url, 'http://localhost').searchParams.get('format')

describe('LaunchView — PowerShell push bundle', () => {
  it('offers PowerShell (.ps1) and auto, and defaults to auto', async () => {
    installRoutes(routes(() => ps1Response('SIM-EDR-013')))
    render(<LaunchView scenario={WIN_SCENARIO} selectedTarget={PUSH_TARGET} />)
    const select = await screen.findByLabelText(/Bundle format/i)
    const values = Array.from(select.querySelectorAll('option')).map((o) => o.value)
    expect(values).toEqual(expect.arrayContaining(['auto', 'bash', 'powershell', 'k8s']))
    expect(within(select).getByRole('option', { name: /PowerShell \(\.ps1\)/ })).toBeInTheDocument()
    expect(select.value).toBe('auto')
  })

  it('requests format=powershell and saves under the server-named .ps1', async () => {
    installRoutes(routes(() => ps1Response('SIM-EDR-013')))
    render(<LaunchView scenario={WIN_SCENARIO} selectedTarget={PUSH_TARGET} />)
    await userEvent.selectOptions(await screen.findByLabelText(/Bundle format/i), 'powershell')
    await userEvent.click(screen.getByRole('button', { name: /Download bundle/i }))
    await waitFor(() => expect(savedAs).toEqual(['cortexsim-SIM-EDR-013.ps1']))
    expect(formatOf(downloadUrls[0])).toBe('powershell')
  })

  it('falls back to .ps1 by format when the server sends no Content-Disposition', async () => {
    installRoutes(routes(() => ps1Response('SIM-EDR-013', { disposition: false })))
    render(<LaunchView scenario={WIN_SCENARIO} selectedTarget={PUSH_TARGET} />)
    await userEvent.selectOptions(await screen.findByLabelText(/Bundle format/i), 'powershell')
    await userEvent.click(screen.getByRole('button', { name: /Download bundle/i }))
    await waitFor(() => expect(savedAs).toHaveLength(1))
    expect(savedAs[0]).toMatch(/\.ps1$/)
  })

  it('the default download lets the server choose (format=auto) and names by the target it chose', async () => {
    installRoutes(routes(() => ps1Response('SIM-EDR-013', { disposition: false })))
    render(<LaunchView scenario={WIN_SCENARIO} selectedTarget={PUSH_TARGET} />)
    await userEvent.click(await screen.findByRole('button', { name: /Download bundle/i }))
    await waitFor(() => expect(savedAs).toHaveLength(1))
    expect(formatOf(downloadUrls[0])).toBe('auto')
    expect(savedAs[0]).toMatch(/\.ps1$/)
  })

  it('a windows-only scenario is not blocked once the bundle can be PowerShell', async () => {
    installRoutes(routes(() => ps1Response('SIM-EDR-013')))
    render(<LaunchView scenario={WIN_SCENARIO} selectedTarget={PUSH_TARGET} />)
    await waitFor(() => expect(screen.getByTestId('preflight-platform')).toBeInTheDocument())
    expect(screen.getByTestId('preflight-platform')).toHaveAttribute('data-status', 'pass')
    expect(screen.queryByTestId('launch-blockers') || document.createElement('div'))
      .not.toHaveTextContent(/Platform:/)
  })
})

describe('LaunchView — a refused download is visible beside the button (UI-04)', () => {
  it('renders code and the unresolved step ids, and offers one-click PowerShell', async () => {
    let n = 0
    installRoutes(routes((url) => {
      n += 1
      if (formatOf(url) === 'bash') {
        return new Response(JSON.stringify(UNSATISFIABLE_POSIX), {
          status: 409, headers: { 'content-type': 'application/json' },
        })
      }
      return ps1Response('SIM-EDR-013')
    }))
    const onError = vi.fn()
    render(<LaunchView scenario={WIN_SCENARIO} selectedTarget={PUSH_TARGET} onError={onError} />)
    await userEvent.selectOptions(await screen.findByLabelText(/Bundle format/i), 'bash')
    await userEvent.click(screen.getByRole('button', { name: /Download bundle/i }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('BUNDLE_TARGET_UNSATISFIABLE')
    expect(alert).toHaveTextContent('step-01')
    expect(alert).toHaveTextContent('step-02')
    expect(alert).toHaveTextContent('POSIX_COMMAND_UNAVAILABLE')
    expect(savedAs).toEqual([])
    expect(onError).toHaveBeenCalled()

    await userEvent.click(within(alert).getByRole('button', { name: /Download as PowerShell/i }))
    await waitFor(() => expect(savedAs).toEqual(['cortexsim-SIM-EDR-013.ps1']))
    expect(n).toBe(2)
    expect(formatOf(downloadUrls[1])).toBe('powershell')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})

describe('downloadScenario (client)', () => {
  it('defaults to format=auto and surfaces the X-CortexSim-Bundle-Target header', async () => {
    installRoutes(routes(() => ps1Response('SIM-EDR-013')))
    const res = await downloadScenario('SIM-EDR-013')
    expect(formatOf(downloadUrls[0])).toBe('auto')
    expect(res.target).toBe('windows')
    expect(res.filename).toBe('cortexsim-SIM-EDR-013.ps1')
    // Not toBeInstanceOf(Blob): under Node 20 (CI) fetch's Response.blob() returns
    // undici's Blob while the test file sees jsdom's, so the instanceof check
    // compares two realms and failed on the first CI run. The brand is the same.
    expect(Object.prototype.toString.call(res.blob)).toBe('[object Blob]')
    expect(res.blob.size).toBeGreaterThan(0)
  })
})

/** The real hook driving the real drawer — push mode, as the quick-launch path is. */
function InspectorHarness({ scenario }) {
  const launch = useLaunchScenario(scenario)
  useEffect(() => { launch.setMode('push') }, []) // eslint-disable-line react-hooks/exhaustive-deps
  return <ScenarioInspector scenario={scenario} open launch={launch} />
}

describe('ScenarioInspector — quick-launch push format', () => {
  it('offers auto, bash, PowerShell and k8s', async () => {
    installRoutes(routes(() => ps1Response('SIM-EDR-013')))
    render(<InspectorHarness scenario={WIN_SCENARIO} />)
    expect(await screen.findByRole('button', { name: /^PowerShell$/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^auto$/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^bash$/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^k8s$/ })).toBeInTheDocument()
  })

  it('downloads the .ps1 when PowerShell is picked', async () => {
    installRoutes(routes(() => ps1Response('SIM-EDR-013')))
    render(<InspectorHarness scenario={WIN_SCENARIO} />)
    await userEvent.click(await screen.findByRole('button', { name: /^PowerShell$/ }))
    await userEvent.click(screen.getByRole('button', { name: /Push bundle/i }))
    await waitFor(() => expect(savedAs).toEqual(['cortexsim-SIM-EDR-013.ps1']))
    expect(formatOf(downloadUrls[0])).toBe('powershell')
  })

  it('renders a refused download as an alert with its detail', async () => {
    installRoutes(routes(() => new Response(JSON.stringify(UNSATISFIABLE_POSIX), {
      status: 409, headers: { 'content-type': 'application/json' },
    })))
    render(<InspectorHarness scenario={WIN_SCENARIO} />)
    await userEvent.click(await screen.findByRole('button', { name: /^bash$/ }))
    await userEvent.click(screen.getByRole('button', { name: /Push bundle/i }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('BUNDLE_TARGET_UNSATISFIABLE')
    expect(alert).toHaveTextContent('step-01')
    expect(alert).toHaveTextContent('step-02')
  })
})
