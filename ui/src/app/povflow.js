/**
 * povflow.js — the flow bar's model, and the one place the CTA rule lives.
 *
 * The rail IS the phase model (see docs/design/DESIGN-SYNC.md). This module
 * answers the two questions the flow bar asks on every surface: which phase am
 * I in, and what is the next action.
 *
 * THE CTA RULE, which is a contract:
 *   Every CTA reads `Next: <destination exactly as the rail names it>`.
 *   `Export report` is the single terminal action.
 *   The Launch Gate keeps its own `Launch chain` button because that is an
 *   action, not navigation — it is rendered by the surface, not by the bar.
 *
 * Before this was one rule, the CTAs were written per-screen and drifted into
 * five different phrasings for the same move ("Continue to Compose →",
 * "Preflight this chain →", "Launch chain →"), which made the bar read as
 * decoration rather than as the wizard it is.
 *
 * PHASE INDEX -1 IS NOT A MISSING VALUE. It means "this surface is not a
 * phase" — the Overview front door. A previous revision let Overview share the
 * Preflight destination and therefore inherit phase index 2, so the app's
 * entry page showed Scope and Compose as already ticked. Overview is
 * deliberately phase-less: no pill active, nothing shown as done.
 */

/** The six phases, in run order. Phase 4 (Launch) has no destination of its
 *  own — it is a state the Composer enters after preflight, so its pill points
 *  back at the gate rather than offering a place that does not exist. */
export const PHASES = [
  { label: 'Scope', caption: 'components · tenant · agents', dest: 'scope' },
  { label: 'Compose', caption: 'steps · payload plan', dest: 'composer' },
  { label: 'Preflight', caption: 'readiness · egress', dest: 'preflight' },
  { label: 'Launch', caption: 'push to the agent', dest: 'preflight' },
  { label: 'Observe', caption: 'live steps · events', dest: 'runs' },
  { label: 'Prove', caption: 'evidence · export', dest: 'proof' },
]

/**
 * The flow entry for a destination.
 *
 * @param {string} destination
 * @param {Object} ctx  derived tallies, so the bar quotes the same numbers the
 *                      surface does rather than restating literals. Every field
 *                      is optional; an absent one renders as words, never as 0:
 *                      { gate:{state,total,pass,warn,unreported},
 *                        components:{total,ready,partial,missing},
 *                        tenantCount, agentCount, agentsOnline, scenarioCount,
 *                        armed, chainSteps, runStep, runDetections }
 * @returns {{ phase:number, title:string, sub:string, cta:string, ctaDest:string|null }}
 */
