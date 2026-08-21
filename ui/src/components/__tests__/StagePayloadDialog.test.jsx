import React from 'react'
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import StagePayloadDialog from '../console/StagePayloadDialog.jsx'
import { SUPPLY } from '../console/supplyState.js'

void React

const ADAPTER = {
  adapter_id: 'TOOL-LINPEAS', name: 'LinPEAS', version: '20250601', tier: 4,
  upstream: { repo: 'https://github.com/carlospolop/PEASS-ng', license: 'MIT', attribution: 'Carlos Polop' },
}

const SUPPLY_UNSTAGED = {
  state: SUPPLY.UNSTAGED, label: 'NOT STAGED', chipClass: 'chip chip--pending',
  egress: 'target_host', egressLabel: 'egress: target → github.com, at run time',
  payloadName: 'linpeas.sh', sha256: 'a'.repeat(64), pinned: true, pinType: 'release-tag',
  pinRef: '20250601', license: 'MIT', packLicense: 'MIT',
  sourceUrl: 'https://github.com/carlospolop/PEASS-ng/releases/download/20250601/linpeas.sh',
  stagePath: '/tmp/linpeas.sh', actionable: true,
}

const renderDialog = (props = {}) => render(
  <StagePayloadDialog adapter={ADAPTER} supply={SUPPLY_UNSTAGED} {...props} />,
)

describe('<StagePayloadDialog />', () => {
  it('gates Stage behind the licence acknowledgement, and says why', () => {
    renderDialog()
    const stage = screen.getByRole('button', { name: /^Stage ▸$/ })
    expect(stage).toBeDisabled()
    expect(screen.getByTestId('stage-blockers')).toHaveTextContent(/Acknowledge the licence/)
    fireEvent.click(screen.getByTestId('stage-license-ack'))
    expect(stage).toBeEnabled()
  })

  it('states the egress boundary before anything is fetched', () => {
    renderDialog()
    expect(screen.getByText(/one outbound HTTPS request from this host/i)).toBeInTheDocument()
    expect(screen.getAllByText(/github\.com/).length).toBeGreaterThan(0)
  })

  it('calls onStage once the licence is acknowledged', () => {
    const onStage = vi.fn()
    renderDialog({ onStage })
    fireEvent.click(screen.getByTestId('stage-license-ack'))
    fireEvent.click(screen.getByRole('button', { name: /^Stage ▸$/ }))
    expect(onStage).toHaveBeenCalledTimes(1)
  })

  it('shows an indeterminate in-flight panel — no fabricated byte count', () => {
    renderDialog({ job: { state: 'staging' } })
    expect(screen.getByTestId('stage-in-flight')).toBeInTheDocument()
    expect(screen.queryByText(/%/)).not.toBeInTheDocument()
    expect(screen.getByText(/no intermediate byte count to report/)).toBeInTheDocument()
  })

  it('renders the digest, the warnings and the pack snippet on success', () => {
    renderDialog({
      job: {
        state: 'done',
        result: {
          payload: { name: 'linpeas.sh', sha256: 'b'.repeat(64), size_bytes: 1106683 },
          warnings: [{ code: 'ARTIFACT_UNPINNED', detail: 'staged without a pin' }],
          pack_snippet: 'install:\n  artifact:\n    filename: linpeas.sh\n',
        },
      },
    })
    expect(screen.getByTestId('stage-done')).toHaveTextContent('1,106,683 bytes')
    expect(screen.getByText('b'.repeat(64))).toBeInTheDocument()
    expect(screen.getByTestId('stage-warning-ARTIFACT_UNPINNED')).toBeInTheDocument()
    expect(screen.getByText(/filename: linpeas\.sh/)).toBeInTheDocument()
  })

  it('offers NO override of any kind on a pin mismatch', () => {
    // The whole point: blessing a changed upstream from a console is how an
    // unreviewed artifact reaches a customer host, and there is no undo.
    renderDialog({
      job: { state: 'failed', error: { code: 'PAYLOAD_PIN_MISMATCH', message: 'Upstream changed', detail: 'expected a… got b…' } },
    })
    expect(screen.getByTestId('stage-failed')).toHaveTextContent('PAYLOAD_PIN_MISMATCH')
    const buttons = screen.getAllByRole('button').map((b) => b.textContent || '')
    expect(buttons.some((t) => /anyway|override|force|trust/i.test(t))).toBe(false)
    expect(screen.queryByRole('button', { name: /Retry/ })).not.toBeInTheDocument()
    expect(screen.getByText(/deliberately no override/i)).toBeInTheDocument()
  })

  it('explains a 502 as a failure, never as a staged artifact, and offers Retry', () => {
    renderDialog({ job: { state: 'failed', error: { code: 'PAYLOAD_FETCH_FAILED', message: 'Upstream refused' } } })
    expect(screen.getByText(/never staged artifacts/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Retry/ })).toBeInTheDocument()
  })

  it('reports a client-side timeout as its own named outcome', () => {
    renderDialog({ job: { state: 'failed', error: { code: 'PAYLOAD_STAGE_TIMEOUT', message: 'timed out' } } })
    expect(screen.getByTestId('stage-failed')).toHaveTextContent('PAYLOAD_STAGE_TIMEOUT')
    expect(screen.getByText(/NOT staged/)).toBeInTheDocument()
  })

  it('replaces the Stage button with the offline recipe on a read-only shelf', () => {
    renderDialog({ writable: false, distDir: '/app/payloads' })
    expect(screen.queryByRole('button', { name: /^Stage ▸$/ })).not.toBeInTheDocument()
    expect(screen.getByText(/build-payloads\.sh/)).toBeInTheDocument()
  })

  it('closes on Escape', async () => {
    const onClose = vi.fn()
    renderDialog({ onClose })
    fireEvent.keyDown(document, { key: 'Escape' })
    await waitFor(() => expect(onClose).toHaveBeenCalled())
  })
})
