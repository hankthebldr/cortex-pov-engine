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
  CHANNELS,
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
  effectiveChannel,
  emitDraftYaml,
  emptyDraft,
  isDraftDirty,
  moveStep,
  nextStepId,
  normalizeStep,
  removeDetection,
  removeStep,
  setCausalityParent,
  setStepChannel,
  setStepEal,
  setStepTarget,
  setNodePosition,
  clearLayout,
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
  it('CHANNELS mirrors the backend — exactly agent,eal', () => {
    expect(CHANNELS).toEqual(['agent', 'eal'])
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

// ─── Phase-2 Stitch Context round-trip ────────────────────────────────────────

describe('stitch_context carried through the draft round-trip', () => {
  const CTX = {
    dst_ip: { literal: '203.0.113.10' },
    dst_port: { literal: 443 },
    src_ip: { resolve: 'auto_ip' },
    account: { resolve: 'canary_principal' },
  }

  it('draftFromScenario parses stitch_context into the camelCase model', () => {
    const d = draftFromScenario({ ...SCENARIO, stitch_context: CTX })
    expect(d.stitchContext).toEqual(CTX)
  })

  it('a context-less scenario yields stitchContext:null and OMITS the wire field', () => {
    const d = draftFromScenario(SCENARIO)
    expect(d.stitchContext).toBeNull()
    expect('stitch_context' in draftToApi(d)).toBe(false)
    // emptyDraft carries the null too, so the two shapes match key-for-key.
    expect(emptyDraft().stitchContext).toBeNull()
  })

  it('draftToApi attaches stitch_context when present', () => {
    const d = draftFromScenario({ ...SCENARIO, stitch_context: CTX })
    expect(draftToApi(d).stitch_context).toEqual(CTX)
  })

  it('round-trips scenario → draft → api → draft byte-identically', () => {
    const d = draftFromScenario({ ...SCENARIO, stitch_context: CTX })
    const body = draftToApi(d)
    const back = draftFromApi({ ...SCENARIO, status: 'draft', stitch_context: body.stitch_context })
    expect(back.stitchContext).toEqual(CTX)
  })

  it('a stitch_context edit reads as DIRTY (it changes what executes)', () => {
    const draft = draftFromScenario(SCENARIO)
    const saved = draftSnapshot(draft)
    const edited = { ...draft, stitchContext: { dst_port: { literal: 443 } } }
    expect(isDraftDirty(draftSnapshot(edited), saved)).toBe(true)
  })
})

// ─── Phase-3a channel routing (channel / target / eal) ────────────────────────

describe('effectiveChannel — absent is agent, never blank', () => {
  it('reads a step with no channel key as agent (back-compat)', () => {
    expect(effectiveChannel({ id: 'step-01' })).toBe('agent')
    expect(effectiveChannel({ id: 'step-01', channel: null })).toBe('agent')
  })
  it('reads an explicit channel through unchanged', () => {
    expect(effectiveChannel({ id: 'step-01', channel: 'eal' })).toBe('eal')
    expect(effectiveChannel({ id: 'step-01', channel: 'agent' })).toBe('agent')
  })
  it('tolerates null/undefined step', () => {
    expect(effectiveChannel(null)).toBe('agent')
    expect(effectiveChannel(undefined)).toBe('agent')
  })
})

describe('normalizeStep / blankStep carry channel fields', () => {
  it('a corpus step lands channel/target/eal null (byte-identical to today)', () => {
    const s = normalizeStep(SCENARIO.steps[0], 0)
    expect(s.channel).toBeNull()
    expect(s.target).toBeNull()
    expect(s.eal).toBeNull()
    expect(effectiveChannel(s)).toBe('agent')
  })
  it('normalizeStep carries an eal block when present', () => {
    const rawParams = { rate: '5' }
    const s = normalizeStep(
      { id: 'step-01', name: 'x', channel: 'eal', eal: { plugin: 'ngfw_eal_emitter', params: rawParams } },
      0,
    )
    expect(s.channel).toBe('eal')
    expect(s.eal).toEqual({ plugin: 'ngfw_eal_emitter', params: { rate: '5' } })
    // params are copied, not aliased to the raw scenario object
    expect(s.eal.params).not.toBe(rawParams)
  })
  it('normalizeStep carries a target string', () => {
    const s = normalizeStep({ id: 'step-01', name: 'x', target: 'web-prod-02' }, 0)
    expect(s.target).toBe('web-prod-02')
  })
  it('blankStep defaults to an agent step with no second endpoint or emitter', () => {
    const b = blankStep('step-09')
    expect(b.channel).toBeNull()
    expect(b.target).toBeNull()
    expect(b.eal).toBeNull()
  })
})

describe('setStepChannel — enforces backend mutual exclusivity', () => {
  const steps = draftFromScenario(SCENARIO).steps

  it('returns the SAME array for an unknown id', () => {
    expect(setStepChannel(steps, 'step-99', 'eal')).toBe(steps)
  })
  it('returns the SAME array for an unknown channel value', () => {
    expect(setStepChannel(steps, 'step-01', 'bogus')).toBe(steps)
  })
  it('agent on an already-agent step with no eal is a SAME-ref no-op', () => {
    expect(setStepChannel(steps, 'step-01', 'agent')).toBe(steps)
  })
  it('switching to eal seeds an empty eal block and clears target', () => {
    const withTarget = setStepTarget(steps, 'step-01', 'web-prod-02')
    const out = setStepChannel(withTarget, 'step-01', 'eal')
    expect(out[0].channel).toBe('eal')
    expect(out[0].target).toBeNull()
    expect(out[0].eal).toEqual({ plugin: '', params: {} })
  })
  it('switching eal back to agent clears the eal block, storing channel as null', () => {
    const asEal = setStepChannel(steps, 'step-01', 'eal')
    const back = setStepChannel(asEal, 'step-01', 'agent')
    expect(back[0].channel).toBeNull()
    expect(back[0].eal).toBeNull()
  })
  it('does not mutate the input array', () => {
    setStepChannel(steps, 'step-01', 'eal')
    expect(steps[0].channel).toBeNull()
  })
})

describe('setStepTarget — agent-channel only', () => {
  const steps = draftFromScenario(SCENARIO).steps

  it('returns the SAME array for an unknown id', () => {
    expect(setStepTarget(steps, 'step-99', 'web-prod-02')).toBe(steps)
  })
  it('sets a second-endpoint target on an agent step', () => {
    const out = setStepTarget(steps, 'step-01', 'web-prod-02')
    expect(out[0].target).toBe('web-prod-02')
    expect(steps[0].target).toBeNull()
  })
  it('empty string clears back to the launch target', () => {
    const withTarget = setStepTarget(steps, 'step-01', 'web-prod-02')
    expect(setStepTarget(withTarget, 'step-01', '')[0].target).toBeNull()
  })
  it('is a SAME-ref no-op when the target is unchanged', () => {
    expect(setStepTarget(steps, 'step-01', null)).toBe(steps)
  })
  it('refuses (SAME array) on a non-agent step', () => {
    const asEal = setStepChannel(steps, 'step-01', 'eal')
    expect(setStepTarget(asEal, 'step-01', 'web-prod-02')).toBe(asEal)
  })
})

describe('setStepEal — eal-channel only, shallow merge', () => {
  const steps = draftFromScenario(SCENARIO).steps
  const asEal = setStepChannel(steps, 'step-01', 'eal')

  it('returns the SAME array for an unknown id or a non-object patch', () => {
    expect(setStepEal(asEal, 'step-99', { plugin: 'x' })).toBe(asEal)
    expect(setStepEal(asEal, 'step-01', null)).toBe(asEal)
  })
  it('refuses (SAME array) on a non-eal step', () => {
    expect(setStepEal(steps, 'step-01', { plugin: 'x' })).toBe(steps)
  })
  it('merges a plugin without touching params', () => {
    const out = setStepEal(asEal, 'step-01', { plugin: 'ngfw_eal_emitter' })
    expect(out[0].eal).toEqual({ plugin: 'ngfw_eal_emitter', params: {} })
  })
  it('replaces params wholesale', () => {
    const withPlugin = setStepEal(asEal, 'step-01', { plugin: 'ngfw_eal_emitter' })
    const out = setStepEal(withPlugin, 'step-01', { params: { rate: '10' } })
    expect(out[0].eal).toEqual({ plugin: 'ngfw_eal_emitter', params: { rate: '10' } })
  })
  it('is a SAME-ref no-op when nothing changes', () => {
    const withPlugin = setStepEal(asEal, 'step-01', { plugin: 'ngfw_eal_emitter' })
    expect(setStepEal(withPlugin, 'step-01', { plugin: 'ngfw_eal_emitter' })).toBe(withPlugin)
  })
})

describe('draftToApi — channel omit-when-default', () => {
  it('a corpus (all-agent) draft emits NONE of the three keys', () => {
    const body = draftToApi(draftFromScenario(SCENARIO))
    expect(body.steps[0]).not.toHaveProperty('channel')
    expect(body.steps[0]).not.toHaveProperty('target')
    expect(body.steps[0]).not.toHaveProperty('eal')
  })
  it('an agent step with a second endpoint emits only target', () => {
    const draft = draftFromScenario(SCENARIO)
    draft.steps = setStepTarget(draft.steps, 'step-01', 'web-prod-02')
    const body = draftToApi(draft)
    expect(body.steps[0].target).toBe('web-prod-02')
    expect(body.steps[0]).not.toHaveProperty('channel')
    expect(body.steps[0]).not.toHaveProperty('eal')
  })
  it('an eal step emits channel + eal, never target', () => {
    const draft = draftFromScenario(SCENARIO)
    draft.steps = setStepEal(
      setStepChannel(draft.steps, 'step-01', 'eal'),
      'step-01',
      { plugin: 'ngfw_eal_emitter', params: { rate: '5' } },
    )
    const body = draftToApi(draft)
    expect(body.steps[0].channel).toBe('eal')
    expect(body.steps[0].eal).toEqual({ plugin: 'ngfw_eal_emitter', params: { rate: '5' } })
    expect(body.steps[0]).not.toHaveProperty('target')
  })
})

describe('channel round-trip + dirty tracking', () => {
  it('an eal step round-trips scenario → api → draft with the eal block intact', () => {
    const draft = draftFromScenario(SCENARIO)
    draft.steps = setStepEal(
      setStepChannel(draft.steps, 'step-01', 'eal'),
      'step-01',
      { plugin: 'ngfw_eal_emitter', params: { rate: '5' } },
    )
    const body = draftToApi(draft)
    const back = draftFromApi({ ...SCENARIO, status: 'draft', steps: body.steps })
    expect(back.steps[0].channel).toBe('eal')
    expect(back.steps[0].eal).toEqual({ plugin: 'ngfw_eal_emitter', params: { rate: '5' } })
  })
  it('a channel edit reads as DIRTY (it changes which endpoint executes)', () => {
    const draft = draftFromScenario(SCENARIO)
    const saved = draftSnapshot(draft)
    const edited = { ...draft, steps: setStepChannel(draft.steps, 'step-01', 'eal') }
    expect(isDraftDirty(draftSnapshot(edited), saved)).toBe(true)
  })
  it('a target edit reads as DIRTY', () => {
    const draft = draftFromScenario(SCENARIO)
    const saved = draftSnapshot(draft)
    const edited = { ...draft, steps: setStepTarget(draft.steps, 'step-01', 'web-prod-02') }
    expect(isDraftDirty(draftSnapshot(edited), saved)).toBe(true)
  })
})

describe('validateDraft — missing EAL plugin', () => {
  it('names an eal-channel step with no plugin declared', () => {
    const draft = draftFromScenario(SCENARIO)
    const steps = setStepChannel(draft.steps, 'step-01', 'eal') // seeds empty plugin
    const v = validateDraft(steps)
    expect(v.missingEalPlugin).toEqual(['step-01'])
    expect(v.ok).toBe(false)
    expect(v.problems.join(' ')).toMatch(/emitter plugin/i)
  })
  it('an eal step WITH a plugin does not flag missingEalPlugin', () => {
    const draft = draftFromScenario(SCENARIO)
    const steps = setStepEal(
      setStepChannel(draft.steps, 'step-01', 'eal'),
      'step-01',
      { plugin: 'ngfw_eal_emitter' },
    )
    expect(validateDraft(steps).missingEalPlugin).toEqual([])
  })
  it('a corpus draft has an empty missingEalPlugin list', () => {
    expect(validateDraft(draftFromScenario(SCENARIO).steps).missingEalPlugin).toEqual([])
  })
})

describe('composer layout', () => {
  it('starts null so a fresh draft is byte-identical to a pre-feature draft', () => {
    expect(emptyDraft().layout).toBeNull()
  })

  it('setNodePosition stores a position without mutating the input', () => {
    const d0 = { ...emptyDraft(), steps: [{ id: 's1' }] }
    const d1 = setNodePosition(d0, 's1', 120, 40)
    expect(d1.layout).toEqual({ s1: { x: 120, y: 40 } })
    expect(d0.layout).toBeNull()          // no mutation
  })

  it('round-trips through the API shape', () => {
    // detections: [] — draftToApi maps over each step's detections, so a
    // bare { id } (unlike the mutation-only tests above, which never reach
    // draftToApi) needs the field normalizeStep would otherwise supply.
    const d = setNodePosition(
      { ...emptyDraft(), steps: [{ id: 's1', detections: [] }] }, 's1', 8, 16,
    )
    expect(draftToApi(d).composer_layout).toEqual({ s1: { x: 8, y: 16 } })
    expect(draftFromApi({ ...draftToApi(d), composer_layout: { s1: { x: 8, y: 16 } } }).layout)
      .toEqual({ s1: { x: 8, y: 16 } })
  })

  it('omits composer_layout entirely when there is no layout', () => {
    expect('composer_layout' in draftToApi(emptyDraft())).toBe(false)
  })

  it('clearLayout resets to null for Re-layout', () => {
    const d = setNodePosition({ ...emptyDraft(), steps: [{ id: 's1' }] }, 's1', 1, 2)
    expect(clearLayout(d).layout).toBeNull()
  })

  it('NEVER leaks coordinates into emitted scenario YAML', () => {
    // That YAML is dropped into scenarios/<plane>/ and validated by the strict
    // loader. Coordinates there would reach the shipped corpus.
    // platforms: [] — emitDraftYaml reads s.platforms.length directly (no
    // Array.isArray guard, by design: an unrecognized step shape should
    // raise, not silently read as empty), so a step built by hand here needs
    // the field normalizeStep would otherwise supply.
    const d = setNodePosition(
      {
        ...emptyDraft(),
        name: 'x',
        plane: 'EDR',
        steps: [{ id: 's1', name: 'a', command: 'id', platforms: [], detections: [] }],
      },
      's1', 999, 777,
    )
    const yaml = emitDraftYaml(d)
    expect(yaml).not.toMatch(/composer_layout|\bx:\s*999|\by:\s*777|position/)
  })
})
