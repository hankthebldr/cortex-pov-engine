/**
 * Unit tests for the shared run-status helpers (runStatus.js).
 *
 * Locks in the GAP-API-003 fix: the backend run-status enum is
 * {pending | running | complete | failed | aborted}. The UI must treat the
 * backend token 'complete' (no trailing -d) as the success state while
 * tolerating the legacy 'completed' alias, and treat 'aborted' as terminal.
 */
import { describe, it, expect } from 'vitest'
import {
  isRunComplete,
  isRunTerminal,
  runStatusToken,
  runStatusGlyph,
} from '../console/runStatus.js'

describe('isRunComplete', () => {
  it('accepts the backend token "complete"', () => {
    expect(isRunComplete('complete')).toBe(true)
  })

  it('tolerates the legacy alias "completed"', () => {
    expect(isRunComplete('completed')).toBe(true)
  })

  it('is case-insensitive', () => {
    expect(isRunComplete('Complete')).toBe(true)
    expect(isRunComplete('COMPLETED')).toBe(true)
  })

  it('rejects non-complete statuses', () => {
    for (const s of ['running', 'pending', 'failed', 'aborted', '', null, undefined]) {
      expect(isRunComplete(s)).toBe(false)
    }
  })
})

describe('isRunTerminal', () => {
  it('treats complete / failed / aborted as terminal', () => {
    expect(isRunTerminal('complete')).toBe(true)
    expect(isRunTerminal('completed')).toBe(true)
    expect(isRunTerminal('failed')).toBe(true)
    expect(isRunTerminal('aborted')).toBe(true) // Phase 2 terminal state
  })

  it('treats running / pending as non-terminal', () => {
    expect(isRunTerminal('running')).toBe(false)
    expect(isRunTerminal('pending')).toBe(false)
    expect(isRunTerminal('')).toBe(false)
  })
})

describe('runStatusToken', () => {
  it('folds complete and completed onto the canonical "completed" CSS state', () => {
    expect(runStatusToken('complete')).toBe('completed')
    expect(runStatusToken('completed')).toBe('completed')
  })

  it('passes terminal/active tokens straight through', () => {
    expect(runStatusToken('failed')).toBe('failed')
    expect(runStatusToken('aborted')).toBe('aborted')
    expect(runStatusToken('running')).toBe('running')
    expect(runStatusToken('pending')).toBe('pending')
  })

  it('falls back to "unknown" for empty / unrecognised input', () => {
    expect(runStatusToken('')).toBe('unknown')
    expect(runStatusToken('weird')).toBe('unknown')
    expect(runStatusToken(null)).toBe('unknown')
  })
})

describe('runStatusGlyph', () => {
  it('maps each state to its glyph', () => {
    expect(runStatusGlyph('complete')).toBe('✓')
    expect(runStatusGlyph('completed')).toBe('✓')
    expect(runStatusGlyph('failed')).toBe('✗')
    expect(runStatusGlyph('aborted')).toBe('✗')
    expect(runStatusGlyph('running')).toBe('◐')
    expect(runStatusGlyph('pending')).toBe('○')
    expect(runStatusGlyph('')).toBe('○')
  })
})
