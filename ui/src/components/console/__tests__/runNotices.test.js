/**
 * runNotices — what SimCore said about a launch it accepted, as one short
 * line each, for the run's page. Read from the run's own transcript and from
 * what this browser heard on the launch response; one line per code.
 */
import { describe, it, expect } from 'vitest'
import { runNotices } from '../runNotices.js'

const GAP = "!! RUNTIME_DEPENDENCY_GAP: agent 'ag-1'; steps step-02, step-03; The agent did not report python3, jq, and the beacon will not run these steps without them."
const INSTALL = "!! RUNTIME_INSTALL_NOT_ENABLED: agent 'ag-1'; Runtime install is off on this SimCore (CORTEXSIM_AGENT_ALLOW_RUNTIME_INSTALL), so nothing will be installed."
const CLEANUP = "!! CLEANUP_NOT_RUN: agent 'ag-1'; this beacon does not run scenario cleanup; update it, or clean the host by hand after the run"
const RELAY = "!! EAL_RELAY_UNAVAILABLE: agent 'ag-1'; steps step-04; this beacon cannot relay EAL steps, so they will fail; update it"
const OFFLINE = "!! TARGET_AGENT_OFFLINE: agent 'ag-1' last polled 14 minutes ago; the task is queued and runs only when its beacon polls again"
const OS_UNKNOWN = '!! TARGET_AGENT_OS_UNKNOWN: the target agent registered no operating system; POSIX was assumed'

const lines = (notices) => notices.map((n) => n.line)

describe('runNotices', () => {
  it('states each launch line the transcript carries in one short line', () => {
    const notices = runNotices({ output: [GAP, INSTALL, CLEANUP, RELAY, OFFLINE, OS_UNKNOWN].join('\n') })
    expect(lines(notices)).toEqual([
      'Agent is missing python3, jq',
      'Runtime install is off on this SimCore',
      'This beacon does not run cleanup',
      'This beacon cannot relay EAL steps',
      'Agent was offline at launch',
      'Agent OS unknown, POSIX assumed',
    ])
    for (const n of notices) expect(n.line.split(' ').length).toBeLessThanOrEqual(8)
  })

  it("keeps SimCore's own sentence as the detail", () => {
    const [gap] = runNotices({ output: GAP })
    expect(gap.code).toBe('RUNTIME_DEPENDENCY_GAP')
    expect(gap.detail).toMatch(/did not report python3, jq/)
  })

  it('reads what only the launch response carried', () => {
    const heard = [
      { code: 'CLEANUP_NOT_RUN', level: 'info', agent_id: 'ag-1', text: 'agent ag-1 — this beacon does not run scenario cleanup' },
      { code: 'RUNTIME_DEPENDENCY_GAP', agent_id: 'ag-1', steps: ['step-02'], missing: ['python3'], text: 'agent ag-1 — steps step-02' },
    ]
    expect(lines(runNotices({ output: '', heard }))).toEqual([
      'This beacon does not run cleanup',
      'Agent is missing python3',
    ])
  })

  it('says a code once when the record and the launch response both carry it', () => {
    const heard = [{ code: 'CLEANUP_NOT_RUN', text: 'this beacon does not run scenario cleanup' }]
    expect(runNotices({ output: CLEANUP, heard })).toHaveLength(1)
  })

  it('states the tools a target must already carry as one notice', () => {
    const one = '!! TARGET_EGRESS_REQUIRED: TOOL-NMAP — pull mode does not install tier-4 tools; the target must already carry TOOL-NMAP on PATH'
    const two = '!! TARGET_EGRESS_REQUIRED: TOOL-HYDRA — pull mode does not install tier-4 tools; the target must already carry TOOL-HYDRA on PATH'
    expect(lines(runNotices({ output: one }))).toEqual(['Target must already carry TOOL-NMAP'])
    const both = runNotices({ output: [one, two].join('\n') })
    expect(lines(both)).toEqual(['Target must already carry 2 tools'])
    expect(both[0].detail).toMatch(/TOOL-NMAP[^]*TOOL-HYDRA/)
  })

  it('leaves step markers and ordinary output alone', () => {
    const output = '!! PLATFORM VARIANT: step=step-02 ran its windows variant\n!! IDENTITY NOT HONOURED: no runuser\nplain line'
    expect(runNotices({ output })).toEqual([])
  })

  it('says live output needs the new beacon only while the run is live on an older one', () => {
    const old = { agent_id: 'ag-1', capabilities: ['shell', 'identity-harness', 'artifact-fetch', 'cleanup'] }
    const current = { agent_id: 'ag-1', capabilities: [...old.capabilities, 'live-output'] }
    expect(lines(runNotices({ output: '', live: true, agent: old }))).toEqual(['Live output needs the new beacon'])
    expect(runNotices({ output: '', live: false, agent: old })).toEqual([])
    expect(runNotices({ output: '', live: true, agent: current })).toEqual([])
    // An agent this console cannot see is not called old.
    expect(runNotices({ output: '', live: true, agent: null })).toEqual([])
  })
})