export function flowFor(destination, ctx = {}) {
  const comp = ctx.components || { total: 0, ready: 0, partial: 0, missing: 0 }
  const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`
  const known = (n) => typeof n === 'number' && Number.isFinite(n)

  // NEVER QUOTE A NUMBER THAT WAS NOT GIVEN.
  // Every row over a surface that renders live SimCore data takes its numbers
  // from `ctx` and falls back to WORDS, never to a figure. This file used to
  // carry the design prototype's literals — "6 of 11 detections observed" on
  // Runs, "Report ready · 6 observed · 5 pending · 0 missed" on Proof & Export,
  // "3 online" on Agents, "9 of 9 checks pass" on a Launch Gate whose SimCore
  // was unreachable — and they rendered on every POV regardless of what had
  // run, directly under the surface showing the real (different) numbers.
  //
  // Rows marked SEED sit over surfaces that still render the seed catalog
  // (povdata/), so their figures mirror what the surface under them shows.
  const MAP = {
    overview: [-1, 'POVengine · overview', 'what it does, how it stays honest', 'Setup Wizard'],
    setup: [0, 'Setup wizard', 'detections → targets → scope → requirements → goals', 'Library'],
    // SEED — componentTally() is passed in, so the bar and the strip agree.
    scope: [0, `${comp.total} components`,
      `${comp.ready} ready · ${comp.partial} partial · ${comp.missing} missing`, 'Library'],
    tenants: [0,
      !known(ctx.tenantCount) ? 'Tenant'
        : ctx.tenantCount === 0 ? 'No tenant bound' : plural(ctx.tenantCount, 'tenant') + ' bound',
      'read-only — nothing here writes to the tenant', 'Library'],
    agents: [0,
      known(ctx.agentCount) ? `${plural(ctx.agentCount, 'beacon')} enrolled` : 'Agents',
      known(ctx.agentsOnline) ? `${ctx.agentsOnline} online` : 'pull-mode dispatch targets', 'Library'],

    library: [1,
      ctx.armed ? `${ctx.armed} armed`
        : known(ctx.scenarioCount) ? plural(ctx.scenarioCount, 'scenario') : 'Library',
      'pick a scenario to arm it for launch', 'Composer'],
    // SEED — CLI Items and Data Streams render the seed catalog.
    cli: [1, '4 CLI items authored', '2 ready · 2 draft · all digest-pinned', 'Composer'],
    adapters: [1, 'Packages', 'what the target runs · staged on this SimCore or fetched at run time', 'Composer'],
    streams: [1, '21 of 34 streams mapped', '7 gaps · relayed to the Broker VM', 'Composer'],
    ttps: [1, 'TTP cards', 'authored detection content, bound to scenarios', 'Composer'],
    uctc: [1, 'UC / TC index', 'use cases → test cases → the evidence that binds them', 'Composer'],
    composer: [1,
      known(ctx.chainSteps) ? `${plural(ctx.chainSteps, 'step')} on the spine` : 'Composer',
      'steps · payload plan', 'Launch Gate'],

    preflight: [2, ...gateLine(ctx.gate), 'Runs'],

    runs: [4, ctx.runStep || 'No run in flight', ctx.runDetections || 'live steps · events', 'Proof & Export'],

    // SEED — Tenant Validation renders the seed validation sheets.
    validation: [5, '0 of 11 tenant-verified', '6 awaiting probe · 2 not present', 'Proof & Export'],
    coverage: [5, 'Coverage', 'planes × detection types, across runs', 'Proof & Export'],
    proof: [5, 'Proof & Export', 'observed · pending · missed, per run', null],
  }

  const row = MAP[destination] || MAP.scope
  const [phase, title, sub, nextLabel] = row
  return {
    phase,
    title,
    sub,
    // The terminal action is the only CTA that is not navigation.
    cta: nextLabel ? `Next: ${nextLabel}` : 'Export report',
    ctaDest: nextLabel ? DEST_BY_LABEL[nextLabel] || null : 'proof',
  }
}

/**
 * The Launch Gate line. A check that never ran is not a check that passed:
 * before the first /api/health answer, and while SimCore is unreachable, the
 * bar says so instead of "9 of 9 checks pass".
 *
 * @param {{state?:'unknown'|'unreachable'|'checked', total?, pass?, warn?, unreported?}|undefined} gate
 * @returns {[string, string]} [title, sub]
 */
function gateLine(gate) {
  if (!gate || gate.state === 'unknown') {
    return ['Readiness not checked yet', 'waiting on the first /api/health answer']
  }
  if (gate.state === 'unreachable') {
    return ['SimCore unreachable', 'no readiness check could run']
  }
  const { total = 0, pass = 0, warn = 0, unreported = 0 } = gate
  return [
    `${pass} of ${total} components ok`,
    `${warn} degraded · ${unreported} not reported`,
  ]
}

/** Rail label → destination id. Built from the labels the CTA rule quotes, so
 *  a rename that misses one is a null CTA rather than a wrong jump. */
const DEST_BY_LABEL = {
  'Setup Wizard': 'setup',
  Library: 'library',
  Composer: 'composer',
  'Launch Gate': 'preflight',
  Runs: 'runs',
  'Proof & Export': 'proof',
}
