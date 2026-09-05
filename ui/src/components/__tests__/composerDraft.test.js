/**
 * composerDraft — the pure draft model behind the Simulation Composer.
 *
 * The rules worth pinning here are the HONESTY ones, not the mechanics: a
 * draft that quietly reports itself valid, or that emits YAML claiming a
 * detection nobody declared, is the failure mode that reaches a customer
 * readout. The step operations are tested for their no-op identity contract
 * because callers rely on it to avoid pointless re-renders.
 */
import { describe, it, expect } from 'vitest'
import {
  BLANK_COMMAND,
  DETECTION_TYPES,
  PIVOTS,
  PLANES,
  addDetection,
  appendStep,
  bindTtpDetection,
  blankStep,
  draftFromApi,
  draftFromScenario,
  draftSnapshot,
  draftToApi,
  duplicateStep,
  editStep,
  emitDraftYaml,
  emptyDraft,
  isDraftDirty,
  moveStep,
  nextStepId,
  normalizeStep,
  removeDetection,
  removeStep,
  setCausalityParent,
  validateDraft,
} from '../console/composerDraft.js'

const SCENARIO = {
  scenario_id: 'SIM-EDR-001',
  name: 'Credential Dumping',
  plane: 'EDR',
  uc_ref: 'UCS-EDR-02',
  tc_ref: 'TC-EDR-03',
  moat_tier: 'LEAD',
  cgo_anchor: { image_name: 'apache2', primary_username: 'www-data' },
  cleanup: { commands: ['rm -f /tmp/mimipenguin.sh'] },
  steps: [
    {
      id: 'step-01',
      name: 'Read /etc/passwd',
      command: 'cat /etc/passwd',
      identity: 'www-data',
      mitre_technique: 'T1087.001',
      platforms: ['linux'],
      expected_detections: [
        { plane: 'EDR', type: 'XQL', description: 'passwd read', ttp_ref: 'TTP-2026-0032', detection_id: 'xql-1' },
      ],
    },
    {
      id: 'step-02',
      name: 'Read /etc/shadow',
      command: 'cat /etc/shadow',
      identity: 'www-data',
      mitre_technique: 'T1003.008',
      causality: { parent_step: 'step-01', pivot: 'process_lineage' },
      expected_detections: [],
    },
  ],
}

describe('draftFromScenario', () => {
  it('carries scenario identity, CGO and scenario-level cleanup', () => {
    const d = draftFromScenario(SCENARIO)
    expect(d.originId).toBe('SIM-EDR-001')
    expect(d.plane).toBe('EDR')
    expect(d.ucRef).toBe('UCS-EDR-02')
    expect(d.cgo).toBe('apache2 / www-data')
    expect(d.teardown).toEqual(['rm -f /tmp/mimipenguin.sh'])
    expect(d.steps).toHaveLength(2)
  })

  it('returns an empty draft for a null scenario rather than throwing', () => {
    expect(draftFromScenario(null)).toEqual(emptyDraft())
  })

  it('marks scenario steps as NOT authored, so hand-edits stay distinguishable', () => {
    const d = draftFromScenario(SCENARIO)
    expect(d.steps.every((s) => s.authored === false)).toBe(true)
  })
})

describe('normalizeStep — absent is not empty', () => {
  it('leaves a missing command as null, not an empty string', () => {
    // The list endpoint omits `command`. Rendering '' would read as "no command
    // configured" when the truth is "this endpoint did not carry it".
    const s = normalizeStep({ id: 'step-01', name: 'x', expected_detections: [] }, 0)
    expect(s.command).toBeNull()
    expect(s.identity).toBeNull()
    expect(s.technique).toBeNull()
  })

  it('does not invent per-step timing the scenario schema has no concept of', () => {
    const s = normalizeStep(SCENARIO.steps[0], 0)
    expect(s).not.toHaveProperty('delay')
    expect(s).not.toHaveProperty('timeout')
  })

  it('carries causality parent + pivot when declared, null when not', () => {
    expect(normalizeStep(SCENARIO.steps[1], 1).causalityParent).toBe('step-01')
    expect(normalizeStep(SCENARIO.steps[0], 0).causalityParent).toBeNull()
  })
})

