/**
 * Detection planes, ingestion doors, and the coverage cross-tab.
 *
 * SEED CATALOG — carried over verbatim from the design prototype
 * (`POVengine Console.dc.html`, Claude Design project "Cortex POV Engine UI
 * Redesign"; see docs/design/DESIGN-SYNC.md).
 *
 * These are the authored corpus values the design was drawn against. Where
 * SimCore serves the same shape over the API, the surface prefers the API and
 * falls back to this; where it does not serve it yet, this IS the model, and
 * its shape is what the endpoint should eventually return. Do not "improve"
 * values here to make a screen look better — they are the numbers the design
 * conversation settled on, and several of them are quoted on more than one
 * surface precisely so those surfaces cannot disagree.
 */

/* Plane tints are the brand's own categorical coding colors — sub-brand hues
   and their documented tint/shade families. Authored at the 3:1 non-text
   floor, so they are used as fills, borders and dots. NEVER as text ink: at
   the 9px this console renders plane chips, the tint leaves the text channel
   entirely. Where a plane is named in text the label is --tx2 and the tint is
   the chip's 3px left border — the same rule ComposerCanvas already follows. */
export const PLANES = [
  ['EDR', 'Endpoint · XDR Agent', 22, '#00C0E8'],
  ['CDR', 'Cloud runtime', 28, '#00AEC4'],
  ['NDR', 'Network · firewall', 12, '#6EC2D6'],
  ['ITDR', 'Identity threat', 20, '#FFCB06'],
  ['ANALYTICS', 'Correlation engine', 23, '#FA582D'],
  ['CLOUD_APP', 'Cloud app security', 10, '#FDAC96'],
  ['TIM', 'Threat intel', 9, '#E5C148'],
  ['KOI', 'Agentic / supply chain', 8, '#D1755F'],
  ['AI_SPM', 'AI posture', 7, '#C7AA47'],
  ['ASM', 'Attack surface', 6, '#B6E1EE'],
  ['AI_ACCESS', 'AI access security', 6, '#40A557'],
  ['BROWSER', 'Prisma Browser', 6, '#ADD8B1'],
  ['AIRS', 'AI runtime security', 5, '#E14F28'],
  ['CSPM', 'Cloud posture', 5, '#238A4D'],
  ['EMAIL', 'Email · 3rd-party', 5, '#C7C7C7'],
  ['DLP', 'Data security', 5, '#A51B00'],
]

export const SOURCES = [
  ['CC', 'Cortex Cloud Connector', 'Cloud plane · agentless API ingest', 'Cloud'],
  ['AGT', 'Cortex Agent', 'Endpoint plane · XDR beacon telemetry', 'Agent'],
  ['DC', 'Cortex Data Connector', 'Data streams · shape-true log ingest', 'Data'],
  ['ENG', 'Cortex Engine', 'Analytics · BIOC/ABIOC/correlation', 'Engine'],
  ['BVM', 'Broker VM', 'Network plane · syslog + relay', 'Broker'],
  ['API', 'API / Marketplace', 'Content packs & integrations', 'API'],
]

export const MATRIX = {
  EDR: ['-', 'c', 'p', 'c', '-', 'p'],
  CDR: ['c', 'p', 'c', 'c', '-', 'p'],
  NDR: ['-', '-', 'c', 'c', 'c', 'p'],
  ITDR: ['-', 'p', 'c', 'c', 'p', 'c'],
  ANALYTICS: ['p', 'c', 'c', 'c', 'c', 'p'],
  CLOUD_APP: ['c', '-', 'c', 'p', '-', 'c'],
  TIM: ['-', '-', 'p', 'c', '-', 'c'],
  KOI: ['-', 'c', 'p', 'p', '-', 'g'],
  AI_SPM: ['c', '-', 'p', 'p', '-', 'g'],
  ASM: ['c', '-', 'g', 'c', '-', 'p'],
  AI_ACCESS: ['p', 'c', 'p', 'c', '-', 'g'],
  BROWSER: ['-', 'c', 'p', 'p', '-', 'g'],
  AIRS: ['p', '-', 'c', 'c', '-', 'p'],
  CSPM: ['c', '-', 'p', 'c', '-', 'p'],
  EMAIL: ['-', '-', 'c', 'p', 'c', 'c'],
  DLP: ['p', 'c', 'c', 'p', '-', 'g'],
}

export const CELL = {
  c: { label: 'FULL', bg: 'var(--pos-soft)', fg: 'var(--pos)', underline: 'var(--pos)' },
  p: { label: 'PART', bg: 'rgba(0,204,102,.05)', fg: '#ADD8B1', underline: 'rgba(0,204,102,.35)' },
  g: { label: 'GAP', bg: 'var(--ac-soft)', fg: 'var(--crit)', underline: 'var(--ac)' },
  '-': { label: 'n/a', bg: 'var(--s1)', fg: 'var(--tx2)', underline: 'transparent' },
}

// Detection objects are categorical, not a verdict — the verdict carries the color.
export const DET = {
  BIOC: { bg: 'var(--s3)', fg: '#00C0E8' },
  ABIOC: { bg: 'var(--s3)', fg: '#6EC2D6' },
  XQL: { bg: 'var(--s3)', fg: '#B6E1EE' },
  Analytics: { bg: 'var(--s3)', fg: '#B6E1EE' },
  Correlation: { bg: 'var(--s3)', fg: '#FFCB06' },
  IOC: { bg: 'var(--s3)', fg: 'var(--tx2)' },
  none: { bg: 'var(--ac-soft)', fg: 'var(--crit)' },
}

/** Tint for a plane code. Fill, border or dot only — never `color:`. */
export function planeTint(code) {
  const row = PLANES.find((p) => p[0] === code)
  return row ? row[3] : 'var(--bd2)'
}

/** Human label for a plane code. */
export function planeLabel(code) {
  const row = PLANES.find((p) => p[0] === code)
  return row ? row[1] : code
}

/** Full name of an ingestion door ('AGT' -> 'Cortex Agent'). */
export function sourceName(code) {
  const row = SOURCES.find((s) => s[0] === code)
  return row ? row[1] : code
}
