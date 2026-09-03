/**
 * composerDraft.js — the pure draft model behind the Simulation Composer.
 *
 * No React, no fetch. Same convention (and the same reason) as `runStatus.js`,
 * `supplyState.js` and `healthModel.js`: a draft that is re-derived inside a
 * component is a draft whose validation, YAML and step ordering can disagree
 * with each other, and the direction that disagreement always takes is
 * "looks launchable".
 *
 * THE ONE RULE
 * ------------
 * **A draft never invents scenario content.** Every field here either comes
 * from a real scenario the API returned, or is explicitly `null` and rendered
 * as "not declared". The scenario schema has no per-step `delay` or `timeout`
 * (checked against `scenarios/_schema.yml`), so this module does not carry
 * them — a Composer that showed `delay 5s · timeout 60s` on every step would
 * be describing a contract the runner does not implement, and a DC would quote
 * it to a customer. `platforms` and `causality.pivot` are real and are carried;
 * teardown is SCENARIO-level (`cleanup.commands`), not per-step, and is
 * labelled as such rather than being copied onto each node.
 */

/** A step the DC added by hand, before they have configured it. */
export const BLANK_COMMAND = '# configure the command for this step'

/**
 * Normalise one scenario step into a draft step.
 *
 * Tolerates both the list shape (`GET /api/scenarios`) and the detail shape
 * (`GET /api/scenarios/:id`) — the list omits `command` and `causality`, so
 * those land as null and the inspector says so rather than showing an empty box
 * that reads like "no command configured".
 */
export function normalizeStep(raw, index) {
  if (!raw || typeof raw !== 'object') return null
  const detections = Array.isArray(raw.expected_detections) ? raw.expected_detections : []
  return {
    id: raw.id || `step-${String(index + 1).padStart(2, '0')}`,
    name: raw.name || '(unnamed step)',
    // null, not '' — "the list endpoint did not carry a command" and "the
    // command is an empty string" must not render identically.
    command: typeof raw.command === 'string' ? raw.command : null,
    identity: raw.identity || null,
    technique: raw.mitre_technique || null,
    platforms: Array.isArray(raw.platforms) ? raw.platforms : [],
    causalityParent: raw.causality?.parent_step || null,
    causalityPivot: raw.causality?.pivot || null,
    detections: detections.map((d) => ({
      plane: d.plane || null,
      type: d.type || null,
      description: d.description || null,
      ttpRef: d.ttp_ref || null,
      detectionId: d.detection_id || null,
    })),
    // Steps that came from a scenario are distinguishable from ones the DC
    // added here — the empty-state copy and the YAML comment both need it.
    authored: false,
  }
}

/** Build a draft from a scenario object (list or detail shape). */
export function draftFromScenario(scenario) {
  if (!scenario) return emptyDraft()
  const steps = (Array.isArray(scenario.steps) ? scenario.steps : [])
    .map(normalizeStep)
    .filter(Boolean)
  return {
    originId: scenario.scenario_id || scenario.id || null,
    name: scenario.name || null,
    plane: scenario.plane || null,
    ucRef: scenario.uc_ref || null,
    tcRef: scenario.tc_ref || null,
    moatTier: scenario.moat_tier || null,
    // The Causality Group Owner is a first-class part of what a chain proves —
    // it is what the START node names, and it is why the steps form one spine.
    cgo: scenario.cgo_anchor
      ? [scenario.cgo_anchor.image_name, scenario.cgo_anchor.primary_username]
        .filter(Boolean).join(' / ') || null
      : null,
    // Scenario-level, per the schema. Never copied onto individual steps.
    teardown: Array.isArray(scenario.cleanup?.commands) ? scenario.cleanup.commands : [],
    steps,
  }
}

export function emptyDraft() {
  return {
    originId: null, name: null, plane: null, ucRef: null, tcRef: null,
    moatTier: null, cgo: null, teardown: [], steps: [],
  }
}