describe('validateDraft', () => {
  it('names the steps that declare no expected detection', () => {
    const v = validateDraft(draftFromScenario(SCENARIO).steps)
    expect(v.ok).toBe(false)
    expect(v.missingDetections).toEqual(['step-02'])
    expect(v.problems.join(' ')).toMatch(/step-02/)
    expect(v.problems.join(' ')).toMatch(/gap/i)
  })

  it('an EMPTY chain is not valid — "valid" must never describe a chain that does nothing', () => {
    const v = validateDraft([])
    expect(v.ok).toBe(false)
    expect(v.problems.join(' ')).toMatch(/empty/i)
  })

  it('is ok only when every step has both a command and an expected detection', () => {
    const v = validateDraft([draftFromScenario(SCENARIO).steps[0]])
    expect(v.ok).toBe(true)
    expect(v.counts).toEqual({ steps: 1, detections: 1, techniques: 1 })
  })

  it('flags a hand-added step whose command is still the placeholder', () => {
    const v = validateDraft([blankStep('step-09')])
    expect(v.missingCommands).toEqual(['step-09'])
    expect(v.ok).toBe(false)
  })

  it('counts DISTINCT techniques, not step count', () => {
    const steps = draftFromScenario(SCENARIO).steps
    const v = validateDraft([...steps, { ...steps[0], id: 'step-03' }])
    expect(v.counts.steps).toBe(3)
    expect(v.counts.techniques).toBe(2)
  })
})

describe('step operations', () => {
  const steps = draftFromScenario(SCENARIO).steps

  it('moveStep returns the SAME array when the move is out of bounds', () => {
    expect(moveStep(steps, 0, -1)).toBe(steps)
    expect(moveStep(steps, steps.length - 1, 1)).toBe(steps)
  })

  it('moveStep swaps neighbours without mutating the input', () => {
    const out = moveStep(steps, 0, 1)
    expect(out.map((s) => s.id)).toEqual(['step-02', 'step-01'])
    expect(steps.map((s) => s.id)).toEqual(['step-01', 'step-02'])
  })

  it('duplicateStep inserts after the source with a FRESH, non-colliding id', () => {
    const out = duplicateStep(steps, 0)
    expect(out).toHaveLength(3)
    expect(out[1].id).toBe('step-03')
    expect(out[1].name).toBe(steps[0].name)
    // A duplicate is a hand-edit — it must not claim to have come from the file.
    expect(out[1].authored).toBe(true)
    expect(new Set(out.map((s) => s.id)).size).toBe(3)
  })

  it('nextStepId skips ids already taken rather than colliding', () => {
    expect(nextStepId([{ id: 'step-01' }, { id: 'step-03' }])).toBe('step-04')
  })

  it('removeStep drops exactly one step', () => {
    expect(removeStep(steps, 0).map((s) => s.id)).toEqual(['step-02'])
    expect(removeStep(steps, 99)).toBe(steps)
  })

  it('appendStep adds a blank step carrying the placeholder command', () => {
    const out = appendStep(steps, blankStep('step-03'))
    expect(out).toHaveLength(3)
    expect(out[2].command).toBe(BLANK_COMMAND)
    expect(out[2].detections).toEqual([])
  })
})

