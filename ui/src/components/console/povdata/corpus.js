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

/* Composer swimlanes — one band per area the simulation launches through.
   Keyed to LANE_CATALOG so the Composer and the Runs topology agree on doors:
   where a node sits on the design canvas IS the door it launches through, and
   dragging it into another band retargets it. */
export const COMPOSER_LANES = [
  ['LAUNCH', 'LAUNCH', 'scope · tenant & agent', 76, 'rgba(255,255,255,.02)'],
  ['AGT', 'ENDPOINT', 'Cortex Agent', 104, 'rgba(0,192,232,.05)'],
  ['CC', 'CLOUD', 'Cortex Cloud Connector', 104, 'rgba(0,174,196,.05)'],
  ['BVM', 'NETWORK', 'Broker VM relay', 104, 'rgba(110,194,214,.05)'],
  ['DC', 'DATA STREAMS', 'Cortex Data Connector', 104, 'rgba(199,170,71,.05)'],
  ['ENG', 'ANALYTICS', 'Cortex Engine', 104, 'rgba(250,88,45,.05)'],
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
