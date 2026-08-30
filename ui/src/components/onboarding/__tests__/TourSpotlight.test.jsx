// @vitest-environment jsdom
import React from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import TourSpotlight from '../TourSpotlight.jsx'

void React

const STOP = { id: 'a', anchor: 'anchor-a', destination: 'library', title: 'Start here', body: 'The Library holds 170 scenarios.' }

beforeEach(() => {
  document.body.innerHTML = ''
  const el = document.createElement('div')
  el.setAttribute('data-tour-id', 'anchor-a')
  document.body.appendChild(el)
})

describe('TourSpotlight', () => {
  it('renders the stop title and body', () => {
    render(<TourSpotlight stop={STOP} index={0} total={5} onNext={vi.fn()} onPrev={vi.fn()} onExit={vi.fn()} />)
    expect(screen.getByText('Start here')).toBeTruthy()
    expect(screen.getByText(/170 scenarios/)).toBeTruthy()
  })

  it('is a modal dialog named by the stop title', () => {
    render(<TourSpotlight stop={STOP} index={0} total={5} onNext={vi.fn()} onPrev={vi.fn()} onExit={vi.fn()} />)
    const dlg = screen.getByRole('dialog')
    expect(dlg.getAttribute('aria-modal')).toBe('true')
    expect(dlg.getAttribute('aria-label')).toBe('Start here')
  })

  it('shows progress as step n of total', () => {
    render(<TourSpotlight stop={STOP} index={2} total={5} onNext={vi.fn()} onPrev={vi.fn()} onExit={vi.fn()} />)
    expect(screen.getByText(/3 of 5/)).toBeTruthy()
  })

  it('Escape exits', () => {
    const onExit = vi.fn()
    render(<TourSpotlight stop={STOP} index={0} total={5} onNext={vi.fn()} onPrev={vi.fn()} onExit={onExit} />)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onExit).toHaveBeenCalled()
  })

  it('the skip control exits', () => {
    const onExit = vi.fn()
    render(<TourSpotlight stop={STOP} index={0} total={5} onNext={vi.fn()} onPrev={vi.fn()} onExit={onExit} />)
    fireEvent.click(screen.getByRole('button', { name: /skip/i }))
    expect(onExit).toHaveBeenCalled()
  })

  it('the last stop offers Done rather than Next', () => {
    render(<TourSpotlight stop={STOP} index={4} total={5} onNext={vi.fn()} onPrev={vi.fn()} onExit={vi.fn()} />)
    expect(screen.getByRole('button', { name: /done/i })).toBeTruthy()
    expect(screen.queryByRole('button', { name: /^next$/i })).toBeNull()
  })

  it('renders nothing when stop is null', () => {
    const { container } = render(<TourSpotlight stop={null} index={-1} total={5} onNext={vi.fn()} onPrev={vi.fn()} onExit={vi.fn()} />)
    expect(container.firstChild).toBeNull()
  })
})
