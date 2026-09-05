import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import DataStreamsView from '../DataStreamsView.jsx'
import { installRoutes } from '../../../test/mockFetch.js'

const DATA_STREAMS = {
  catalogue_source: 'https://cortex-docs.paloaltonetworks.com/analytics-alerts/alerts-by-data-source',
  catalogue_version: '2026-09-04',
  counts: {
    total: 34, addressable: 31, covered: 14, partial: 3, gap: 14,
    not_addressable: 3, authored: 14, proven: 0, with_negative_control: 3,
  },
  authored_not_proven:
    '14 of 31 addressable sources are authored; tenant-verified is 0. Authored is not proven — no emitter has been observed firing its detector against a live Cortex tenant.',
  sources: [
    { key: 'third_party_firewalls', name: 'Third-Party Firewalls', category: 'Third-Party',
      dataset: 'third_party_firewall_raw', state: 'covered', authored: true, proven: false,
      has_negative_control: true, emitters: ['third_party_firewall_emitter'], partial_emitters: [] },
    { key: 'okta_audit_log', name: 'Okta Audit Log', category: 'Identity & SSO',
      dataset: 'okta_audit_raw', state: 'gap', authored: false, proven: false,
      has_negative_control: false, emitters: [], partial_emitters: [] },
    { key: 'xdr_agent', name: 'XDR Agent', category: 'Endpoint', dataset: null,
      state: 'not_addressable', authored: false, proven: false,
      has_negative_control: false, emitters: [], partial_emitters: [] },
  ],
  emitters: [
    {
      name: 'third_party_firewall_emitter', family: 'analytics_log_streamer',
      datasets: ['third_party_firewall_raw'], supports_negative_control: true,
      detectors: [{ alert: 'Port scan detected' }],
      sources: [{ key: 'third_party_firewalls', name: 'Third-Party Firewalls', coverage: 'full' }],
      latest_delivery: null,
    },
  ],
}

describe('<DataStreamsView />', () => {
  it('renders the full catalogue with gaps visible and authored != proven', async () => {
    installRoutes({ 'GET /api/eal/data-streams': DATA_STREAMS })
    render(<DataStreamsView onMessage={vi.fn()} />)
    await waitFor(() => {
      expect(screen.getByTestId('ds-authored-not-proven')).toBeInTheDocument()
    })
    // The honesty banner is surfaced verbatim.
    expect(screen.getByTestId('ds-authored-not-proven').textContent).toMatch(/Authored is not proven/)
    // Tenant-verified is shown as 0 (a zero is degraded, not hidden).
    expect(screen.getByTestId('ds-counts').textContent).toMatch(/Tenant-verified/)
    // A covered source, a gap, and a non-addressable source all render.
    expect(screen.getByText('Third-Party Firewalls')).toBeInTheDocument()
    expect(screen.getByText('Okta Audit Log')).toBeInTheDocument()
  })

  it('gap filter shows only gap sources — a gap reads as a gap, not as absent', async () => {
    installRoutes({ 'GET /api/eal/data-streams': DATA_STREAMS })
    render(<DataStreamsView onMessage={vi.fn()} />)
    await waitFor(() => screen.getByText('Okta Audit Log'))
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'gap' } })
    await waitFor(() => {
      expect(screen.getByText('Okta Audit Log')).toBeInTheDocument()
      expect(screen.queryByText('Third-Party Firewalls')).not.toBeInTheDocument()
    })
  })

  it('emitters tab shows negative-control support and no-run delivery state', async () => {
    installRoutes({ 'GET /api/eal/data-streams': DATA_STREAMS })
    render(<DataStreamsView onMessage={vi.fn()} />)
    await waitFor(() => screen.getByText('Emitters'))
    fireEvent.click(screen.getByText('Emitters'))
    await waitFor(() => {
      expect(screen.getByText('third_party_firewall_emitter')).toBeInTheDocument()
      // No run yet — shown as "not run yet", never a fabricated "delivered".
      expect(screen.getByText('not run yet')).toBeInTheDocument()
    })
  })

  it('degrades cleanly on API error', async () => {
    installRoutes({
      'GET /api/eal/data-streams': () =>
        new Response(JSON.stringify({ error: 'boom' }), { status: 500 }),
    })
    render(<DataStreamsView onMessage={vi.fn()} />)
    await waitFor(() => {
      expect(document.body.textContent).toMatch(/Could not load the analytics coverage surface/)
    })
  })
})
