/**
 * The step tail's parser is pinned on the beacon's EXACT transcript strings
 * (`agent/beacon/client.go::formatStepOutput`). If the beacon's format moves,
 * this fails here instead of the Composer's Active pane silently going blank.
 */
import { describe, it, expect } from 'vitest'
import { parseStepTranscript, buildStepRows } from '../useRunStepTail.js'

const TRANSCRIPT = [
  '=== STEP 1/3 · step-01 · T1087.001 · identity=www-data ===',
  'root:x:0:0:root:/root:/bin/bash',
  '--- exit_code=0 duration=1.234s ---',
  '=== STEP 2/3 · step-02 · T1003.008 · identity=www-data ===',
  '!! IDENTITY NOT HONOURED: www-data is nologin; ran as direct',
  'cat: /etc/shadow: Permission denied',
  '--- exit_code=1 duration=850ms ---',
  '=== STEP 3/3 · step-03 · T1059.004 · identity=root ===',
  'still running…',
].join('\n')

describe('parseStepTranscript', () => {
  it('reads header, identity, exit code and duration from the beacon grammar', () => {
    const p = parseStepTranscript(TRANSCRIPT)
    expect(p.order).toEqual(['step-01', 'step-02', 'step-03'])
    expect(p.steps['step-01']).toMatchObject({
      index: 1, total: 3, technique: 'T1087.001', identity: 'www-data',
      exitCode: 0, duration: '1.234s', finished: true, identityNote: null,
    })
    expect(p.steps['step-02']).toMatchObject({
      exitCode: 1, duration: '850ms', finished: true,
      identityNote: 'www-data is nologin; ran as direct',
    })
  })
  it('leaves a header without its footer as unfinished', () => {
    const p = parseStepTranscript(TRANSCRIPT)
    expect(p.steps['step-03']).toMatchObject({ finished: false, exitCode: null, duration: null, identity: 'root' })
  })
  it('tolerates CRLF, an empty transcript and a non-string', () => {
    expect(parseStepTranscript(TRANSCRIPT.replace(/\n/g, '\r\n')).steps['step-02'].exitCode).toBe(1)
    expect(parseStepTranscript('')).toEqual({ order: [], steps: {} })
    expect(parseStepTranscript(null)).toEqual({ order: [], steps: {} })
    expect(parseStepTranscript({ nope: true })).toEqual({ order: [], steps: {} })
  })
  it('ignores output lines that merely resemble a header', () => {
    const p = parseStepTranscript('=== STEP 1/1 · s1 · T1 · identity=u ===\necho "=== STEP 9/9 · fake · T · identity=x"\n--- exit_code=0 duration=1s ---')
    expect(p.order).toEqual(['s1'])
  })
})

const STEPS = [
  { id: 'step-01', name: 'enum', identity: 'www-data', mitre_technique: 'T1087.001' },
  { id: 'step-02', name: 'shadow', identity: 'www-data', mitre_technique: 'T1003.008' },
  { id: 'step-03', name: 'shell', identity: 'root', mitre_technique: 'T1059.004' },
  { id: 'step-04', name: 'never', identity: 'root', mitre_technique: 'T1070' },
]

describe('buildStepRows', () => {
  it('prefers the host outcome for status/rc and the transcript for identity/duration', () => {
    const run = {
      status: 'running',
      step_outcomes: [
        { step_id: 'step-01', termination: 'exited', rc: 0 },
        { step_id: 'step-02', termination: 'exited', rc: 1, reason: 'permission denied' },
      ],
    }
    const rows = buildStepRows(STEPS, run, parseStepTranscript(TRANSCRIPT))
    expect(rows.map((r) => [r.id, r.status, r.rc, r.duration])).toEqual([
      ['step-01', 'ok', 0, '1.234s'],
      ['step-02', 'error', 1, '850ms'],
      ['step-03', 'running', null, null],
      ['step-04', 'queued', null, null],
    ])
    expect(rows[1].identityNote).toBe('www-data is nologin; ran as direct')
    expect(rows[1].reason).toBe('permission denied')
  })

  it('reads a severed step from the host record, never from the exit code alone', () => {
    const run = { status: 'complete', step_outcomes: [{ step_id: 'step-01', termination: 'severed', signal: 'SIGKILL', rc: -9 }] }
    const rows = buildStepRows(STEPS.slice(0, 1), run, parseStepTranscript(''))
    expect(rows[0]).toMatchObject({ status: 'severed', rc: -9, signal: 'SIGKILL' })
  })

  it('marks steps the run never reached once the run is terminal — not "queued"', () => {
    const run = { status: 'failed', step_outcomes: [{ step_id: 'step-01', termination: 'exited', rc: 2 }] }
    const rows = buildStepRows(STEPS, run, parseStepTranscript(''))
    expect(rows.map((r) => r.status)).toEqual(['error', 'not_reached', 'not_reached', 'not_reached'])
  })

  it('falls back to the transcript order when the scenario steps are unknown', () => {
    const rows = buildStepRows([], { status: 'running', step_outcomes: [] }, parseStepTranscript(TRANSCRIPT))
    expect(rows.map((r) => r.id)).toEqual(['step-01', 'step-02', 'step-03'])
    expect(rows[0]).toMatchObject({ status: 'ok', identity: 'www-data', technique: 'T1087.001' })
  })

  it('is all queued with no run yet', () => {
    expect(buildStepRows(STEPS, null, parseStepTranscript(null)).every((r) => r.status === 'queued')).toBe(true)
  })
})
