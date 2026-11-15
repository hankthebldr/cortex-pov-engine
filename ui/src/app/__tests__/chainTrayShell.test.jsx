/**
 * The chain tray lives in the SHELL, not in a page (design v26 §4.4).
 *
 * "Add to chain" on a Catalog page and Customize in Build a chain are two
 * destinations; the tray has to carry the item between them. The shell keys
 * each page's view by destination, so a tray held inside the page would be
 * emptied by the very navigation it exists to survive. The surfaces are
 * stubbed: this pins the shell's contract, not the Catalog's or the Composer's.
 */
import React from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { useChainTray } from '../../context/ChainTrayContext.jsx'

void React

vi.mock('../../components/console/TtpBrowserView.jsx', () => ({
  default: function TtpStub() {
    const { add } = useChainTray()
    return (
      <button type="button" onClick={() => add({ kind: 'ttp', id: 'TTP-2026-0002', label: 'TTP-2026-0002 · LSASS dump' })}>
        Add to chain
      </button>
    )
  },
}))

vi.mock('../../components/console/ComposerView.jsx', () => ({
  default: function ComposerStub({ params = {} }) {
    const { items } = useChainTray()
    return (
      <div data-testid="composer-stub">
        <span data-testid="composer-params">{JSON.stringify(params)}</span>
        <span data-testid="composer-tray">{items.map((i) => i.id).join(',')}</span>
      </div>
    )
  },
}))

describe('Shell — the chain tray survives navigation', () => {
  let AppConsole
  beforeEach(async () => {
    window.localStorage.clear()
    window.sessionStorage.clear()
    AppConsole = (await import('../../AppConsole.jsx')).default
  })
  afterEach(() => { window.location.hash = '' })

  it('Add to chain on the Catalog confirms by toast, shows the tray, and Customize carries it into Build a chain', async () => {
    window.location.hash = '#/ttps'
    render(<AppConsole />)
    fireEvent.click(await screen.findByRole('button', { name: 'Add to chain' }))

    expect(await screen.findByText('Added TTP-2026-0002 · LSASS dump to your chain')).toBeInTheDocument()
    const tray = screen.getByRole('region', { name: 'Your chain' })
    expect(tray).toHaveTextContent('1 added')

    fireEvent.click(within(tray).getByRole('button', { name: 'Customize' }))
    await waitFor(() => expect(window.location.hash).toBe('#/composer?tray=1'))
    expect(await screen.findByTestId('composer-tray')).toHaveTextContent('TTP-2026-0002')
  })
})
