/**
 * The v26 shared constructs, as a DC (or a screen reader) meets them.
 */
import React from 'react'
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { StatusIcon, ErrorBadge, Button, VERBS, VERB_ICONS, Toast, PageIcon, Icon, ICONS } from '../index.js'

void React

describe('StatusIcon', () => {
  it.each([
    ['up', 'Up', '✓'],
    ['degraded', 'Degraded', '!'],
    ['down', 'Down', '✕'],
    ['off', 'Off', '–'],
    ['unknown', 'Unknown', '?'],
  ])('%s is a glyph AND a word, announced once', (status, word, glyph) => {
    const { container } = render(<StatusIcon status={status} />)
    const icon = screen.getByRole('img', { name: word })
    expect(icon).toHaveTextContent(glyph)
    // The visible word is there for sighted users and hidden from the
    // accessibility tree, so the state is not read twice.
    const visible = container.querySelector('.kit-status__word')
    expect(visible).toHaveTextContent(word)
    expect(visible).toHaveAttribute('aria-hidden', 'true')
  })

  it('an unrecognised status reads Unknown, never a borrowed colour', () => {
    const { container } = render(<StatusIcon status="green" />)
    expect(screen.getByRole('img', { name: 'Unknown' })).toBeInTheDocument()
    expect(container.firstChild).toHaveClass('kit-status--unknown')
  })

  it('takes a configuration word for the same tone', () => {
    render(<StatusIcon status="unknown" word="Can't check" />)
    expect(screen.getByRole('img', { name: "Can't check" })).toBeInTheDocument()
  })
})

describe('ErrorBadge', () => {
  it('is a button named by the full error, which opens the fix', () => {
    const onClick = vi.fn()
    render(<ErrorBadge tone="crit" onClick={onClick}>Collector rejected the key (401)</ErrorBadge>)
    const badge = screen.getByRole('button', { name: /Collector rejected the key \(401\)/ })
    expect(badge).toHaveAttribute('title', 'Collector rejected the key (401)')
    fireEvent.click(badge)
    expect(onClick).toHaveBeenCalledTimes(1)
  })
})

describe('Button', () => {
  it('renders the variant and never submits a form by accident', () => {
    render(<Button variant="primary">Run</Button>)
    const b = screen.getByRole('button', { name: 'Run' })
    expect(b).toHaveClass('kit-btn--primary')
    expect(b).toHaveAttribute('type', 'button')
  })

  it('names the closed verb set from the design', () => {
    expect(VERBS).toEqual(['Configure', 'Test', 'Run', 'Customize', 'Add to chain', 'Read back', 'Open results', 'Abort run'])
  })

  it('gives every verb, and only a verb, an icon from the closed Icon set (design v33 C10)', () => {
    expect(Object.keys(VERB_ICONS)).toEqual([...VERBS])
    for (const verb of VERBS) expect(ICONS).toContain(VERB_ICONS[verb])
    expect(new Set(Object.values(VERB_ICONS)).size).toBe(VERBS.length)
  })

  it('a verb button draws its icon before the verb, hidden from assistive tech', () => {
    render(<Button variant="primary" verb="Read back" />)
    const b = screen.getByRole('button', { name: 'Read back' })
    expect(b).toHaveAttribute('data-verb', 'Read back')
    const svg = b.querySelector('svg')
    expect(svg).toHaveAttribute('data-icon', 'rotate')
    expect(svg).toHaveAttribute('aria-hidden', 'true')
    expect(b.firstChild).toBe(svg)
  })

  it('keeps the verb icon through a busy label', () => {
    render(<Button verb="Run">Launching…</Button>)
    const b = screen.getByRole('button', { name: 'Launching…' })
    expect(b.querySelector('svg')).toHaveAttribute('data-icon', 'play')
  })

  it('a button without a verb draws no icon', () => {
    render(<Button>Close</Button>)
    expect(screen.getByRole('button', { name: 'Close' }).querySelector('svg')).toBeNull()
  })
})

describe('Toast', () => {
  it('keeps one polite live region mounted and fills it with the message', () => {
    const { rerender } = render(<Toast toast={null} />)
    const region = screen.getByRole('status')
    expect(region).toHaveAttribute('aria-live', 'polite')
    expect(region).toBeEmptyDOMElement()

    rerender(<Toast toast={{ message: 'Run SIM-EDR-001 aborted', type: 'success' }} />)
    expect(screen.getByRole('status')).toBe(region)
    expect(region).toHaveTextContent('Run SIM-EDR-001 aborted')
  })

  it('reports a failure with a warning glyph, not a tick', () => {
    const { container } = render(<Toast toast={{ message: 'Abort failed: HTTP 500', type: 'error' }} />)
    expect(container.querySelector('.kit-toast')).toHaveClass('kit-toast--crit')
    expect(container.querySelector('.kit-toast__glyph')).toHaveTextContent('!')
  })
})

describe('PageIcon', () => {
  it('is decorative and draws the rail icon', () => {
    const { container } = render(<PageIcon icon="keyboard" size="tile" />)
    const tile = container.firstChild
    expect(tile).toHaveAttribute('aria-hidden', 'true')
    expect(tile).toHaveClass('kit-pageicon--tile')
    expect(tile.querySelector('img')).toHaveAttribute('src', '/icons/keyboard.png')
  })

  it('renders nothing without an icon', () => {
    const { container } = render(<PageIcon />)
    expect(container).toBeEmptyDOMElement()
  })
})

describe('Icon', () => {
  it('draws each control glyph, hidden from assistive tech (the control names itself)', () => {
    for (const name of ICONS) {
      const { container, unmount } = render(<Icon name={name} />)
      const svg = container.querySelector('svg')
      expect(svg).toHaveAttribute('data-icon', name)
      expect(svg).toHaveAttribute('aria-hidden', 'true')
      unmount()
    }
    expect(ICONS).toEqual([
      'play', 'stop', 'repeat', 'plus', 'star', 'sliders',
      'check', 'gear', 'pencil', 'rotate', 'external', 'target',
    ])
  })

  it('renders nothing for a name outside the set', () => {
    const { container } = render(<Icon name="rocket" />)
    expect(container).toBeEmptyDOMElement()
  })
})
