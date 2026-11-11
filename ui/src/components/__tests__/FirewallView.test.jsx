/**
 * Firewall wizard (Connect → Check → Sources), spec 2026-11-10 §6.
 *
 * The rules under test are honesty rules, not layout: the admin password never
 * outlives the keygen call, a report nobody answered says so, a blocked stage
 * is never collapsed into a summary, and a NAT/collision row reads as blocked.
 */
import React from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, within, cleanup } from '@testing-library/react'
import { EnvironmentContext, DEFAULT_ENV } from '../../context/EnvironmentContext.jsx'

vi.mock('../../api/client.js', () => ({
  listFirewalls: vi.fn(),
  putFirewall: vi.fn(),
  firewallKeygen: vi.fn(),
  firewallPreflight: vi.fn(),
  putFirewallSource: vi.fn(),
  deleteFirewall: vi.fn(),
}))

import * as client from '../../api/client.js'
import FirewallView from '../console/FirewallView.jsx'

void React

const PASSWORD = 'hunter2-Sup3rSecret!'

const FW = {
  name: 'pa-440', host: '192.168.1.1', verify_tls: false, vsys: 'vsys1',
  forwarding_path: 'auto', last_verified_ok: null, last_verified_error: null,
  last_verified_at: null,
}

function report(overrides = {}) {
  return {
    firewall: 'pa-440', kind: 'panos_ngfw', mgmt_host: '192.168.1.1', overall: 'blocked',
    stages: [
      { id: 'config', status: 'ok', code: 'PN_OK', detail: 'host valid' },
      { id: 'dns_tls', status: 'degraded', code: 'PN_TLS_UNVERIFIED', detail: 'verify_tls is off',
        remediation: 'Install a trusted management certificate.' },
      { id: 'forwarding_sls', status: 'blocked', code: 'PN_COLLECTOR_INACTIVE',
        detail: 'no collector active',
        remediation: 'threat logs from this firewall will not reach the tenant' },
      { id: 'decryption', status: 'skipped', code: 'PN_PATH_NOT_SELECTED', detail: 'not selected' },
    ],
    sources: [
      {
        agent_id: 'agent-a', hostname: 'pov-target-01', fw_observed_src: '10.0.0.5',
        fw_src_provenance: 'inferred:last_ip', agent_addresses: ['172.17.0.2'],
        agent_addr_provenance: 'entered', nat_verdict: 'nat_between', collision_with: ['ms-01'],
        status: 'blocked',
        stages: [
          { id: 'address', status: 'blocked', code: 'PN_NAT_BETWEEN_AGENT_AND_FW',
            detail: 'NAT sits between them', remediation: 'no 5-tuple stitch can form' },
        ],
        probes: [
          { destination: '1.1.1.1', port: 53, protocol: 'udp', application: 'dns-base',
            rule: 'allow-out', rule_index: 3, action: 'allow',
            profiles: {
              virus: { profile: 'default', source: 'predefined', bucket: 'signature-default' },
              spyware: { profile: 'lab', source: 'config', bucket: 'alert', scoped_rules: [] },
            },
            dns_security: { profile: 'lab', bucket: 'sinkhole' },
            log_setting: 'fwd-all', skipped: null },
        ],
      },
      {
        agent_id: 'agent-b', hostname: 'ms-01', fw_observed_src: '10.0.0.5',
        fw_src_provenance: 'entered', agent_addresses: [],
        agent_addr_provenance: 'unknown', nat_verdict: 'unknown', collision_with: ['pov-target-01'],
        status: 'blocked', stages: [], probes: [],
      },
    ],
    queries_issued: 14, panos_version: '10.2.8', config_source: 'running',
    proves: 'Firewall configuration as read by 14 live call(s).', tenant_verified: 0,
    ...overrides,
  }
}

function mount() {
  return render(
    <EnvironmentContext.Provider value={DEFAULT_ENV}>
      <FirewallView />
    </EnvironmentContext.Provider>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  client.listFirewalls.mockResolvedValue({ firewalls: [FW], write_enabled: false, tenant_verified: 0 })
})
afterEach(cleanup)

