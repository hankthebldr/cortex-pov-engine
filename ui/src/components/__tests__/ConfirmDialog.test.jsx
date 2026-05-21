/**
 * Smoke + interaction tests for ConfirmDialog.
 */
import React from 'react'
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import ConfirmDialog from '../console/ConfirmDialog.jsx'

void React

describe('<ConfirmDialog />', () => {
  it('renders nothing when closed', () => {
    const { container } = render(<ConfirmDialog open={false} />)
    expect(container.querySelector('.confirm-dialog')).toBeFalsy()
  })

  it('renders title + body + actions when open', () => {
    render(
      <ConfirmDialog
        open
        title="Abort run?"
        body="This will stop the agent."
        confirmLabel="Abort"
      />
    )
    expect(screen.getByText('Abort run?')).toBeInTheDocument()
    expect(screen.getByText('This will stop the agent.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /abort/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /cancel/i })).toBeInTheDocument()
  })

  it('Confirm fires onConfirm and not onClose', () => {
    const onConfirm = vi.fn()
    const onClose = vi.fn()
    render(
      <ConfirmDialog
        open
        onConfirm={onConfirm}
        onClose={onClose}
        confirmLabel="Yes"
      />
    )
    fireEvent.click(screen.getByRole('button', { name: 'Yes' }))
    expect(onConfirm).toHaveBeenCalled()
    expect(onClose).not.toHaveBeenCalled()
  })

  it('Cancel fires onClose and not onConfirm', () => {
    const onConfirm = vi.fn()
    const onClose = vi.fn()
    render(
      <ConfirmDialog open onConfirm={onConfirm} onClose={onClose} />
    )
    fireEvent.click(screen.getByRole('button', { name: /cancel/i }))
    expect(onClose).toHaveBeenCalled()
    expect(onConfirm).not.toHaveBeenCalled()
  })

  it('clicking the backdrop closes', () => {
    const onClose = vi.fn()
    const { container } = render(<ConfirmDialog open onClose={onClose} />)
    fireEvent.click(container.querySelector('.confirm-dialog__backdrop'))
    expect(onClose).toHaveBeenCalled()
  })

  it('clicking inside the dialog does NOT close', () => {
    const onClose = vi.fn()
    const { container } = render(<ConfirmDialog open onClose={onClose} />)
    fireEvent.click(container.querySelector('.confirm-dialog'))
    expect(onClose).not.toHaveBeenCalled()
  })

  it('accepts JSX body and renders it', () => {
    render(
      <ConfirmDialog
        open
        title="t"
        body={<span data-testid="body">custom node</span>}
      />
    )
    expect(screen.getByTestId('body')).toBeInTheDocument()
  })

  it('supports a primary variant for non-destructive confirms', () => {
    const { container } = render(
      <ConfirmDialog open confirmVariant="primary" confirmLabel="Save" />
    )
    expect(container.querySelector('.btn--primary')).toBeTruthy()
    expect(container.querySelector('.btn--danger-solid')).toBeFalsy()
  })
})
