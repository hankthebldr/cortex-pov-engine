/**
 * The address a DC types into Get started, checked against SimCore's Host guard
 * (RG-UI-05).
 *
 * Round 1 added an outermost Host guard: SimCore answers 400 HOST_NOT_ALLOWED
 * unless `Host` is localhost, an IP literal, the machine's own name, or a name
 * in CORTEXSIM_ALLOWED_HOSTS. Get started still told the DC to "use this
 * machine's LAN IP or DNS name" and built the install line from whatever was
 * typed. A refused name fails on the target as `curl: (22) ... error: 400`
 * before the installer body is served, so no install attempt is reported and
 * the step waits forever with nothing to read.
 *
 * `/api/health` already publishes the guard's configuration on the `api_auth`
 * component (`host_guard`, `allowed_hosts`), so the console can say what it
 * knows — and say plainly what it cannot see (the machine's own host name).
 *
 * jsdom only. No SimCore was contacted; the guard's own behaviour is pinned
 * server-side in tests/security.
 */
import React from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { installRoutes } from '../../test/mockFetch.js'
import { EnvironmentContext, DEFAULT_ENV } from '../../context/EnvironmentContext.jsx'
import { buildHealthModel } from '../console/readiness/healthModel.js'
import GetStartedView from '../console/GetStartedView.jsx'
import { serverHostVerdict } from '../console/deployCommands.js'

void React

const health = (apiAuth) => buildHealthModel({
  status: 'degraded',
  components: apiAuth === undefined ? {} : { api_auth: apiAuth },
})
const GUARD = (allowed, mode = 'enforced') => ({
  status: 'degraded', code: 'API_UNAUTHENTICATED', detail: 'x',
  authenticated: false, bind: '0.0.0.0', port: 8888, host_guard: mode, allowed_hosts: allowed, cors_origins: [],
})

describe('serverHostVerdict — what the console can and cannot confirm', () => {
  const v = (url, model, consoleHost = 'localhost') => serverHostVerdict(model, url, consoleHost)

  it('loopback and IP literals are always admitted, with no health read needed', () => {
    for (const url of ['http://localhost:8888', 'http://127.0.0.1:8888', 'http://[::1]:8888',
      'http://10.0.0.5:8888', 'https://192.168.1.20', 'http://[fd00::12]:8888']) {
      expect(v(url, null).state, url).toBe('admitted')
    }
  })

  it('the name this console was itself reached by is admitted: the page loading proves it', () => {
    expect(v('http://jumpbox-01:8888', health(GUARD([])), 'jumpbox-01')).toMatchObject({ state: 'admitted', why: 'console-host' })
    expect(v('http://JumpBox-01.:8888', health(GUARD([])), 'jumpbox-01').state).toBe('admitted')
  })

  it('a name listed in CORTEXSIM_ALLOWED_HOSTS is admitted, wildcards as the server reads them', () => {
    const m = health(GUARD(['simcore.lab.example', '*.pov.example']))
    expect(v('http://simcore.lab.example:8888', m)).toMatchObject({ state: 'admitted', why: 'listed' })
    expect(v('http://a.pov.example:8888', m).state).toBe('admitted')
    expect(v('http://a.b.pov.example:8888', m).state).toBe('admitted')
    // `*.x` matches subdomains only, never the apex — the server's own rule.
    expect(v('http://pov.example:8888', m).state).toBe('not_listed')
  })

  it('a DNS name that is not listed is not_listed, and carries the list it was checked against', () => {
    const r = v('http://simcore.lab.customer.example:8888', health(GUARD(['jumpbox-01'])))
    expect(r.state).toBe('not_listed')
    expect(r.host).toBe('simcore.lab.customer.example')
    expect(r.allowed).toEqual(['jumpbox-01'])
  })

  it('a disabled guard admits everything', () => {
    expect(v('http://anything.example:8888', health(GUARD(['*'], 'disabled'))).state).toBe('admitted')
  })

  it('a SimCore that does not report the guard is unknown — never admitted, never refused', () => {
    expect(v('http://simcore.lab.example:8888', health(undefined)).state).toBe('unknown')
    expect(v('http://simcore.lab.example:8888', null).state).toBe('unknown')
    // An unrecognised shape is not read as an empty allowlist.
    expect(v('http://simcore.lab.example:8888', health({ ...GUARD([]), allowed_hosts: 'jumpbox-01' })).state).toBe('unknown')
    expect(v('http://simcore.lab.example:8888', health({ ...GUARD([]), host_guard: 'sideways' })).state).toBe('unknown')
  })

  it('text that is not an http(s) URL is invalid, not a host', () => {
    expect(v('simcore.lab.example:8888', health(GUARD([]))).state).toBe('invalid')
    expect(v('', health(GUARD([]))).state).toBe('invalid')
    expect(v('ftp://x', health(GUARD([]))).state).toBe('invalid')
  })
})

