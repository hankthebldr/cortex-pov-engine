/**
 * A Windows install path that does not pipe a download into `iex` (UI-17).
 *
 * The primary Windows command is `iwr -useb … | iex`: download-and-execute in
 * one line, the pattern AMSI and endpoint prevention are built to stop — on the
 * endpoints a POV targets, which run the product being demonstrated. The deploy
 * modal already offered the installer as a file; Get started offered nothing
 * but the cradle, and neither let a DC bring the BEACON as a file they had
 * checked.
 *
 * Everything this uses is served by SimCore today:
 *   GET /api/agents/install?os=windows            the installer (.ps1)
 *   GET /api/agents/binary?os=windows&arch=amd64  the beacon (.exe)
 *   GET /api/agents/binary/sha256?os=windows&…    its SHA-256 (text)
 * and the installer already honours $env:CORTEXSIM_BIN (a pre-staged beacon,
 * checked against SimCore's digest).
 *
 * The installer link carries the address the beacon must call. The link used
 * to be the relative `/api/agents/install?os=windows`, so SimCore baked SERVER
 * from the request's Host header — the address the CONSOLE was opened at. Get
 * started has a typed "address the host can reach" and builds the one-liner
 * from it; the download ignored it. With the console on an SSH tunnel
 * (localhost:8888) and the LAN IP typed, the saved .ps1 carried
 * SERVER=http://localhost:8888, failed at enrol on the target with
 * SERVER_UNREACHABLE, and reported nothing here. `GET /api/agents/install`
 * has always accepted `?server=`; the link now sends it.
 *
 * What this does NOT show: that either path gets past a prevention profile.
 * No Windows host has run any of it; the copy must say so.
 */
import React from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, within, waitFor } from '@testing-library/react'
import { installRoutes } from '../../test/mockFetch.js'
import { EnvironmentContext, DEFAULT_ENV } from '../../context/EnvironmentContext.jsx'
import TargetsView from '../console/TargetsView.jsx'
import GetStartedView from '../console/GetStartedView.jsx'
import {
  WINDOWS_BEACON_FILE, serverUrlProblem, windowsPrestageVerifyLine, windowsPrestageRunLine,
} from '../console/deployCommands.js'
import { agentBinaryUrl, agentInstallUrl, getAgentBinarySha256 } from '../../api/client.js'

void React

const SHA = 'a3'.repeat(32)
const TOKEN = {
  id: 4, token: 'cxs_NoCradleAbCdEfGhIjKlMnOpQrStUvWxYz', created_at: '2026-11-10T10:00:00',
  expires_at: '2026-11-10T11:00:00', max_uses: 1, used_count: 0, remaining_uses: 1, revoked: false,
}
const text = (body, status = 200) => new Response(body, { status, headers: { 'content-type': 'text/plain; charset=utf-8' } })

function routes(sha = () => text(`${SHA}\n`)) {
  installRoutes({
    'GET /api/infra/bundles': { bundles: [] },
    'GET /api/tenants': [], 'GET /api/xsiam/tenants': [],
    'POST /api/agents/enroll/tokens': TOKEN,
    'GET /api/agents/install/attempts': { attempts: [], total: 0, retained_max: 100 },
    'GET /api/agents/binary/sha256': sha,
  })
}

describe('the commands, spelled once', () => {
  it('verify compares the file\'s hash with the digest and prints True or False', () => {
    expect(windowsPrestageVerifyLine({ sha256: SHA })).toBe(
      `(Get-FileHash .\\${WINDOWS_BEACON_FILE} -Algorithm SHA256).Hash.ToLower() -eq '${SHA}'`,
    )
  })

  it('run hands the installer the token and the pre-staged beacon, with no iwr and no iex', () => {
    const line = windowsPrestageRunLine({ token: 'cxs_abc' })
    expect(line).toBe(
      `Unblock-File .\\install-cortexsim-agent.ps1, .\\${WINDOWS_BEACON_FILE}; $env:CORTEXSIM_TOKEN='cxs_abc'; `
      + `$env:CORTEXSIM_BIN=(Resolve-Path .\\${WINDOWS_BEACON_FILE}).Path; `
      + 'powershell -NoProfile -ExecutionPolicy Bypass -File .\\install-cortexsim-agent.ps1',
    )
    expect(line).not.toMatch(/\biex\b|\biwr\b|Invoke-Expression|Invoke-WebRequest/i)
  })

  it('the beacon is fetched from the route SimCore already serves', () => {
    expect(agentBinaryUrl({ os: 'windows' })).toBe('/api/agents/binary?os=windows&arch=amd64')
    expect(WINDOWS_BEACON_FILE).toBe('cortexsim-agent-windows-amd64.exe')
  })

  it('the installer URL can name the address SimCore bakes in, and stays tokenless', () => {
    expect(agentInstallUrl({ os: 'windows', server: 'http://10.0.0.5:8888' }))
      .toBe('/api/agents/install?os=windows&interval=10&server=http%3A%2F%2F10.0.0.5%3A8888')
    // Without one the URL is what it always was: SimCore falls back to Host.
    expect(agentInstallUrl({ os: 'windows' })).toBe('/api/agents/install?os=windows&interval=10')
    expect(agentInstallUrl({ os: 'linux', server: '' })).toBe('/api/agents/install?os=linux&interval=10')
  })
})

