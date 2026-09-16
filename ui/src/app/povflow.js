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
 *                      surface does rather than restating literals:
 *                      { gate:{total,pass,warn}, components:{total,ready,partial,missing},
 *                        scenarioCount, chainSteps, runStep }
 * @returns {{ phase:number, title:string, sub:string, cta:string, ctaDest:string|null }}
 */
export function flowFor(destination, ctx = {}) {
  const gate = ctx.gate || { total: 0, pass: 0, warn: 0 }
  const comp = ctx.components || { total: 0, ready: 0, partial: 0, missing: 0 }

  const MAP = {
    overview: [-1, 'POVengine · overview', 'what it does, how it stays honest', 'Setup Wizard'],
    setup: [0, 'Setup wizard', 'detections → targets → scope → requirements → goals', 'Library'],
    scope: [0, `${comp.total} components`,
      `${comp.ready} ready · ${comp.partial} partial · ${comp.missing} missing`, 'Library'],
    tenants: [0, '1 tenant bound', 'read-only · 6 of 9 datasets readable', 'Library'],
    agents: [0, `${ctx.agentCount ?? 5} beacons enrolled`, '3 online · identity harness on 3', 'Library'],

    library: [1, ctx.armed ? `${ctx.armed} armed` : `${ctx.scenarioCount ?? 0} scenarios`,
      '7 steps · 11 expected detections', 'Composer'],
    cli: [1, '4 CLI items authored', '2 ready · 2 draft · all digest-pinned', 'Composer'],
    adapters: [1, '8 packages staged', '48 exemption-declared · 0 undeclared', 'Composer'],
    streams: [1, '21 of 34 streams mapped', '7 gaps · relayed to the Broker VM', 'Composer'],
    ttps: [1, '175 TTP cards bound', '1,797 detection objects resolve', 'Composer'],
    uctc: [1, '34 test cases in scope', '12 use cases · 140 assertion-shaped', 'Composer'],
    composer: [1, `${ctx.chainSteps ?? 6} steps on the spine`,
      'one lineage · 1 step with no expected detection', 'Launch Gate'],

    preflight: [2, `${gate.pass} of ${gate.total} checks pass`,
      `${gate.warn} warn · 0 blocking`, 'Runs'],

    runs: [4, ctx.runStep || 'No run in flight', '6 of 11 detections observed', 'Proof & Export'],

    validation: [5, '0 of 11 tenant-verified', '6 awaiting probe · 2 not present', 'Proof & Export'],
    coverage: [5, 'Coverage assembled', '16 planes × 6 doors · 12 gaps', 'Proof & Export'],
    proof: [5, 'Report ready', '6 observed · 5 pending · 0 missed', null],
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
