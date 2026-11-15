/**
 * runtimeLog.buildRuntimeLog — one block per step of a run: the command that
 * was attempted, how it went on the host, what it printed.
 *
 * A finished run is built from its stored output and the host's step record;
 * a live one from the same record as it grows. The words are execution words
 * only (Queued · Running · Ran · Failed · Timed out · Did not run · Aborted ·
 * Staged).
 */
import { describe, it, expect } from 'vitest'
import { buildRuntimeLog, blockMeta } from '../runtimeLog.js'

const STEPS = [
  { id: 'step-01', name: 'Read the shadow file', command: 'cat /etc/shadow', identity: 'root', mitre_technique: 'T1003.008' },
  { id: 'step-02', name: 'Dump the process', command: 'gcore -o /tmp/dump 1', identity: 'www-data', mitre_technique: 'T1003.007' },
  { id: 'step-03', name: 'Stage the archive', command: 'tar czf /tmp/loot.tgz /tmp/dump.1', identity: 'www-data', mitre_technique: 'T1560.001' },
]
const BLOCK_1 = [
  '=== STEP 1/3 · step-01 · T1003.008 · identity=root ===',
  'root:$6$abc:19000:0:99999:7:::',
  '--- exit_code=0 duration=1.2s ---',
]
const BLOCK_2 = [
  '=== STEP 2/3 · step-02 · T1003.007 · identity=www-data ===',
  'gcore: failed to attach: Operation not permitted',
  '--- exit_code=1 duration=310ms ---',
]
const states = (log) => log.blocks.map((b) => b.state.key)
const words = (log) => log.blocks.map((b) => b.state.word)

describe('a finished run, from its stored output and the host record', () => {
  const run = {
    status: 'failed', mode: 'pull', delivered_at: '2026-11-13T17:00:03',
    output: [...BLOCK_1, ...BLOCK_2].join('\n'),
    step_outcomes: [
      { step_id: 'step-01', termination: 'exited', rc: 0 },
      { step_id: 'step-02', termination: 'exited', rc: 1 },
    ],
  }
  const log = buildRuntimeLog({ steps: STEPS, run })

  it('has one block per scenario step, in order, each with its command', () => {
    expect(log.blocks.map((b) => b.id)).toEqual(['step-01', 'step-02', 'step-03'])
    expect(log.blocks.map((b) => b.command)).toEqual(STEPS.map((s) => s.command))
    expect(log.blocks.every((b) => b.commandFrom === 'scenario')).toBe(true)
    expect(log.blocks[0]).toMatchObject({ index: 1, name: 'Read the shadow file', technique: 'T1003.008', account: 'root' })
  })

  it('says Ran, Failed and Did not run, with the exit code and duration the host recorded', () => {
    expect(words(log)).toEqual(['Ran', 'Failed', 'Did not run'])
    expect(blockMeta(log.blocks[0])).toBe('exit 0 · 1.2s')
    expect(blockMeta(log.blocks[1])).toBe('exit 1 · 310ms')
    expect(blockMeta(log.blocks[2])).toBe('run stopped first')
  })

  it('gives each step the lines it printed, and none to a step that did not run', () => {
    expect(log.blocks[0].lines).toEqual(['root:$6$abc:19000:0:99999:7:::'])
    expect(log.blocks[1].lines).toEqual(['gcore: failed to attach: Operation not permitted'])
    expect(log.blocks[2].lines).toEqual([])
  })
})