async function runCheck(body) {
  client.firewallPreflight.mockResolvedValue(body)
  mount()
  await screen.findByText('192.168.1.1')
  fireEvent.click(screen.getByRole('button', { name: /run preflight/i }))
  await waitFor(() => expect(client.firewallPreflight).toHaveBeenCalled())
}

describe('FirewallView', () => {
  it('renders the three step headings under one page title', async () => {
    mount()
    expect(screen.getByRole('heading', { level: 1, name: 'Firewall' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 2, name: /connect/i })).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 2, name: /check/i })).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 2, name: /sources/i })).toBeInTheDocument()
    await screen.findByText('192.168.1.1')
  })

  it('keygen sends the password once, then clears it and never renders it', async () => {
    // Capture the body as sent: the component nulls its own reference once the
    // call returns, so the recorded argument no longer carries the password.
    let sent = null
    client.firewallKeygen.mockImplementation(async (b) => {
      sent = { ...b }
      return {
        name: 'pa-440', kind: 'panos_ngfw', overrides_cleared: true,
        warnings: ['password sent over unverified TLS'],
      }
    })
    mount()
    fireEvent.click(screen.getByRole('radio', { name: /generate key from admin login/i }))
    expect(screen.getByText(/Your password is used once to generate an API key and is not stored\. Generating a key does not revoke this admin's other keys\. Use a read-only API admin role\./)).toBeInTheDocument()

    fireEvent.change(screen.getByRole('textbox', { name: /^name$/i }), { target: { value: 'pa-440' } })
    fireEvent.change(screen.getByRole('textbox', { name: /management host/i }), { target: { value: '192.168.1.1' } })
    fireEvent.change(screen.getByRole('textbox', { name: /admin username/i }), { target: { value: 'apiadmin' } })
    const pw = screen.getByLabelText(/admin password/i)
    expect(pw).toHaveAttribute('type', 'password')
    expect(pw).toHaveAttribute('autocomplete', 'off')
    fireEvent.change(pw, { target: { value: PASSWORD } })

    fireEvent.click(screen.getByRole('button', { name: /generate key and register/i }))
    await waitFor(() => expect(client.firewallKeygen).toHaveBeenCalledTimes(1))
    expect(sent).toMatchObject({
      name: 'pa-440', host: '192.168.1.1', username: 'apiadmin', password: PASSWORD,
    })
    expect(client.firewallKeygen.mock.calls[0][0].password).toBeNull()
    await screen.findByText('password sent over unverified TLS')
    expect(screen.getByText(/address overrides from the previous firewall were cleared/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/admin password/i)).toHaveValue('')
    expect(document.body.textContent).not.toContain(PASSWORD)
    expect(document.body.innerHTML).not.toContain(PASSWORD)
  })

  it('clears the password when keygen fails too', async () => {
    const err = Object.assign(new Error('Firewall rejected the credentials [PN_KEYGEN_REJECTED]'),
      { code: 'PN_KEYGEN_REJECTED', status: 401, detail: 'The firewall did not issue an API key.' })
    client.firewallKeygen.mockRejectedValue(err)
    mount()
    fireEvent.click(screen.getByRole('radio', { name: /generate key from admin login/i }))
    fireEvent.change(screen.getByRole('textbox', { name: /^name$/i }), { target: { value: 'pa-440' } })
    fireEvent.change(screen.getByRole('textbox', { name: /management host/i }), { target: { value: '192.168.1.1' } })
    fireEvent.change(screen.getByRole('textbox', { name: /admin username/i }), { target: { value: 'apiadmin' } })
    fireEvent.change(screen.getByLabelText(/admin password/i), { target: { value: PASSWORD } })
    fireEvent.click(screen.getByRole('button', { name: /generate key and register/i }))
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('PN_KEYGEN_REJECTED')
    expect(screen.getByLabelText(/admin password/i)).toHaveValue('')
    expect(document.body.innerHTML).not.toContain(PASSWORD)
  })

  it('turning Verify TLS off states why it is allowed and what the preflight will say', async () => {
    mount()
    const tls = screen.getByRole('checkbox', { name: /verify tls/i })
    expect(tls).toBeChecked()
    fireEvent.click(tls)
    expect(screen.getByText('Self-signed management certificates are common in labs. Every preflight will report this connection as unverified.')).toBeInTheDocument()
    await screen.findByText('192.168.1.1')
  })

  it('paste-key registration goes through putFirewall and clears the key', async () => {
    client.putFirewall.mockResolvedValue({ name: 'fw2', kind: 'panos_ngfw' })
    mount()
    fireEvent.change(screen.getByRole('textbox', { name: /^name$/i }), { target: { value: 'fw2' } })
    fireEvent.change(screen.getByRole('textbox', { name: /management host/i }), { target: { value: '10.1.1.1' } })
    fireEvent.change(screen.getByLabelText(/^api key$/i), { target: { value: 'LUFRPT-key' } })
    fireEvent.click(screen.getByRole('button', { name: /register firewall/i }))
    await waitFor(() => expect(client.putFirewall).toHaveBeenCalledTimes(1))
    expect(client.putFirewall.mock.calls[0][0]).toMatchObject({
      name: 'fw2', host: '10.1.1.1', verify_tls: true, vsys: 'vsys1', forwarding_path: 'auto',
      api_key: 'LUFRPT-key',
    })
    await waitFor(() => expect(screen.getByLabelText(/^api key$/i)).toHaveValue(''))
  })

  it('a report with queries_issued 0 says it was not answered by a firewall', async () => {
    await runCheck(report({ queries_issued: 0 }))
    expect(await screen.findByText(/queries issued: 0/)).toBeInTheDocument()
    expect(screen.getByText(/this report was not answered by a firewall/)).toBeInTheDocument()
    expect(client.firewallPreflight.mock.calls[0][0]).toBe('pa-440')
  })

  it('a report with queries_issued > 0 does not claim it was unanswered, and states the footer', async () => {
    await runCheck(report())
    expect(await screen.findByText(/queries issued: 14/)).toBeInTheDocument()
    expect(screen.queryByText(/this report was not answered by a firewall/)).toBeNull()
    expect(screen.getByText(/Proves: the firewall is configured to write and forward these records\. Does not prove: the tenant ingested them, or they stitched — tenant-verified: 0/)).toBeInTheDocument()
    expect(screen.getByText(/url-filtering and wildfire-analysis/i)).toBeInTheDocument()
  })

  it('renders every stage with code and remediation, never collapsed', async () => {
    await runCheck(report())
    const ladder = await screen.findByTestId('fw-ladder')
    const rows = within(ladder).getAllByRole('listitem')
    expect(rows).toHaveLength(4)
    expect(ladder.querySelector('details')).toBeNull()
    const blocked = rows.find((r) => r.getAttribute('data-status') === 'blocked')
    expect(blocked.textContent).toContain('PN_COLLECTOR_INACTIVE')
    expect(blocked.textContent).toContain('threat logs from this firewall will not reach the tenant')
    const degraded = rows.find((r) => r.getAttribute('data-status') === 'degraded')
    expect(degraded.textContent).toContain('PN_TLS_UNVERIFIED')
    expect(degraded.textContent).toContain('Install a trusted management certificate.')
  })

  it('a NAT-between row with a collision renders as blocked and names the other host', async () => {
    await runCheck(report())
    const row = await screen.findByTestId('fw-source-agent-a')
    expect(row).toHaveAttribute('data-status', 'blocked')
    expect(row.textContent).toMatch(/nat_between/)
    expect(row.textContent).toContain('ms-01')
    expect(row.textContent).toContain('PN_NAT_BETWEEN_AGENT_AND_FW')
  })

  it('a collision row is never shown kinder than its own facts, whatever status it carries', async () => {
    const r = report()
    r.sources = [{ ...r.sources[1], status: 'degraded' }]
    await runCheck(r)
    const row = await screen.findByTestId('fw-source-agent-b')
    expect(row).toHaveAttribute('data-status', 'blocked')
    expect(row.textContent).toContain('pov-target-01')
  })

  it('renders provenance labels verbatim', async () => {
    await runCheck(report())
    const a = await screen.findByTestId('fw-source-agent-a')
    expect(a.textContent).toContain('inferred from last_ip')
    expect(a.textContent).toContain('entered')
    const b = screen.getByTestId('fw-source-agent-b')
    expect(b.textContent).toContain('entered')
    expect(b.textContent).toContain('unknown')
  })

  it('renders per-probe rule, profile buckets and DNS Security separately', async () => {
    await runCheck(report())
    const a = await screen.findByTestId('fw-source-agent-a')
    expect(a.textContent).toContain('allow-out')
    expect(a.textContent).toContain('signature-default')
    expect(a.textContent).toMatch(/DNS Security/i)
    expect(a.textContent).toContain('sinkhole')
    expect(a.textContent).toMatch(/not evaluated/i)
  })

  it('a PN_NO_AGENTS sources entry renders as degraded, not as an empty list', async () => {
    await runCheck(report({
      sources: [{ code: 'PN_NO_AGENTS', status: 'degraded', detail: 'no enrolled agents' }],
    }))
    const entry = await screen.findByTestId('fw-sources-entry')
    expect(entry).toHaveAttribute('data-status', 'degraded')
    expect(entry.textContent).toContain('PN_NO_AGENTS')
  })

  it('inline source edit calls putFirewallSource', async () => {
    client.putFirewallSource.mockResolvedValue({
      firewall: 'pa-440', agent_id: 'agent-a', observed_source: '10.0.0.9',
      agent_addresses: ['10.0.0.9', '172.17.0.2'],
    })
    await runCheck(report())
    fireEvent.click(await screen.findByRole('button', { name: /edit addresses for pov-target-01/i }))
    fireEvent.change(screen.getByRole('textbox', { name: /firewall-observed source for pov-target-01/i }),
      { target: { value: '10.0.0.9' } })
    fireEvent.change(screen.getByRole('textbox', { name: /agent-side addresses for pov-target-01/i }),
      { target: { value: '10.0.0.9, 172.17.0.2' } })
    fireEvent.click(screen.getByRole('button', { name: /save addresses for pov-target-01/i }))
    await waitFor(() => expect(client.putFirewallSource).toHaveBeenCalledWith('pa-440', 'agent-a', {
      observed_source: '10.0.0.9', agent_addresses: ['10.0.0.9', '172.17.0.2'],
    }))
    expect(await screen.findByText(/run the check again/i)).toBeInTheDocument()
  })

  it('lists registered firewalls with their last preflight result', async () => {
    client.listFirewalls.mockResolvedValue({ firewalls: [
      FW,
      { ...FW, name: 'fw-b', host: '10.9.9.9', last_verified_ok: false,
        last_verified_error: 'PN_AUTH_REJECTED', last_verified_at: '2026-11-10T10:00:00+00:00' },
    ], write_enabled: false, tenant_verified: 0 })
    mount()
    const a = await screen.findByTestId('fw-registered-pa-440')
    expect(a.textContent).toMatch(/never checked/i)
    const b = screen.getByTestId('fw-registered-fw-b')
    expect(b.textContent).toContain('PN_AUTH_REJECTED')
    expect(screen.getByRole('button', { name: /remove firewall fw-b/i })).toBeInTheDocument()
  })

  it('with no firewall registered, Check is degraded and offers no run', async () => {
    client.listFirewalls.mockResolvedValue({ firewalls: [], write_enabled: false, tenant_verified: 0 })
    mount()
    expect(await screen.findByText(/no firewall is registered/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /run preflight/i })).toBeNull()
  })
})