describe('emitDraftYaml', () => {
  const draft = draftFromScenario(SCENARIO)
  const yaml = emitDraftYaml(draft, { tenant: 'acme-pov-na', agent: 'web-prod-01' })

  it('emits every step id, command and declared detection', () => {
    expect(yaml).toMatch(/- id: step-01/)
    expect(yaml).toMatch(/cat \/etc\/passwd/)
    expect(yaml).toMatch(/ttp_ref: TTP-2026-0032/)
  })

  it('marks a step with no expected detection IN the file, not just in the UI', () => {
    // Someone reading only the YAML must still see which step would be a gap.
    expect(yaml).toMatch(/NO EXPECTED DETECTION/)
  })

  it('writes launch scope as a COMMENT — tenant/agent are not scenario fields', () => {
    // Emitting them as real keys would produce a file the loader rejects.
    expect(yaml).toMatch(/#\s+tenant: acme-pov-na/)
    expect(yaml).not.toMatch(/^tenant:/m)
    expect(yaml).not.toMatch(/^agent:/m)
  })

  it('says it is a subset of the schema rather than implying a complete scenario', () => {
    expect(yaml).toMatch(/SUBSET of scenarios\/_schema\.yml/)
  })

  it('carries scenario-level cleanup once, not copied onto each step', () => {
    expect(yaml.match(/rm -f \/tmp\/mimipenguin\.sh/g)).toHaveLength(1)
  })

  it('renders an empty chain as an explicit "nothing would execute", not a bare key', () => {
    const empty = emitDraftYaml(emptyDraft(), {})
    expect(empty).toMatch(/empty chain — nothing would execute/)
  })

  it('quotes scalars that would otherwise break YAML', () => {
    const tricky = emitDraftYaml(
      { ...emptyDraft(), name: 'Dump: creds, now', steps: [] }, {},
    )
    expect(tricky).toMatch(/name: "Dump: creds, now"/)
  })
})

// ─── Composer enums ───────────────────────────────────────────────────────────

describe('composer enums mirror the backend verbatim', () => {
  it('has exactly the seven causality pivots', () => {
    expect(PIVOTS).toEqual([
      'process_lineage', 'network_session', 'endpoint_network_stitch',
      'shared_entity', 'exposure_exploit', 'exploit_impact', 'temporal',
    ])
  })
  it('has exactly the six detection types, ABIOC included', () => {
    expect(DETECTION_TYPES).toEqual(['BIOC', 'XQL', 'Analytics', 'Correlation', 'IOC', 'ABIOC'])
  })
  it('has the sixteen planes', () => {
    expect(PLANES).toHaveLength(16)
    expect(PLANES).toContain('DLP')
    expect(PLANES[0]).toBe('EDR')
  })
})

// ─── Immutable step edit operations ───────────────────────────────────────────

describe('editStep', () => {
  const steps = draftFromScenario(SCENARIO).steps

  it('returns the SAME array for an unknown id (no-op identity)', () => {
    expect(editStep(steps, 'step-99', { name: 'x' })).toBe(steps)
  })

  it('returns the SAME array when the patch changes nothing', () => {
    expect(editStep(steps, 'step-01', { name: steps[0].name })).toBe(steps)
  })

  it('returns the SAME array for an empty/absent patch', () => {
    expect(editStep(steps, 'step-01', {})).toBe(steps)
    expect(editStep(steps, 'step-01', null)).toBe(steps)
  })

  it('patches only editable keys, without mutating the input', () => {
    const out = editStep(steps, 'step-01', { command: 'whoami', identity: 'root', bogus: 1 })
    expect(out).not.toBe(steps)
    expect(out[0].command).toBe('whoami')
    expect(out[0].identity).toBe('root')
    expect(out[0]).not.toHaveProperty('bogus')
    // input untouched
    expect(steps[0].command).toBe('cat /etc/passwd')
  })
})

describe('addDetection / removeDetection', () => {
  const steps = draftFromScenario(SCENARIO).steps

  it('addDetection returns the SAME array for an unknown id', () => {
    expect(addDetection(steps, 'step-99', { plane: 'EDR', type: 'XQL' })).toBe(steps)
  })

  it('addDetection returns the SAME array when the detection is not an object', () => {
    expect(addDetection(steps, 'step-01', null)).toBe(steps)
  })

  it('addDetection appends a normalised detection without mutating the input', () => {
    const out = addDetection(steps, 'step-02', {
      plane: 'EDR', type: 'BIOC', description: 'shadow read', ttp_ref: 'TTP-2026-0099',
    })
    expect(out[1].detections).toHaveLength(1)
    expect(out[1].detections[0]).toMatchObject({
      plane: 'EDR', type: 'BIOC', ttpRef: 'TTP-2026-0099',
    })
    expect(steps[1].detections).toHaveLength(0)
  })

  it('removeDetection returns the SAME array for an unknown id or bad index', () => {
    expect(removeDetection(steps, 'step-99', 0)).toBe(steps)
    expect(removeDetection(steps, 'step-01', 5)).toBe(steps)
    expect(removeDetection(steps, 'step-01', -1)).toBe(steps)
  })

  it('removeDetection drops exactly one detection', () => {
    const out = removeDetection(steps, 'step-01', 0)
    expect(out[0].detections).toHaveLength(0)
    expect(steps[0].detections).toHaveLength(1)
  })
})

describe('setCausalityParent', () => {
  const steps = draftFromScenario(SCENARIO).steps

  it('returns the SAME array for an unknown step id', () => {
    expect(setCausalityParent(steps, 'step-99', 'step-01')).toBe(steps)
  })

  it('refuses a self-ref (returns the SAME array)', () => {
    expect(setCausalityParent(steps, 'step-02', 'step-02')).toBe(steps)
  })

  it('refuses a forward-ref by array order (returns the SAME array)', () => {
    // step-01 cannot descend from step-02, which comes AFTER it.
    expect(setCausalityParent(steps, 'step-01', 'step-02')).toBe(steps)
  })

  it('refuses an unknown parent id (returns the SAME array)', () => {
    expect(setCausalityParent(steps, 'step-02', 'step-77')).toBe(steps)
  })

  it('returns the SAME array when the parent+pivot are unchanged', () => {
    expect(setCausalityParent(steps, 'step-02', 'step-01', 'process_lineage')).toBe(steps)
  })

  it('sets a valid backward parent with a chosen pivot', () => {
    const out = setCausalityParent(steps, 'step-02', 'step-01', 'network_session')
    expect(out[1].causalityParent).toBe('step-01')
    expect(out[1].causalityPivot).toBe('network_session')
  })

  it('clears the link to a chain root when parentId is null', () => {
    const out = setCausalityParent(steps, 'step-02', null)
    expect(out[1].causalityParent).toBeNull()
  })
})

describe('bindTtpDetection', () => {
  const steps = draftFromScenario(SCENARIO).steps

  it('returns the SAME array for an unknown id or a non-object card', () => {
    expect(bindTtpDetection(steps, 'step-99', { ttp_id: 'x' })).toBe(steps)
    expect(bindTtpDetection(steps, 'step-02', null)).toBe(steps)
  })

  it('appends one detection pre-filled from a TTP card', () => {
    const out = bindTtpDetection(steps, 'step-02', {
      ttp_id: 'TTP-2026-0100', plane: 'EDR', detection_type: 'BIOC',
      name: 'shadow read', detection_id: 'bioc-9',
    })
    expect(out[1].detections).toHaveLength(1)
    expect(out[1].detections[0]).toMatchObject({
      plane: 'EDR', type: 'BIOC', ttpRef: 'TTP-2026-0100', detectionId: 'bioc-9',
    })
  })
})

// ─── Round-trip serializers ───────────────────────────────────────────────────

describe('draftToApi / draftFromApi', () => {
  it('draftToApi builds the frozen snake_case body and omits server-derived fields', () => {
    const draft = draftFromScenario(SCENARIO)
    const body = draftToApi(draft)
    expect(body.name).toBe('Credential Dumping')
    expect(body.plane).toBe('EDR')
    expect(body.author).toBe('composer')
    expect(body.uc_ref).toBe('UCS-EDR-02')
    expect(body.tc_ref).toBe('TC-EDR-03')
    expect(body.cgo_anchor).toEqual({ image_name: 'apache2', primary_username: 'www-data' })
    expect(body.cleanup).toEqual({ commands: ['rm -f /tmp/mimipenguin.sh'] })
    // steps carry snake_case detection keys + causality
    expect(body.steps[0].mitre_technique).toBe('T1087.001')
    expect(body.steps[0].expected_detections[0]).toMatchObject({
      plane: 'EDR', type: 'XQL', ttp_ref: 'TTP-2026-0032', detection_id: 'xql-1',
    })
    expect(body.steps[1].causality).toEqual({ parent_step: 'step-01', pivot: 'process_lineage' })
    // server-derived fields must NOT be present
    expect(body).not.toHaveProperty('detection_types')
    expect(body).not.toHaveProperty('push_supported')
    expect(body).not.toHaveProperty('pull_supported')
    expect(body).not.toHaveProperty('status')
    expect(body).not.toHaveProperty('version')
  })

  it('respects an explicit author override', () => {
    const body = draftToApi(emptyDraft(), { author: 'henry' })
    expect(body.author).toBe('henry')
  })

  it('draftFromApi is a superset of draftFromScenario carrying persistence identity', () => {
    const row = {
      ...SCENARIO,
      scenario_id: 'SIM-DRAFT-credential-dumping',
      status: 'draft',
      author: 'henry',
      tags: ['composer-draft', 'edr'],
    }
    const d = draftFromApi(row)
    expect(d.scenarioId).toBe('SIM-DRAFT-credential-dumping')
    expect(d.status).toBe('draft')
    expect(d.author).toBe('henry')
    expect(d.tags).toEqual(['composer-draft', 'edr'])
    // shared fields still come through
    expect(d.plane).toBe('EDR')
    expect(d.steps).toHaveLength(2)
  })

  it('draftFromApi(null) returns an empty draft, matching draftFromScenario(null) shape', () => {
    expect(draftFromApi(null)).toEqual(emptyDraft())
  })

  it('round-trips a corpus scenario through api and back with matching launch fields', () => {
    const draft = draftFromScenario(SCENARIO)
    const body = draftToApi({ ...draft, author: 'composer', tags: [] })
    // Re-hydrate as if the server echoed the body back inside a Scenario.to_dict()
    const echoed = {
      scenario_id: 'SIM-DRAFT-credential-dumping', status: 'draft',
      name: body.name, plane: body.plane, author: body.author, tags: body.tags,
      uc_ref: body.uc_ref, tc_ref: body.tc_ref, cgo_anchor: body.cgo_anchor,
      cleanup: body.cleanup, steps: body.steps,
    }
    const back = draftFromApi(echoed)
    expect(draftSnapshot(back)).toEqual(draftSnapshot(draft))
  })
})

// ─── Dirty tracking ───────────────────────────────────────────────────────────

describe('draftSnapshot / isDraftDirty', () => {
  it('isDraftDirty(current, null) is ALWAYS true — an unsaved draft is dirty by definition', () => {
    const draft = draftFromScenario(SCENARIO)
    expect(isDraftDirty(draftSnapshot(draft), null)).toBe(true)
  })

  it('is false when the snapshot matches the saved snapshot', () => {
    const draft = draftFromScenario(SCENARIO)
    const saved = draftSnapshot(draft)
    expect(isDraftDirty(draftSnapshot(draft), saved)).toBe(false)
  })

  it('is true after a launch-relevant edit (command change)', () => {
    const draft = draftFromScenario(SCENARIO)
    const saved = draftSnapshot(draft)
    const edited = { ...draft, steps: editStep(draft.steps, 'step-01', { command: 'id' }) }
    expect(isDraftDirty(draftSnapshot(edited), saved)).toBe(true)
  })

  it('ignores a cosmetic field the run does not depend on (moatTier)', () => {
    const draft = draftFromScenario(SCENARIO)
    const saved = draftSnapshot(draft)
    const cosmetic = { ...draft, moatTier: 'CHANGED' }
    expect(isDraftDirty(draftSnapshot(cosmetic), saved)).toBe(false)
  })
})
