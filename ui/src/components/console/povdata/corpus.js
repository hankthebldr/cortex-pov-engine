/**
 * Composer lane vocabulary — the ingestion doors a simulation step launches
 * through, plus the LAUNCH and PROOF terminals, and the band geometry the
 * Lanes lens draws them at.
 *
 * VOCABULARY ONLY. This module used to be the design prototype's seed corpus
 * (scenarios, chains, workflows, run records) and the console rendered those
 * records as if they were this instance's state. Everything that described a
 * particular POV is gone; what remains is naming and geometry, which is the
 * same on every install. Guarded by povdata.test.js.
 */

/* Composer swimlanes — one band per SIGNAL PATH: the mechanism a step uses to
   produce its evidence (composerSignal.js), never the plane that observes it.
   The Composer, the execution timeline and the Runs topology all band by these
   keys through composerLanes.laneOf. A lane is derived from what the step
   does, so dragging a card cannot move it. The Engine band holds tenant
   content (correlation rules), never a step.
   Spec: docs/superpowers/specs/2026-11-10-composer-doors-design.md */
export const COMPOSER_LANES = [
  ['LAUNCH', 'LAUNCH', 'scope · tenant & agent', 76, 'rgba(255,255,255,.02)'],
  ['ENDPOINT', 'ENDPOINT RUNTIME', 'the beacon runs it on the host · the Cortex Agent sees the process', 104, 'rgba(0,192,232,.05)'],
  ['COLLECTOR', 'LOG INJECTION', 'records POSTed to a collector · Broker VM or HTTP collector', 104, 'rgba(199,170,71,.05)'],
  ['WIRE', 'LIVE TRAFFIC', 'a traffic generator · a sensor sees it on the wire, NGFW or SaaS', 104, 'rgba(110,194,214,.05)'],
  ['CLOUD', 'CLOUD CONTROL PLANE', 'a cloud or cluster API call · the provider\'s audit log carries it', 104, 'rgba(0,174,196,.05)'],
  ['NONE', 'NO ACTION', 'the step only logs or waits · it produces no signal itself', 104, 'rgba(255,255,255,.015)'],
  ['ENG', 'CORTEX ENGINE', 'tenant content · correlation rules, not steps', 104, 'rgba(250,88,45,.05)'],
  ['PROOF', 'PROOF', 'teardown & report', 76, 'rgba(255,255,255,.02)'],
]

export const LANE_BANDS = (() => {
  let y = 0
  return COMPOSER_LANES.map(([key, label, sub, h, fill]) => {
    const band = { key, label, sub, h, fill, top: y, nodeY: y + 5 }
    y += h
    return band
  })
})()
