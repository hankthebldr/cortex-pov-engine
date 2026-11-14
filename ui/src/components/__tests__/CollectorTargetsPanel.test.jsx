import React from 'react'
import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'

import CollectorTargetsPanel from '../console/CollectorTargetsPanel.jsx'
import { installRoutes } from '../../test/mockFetch.js'

const EMPTY = {
  targets: [], count: 0, default_target: null, status: 'degraded',
  code: 'NO_COLLECTOR_TARGETS',
  detail: 'No collector target is configured, so no collector step can run live.',
  collector_plugins: ['cloud_audit_emitter', 'idp_signin_emulator'],
  unbound_plugins: ['cloud_audit_emitter', 'idp_signin_emulator'],
}

const ONE = {
  ...EMPTY, count: 1, status: 'ok', code: null, detail: undefined,
  unbound_plugins: ['cloud_audit_emitter'],
  targets: [{
    name: 'okta-hec', kind: 'xsiam_http_collector',
    url: 'https://api-acme.example.test/logs/v1/event', dataset: 'okta_sso_raw',
    auth_header: 'Authorization', auth_scheme: '', plugins: ['idp_signin_emulator'],
    is_default: false, has_key: true, warnings: [], compress: true,
    last_check: { verdict: 'reachable', checked_at: '2026-11-10T22:00:00+00:00' },
  }],
}

function bodyOf(call) {
  return JSON.parse(call[1].body)
}