/** Next free `step-NN` id for a draft, so duplicates never collide. */
export function nextStepId(steps) {
  let n = steps.length + 1
  const taken = new Set(steps.map((s) => s.id))
  while (taken.has(`step-${String(n).padStart(2, '0')}`)) n += 1
  return `step-${String(n).padStart(2, '0')}`
}

export function blankStep(id, { name = 'New step', technique = null } = {}) {
  return {
    id,
    name,
    command: BLANK_COMMAND,
    identity: null,
    technique,
    platforms: [],
    causalityParent: null,
    causalityPivot: 'process_lineage',
    detections: [],
    authored: true,
  }
}

// ─── Immutable step operations ───────────────────────────────────────────────
// Each returns the SAME array reference when the operation is a no-op, so a
// caller's `setState` does not schedule a pointless re-render (and so a test
// can assert "moving the first step up did nothing" by identity).

export function moveStep(steps, index, delta) {
  const j = index + delta
  if (index < 0 || index >= steps.length || j < 0 || j >= steps.length) return steps
  const out = steps.slice()
  const tmp = out[index]
  out[index] = out[j]
  out[j] = tmp
  return out
}

export function duplicateStep(steps, index) {
  if (index < 0 || index >= steps.length) return steps
  const out = steps.slice()
  out.splice(index + 1, 0, { ...steps[index], id: nextStepId(steps), authored: true })
  return out
}

export function removeStep(steps, index) {
  if (index < 0 || index >= steps.length) return steps
  return steps.filter((_, i) => i !== index)
}

export function appendStep(steps, step) {
  return steps.concat([step])
}

// ─── Validation ──────────────────────────────────────────────────────────────

/**
 * What is wrong with this draft, named.
 *
 * A step with no `expected_detections` is the failure this exists to catch: it
 * will execute happily and then be reported as a GAP in the POV evidence,
 * because nothing declared what Cortex was supposed to raise. That is the most
 * expensive way to discover the mistake — in front of the customer, in the
 * readout — so it is surfaced at compose time as a named list of step ids, not
 * a boolean.
 *
 * An empty draft is `ok: false` with its own reason: "valid" must never be the
 * verdict on a chain that would do nothing.
 */
export function validateDraft(steps) {
  const missingDetections = steps.filter((s) => !s.detections.length).map((s) => s.id)
  const missingCommands = steps
    .filter((s) => !s.command || s.command === BLANK_COMMAND)
    .map((s) => s.id)

  const detectionCount = steps.reduce((n, s) => n + s.detections.length, 0)
  const techniques = steps
    .map((s) => s.technique)
    .filter(Boolean)
    .filter((t, i, arr) => arr.indexOf(t) === i)

  const problems = []
  if (!steps.length) {
    problems.push('The chain is empty — add at least one step before preflight.')
  }
  // Agreement matters here because this string is read aloud in a review:
  // "1 step declare no expected detection" is the kind of wrong that makes a
  // careful reader distrust the number next to it.
  if (missingCommands.length) {
    const one = missingCommands.length === 1
    problems.push(
      `${missingCommands.length} step${one ? '' : 's'} ${one ? 'has' : 'have'} `
      + `no command configured (${missingCommands.join(', ')}).`,
    )
  }
  if (missingDetections.length) {
    const one = missingDetections.length === 1
    problems.push(
      `${missingDetections.length} step${one ? '' : 's'} ${one ? 'declares' : 'declare'} `
      + `no expected detection (${missingDetections.join(', ')}) — a run would `
      + `report ${one ? 'it' : 'them'} as ${one ? 'a gap' : 'gaps'}.`,
    )
  }

  return {
    ok: problems.length === 0,
    problems,
    missingDetections,
    missingCommands,
    counts: { steps: steps.length, detections: detectionCount, techniques: techniques.length },
    techniques,
  }
}

// ─── YAML emit ───────────────────────────────────────────────────────────────

