// @vitest-environment jsdom
import React from 'react'
import { describe, it, expect, beforeAll, beforeEach } from 'vitest'
import { renderHook, act, render, screen } from '@testing-library/react'
import { useFirstUseHint } from '../useFirstUseHint.js'
import FirstUseHint from '../FirstUseHint.jsx'

void React

function makeStorageStub() {
  const m = new Map()
  return {
    get length() { return m.size },
    key: (i) => Array.from(m.keys())[i] ?? null,
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => { m.set(k, String(v)) },
    removeItem: (k) => { m.delete(k) },
    clear: () => { m.clear() },
  }
}
beforeAll(() => {
  if (!window.localStorage) {
    Object.defineProperty(window, 'localStorage', {
      value: makeStorageStub(), writable: true, configurable: true,
    })
  }
})
beforeEach(() => { window.localStorage.clear() })

describe('useFirstUseHint', () => {
  it('shows on a fresh profile', () => {
    const { result } = renderHook(() => useFirstUseHint('launch'))
    expect(result.current.show).toBe(true)
  })

  it('clears permanently once the control is USED', () => {
    const { result } = renderHook(() => useFirstUseHint('launch'))
    act(() => { result.current.onUse() })
    expect(result.current.show).toBe(false)
    const again = renderHook(() => useFirstUseHint('launch'))
    expect(again.result.current.show).toBe(false)
  })

  it('is independent per control id', () => {
    const launch = renderHook(() => useFirstUseHint('launch'))
    act(() => { launch.result.current.onUse() })
    const abort = renderHook(() => useFirstUseHint('abort'))
    expect(abort.result.current.show).toBe(true)
  })
})

describe('FirstUseHint', () => {
  it('renders its text when show is true', () => {
    render(<FirstUseHint show text="Launch runs the armed scenario." onDismiss={() => {}} />)
    expect(screen.getByText(/Launch runs the armed scenario/)).toBeTruthy()
  })

  it('renders nothing when show is false', () => {
    const { container } = render(<FirstUseHint show={false} text="hidden" onDismiss={() => {}} />)
    expect(container.firstChild).toBeNull()
  })
})

describe('FirstUseHint with hook integration', () => {
  it('persists when control is USED, but NOT when dismissed', () => {
    // Harness: render the hook + component wired together
    function HarnessWithDismiss() {
      const { show, onUse } = useFirstUseHint('launch')
      const [hidden, setHidden] = React.useState(false)
      return (
        <FirstUseHint
          show={show && !hidden}
          text="Launch runs the armed scenario."
          onDismiss={() => { setHidden(true) }}
        />
      )
    }

    const { rerender } = render(<HarnessWithDismiss />)

    // Click dismiss button
    const dismissBtn = screen.getByLabelText('Dismiss hint')
    act(() => { dismissBtn.click() })

    // Hint should be hidden in the UI
    expect(screen.queryByText(/Launch runs the armed scenario/)).not.toBeInTheDocument()

    // BUT storage should still be null (not persisted)
    expect(window.localStorage.getItem('cortexsim.onboarding.hint.launch')).toBeNull()

    // Re-render with a fresh hook
    function HarnessFresh() {
      const { show } = useFirstUseHint('launch')
      return <FirstUseHint show={show} text="Launch runs the armed scenario." onDismiss={() => {}} />
    }
    rerender(<HarnessFresh />)

    // Hint should return because the control was never USED
    expect(screen.getByText(/Launch runs the armed scenario/)).toBeTruthy()
  })
})
