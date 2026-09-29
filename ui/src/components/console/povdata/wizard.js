/**
 * wizard.js — the setup wizard's model.
 *
 * THE WHOLE POINT: THE REQUIREMENTS ARE DERIVED, NOT LISTED.
 * This is a five-step chain — Detections → Targets → Refine scope →
 * Requirements → Goals — and each step's output is the next step's input.
 * Pick Correlation and you inherit "correlation content installed in the
 * tenant" (blocked) and "API scope covers the correlations dataset" (warn).
 * Pick network targets and you inherit the missing Broker VM relay rule.
 * Deselect them and those requirements disappear. A fixed readiness checklist
 * cannot do that, and a fixed checklist is what this replaced: it listed
 * things this POV did not need and stayed silent about things it did.
 *
 * STEP 3 IS SCOPE REFINEMENT, NOT POLICY MANAGEMENT.
 * An earlier version of step 3 was an agent/NGFW policy editor. That was
 * managing Cortex, not scope — POVengine does not own the customer's policy
 * and should never imply it can change it. What it CAN do is record which
 * disposition was chosen for each constraint the environment already has, so
 * the readout can be read honestly later:
 *     prove it     → becomes a prerequisite on the customer; claim stays
 *     work around  → keeps the claim by another path; substitution is stated
 *     drop it      → the claim leaves the POV, reported as not-exercised
 * See `scope.js` for the constraints themselves.
 *
 * SEED CATALOG — see the note in the sibling modules.
 */

/** Step 1. What kinds of detection must this POV prove? */
export function detectionTypes() {
  return [
    ['BIOC', 'Behavioural IOC', 'Fires on a behaviour the agent sees happen on the host.', ['AGENT'], '742 objects'],
    ['ABIOC', 'Analytics BIOC', 'Engine-scored anomaly layered on agent telemetry.', ['AGENT', 'ENGINE'], '196 objects'],
    ['XQL', 'XQL query detection', 'A query you can run and show the customer in their own tenant.', ['AGENT', 'CLOUD', 'DATA'], '611 objects'],
    ['Correlation', 'Correlation rule', 'Joins two planes into a single story — the hardest thing to prove.', ['ENGINE'], '184 objects'],
    ['IOC', 'Indicator match', 'Atomic indicator from intel or a marketplace pack.', ['API'], '64 objects'],
  ].map(([type, name, blurb, needs, count]) => ({ type, name, blurb, needs, count }))
}

/** Step 2. What is being targeted? */
export function targetTypes() {
  return [
    ['host', 'Endpoint hosts', 'Cortex Agent · endpoint plane', '4 enrolled · 2 online', '#00C0E8'],
    ['cloud', 'Cloud environments', 'Cortex Cloud Connector · cloud plane', '3 environments · 2 connectors live', '#00AEC4'],
    ['net', 'Network / Broker VM', 'Broker VM relay · network plane', '3 collectors · no relay rule', '#6EC2D6'],
    ['stream', 'Third-party data streams', 'Cortex Data Connector · data plane', '34 shapes · 21 covered', '#C7AA47'],
  ].map(([key, name, door, inScope, tint]) => ({ key, name, door, inScope, tint }))
}

/** Which selection in step 2 each ingestion door depends on. */
export const TARGET_FOR = { AGT: 'host', CC: 'cloud', BVM: 'net', DC: 'stream' }

const TARGET_LABEL = {
  AGT: 'endpoint hosts', CC: 'cloud environments',
  BVM: 'network / Broker VM', DC: 'data streams',
}

/** The word a requirement uses to say which target class produced it. */
const BECAUSE_FOR = {
  AGT: 'endpoint', CC: 'cloud', BVM: 'network', DC: 'data streams',
}

/** Step 5. The goals this POV is trying to close. */
export const GOAL_DEFS = [
  ['GOAL-01', 'Credential theft is caught on the endpoint before anything leaves', ['AGT'], ['BIOC', 'ABIOC']],
  ['GOAL-02', 'A stolen key used in the cloud stitches back to the endpoint that took it', ['AGT', 'CC'], ['Correlation']],
  ['GOAL-03', 'Egress is observed on the network plane, not inferred from the host', ['BVM'], ['XQL']],
  ['GOAL-04', 'A third-party stream lands shape-true and scores like native telemetry', ['BVM', 'DC'], ['XQL']],
]

/**
 * Score every goal against the requirements the first three steps produced.
 *
 * The four verdicts are deliberately different claims:
 *   OUT OF SCOPE — you did not select the target class or detection type it
 *                  needs. Not a failure; it says what to add to put it back in
 *                  play.
 *   BLOCKED      — a requirement behind it cannot be met. Names the reason.
 *   PARTIAL      — a requirement behind it is a warn.
 *   PROVABLE     — every requirement behind it is met; it can close on the
 *                  first run.
 *
 * @param {string[]} dets     selected detection types
 * @param {string[]} targets  selected target-class keys
 * @param {Array}    reqs     rows from `requirements()`
 */
export function scoreGoals(dets, targets, reqs) {
  return GOAL_DEFS.map(([id, label, lanes, needDets]) => {
    const missingTarget = lanes.filter((l) => !targets.includes(TARGET_FOR[l]))
    const missingDet = needDets.filter((d) => !dets.includes(d))
    const relevant = reqs.filter((r) =>
      lanes.some((l) => String(r.because).includes(BECAUSE_FOR[l])) || needDets.includes(r.because))

    if (missingTarget.length || missingDet.length) {
      return {
        id, label, state: 'out of scope', tone: 'dim',
        note: missingTarget.length
          ? `Not selected: ${missingTarget.map((l) => TARGET_LABEL[l]).join(', ')}. Add it in step 2 to put this goal back in play.`
          : `Needs ${missingDet.join(' and ')} selected in step 1.`,
      }
    }
    const blocked = relevant.find((r) => r.state === 'blocked')
    if (blocked) return { id, label, state: 'blocked', tone: 'crit', note: blocked.detail }
    const warn = relevant.find((r) => r.state === 'warn')
    if (warn) return { id, label, state: 'partial', tone: 'warn', note: warn.detail }
    return {
      id, label, state: 'provable', tone: 'pos',
      note: 'Every requirement behind this goal is met. It can close on the first run.',
    }
  })
}