// RG-UI-05 residual: /api/health now publishes the machine's own names the guard
// admits (`local_host_names` from gethostname, `local_fqdn_names` once the running
// guard resolved its FQDN, null before). The console no longer has to say "may".
describe('serverHostVerdict — the machine\'s own names, as SimCore publishes them', () => {
  const v = (url, model) => serverHostVerdict(model, url, 'localhost')
  const OWN = (hostNames, fqdn) => health({ ...GUARD(['jumpbox-01']), local_host_names: hostNames, local_fqdn_names: fqdn })

  it('a name the guard admits as this machine\'s own is admitted', () => {
    const m = OWN(['simcore-01', 'simcore-01.lab.example'], ['simcore-01', 'simcore-01.corp.example'])
    expect(v('http://simcore-01:8888', m)).toMatchObject({ state: 'admitted', why: 'own-name' })
    expect(v('http://SIMCORE-01.lab.example:8888', m).state).toBe('admitted')
    expect(v('http://simcore-01.corp.example:8888', m)).toMatchObject({ state: 'admitted', why: 'own-name' })
  })

  it('with the FQDN known, an unlisted name is a certain refusal that names the machine', () => {
    const r = v('http://simcore.lab.customer.example:8888', OWN(['4f2a9c'], []))
    expect(r).toMatchObject({ state: 'not_listed', certain: true, ownNames: ['4f2a9c'] })
  })

  it('with the FQDN not looked up yet, the refusal is not certain, and says why', () => {
    const r = v('http://simcore.lab.customer.example:8888', OWN(['4f2a9c'], null))
    expect(r).toMatchObject({ state: 'not_listed', certain: false, ownNames: ['4f2a9c'] })
  })

  it('a SimCore that publishes no local names keeps the old, uncertain reading', () => {
    const r = v('http://simcore.lab.customer.example:8888', health(GUARD(['jumpbox-01'])))
    expect(r).toMatchObject({ state: 'not_listed', certain: false, ownNames: null })
  })
})

function mount(env = {}) {
  installRoutes({
    'GET /api/tenants': [], 'GET /api/xsiam/tenants': [],
    'GET /api/agents/install/attempts': { attempts: [], total: 0, retained_max: 100 },
  })
  return render(
    <EnvironmentContext.Provider value={{ ...DEFAULT_ENV, refreshAgents: vi.fn(), ...env }}>
      <GetStartedView />
    </EnvironmentContext.Provider>,
  )
}
const typeServer = (value) => fireEvent.change(
  screen.getByLabelText(/POVengine address the host can reach/i), { target: { value } },
)