// A COPY of SimCore's `_server_problem` (core/api/agents.py): the address is
// baked into the script text, so SimCore answers 400 BAD_SERVER for anything
// outside a narrow shape. A download link for such an address would save that
// 400's JSON body under the name install-cortexsim-agent.ps1.
describe('serverUrlProblem — would SimCore bake this address into an installer?', () => {
  it('accepts http(s)://host[:port][/path]', () => {
    for (const ok of [
      'http://10.0.0.5:8888', 'https://simcore.lab.example', 'http://localhost:8888', 'HTTP://Host:8888',
      'http://[::1]:8888', 'http://[fd00::5]', 'http://jumpbox:8888/base/path', 'http://10.0.0.5:8888/', '  http://10.0.0.5:8888  ',
      'http://[::]', 'http://[FD00:0:0:0:0:0:0:5]:8888', 'http://[::ffff:10.0.0.5]:8888', 'http://[1:2:3:4:5:6:7::]',
    ]) {
      expect(serverUrlProblem(ok), ok).toBeNull()
    }
  })

  it('refuses what SimCore refuses, and says why', () => {
    expect(serverUrlProblem('')).toMatch(/empty/)
    expect(serverUrlProblem(undefined)).toMatch(/empty/)
    expect(serverUrlProblem('10.0.0.5:8888')).toMatch(/scheme must be http or https/)
    expect(serverUrlProblem('jumpbox:8888')).toMatch(/scheme must be http or https/)
    expect(serverUrlProblem('ftp://10.0.0.5')).toMatch(/scheme must be http or https/)
    expect(serverUrlProblem('http://')).toMatch(/no host/)
    expect(serverUrlProblem('http:/10.0.0.5')).toMatch(/no host/)
    expect(serverUrlProblem('http://:8888')).toMatch(/no host/)
    expect(serverUrlProblem('http://10.0.0.5:99999')).toMatch(/port/)
    expect(serverUrlProblem('http://10.0.0.5:0')).toMatch(/port/)
    expect(serverUrlProblem('http://10.0.0.5:88x8')).toMatch(/port/)
    expect(serverUrlProblem('http://[::1')).toMatch(/bracket/)
    // A bracketed host must be an IPv6 address: SimCore's URL parser refuses
    // an IPv4 address in brackets and anything that is not an address.
    for (const bad of [
      'http://[10.0.0.5]:8888', 'http://[::::]:8888', 'http://[1:2]:8888', 'http://[12345::1]', 'http://[]:8888',
      'http://[1:2:3:4:5:6:7:8:9]', 'http://[1::2::3]', 'http://[::1]x:8888', 'http://x[::1]', 'http://[::1.2.3.256]',
    ]) {
      expect(serverUrlProblem(bad), bad).toMatch(/bracket/)
    }
    for (const bad of [
      'http://my host:8888', 'http://user:pw@10.0.0.5:8888', 'http://10.0.0.5:8888/?x=1', 'http://10.0.0.5:8888/#a',
      "http://10.0.0.5:8888'; Write-Host PWNED; '", 'http://10.0.0.5:8888/a%20b', 'http://exämple:8888',
    ]) {
      expect(serverUrlProblem(bad), bad).toMatch(/only http\(s\):\/\/host/)
    }
  })
})

describe('getAgentBinarySha256 — a digest or an error, never a guess', () => {
  it('returns the 64 hex characters SimCore serves', async () => {
    routes()
    await expect(getAgentBinarySha256({ os: 'windows' })).resolves.toBe(SHA)
  })

  it('rejects a body that is not a SHA-256 (a proxy page, an empty answer)', async () => {
    routes(() => text('<html>login</html>'))
    await expect(getAgentBinarySha256({ os: 'windows' })).rejects.toMatchObject({ code: 'DIGEST_UNRECOGNISED' })
    routes(() => text(''))
    await expect(getAgentBinarySha256({ os: 'windows' })).rejects.toMatchObject({ code: 'DIGEST_UNRECOGNISED' })
  })

  it('passes a 404 through with its code', async () => {
    routes(() => new Response(JSON.stringify({ detail: { error: 'No prebuilt beacon for that target', code: 'AGENT_BINARY_UNAVAILABLE', detail: 'x' } }),
      { status: 404, headers: { 'content-type': 'application/json' } }))
    await expect(getAgentBinarySha256({ os: 'windows' })).rejects.toMatchObject({ code: 'AGENT_BINARY_UNAVAILABLE' })
  })
})