describe('the command that was attempted', () => {
  it("prefers the agent's own record of the command when the run carries it", () => {
    const run = {
      status: 'complete', output: BLOCK_1.join('\n'),
      step_starts: [{ step_id: 'step-01', command: 'cat /etc/shadow | head -5', started_at: '2026-11-13T17:00:04' }],
    }
    const [one, two] = buildRuntimeLog({ steps: STEPS, run }).blocks
    expect(one).toMatchObject({ command: 'cat /etc/shadow | head -5', commandFrom: 'agent' })
    expect(two).toMatchObject({ command: 'gcore -o /tmp/dump 1', commandFrom: 'scenario' })
  })

  it('reads a start record as the step having begun', () => {
    const run = { status: 'running', output: '', step_starts: [{ step_id: 'step-01', command: 'cat /etc/shadow' }] }
    expect(states(buildRuntimeLog({ steps: STEPS, run }))).toEqual(['running', 'queued', 'queued'])
  })

  it("shows the step's platform variant when the run says it ran one", () => {
    const steps = [{ ...STEPS[0], platform_variants: { windows: 'type C:\\Windows\\System32\\config\\SAM' } }]
    const run = {
      status: 'complete',
      output: ['!! PLATFORM VARIANT: step=step-01 ran its windows variant; unverified for this OS', ...BLOCK_1].join('\n'),
    }
    const log = buildRuntimeLog({ steps, run })
    expect(log.blocks[0]).toMatchObject({ command: 'type C:\\Windows\\System32\\config\\SAM', commandFrom: 'variant' })
    // The marker itself stays in the log, where the launch wrote it.
    expect(log.preface.flat().join('\n')).toMatch(/PLATFORM VARIANT/)
  })

  it('has no command for a step the scenario no longer lists, and keeps its output', () => {
    const log = buildRuntimeLog({ steps: [], run: { status: 'complete', output: BLOCK_1.join('\n') } })
    expect(log.blocks).toHaveLength(1)
    expect(log.blocks[0]).toMatchObject({ id: 'step-01', command: null, commandFrom: null })
    expect(log.blocks[0].lines).toEqual(['root:$6$abc:19000:0:99999:7:::'])
  })
})

describe('how a step went', () => {
  const one = (run, steps = STEPS.slice(0, 1)) => buildRuntimeLog({ steps, run }).blocks[0]

  it('a step the host timed out reads Timed out', () => {
    const b = one({
      status: 'failed',
      output: ['=== STEP 1/1 · step-01 · T1003.008 · identity=root ===', '!! STEP TIMEOUT: killed after 180s', '--- exit_code=124 duration=3m0s ---'].join('\n'),
      step_outcomes: [{ step_id: 'step-01', termination: 'timeout', rc: 124 }],
    })
    expect(b.state.word).toBe('Timed out')
    expect(blockMeta(b)).toBe('exit 124 · 3m0s')
  })

  it('a step stopped by an abort reads Aborted, and the steps after it Did not run', () => {
    const log = buildRuntimeLog({
      steps: STEPS.slice(0, 2),
      run: {
        status: 'aborted', output: BLOCK_1.slice(0, 2).join('\n'),
        step_outcomes: [{ step_id: 'step-01', termination: 'aborted', rc: 130 }],
      },
    })
    expect(words(log)).toEqual(['Aborted', 'Did not run'])
    expect(log.blocks[1].reason).toBe('run aborted')
  })

  it('a step the host refused reads Did not run, with the reason', () => {
    const b = one({
      status: 'complete',
      output: ['=== STEP 1/1 · step-01 · T1003.008 ===', '!! STEP NOT RUN: PLATFORM_UNSATISFIABLE step=step-01 declared=linux host=windows', '--- exit_code=none duration=0s ---'].join('\n'),
      step_outcomes: [{ step_id: 'step-01', termination: 'not_run', rc: null, reason: 'PLATFORM_UNSATISFIABLE' }],
    })
    expect(b.state.word).toBe('Did not run')
    expect(blockMeta(b)).toBe('PLATFORM_UNSATISFIABLE')
  })

  it('reads the reason off the transcript when the host record has none', () => {
    const b = one({
      status: 'complete',
      output: '!! STEP NOT RUN: PLATFORM_UNSATISFIABLE step=step-01 declared=linux host=windows',
    })
    expect(b.state.word).toBe('Did not run')
    expect(b.reason).toBe('PLATFORM_UNSATISFIABLE')
  })

  it('an agent that stopped reporting mid-step reads Failed, and says so', () => {
    const b = one({
      status: 'failed', output: '',
      step_outcomes: [{ step_id: 'step-01', termination: 'beacon_lost', rc: null, started_at: '2026-11-13T17:00:04' }],
    })
    expect(b.state.word).toBe('Failed')
    expect(b.reason).toBe('agent stopped reporting')
  })

  it('a step killed by a signal reads Failed, with the signal', () => {
    const b = one({
      status: 'complete', output: '',
      step_outcomes: [{ step_id: 'step-01', termination: 'signaled', rc: 137, signal: 9 }],
    })
    expect(b.state.word).toBe('Failed')
    expect(blockMeta(b)).toBe('exit 137 · signal 9')
  })

  it('names the account a step really ran as when its identity was not honoured', () => {
    const b = one({
      status: 'complete',
      output: [
        '=== STEP 1/1 · step-01 · T1003.008 · identity=svc-backup ===',
        '!! IDENTITY NOT HONOURED: windows has no unattended impersonation; ran as direct',
        '--- exit_code=0 duration=1s ---',
      ].join('\n'),
    })
    expect(b.account).toBe('the agent account')
    expect(b.accountNote).toBe('not svc-backup')
    expect(b.accountDetail).toMatch(/no unattended impersonation/)
  })
})