describe('GetStartedView — the typed address meets the Host guard', () => {
  beforeEach(() => { window.localStorage.clear() })

  it('the loopback hint sends the DC to the LAN IP, and no longer says "or DNS name" unqualified', () => {
    mount({ healthModel: health(GUARD([])) })
    const hint = screen.getByTestId('gs-server-loopback').textContent
    expect(hint).toMatch(/LAN IP/)
    expect(hint).not.toMatch(/LAN IP or DNS name/)
    expect(hint).toContain('CORTEXSIM_ALLOWED_HOSTS')
  })

  it('an unlisted DNS name warns with the code, the symptom on the target and both fixes', () => {
    mount({ healthModel: health(GUARD(['jumpbox-01'])) })
    typeServer('http://simcore.lab.customer.example:8888')
    const warn = screen.getByTestId('gs-server-host-warn')
    expect(warn.getAttribute('data-state')).toBe('not_listed')
    expect(warn.textContent).toContain('simcore.lab.customer.example')
    expect(warn.textContent).toContain('HOST_NOT_ALLOWED')
    expect(warn.textContent).toContain('CORTEXSIM_ALLOWED_HOSTS')
    expect(warn.textContent).toContain('jumpbox-01')
    expect(warn.textContent).toMatch(/error: 400/)
    expect(warn.textContent).toMatch(/no install attempt/i)
    expect(warn.textContent).toMatch(/IP address/i)
  })

  it('with every local name known it says SimCore WILL refuse, and names the machine', () => {
    mount({ healthModel: health({ ...GUARD(['jumpbox-01']), local_host_names: ['4f2a9c'], local_fqdn_names: [] }) })
    typeServer('http://simcore.lab.customer.example:8888')
    const warn = screen.getByTestId('gs-server-host-warn')
    expect(warn.getAttribute('data-state')).toBe('not_listed')
    expect(warn.textContent).toMatch(/POVengine refuses/)
    expect(warn.textContent).not.toMatch(/may refuse/)
    expect(warn.textContent).not.toMatch(/cannot see the machine/)
    expect(warn.textContent).toContain('4f2a9c')
    expect(warn.textContent).toContain('HOST_NOT_ALLOWED')
  })

  it('with the FQDN not looked up yet it names the host name and says what is left unknown', () => {
    mount({ healthModel: health({ ...GUARD([]), local_host_names: ['4f2a9c'], local_fqdn_names: null }) })
    typeServer('http://simcore.lab.customer.example:8888')
    const warn = screen.getByTestId('gs-server-host-warn')
    expect(warn.textContent).toContain('4f2a9c')
    expect(warn.textContent).toMatch(/fully-qualified/)
    expect(warn.textContent).not.toMatch(/cannot see the machine/)
  })

  it('the machine\'s own published name raises nothing', () => {
    mount({ healthModel: health({ ...GUARD([]), local_host_names: ['simcore-01'], local_fqdn_names: null }) })
    typeServer('http://simcore-01:8888')
    expect(screen.queryByTestId('gs-server-host-warn')).toBeNull()
  })

  it('says the allowlist is empty rather than printing nothing', () => {
    mount({ healthModel: health(GUARD([])) })
    typeServer('http://simcore.lab.customer.example:8888')
    expect(screen.getByTestId('gs-server-host-warn').textContent).toMatch(/CORTEXSIM_ALLOWED_HOSTS is empty/)
  })

  it('a listed name, an IP and the console\'s own name raise nothing', () => {
    mount({ healthModel: health(GUARD(['simcore.lab.example'])) })
    for (const url of ['http://simcore.lab.example:8888', 'http://10.0.0.5:8888', window.location.origin]) {
      typeServer(url)
      expect(screen.queryByTestId('gs-server-host-warn'), url).toBeNull()
    }
  })

  it('when SimCore does not report the guard, it says it cannot confirm — not that the name is fine', () => {
    mount({ healthModel: health(undefined) })
    typeServer('http://simcore.lab.customer.example:8888')
    const warn = screen.getByTestId('gs-server-host-warn')
    expect(warn.getAttribute('data-state')).toBe('unknown')
    expect(warn.textContent).toMatch(/cannot confirm/i)
  })

  it('text that is not a URL is called out before it reaches an install line', () => {
    mount({ healthModel: health(GUARD([])) })
    typeServer('simcore.lab.example:8888')
    expect(screen.getByTestId('gs-server-host-warn').getAttribute('data-state')).toBe('invalid')
  })
})
