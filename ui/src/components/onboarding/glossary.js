/**
 * glossary — every CortexSim term defined exactly once.
 *
 * Referenced by key via <Term k="...">. Never inline a definition as a title=
 * string: the same term appears on several surfaces and duplicated prose drifts.
 */
export const GLOSSARY = {
  mttd: {
    term: 'MTTD',
    definition: 'Mean time to detect — observed_at minus executed_at for a seeded result. Real only when an observation was ingested; a manual checkbox is not an MTTD.',
  },
  abioc: {
    term: 'ABIOC',
    definition: 'A Palo Alto-authored, auto-tuned behavioural-ML detection carrying a causality chain. Not a static match wearing a label.',
  },
  bioc: {
    term: 'BIOC',
    definition: 'Behavioural indicator of compromise — a rule keyed on behaviour. A BIOC keyed on a filename tests the filename, not the behaviour.',
  },
  'cgo-anchor': {
    term: 'CGO anchor',
    definition: 'Causality Group Owner — the realistic initial-access process that owns a run\'s process chain, so the sensor sees one connected spine instead of a star rooted at the agent.',
  },
  'tenant-verified': {
    term: 'tenant-verified',
    definition: 'A run or assertion executed against a live Cortex tenant. It is currently 0 across this repo. Authored is not proven.',
  },
  'moat-tier': {
    term: 'moat tier',
    definition: 'Sales-motion differentiation tier carried by a scenario and its bound index row. Disagreements (S-13) are deliberate positioning calls, not defects.',
  },
  'xdm-substrate': {
    term: 'XDM substrate',
    definition: 'The modeling-rule normalization layer. Surfaced and exported, but counted informationally — it is not one of the six detection types.',
  },
  's-13': {
    term: 'S-13',
    definition: 'Loader warning: a scenario declares a moat tier that differs from its bound index row. 105 of these exist on purpose; silencing them is a regression.',
  },
  'detection-type': {
    term: 'detection type',
    definition: 'Exactly six values: BIOC, XQL, Analytics, Correlation, IOC, ABIOC.',
  },
  'push-bundle': {
    term: 'push bundle',
    definition: 'A self-contained script generated for offline execution. It runs on a clean Ubuntu 22.04 host with no SimCore dependency at run time.',
  },
  'identity-harness': {
    term: 'identity harness',
    definition: 'Runs each step as a service account (www-data, postgres, nobody) so the process causality chain looks like a real intrusion rather than one agent spawning everything.',
  },
}

export function lookup(key) {
  return Object.prototype.hasOwnProperty.call(GLOSSARY, key) ? GLOSSARY[key] : null
}