describe('a live run', () => {
  const live = (over) => ({ status: 'running', mode: 'pull', output: BLOCK_1.join('\n'), step_outcomes: [{ step_id: 'step-01', termination: 'exited', rc: 0 }], ...over })

  it('once the agent has the run, the first step with no record is the one Running', () => {
    expect(states(buildRuntimeLog({ steps: STEPS, run: live({ delivered_at: '2026-11-13T17:00:03' }) })))
      .toEqual(['ran', 'running', 'queued'])
  })

  it('before the agent takes it, every step is Queued', () => {
    const run = { status: 'running', mode: 'pull', output: '', delivered_at: null }
    expect(states(buildRuntimeLog({ steps: STEPS, run }))).toEqual(['queued', 'queued', 'queued'])
  })

  it('a block with a header and no footer yet is Running, with the lines so far', () => {
    const run = live({ delivered_at: 'x', output: [...BLOCK_1, BLOCK_2[0], 'attaching…'].join('\n') })
    const two = buildRuntimeLog({ steps: STEPS, run }).blocks[1]
    expect(two.state.word).toBe('Running')
    expect(two.lines).toEqual(['attaching…'])
  })
})

describe('other kinds of run', () => {
  it('a push bundle: every step Staged, with the command the bundle carries', () => {
    const log = buildRuntimeLog({ steps: STEPS, run: { status: 'staged', mode: 'push', output: '' } })
    expect(words(log)).toEqual(['Staged', 'Staged', 'Staged'])
    expect(log.blocks[0].command).toBe('cat /etc/shadow')
  })

  it('a step sent in-process reads from its dispatch ledger, not as Did not run', () => {
    const steps = [{ id: 'step-01', name: 'Sign-in burst', channel: 'eal', eal: { plugin: 'idp_signin_emulator' } }]
    const run = (status, extra = {}) => ({
      status: 'complete', output: '',
      channel_dispatch: [{ step_id: 'step-01', channel: 'eal', plugin: 'idp_signin_emulator', status, ...extra }],
    })
    const sent = buildRuntimeLog({ steps, run: run('dispatched') }).blocks[0]
    expect(sent.state.word).toBe('Ran')
    expect(sent.command).toBe('EAL plugin idp_signin_emulator')
    const refused = buildRuntimeLog({ steps, run: run('not_delivered', { code: 'COLLECTOR_REFUSED', detail: 'HTTP 401' }) }).blocks[0]
    expect(refused.state.word).toBe('Failed')
    expect(blockMeta(refused)).toBe('COLLECTOR_REFUSED')
    expect(refused.lines).toEqual(['HTTP 401'])
    expect(buildRuntimeLog({ steps, run: run('dry_run') }).blocks[0].reason).toBe('dry run')
  })
})

describe('lines outside any step', () => {
  it('launch markers lead the log; a stop notice follows the step it came after', () => {
    const run = {
      status: 'aborted',
      output: ['!! TARGET_AGENT_OS_UNKNOWN: POSIX assumed', ...BLOCK_1, '--- ABORTED (operator) ---'].join('\n'),
    }
    const log = buildRuntimeLog({ steps: STEPS.slice(0, 1), run })
    expect(log.preface).toEqual([['!! TARGET_AGENT_OS_UNKNOWN: POSIX assumed']])
    expect(log.blocks[0].notes).toEqual([['--- ABORTED (operator) ---']])
  })

  it('a run with no record at all yields no blocks and no lines', () => {
    expect(buildRuntimeLog()).toEqual({ blocks: [], preface: [] })
  })
})