function mountTargets() {
  return render(
    <EnvironmentContext.Provider value={{ ...DEFAULT_ENV, refreshAgents: vi.fn() }}>
      <TargetsView />
    </EnvironmentContext.Provider>,
  )
}
async function mintedDialog(os) {
  mountTargets()
  fireEvent.click(screen.getAllByRole('button', { name: /Deploy agent/i })[0])
  const dialog = await screen.findByRole('dialog')
  if (os) fireEvent.click(within(dialog).getByRole('button', { name: new RegExp(os) }))
  fireEvent.click(within(dialog).getByRole('button', { name: /Mint enrollment token/i }))
  await within(dialog).findByTestId('deploy-token')
  return dialog
}

describe('Deploy modal — download, verify, run', () => {
  beforeEach(() => { window.localStorage.clear() })

  it('offers the installer and the beacon as files, with the digest SimCore serves', async () => {
    routes()
    const dialog = await mintedDialog('Windows')
    const box = within(dialog).getByTestId('deploy-nocradle')
    const installer = within(box).getByRole('link', { name: /installer/i })
    // The modal has no address field: the one-liner uses the address this
    // console was opened at, and so does the download — said, not left to Host.
    expect(installer.getAttribute('href'))
      .toBe(`/api/agents/install?os=windows&interval=10&server=${encodeURIComponent(window.location.origin)}`)
    expect(installer.getAttribute('href')).not.toMatch(/token=|cxs_/)
    expect(installer.getAttribute('download')).toBe('install-cortexsim-agent.ps1')
    expect(box.textContent).toContain(`contacts SimCore at ${window.location.origin}`)
    const beacon = within(box).getByRole('link', { name: /beacon/i })
    expect(beacon.getAttribute('href')).toBe('/api/agents/binary?os=windows&arch=amd64')
    expect(beacon.getAttribute('download')).toBe(WINDOWS_BEACON_FILE)
    await waitFor(() => expect(within(box).getByTestId('deploy-nocradle-sha256').textContent).toBe(SHA))
  })

  it('gives the verify line with that digest and the run line with the token', async () => {
    routes()
    const dialog = await mintedDialog('Windows')
    const box = within(dialog).getByTestId('deploy-nocradle')
    await waitFor(() => expect(within(box).getByTestId('deploy-nocradle-verify').textContent)
      .toBe(windowsPrestageVerifyLine({ sha256: SHA })))
    expect(within(box).getByTestId('deploy-nocradle-run').textContent)
      .toBe(windowsPrestageRunLine({ token: TOKEN.token }))
    expect(within(box).getByRole('button', { name: 'Copy hash check' })).toBeTruthy()
    expect(within(box).getByRole('button', { name: 'Copy run line for the downloaded installer and beacon' })).toBeTruthy()
  })

  it('says what is and is not known: not run on Windows, prevention outcome untested', async () => {
    routes()
    const dialog = await mintedDialog('Windows')
    const t = within(dialog).getByTestId('deploy-nocradle').textContent
    expect(t).toMatch(/not yet run on a Windows host/i)
    expect(t).toMatch(/has not been tested/i)
    expect(t).not.toMatch(/\b(bypasses|evades|avoids detection|will not be blocked)\b/i)
    expect(t).toMatch(/unsigned/i)
  })

  it('with no digest it says so and offers no hash check built on a blank', async () => {
    routes(() => new Response(JSON.stringify({ detail: { error: 'No prebuilt beacon for that target', code: 'AGENT_BINARY_UNAVAILABLE', detail: 'x' } }),
      { status: 404, headers: { 'content-type': 'application/json' } }))
    const dialog = await mintedDialog('Windows')
    const box = within(dialog).getByTestId('deploy-nocradle')
    await waitFor(() => expect(within(box).getByTestId('deploy-nocradle-sha256-error').textContent)
      .toMatch(/AGENT_BINARY_UNAVAILABLE/))
    expect(within(box).queryByTestId('deploy-nocradle-verify')).toBeNull()
    expect(within(box).queryByTestId('deploy-nocradle-sha256')).toBeNull()
  })

  it('is a Windows-only section', async () => {
    routes()
    const dialog = await mintedDialog(null)
    expect(within(dialog).queryByTestId('deploy-nocradle')).toBeNull()
  })
})

