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

import { parseStitchContext, emitStitchContext } from './stitchContext.js'

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
    // Phase-2 Stitch Context — the authored shared-entity intent. Parsed to the
    // panel's `{[key]:{literal|resolve}}` model, or null for a context-less
    // scenario (the 177-scenario corpus + Phase-1 drafts all land here as null).
    // Because `draftFromApi` spreads `...draftFromScenario(row)`, drafts round-
    // trip through this ONE place — no separate `draftFromApi` edit.
    stitchContext: parseStitchContext(scenario.stitch_context),
    steps,
    // Persistence identity — null here, filled by `draftFromApi` when the row
    // came back from the drafts API. A draft seeded from a corpus scenario is
    // NOT itself a persisted draft until it is saved, so these stay null and
    // the two shapes (`draftFromScenario` / `draftFromApi`) match key-for-key.
    scenarioId: null,
    status: null,
    author: null,
    tags: [],
  }
}

export function emptyDraft() {
  return {
    originId: null, name: null, plane: null, ucRef: null, tcRef: null,
    moatTier: null, cgo: null, teardown: [], stitchContext: null, steps: [],
    scenarioId: null, status: null, author: null, tags: [],
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

// ─── Composer enums (mirror the backend verbatim) ─────────────────────────────
// These drive the inspector's pickers. They are the SAME enums the strict
// loader and DraftScenarioSchema enforce server-side — a picker that offered a
// value the backend rejects would let a DC author a draft that 422s at save,
// so the two lists must not drift. Keep them in this order (matches the spec's
// frozen cross-file facts: pivots=7, detection types=6, planes=16).

/** Causality pivot vocabulary (`StepCausalitySchema._PIVOTS`). */
export const PIVOTS = [
  'process_lineage',
  'network_session',
  'endpoint_network_stitch',
  'shared_entity',
  'exposure_exploit',
  'exploit_impact',
  'temporal',
]

/** The `detection_type` vocabulary — six, ABIOC included. */
export const DETECTION_TYPES = ['BIOC', 'XQL', 'Analytics', 'Correlation', 'IOC', 'ABIOC']

/** Detection planes (`ScenarioSchema.validate_plane` enum). */
export const PLANES = [
  'EDR', 'CDR', 'NDR', 'ITDR', 'CLOUD_APP', 'ANALYTICS', 'AI_ACCESS', 'AIRS',
  'AI_SPM', 'BROWSER', 'KOI', 'ASM', 'CSPM', 'TIM', 'EMAIL', 'DLP',
]

// ─── Immutable step edit operations ───────────────────────────────────────────
// Same no-op-returns-same-ref contract as moveStep/duplicateStep above: a caller
// wiring these to setState relies on identity to skip a pointless re-render, and
// a test asserts "editing an unknown id did nothing" by reference equality.

const _EDITABLE_KEYS = new Set([
  'name', 'command', 'identity', 'technique', 'platforms',
  'causalityParent', 'causalityPivot',
])

/**
 * Patch one step's mutable fields. `patch` ⊆ the editable-key set; unknown keys
 * are ignored. Returns the SAME array when the id is unknown or the patch would
 * change nothing (shallow compare, arrays compared by identity — callers pass a
 * fresh array for a platforms change).
 */
export function editStep(steps, id, patch) {
  if (!patch || typeof patch !== 'object') return steps
  const index = steps.findIndex((s) => s.id === id)
  if (index < 0) return steps
  const current = steps[index]
  let changed = false
  const next = { ...current }
  for (const [k, v] of Object.entries(patch)) {
    if (!_EDITABLE_KEYS.has(k)) continue
    if (current[k] !== v) {
      next[k] = v
      changed = true
    }
  }
  if (!changed) return steps
  const out = steps.slice()
  out[index] = next
  return out
}

/** One expected-detection object, normalised to the draft's camelCase shape. */
function _normalizeDetection(det) {
  if (!det || typeof det !== 'object') return null
  return {
    plane: det.plane || null,
    type: det.type || null,
    description: det.description || null,
    ttpRef: det.ttpRef ?? det.ttp_ref ?? null,
    detectionId: det.detectionId ?? det.detection_id ?? null,
    verificationXql: det.verificationXql ?? det.verification_xql ?? null,
  }
}

/**
 * Append one expected detection to a step. Returns the SAME array when the id is
 * unknown or the detection is not an object.
 */
export function addDetection(steps, id, det) {
  const normalized = _normalizeDetection(det)
  if (!normalized) return steps
  const index = steps.findIndex((s) => s.id === id)
  if (index < 0) return steps
  const out = steps.slice()
  out[index] = { ...steps[index], detections: steps[index].detections.concat([normalized]) }
  return out
}

/**
 * Remove the detection at `detIndex` from a step. Returns the SAME array when
 * the id is unknown or the index is out of range.
 */
export function removeDetection(steps, id, detIndex) {
  const index = steps.findIndex((s) => s.id === id)
  if (index < 0) return steps
  const dets = steps[index].detections
  if (detIndex < 0 || detIndex >= dets.length) return steps
  const out = steps.slice()
  out[index] = { ...steps[index], detections: dets.filter((_, i) => i !== detIndex) }
  return out
}

/**
 * Re-parent a step in the causality spine. `parentId === null` clears the link
 * (the step becomes a chain root, hanging off the CGO).
 *
 * Returns the SAME array when the edit would author an invalid spine — a
 * self-ref, or a FORWARD ref (a parent that appears at or after this step in
 * array order). That mirrors the loader's spine rule (`no self/forward refs`),
 * so the canvas physically cannot build a chain the backend would reject.
 */
export function setCausalityParent(steps, id, parentId, pivot = 'process_lineage') {
  const index = steps.findIndex((s) => s.id === id)
  if (index < 0) return steps

  if (parentId != null) {
    if (parentId === id) return steps // self-ref
    const parentIndex = steps.findIndex((s) => s.id === parentId)
    if (parentIndex < 0) return steps // unknown parent
    if (parentIndex >= index) return steps // forward (or self) ref
  }

  const current = steps[index]
  const nextParent = parentId ?? null
  // When clearing, the pivot is meaningless — keep the step's existing pivot so
  // re-linking later restores a sensible default rather than a surprise.
  const nextPivot = nextParent ? (pivot || 'process_lineage') : current.causalityPivot
  if (current.causalityParent === nextParent && current.causalityPivot === nextPivot) {
    return steps
  }
  const out = steps.slice()
  out[index] = { ...current, causalityParent: nextParent, causalityPivot: nextPivot }
  return out
}

/**
 * Append one expected detection pre-filled from a TTP card object — the
 * launch-gate satisfaction path (§6.6). A TTP card carries a plane, a detection
 * type and its own id; binding it gives the step a declared detection so it is
 * no longer reported as a gap. Returns the SAME array when the id is unknown or
 * the card is not an object.
 */
export function bindTtpDetection(steps, id, ttpCard) {
  if (!ttpCard || typeof ttpCard !== 'object') return steps
  const index = steps.findIndex((s) => s.id === id)
  if (index < 0) return steps
  const det = {
    plane: ttpCard.plane || steps[index].detections[0]?.plane || null,
    type: ttpCard.detection_type || ttpCard.type || null,
    description: ttpCard.name || ttpCard.description || null,
    ttpRef: ttpCard.ttp_id || ttpCard.id || ttpCard.ttpRef || null,
    detectionId: ttpCard.detection_id || ttpCard.detectionId || null,
  }
  return addDetection(steps, id, det)
}

// ─── Round-trip serializers (draft ⇄ drafts API) ─────────────────────────────

/** Parse the joined `cgo` display string back into a `{image_name,...}` object. */
function _cgoAnchorFromDisplay(cgo) {
  if (!cgo) return null
  const [image_name, primary_username] = String(cgo).split(' / ').map((s) => s.trim())
  if (!image_name) return null
  const anchor = { image_name }
  if (primary_username) anchor.primary_username = primary_username
  return anchor
}

/**
 * Build the FROZEN snake_case `DraftCreateRequest` body for POST/PUT.
 *
 * OMITS every server-derived field (`detection_types`, `push_supported`,
 * `pull_supported`, `required_base_platform`, `required_addons`, `status`,
 * `version`) — the backend derives those from the steps and the index, and a
 * client that sent them would be asserting a contract it does not own.
 */
export function draftToApi(draft, { author = 'composer' } = {}) {
  const body = {
    name: draft.name || '',
    plane: draft.plane || '',
    author: draft.author || author,
    tags: Array.isArray(draft.tags) ? draft.tags : [],
    steps: draft.steps.map((s) => {
      const step = {
        id: s.id,
        name: s.name,
        command: s.command ?? '',
        identity: s.identity ?? '',
        mitre_technique: s.technique ?? '',
        expected_detections: s.detections.map((d) => {
          const det = { plane: d.plane, type: d.type, description: d.description }
          if (d.ttpRef) det.ttp_ref = d.ttpRef
          if (d.detectionId) det.detection_id = d.detectionId
          if (d.verificationXql) det.verification_xql = d.verificationXql
          return det
        }),
      }
      if (s.causalityParent) {
        step.causality = {
          parent_step: s.causalityParent,
          pivot: s.causalityPivot || 'process_lineage',
        }
      }
      if (Array.isArray(s.platforms) && s.platforms.length) step.platforms = s.platforms
      if (s.platformVariants && Object.keys(s.platformVariants).length) {
        step.platform_variants = s.platformVariants
      }
      return step
    }),
  }
  if (draft.ucRef) body.uc_ref = draft.ucRef
  if (draft.tcRef) body.tc_ref = draft.tcRef
  if (Array.isArray(draft.tcRefs) && draft.tcRefs.length) body.tc_refs = draft.tcRefs
  // Phase-2 Stitch Context — OMITTED when empty so a context-less draft
  // serializes byte-identically to the corpus. Additive/optional, matching the
  // backend `stitch_context: Optional[StitchContextSchema] = None`.
  const sc = emitStitchContext(draft.stitchContext)
  if (sc) body.stitch_context = sc
  if (draft.povScenarioId) body.pov_scenario_id = draft.povScenarioId
  if (draft.mitreTactic) body.mitre_tactic = draft.mitreTactic
  if (draft.mitreTacticName) body.mitre_tactic_name = draft.mitreTacticName
  if (draft.mitreTechniqueName) body.mitre_technique_name = draft.mitreTechniqueName
  const cgoAnchor = _cgoAnchorFromDisplay(draft.cgo)
  if (cgoAnchor) body.cgo_anchor = cgoAnchor
  if (draft.executionIdentity) body.execution_identity = draft.executionIdentity
  if (Array.isArray(draft.teardown) && draft.teardown.length) {
    body.cleanup = { commands: draft.teardown }
  }
  if (Array.isArray(draft.externalTools) && draft.externalTools.length) {
    body.external_tools = draft.externalTools
  }
  return body
}

/**
 * Inverse of `draftToApi` over a draft `Scenario.to_dict()` body.
 *
 * A SUPERSET of `draftFromScenario`: it reuses it for the shared camelCase
 * fields (never forking `normalizeStep`) and ALSO carries the persistence
 * identity the drafts API returns (`scenario_id`, `status`, `author`, `tags`).
 */
export function draftFromApi(row) {
  if (!row) return emptyDraft()
  const base = draftFromScenario(row)
  return {
    ...base,
    scenarioId: row.scenario_id || null,
    status: row.status || null,
    author: row.author || null,
    tags: Array.isArray(row.tags) ? row.tags : [],
  }
}

// ─── Dirty tracking (the launch gate's "save before launch" guard) ────────────

/**
 * A plain, serializable snapshot of only the LAUNCH-relevant fields. SimCore
 * runs the SAVED chain, not the canvas, so the console must know when the canvas
 * has drifted from what was persisted. Fields that do not change what executes
 * (e.g. `moatTier`, `authored` flags) are deliberately excluded so a cosmetic
 * change does not read as "unsaved work that changes the run".
 */
export function draftSnapshot(draft) {
  if (!draft) return null
  return {
    name: draft.name ?? null,
    plane: draft.plane ?? null,
    ucRef: draft.ucRef ?? null,
    tcRef: draft.tcRef ?? null,
    cgo: draft.cgo ?? null,
    // Load-bearing, NOT cosmetic: the context changes WHAT EXECUTES (the
    // {stitch:*} substitutions the server injects), so a context edit MUST read
    // as dirty and force a re-save before launch, exactly like a step edit. The
    // emitted (wire) form is the stable comparison key.
    stitchContext: emitStitchContext(draft.stitchContext),
    steps: (draft.steps || []).map((s) => ({
      id: s.id,
      command: s.command ?? null,
      identity: s.identity ?? null,
      technique: s.technique ?? null,
      platforms: Array.isArray(s.platforms) ? s.platforms.slice() : [],
      causalityParent: s.causalityParent ?? null,
      causalityPivot: s.causalityPivot ?? null,
      detections: (s.detections || []).map((d) => ({
        plane: d.plane ?? null,
        type: d.type ?? null,
        description: d.description ?? null,
        ttpRef: d.ttpRef ?? null,
        detectionId: d.detectionId ?? null,
        verificationXql: d.verificationXql ?? null,
      })),
    })),
  }
}

/**
 * Has the current draft drifted from the last saved snapshot?
 *
 * A `null` savedSnapshot ALWAYS returns true — an unsaved draft is dirty by
 * definition, and the launch gate leans on exactly this: it refuses to launch
 * a chain that was never persisted.
 */
export function isDraftDirty(current, savedSnapshot) {
  if (savedSnapshot == null) return true
  return JSON.stringify(current) !== JSON.stringify(savedSnapshot)
}
