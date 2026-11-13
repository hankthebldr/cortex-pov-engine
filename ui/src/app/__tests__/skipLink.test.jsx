/**
 * "Skip to workspace" moves keyboard focus into the workspace on the SAME page.
 *
 * The console routes on the URL hash, so a plain `href="#cortexsim-main"`
 * fragment jump rewrote the hash, the router found no destination named
 * "cortexsim-main", and the keyboard user was dropped on Get started instead
 * of into the page they were on.
 */
import React from 'react'
import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import AppConsole from '../../AppConsole.jsx'

void React

// Long enough for a fragment navigation's hashchange to have landed.
const settle = () => act(() => new Promise((r) => setTimeout(r, 50)))

beforeEach(() => {
  // Keep the first-run help overlay closed so Tab starts at the skip link.
  window.localStorage.setItem('cortexsim.helpOverlay.seenV1', 'true')
  window.location.hash = '#/runs'
})

afterEach(() => {
  window.localStorage.clear()
  window.sessionStorage.clear()
  window.location.hash = ''
})

describe('skip link', () => {
  it('Enter on "Skip to workspace" focuses the workspace and stays on the page', async () => {
    const user = userEvent.setup()
    render(<AppConsole />)
    await settle()
    expect(screen.getByTestId('dest-button-runs')).toHaveAttribute('aria-current', 'page')

    await user.tab()
    const skip = screen.getByRole('link', { name: /skip to workspace/i })
    expect(skip).toHaveFocus()

    await user.keyboard('{Enter}')
    await settle()

    expect(window.location.hash).toBe('#/runs')
    expect(screen.getByTestId('dest-button-runs')).toHaveAttribute('aria-current', 'page')
    expect(screen.getByRole('main', { name: /workspace/i })).toHaveFocus()
  })
})