/** Quote a scalar only when YAML would otherwise mis-read it. */
function scalar(v) {
  if (v == null) return 'null'
  const s = String(v)
  return /^[A-Za-z0-9._/-]+$/.test(s) ? s : JSON.stringify(s)
}

/**
 * Emit the draft as scenario YAML.
 *
 * This is the artifact "Download draft YAML" writes. It is deliberately a
 * SUBSET of `scenarios/_schema.yml` and says so in its own header comment: this
 * SimCore exposes no scenario-create endpoint (checked across `api/client.js`),
 * so the honest hand-off is a file the DC drops into `scenarios/` and reloads,
 * not a Save button that pretends to persist somewhere.
 */
export function emitDraftYaml(draft, { tenant = null, agent = null } = {}) {
  const lines = []
  lines.push('# Draft emitted by the CortexSim Simulation Composer.')
  lines.push('# This is a SUBSET of scenarios/_schema.yml — review it, add the')
  lines.push('# methodology/KPI block, then drop it into scenarios/<plane>/ and reload.')
  lines.push(`scenario_id: ${scalar(draft.originId ? `${draft.originId}-draft` : 'SIM-DRAFT-001')}`)
  lines.push(`name: ${scalar(draft.name || 'Untitled draft chain')}`)
  if (draft.plane) lines.push(`plane: ${scalar(draft.plane)}`)
  if (draft.ucRef) lines.push(`uc_ref: ${scalar(draft.ucRef)}`)
  if (draft.tcRef) lines.push(`tc_ref: ${scalar(draft.tcRef)}`)

  // Scope is a comment, not a scenario field: tenant and agent are chosen at
  // LAUNCH time (POST /api/runs), not baked into a scenario file. Writing them
  // as real keys would produce a file the loader rejects.
  lines.push('')
  lines.push('# Launch scope at compose time (not part of the scenario schema):')
  lines.push(`#   tenant: ${tenant || '(none selected)'}`)
  lines.push(`#   agent:  ${agent || '(none selected)'}`)

  lines.push('')
  lines.push('steps:')
  if (!draft.steps.length) {
    lines.push('  []  # empty chain — nothing would execute')
  }
  for (const s of draft.steps) {
    lines.push(`  - id: ${scalar(s.id)}`)
    lines.push(`    name: ${scalar(s.name)}`)
    lines.push('    command: |')
    for (const line of String(s.command ?? BLANK_COMMAND).split('\n')) {
      lines.push(`      ${line}`)
    }
    if (s.identity) lines.push(`    identity: ${scalar(s.identity)}`)
    if (s.technique) lines.push(`    mitre_technique: ${scalar(s.technique)}`)
    if (s.platforms.length) {
      lines.push(`    platforms: [${s.platforms.map(scalar).join(', ')}]`)
    }
    if (s.causalityParent || s.causalityPivot) {
      lines.push('    causality:')
      if (s.causalityParent) lines.push(`      parent_step: ${scalar(s.causalityParent)}`)
      if (s.causalityPivot) lines.push(`      pivot: ${scalar(s.causalityPivot)}`)
    }
    lines.push('    expected_detections:')
    if (!s.detections.length) {
      lines.push('      []  # NO EXPECTED DETECTION — this step would be reported as a gap')
    }
    for (const d of s.detections) {
      lines.push(`      - plane: ${scalar(d.plane)}`)
      lines.push(`        type: ${scalar(d.type)}`)
      if (d.description) lines.push(`        description: ${scalar(d.description)}`)
      if (d.ttpRef) lines.push(`        ttp_ref: ${scalar(d.ttpRef)}`)
      if (d.detectionId) lines.push(`        detection_id: ${scalar(d.detectionId)}`)
    }
  }

  if (draft.teardown.length) {
    lines.push('')
    lines.push('cleanup:')
    lines.push('  commands:')
    for (const c of draft.teardown) lines.push(`    - ${scalar(c)}`)
  }

  return lines.join('\n') + '\n'
}
