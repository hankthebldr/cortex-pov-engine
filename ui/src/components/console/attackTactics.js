/**
 * attackTactics — the ATT&CK Enterprise tactics in kill-chain order.
 *
 * ONE table. The Navigator export derives its shortname map from it, the run
 * Techniques tab draws its strip from it, so the two can never disagree on
 * what comes after Credential Access. `/api/mitre/coverage` is NOT the source
 * for per-technique placement: it assigns the scenario-level tactic to every
 * step technique, which would file a Discovery step under Credential Access.
 */
export const ATTACK_TACTICS = Object.freeze([
  { id: 'TA0043', name: 'Reconnaissance',       slug: 'reconnaissance' },
  { id: 'TA0042', name: 'Resource Development', slug: 'resource-development' },
  { id: 'TA0001', name: 'Initial Access',       slug: 'initial-access' },
  { id: 'TA0002', name: 'Execution',            slug: 'execution' },
  { id: 'TA0003', name: 'Persistence',          slug: 'persistence' },
  { id: 'TA0004', name: 'Privilege Escalation', slug: 'privilege-escalation' },
  { id: 'TA0005', name: 'Defense Evasion',      slug: 'defense-evasion' },
  { id: 'TA0006', name: 'Credential Access',    slug: 'credential-access' },
  { id: 'TA0007', name: 'Discovery',            slug: 'discovery' },
  { id: 'TA0008', name: 'Lateral Movement',     slug: 'lateral-movement' },
  { id: 'TA0009', name: 'Collection',           slug: 'collection' },
  { id: 'TA0011', name: 'Command and Control',  slug: 'command-and-control' },
  { id: 'TA0010', name: 'Exfiltration',         slug: 'exfiltration' },
  { id: 'TA0040', name: 'Impact',               slug: 'impact' },
])

export const TACTIC_IDS = ATTACK_TACTICS.map((t) => t.id)

const BY_ID = Object.fromEntries(ATTACK_TACTICS.map((t) => [t.id, t]))

/** Navigator-style slug for a tactic id; an unknown id lower-cases itself. */
export function tacticSlug(tacticId) {
  return BY_ID[tacticId]?.slug || String(tacticId || '').toLowerCase()
}

/** Display name for a tactic id; an unknown id is returned as given. */
export function tacticName(tacticId) {
  return BY_ID[tacticId]?.name || String(tacticId || '')
}

/** Kill-chain position (0-based) or -1 for an id that is not a tactic. */
export function tacticIndex(tacticId) {
  return TACTIC_IDS.indexOf(tacticId)
}

/** Parent technique id: T1003.008 → T1003; T1003 → T1003. */
export function parentTechnique(techniqueId) {
  const s = String(techniqueId || '').trim().toUpperCase()
  const dot = s.indexOf('.')
  return dot > 0 ? s.slice(0, dot) : s
}