describe('Get started — the same path beside the one-liner', () => {
  beforeEach(() => { window.localStorage.clear() })

  function mountGetStarted() {
    render(
      <EnvironmentContext.Provider value={{ ...DEFAULT_ENV, refreshAgents: vi.fn() }}>
        <GetStartedView />
      </EnvironmentContext.Provider>,
    )
  }
  async function windowsWithAddress(address) {
    routes()
    mountGetStarted()
    fireEvent.click(screen.getByRole('radio', { name: 'Windows' }))
    fireEvent.change(screen.getByRole('textbox'), { target: { value: address } })
    fireEvent.click(screen.getByTestId('gs-agent-generate'))
    await screen.findByTestId('gs-agent-command')
    return screen.getByTestId('gs-nocradle')
  }

  // The SSH-tunnel case Get started itself anticipates: the console is open at
  // a loopback address and the DC types the address the TARGET can reach.
  it('the installer download bakes the TYPED address, not the one the console was opened at', async () => {
    const TYPED = 'http://10.20.30.40:8888'
    expect(TYPED).not.toBe(window.location.origin)
    const box = await windowsWithAddress(TYPED)

    // The one-liner already used it …
    expect(screen.getByTestId('gs-agent-command').textContent).toContain(`"${TYPED}/api/agents/install?os=windows&interval=10"`)
    // … and the downloaded installer must be told the same address.
    const href = within(box).getByRole('link', { name: /installer/i }).getAttribute('href')
    const url = new URL(href, window.location.origin)
    expect(url.origin).toBe(window.location.origin) // fetched by the browser, from the console
    expect(url.pathname).toBe('/api/agents/install')
    expect(url.searchParams.get('server')).toBe(TYPED)
    expect(url.searchParams.get('os')).toBe('windows')
    expect(url.searchParams.has('token')).toBe(false)
    expect(box.textContent).toContain(`contacts SimCore at ${TYPED}`)
  })

  // A pasted address often ends in a space. That used to stop the trailing-slash
  // strip, so the one-liner read `iwr " http://…:8888/ /api/agents/install…"`.
  it('a trailing slash or stray space in the typed address reaches neither the download nor the one-liner', async () => {
    const box = await windowsWithAddress(' http://10.20.30.40:8888/ ')
    const href = within(box).getByRole('link', { name: /installer/i }).getAttribute('href')
    expect(new URL(href, window.location.origin).searchParams.get('server')).toBe('http://10.20.30.40:8888')
    expect(screen.getByTestId('gs-agent-command').textContent)
      .toContain('iwr -useb "http://10.20.30.40:8888/api/agents/install?os=windows&interval=10" | iex')
  })

  it('an address SimCore would refuse gets no installer link, and says why', async () => {
    const box = await windowsWithAddress('10.20.30.40:8888')
    expect(within(box).queryByRole('link', { name: /installer/i })).toBeNull()
    const why = within(box).getByTestId('gs-nocradle-installer-unavailable')
    expect(why.textContent).toMatch(/scheme must be http or https/)
    expect(why.textContent).toMatch(/BAD_SERVER/)
    // The beacon is the same bytes whatever the address: its link stays.
    expect(within(box).getByRole('link', { name: /beacon/i }).getAttribute('href')).toBe('/api/agents/binary?os=windows&arch=amd64')
  })

  it('Windows gets it once a command is generated; Linux does not', async () => {
    routes()
    render(
      <EnvironmentContext.Provider value={{ ...DEFAULT_ENV, refreshAgents: vi.fn() }}>
        <GetStartedView />
      </EnvironmentContext.Provider>,
    )
    fireEvent.click(screen.getByTestId('gs-agent-generate'))
    await screen.findByTestId('gs-agent-command')
    expect(screen.queryByTestId('gs-nocradle')).toBeNull()

    fireEvent.click(screen.getByRole('radio', { name: 'Windows' }))
    fireEvent.click(screen.getByTestId('gs-agent-generate'))
    await screen.findByTestId('gs-agent-command')
    const box = screen.getByTestId('gs-nocradle')
    expect(within(box).getByTestId('gs-nocradle-run').textContent).toBe(windowsPrestageRunLine({ token: TOKEN.token }))
    await waitFor(() => expect(within(box).getByTestId('gs-nocradle-sha256').textContent).toBe(SHA))
  })
})