describe('CollectorTargetsPanel', () => {
  let fetchSpy

  it('says plainly when nothing is configured, and which plugins that strands', async () => {
    installRoutes({ 'GET /api/collector-targets': EMPTY })
    render(<CollectorTargetsPanel />)
    expect(await screen.findByTestId('ct-empty')).toHaveTextContent(/no collector target/i)
    const unbound = screen.getByTestId('ct-unbound')
    expect(unbound).toHaveTextContent('cloud_audit_emitter')
    expect(unbound).toHaveTextContent('idp_signin_emulator')
  })

  describe('with one target', () => {
    beforeEach(() => {
      fetchSpy = installRoutes({
        'GET /api/collector-targets': ONE,
        'PUT /api/collector-targets/:name': ONE.targets[0],
        'POST /api/collector-targets/:name/preflight': (url, init) => ({
          target: 'okta-hec',
          verdict: JSON.parse(init.body).write_canary ? 'accepted' : 'reachable',
          records_written: JSON.parse(init.body).write_canary ? 1 : 0,
          rungs: [], tenant_verified: 0, warnings: [],
        }),
      })
    })

    it('lists the target with its key state and last check, never a key', async () => {
      render(<CollectorTargetsPanel />)
      const row = await screen.findByTestId('ct-row-okta-hec')
      expect(row).toHaveTextContent('api-acme.example.test/logs/v1/event')
      expect(row).toHaveTextContent('okta_sso_raw')
      expect(row).toHaveTextContent(/key stored/i)
      expect(row).toHaveTextContent(/reachable/i)
      expect(row).toHaveTextContent(/not proven/i)
    })

    it('checks reachability without writing to the tenant', async () => {
      render(<CollectorTargetsPanel />)
      const row = await screen.findByTestId('ct-row-okta-hec')
      fireEvent.click(within(row).getByRole('button', { name: /check reachability/i }))
      await waitFor(() => {
        const call = fetchSpy.mock.calls.find(([u, i]) => String(u).endsWith('/preflight') && i?.method === 'POST')
        expect(call).toBeTruthy()
        expect(bodyOf(call)).toEqual({ write_canary: false })
      })
    })

    it('sends a test record only after saying it writes into the tenant', async () => {
      render(<CollectorTargetsPanel />)
      const row = await screen.findByTestId('ct-row-okta-hec')
      fireEvent.click(within(row).getByRole('button', { name: /send test record/i }))
      expect(within(row).getByTestId('ct-confirm-write')).toHaveTextContent(/writes one .*record into the tenant/i)
      expect(fetchSpy.mock.calls.some(([u]) => String(u).endsWith('/preflight'))).toBe(false)
      fireEvent.click(within(row).getByRole('button', { name: /^send it$/i }))
      await waitFor(() => {
        const call = fetchSpy.mock.calls.find(([u]) => String(u).endsWith('/preflight'))
        expect(bodyOf(call)).toEqual({ write_canary: true })
      })
    })

    it('edits without re-sending a key it never had', async () => {
      render(<CollectorTargetsPanel />)
      const row = await screen.findByTestId('ct-row-okta-hec')
      fireEvent.click(within(row).getByRole('button', { name: /edit/i }))
      const form = screen.getByTestId('ct-form')
      const keyInput = within(form).getByLabelText(/collector key/i)
      expect(keyInput).toHaveAttribute('type', 'password')
      expect(keyInput).toHaveValue('')
      fireEvent.click(within(form).getByRole('button', { name: /save/i }))
      await waitFor(() => {
        const call = fetchSpy.mock.calls.find(([, i]) => i?.method === 'PUT')
        expect(call[0]).toContain('/api/collector-targets/okta-hec')
        const body = bodyOf(call)
        expect(body).not.toHaveProperty('token')
        expect(body.plugins).toEqual(['idp_signin_emulator'])
        // An edit keeps the target's compression. PUT replaces the target and
        // the API defaults compress off, so leaving it out turned it off.
        expect(body.compress).toBe(true)
      })
    })

    it('shows the target\'s compression on its row', async () => {
      render(<CollectorTargetsPanel />)
      expect(await screen.findByTestId('ct-row-compress-okta-hec')).toHaveTextContent('gzip')
    })
  })

  it('creates a target with its key and clears the key field after saving', async () => {
    fetchSpy = installRoutes({
      'GET /api/collector-targets': EMPTY,
      'PUT /api/collector-targets/:name': ONE.targets[0],
    })
    render(<CollectorTargetsPanel />)
    fireEvent.click(await screen.findByRole('button', { name: /add collector target/i }))
    const form = screen.getByTestId('ct-form')
    fireEvent.change(within(form).getByLabelText(/^name/i), { target: { value: 'okta-hec' } })
    fireEvent.change(within(form).getByLabelText(/^url/i), { target: { value: ONE.targets[0].url } })
    fireEvent.click(within(form).getByLabelText('idp_signin_emulator'))
    fireEvent.change(within(form).getByLabelText(/collector key/i), { target: { value: 'k-secret' } })
    fireEvent.click(within(form).getByRole('button', { name: /save/i }))
    await waitFor(() => {
      const call = fetchSpy.mock.calls.find(([, i]) => i?.method === 'PUT')
      expect(bodyOf(call)).toMatchObject({
        kind: 'xsiam_http_collector', url: ONE.targets[0].url,
        plugins: ['idp_signin_emulator'], token: 'k-secret',
        // A new target is gzip unless the DC turns it off.
        compress: true,
      })
    })
    await waitFor(() => expect(screen.queryByTestId('ct-form')).toBeNull())
  })

  it('a new target can be declared uncompressed, and the form says why gzip is the default', async () => {
    fetchSpy = installRoutes({
      'GET /api/collector-targets': EMPTY,
      'PUT /api/collector-targets/:name': ONE.targets[0],
    })
    render(<CollectorTargetsPanel />)
    fireEvent.click(await screen.findByRole('button', { name: /add collector target/i }))
    const form = screen.getByTestId('ct-form')
    expect(form).toHaveTextContent(/gzip-only reject uncompressed/)
    const tick = within(form).getByTestId('ct-compress')
    expect(tick).toBeChecked()
    fireEvent.click(tick)
    fireEvent.change(within(form).getByLabelText(/^name/i), { target: { value: 'plain-hec' } })
    fireEvent.change(within(form).getByLabelText(/^url/i), { target: { value: ONE.targets[0].url } })
    fireEvent.click(within(form).getByRole('button', { name: /save/i }))
    await waitFor(() => {
      const call = fetchSpy.mock.calls.find(([, i]) => i?.method === 'PUT')
      expect(bodyOf(call).compress).toBe(false)
    })
  })

  it('shows the server refusal code instead of a generic failure', async () => {
    installRoutes({
      'GET /api/collector-targets': EMPTY,
      'PUT /api/collector-targets/:name': () => new Response(JSON.stringify({
        detail: { error: 'Plugin already served', code: 'TARGET_PLUGIN_ALREADY_BOUND',
                  detail: "['idp_signin_emulator'] already go to target 'other'" },
      }), { status: 422, headers: { 'content-type': 'application/json' } }),
    })
    render(<CollectorTargetsPanel />)
    fireEvent.click(await screen.findByRole('button', { name: /add collector target/i }))
    const form = screen.getByTestId('ct-form')
    fireEvent.change(within(form).getByLabelText(/^name/i), { target: { value: 'okta-b' } })
    fireEvent.change(within(form).getByLabelText(/^url/i), { target: { value: ONE.targets[0].url } })
    fireEvent.click(within(form).getByRole('button', { name: /save/i }))
    expect(await within(form).findByRole('alert')).toHaveTextContent(/already go to target/)
  })
})
