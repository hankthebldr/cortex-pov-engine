/**
 * resultTabs.js — the tabs of Results, and where a run's old tab links go.
 *
 * Runs shows what was attempted; Results shows what Cortex saw. The five
 * detection views that used to be tabs of a run's page are tabs of Results
 * now (`#/proof?run=<id>&tab=<tab>`), so a link written against the old
 * address (`#/runs?run=<id>&tab=evidence`) forwards to the same tab there.
 * One list, read by both surfaces and by the router's history.
 */
import { openParam } from './openParam.js'

export const RESULT_TABS = Object.freeze([
  { id: 'detections', label: 'Detections' },
  { id: 'storyline', label: 'Storyline' },
  { id: 'techniques', label: 'Techniques' },
  { id: 'topology', label: 'Topology' },
  { id: 'causality', label: 'Causality' },
  { id: 'tenant', label: 'Tenant evidence' },
])

/** The run page's former tab ids that are Results tabs now. */
const MOVED = new Set(['evidence', 'storyline', 'techniques', 'topology', 'causality'])

/** The Results tab a `?tab=` value opens. `evidence` is the old name for Detections. */
export function resultTab(tab) {
  if (tab === 'evidence') return 'detections'
  return RESULT_TABS.some((t) => t.id === tab) ? tab : 'detections'
}

/**
 * Where a Runs URL that names a moved tab should land, or null.
 *
 * @param {Object} params  the Runs surface's URL params
 * @returns {{run: string, tab: string}|null}  the Results params
 */
export function runsTabRedirect(params) {
  const run = openParam(params, 'run')
  const tab = params && params.tab
  if (!run || !MOVED.has(tab)) return null
  return { run, tab }
}
