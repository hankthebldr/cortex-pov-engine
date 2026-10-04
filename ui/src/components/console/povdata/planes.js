/**
 * Detection plane vocabulary — code, human label and categorical tint.
 *
 * VOCABULARY ONLY. The per-plane scenario counts, the ingestion-door list and
 * the planes × doors coverage matrix that used to live here were the design
 * prototype's seed data, identical on every install, and were removed along
 * with the surfaces that rendered them as measurements. Guarded by
 * povdata.test.js.
 */

/* Plane tints are the brand's own categorical coding colors — sub-brand hues
   and their documented tint/shade families. Authored at the 3:1 non-text
   floor, so they are used as fills, borders and dots. NEVER as text ink: at
   the 9px this console renders plane chips, the tint leaves the text channel
   entirely. Where a plane is named in text the label is --tx2 and the tint is
   the chip's 3px left border — the same rule ComposerCanvas already follows. */
export const PLANES = [
  ['EDR', 'Endpoint · XDR Agent', '#00C0E8'],
  ['CDR', 'Cloud runtime', '#00AEC4'],
  ['NDR', 'Network · firewall', '#6EC2D6'],
  ['ITDR', 'Identity threat', '#FFCB06'],
  ['ANALYTICS', 'Correlation engine', '#FA582D'],
  ['CLOUD_APP', 'Cloud app security', '#FDAC96'],
  ['TIM', 'Threat intel', '#E5C148'],
  ['KOI', 'Agentic / supply chain', '#D1755F'],
  ['AI_SPM', 'AI posture', '#C7AA47'],
  ['ASM', 'Attack surface', '#B6E1EE'],
  ['AI_ACCESS', 'AI access security', '#40A557'],
  ['BROWSER', 'Prisma Browser', '#ADD8B1'],
  ['AIRS', 'AI runtime security', '#E14F28'],
  ['CSPM', 'Cloud posture', '#238A4D'],
  ['EMAIL', 'Email · 3rd-party', '#C7C7C7'],
  ['DLP', 'Data security', '#A51B00'],
]

/** Tint for a plane code. Fill, border or dot only — never `color:`. */
export function planeTint(code) {
  const row = PLANES.find((p) => p[0] === code)
  return row ? row[2] : 'var(--bd2)'
}

/** Human label for a plane code. */
export function planeLabel(code) {
  const row = PLANES.find((p) => p[0] === code)
  return row ? row[1] : code
}
