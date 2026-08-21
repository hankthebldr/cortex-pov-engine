/**
 * supplyState.js — the ONE mapping from (adapter, shelf) to a supply descriptor.
 *
 * Pure. No React, no fetch. Same convention as `runStatus.js`, and for the same
 * reason: presentation that branches on raw fields in five components drifts in
 * five directions, and the direction it drifts in here is "green".
 *
 * THE RULE NO COMPONENT MAY BREAK
 * -------------------------------
 * No component branches on `adapter.tier` or on a raw state string. Everything
 * goes through `supplyOf()`, and `UNKNOWN` is a first-class fifth state that is
 * NEVER coerced into STAGED or UNSTAGED.
 *
 * Why UNKNOWN has to exist: an older SimCore has no `/api/shelf/*` and its K8s
 * inventory carries no `declared[]`. Guessing "unstaged" sends a DC to re-stage
 * something already there — annoying. Guessing "staged" tells them the target
 * needs no egress when it does — and the step then runs WITHOUT its tool,
 * produces no detection, and the absent detection reads in a POV report as
 * "Cortex missed it". That is the manufactured false negative this whole
 * subsystem exists to prevent, so the honest third answer is mandatory.
 */

export const SUPPLY = {
  STAGED: 'staged',
  UNSTAGED: 'unstaged',
  RUNTIME_FETCH: 'runtime_fetch',
  NOT_APPLICABLE: 'not_applicable',
  UNKNOWN: 'unknown',
}

/** Label text. Colour is never the only signal — a projector flattens hue and
 *  WCAG does not accept it either, so every chip renders this string. */
export const SUPPLY_LABEL = {
  [SUPPLY.STAGED]: 'STAGED',
  [SUPPLY.UNSTAGED]: 'NOT STAGED',
  [SUPPLY.RUNTIME_FETCH]: 'FETCHES AT RUN TIME',
  [SUPPLY.NOT_APPLICABLE]: 'N/A',
  [SUPPLY.UNKNOWN]: 'UNKNOWN',
}

/**
 * `unstaged` and `runtime_fetch` deliberately SHARE a colour. Operationally
 * they are the same risk — the target reaches the public internet mid-run.
 * They differ only in what the DC can DO about it, and that difference belongs
 * in the call to action, not in a second amber the operator has to decode.
 */
export const SUPPLY_CHIP = {
  [SUPPLY.STAGED]: 'chip chip--detected',
  [SUPPLY.UNSTAGED]: 'chip chip--pending',
  [SUPPLY.RUNTIME_FETCH]: 'chip chip--pending',
  [SUPPLY.NOT_APPLICABLE]: 'chip',
  [SUPPLY.UNKNOWN]: 'chip',
}

const TIER_REASON = {
  1: 'tier 1 — ships in-tree with SimCore',
  2: 'tier 2 — ships as a git submodule',
  3: 'tier 3 — provisioned by an IaC module onto the lab host',
  5: 'tier 5 — reference only, CortexSim never installs it',
}

/** Hostname of a URL, or null. Never throws — a malformed url in a pack must
 *  not blank a card. */
export function hostOf(url) {
  if (!url || typeof url !== 'string') return null
  try {
    return new URL(url).hostname || null
  } catch {
    const m = /^[a-z]+:\/\/([^/:]+)/i.exec(url)
    return m ? m[1] : null
  }
}

/**
 * Normalise the two shelf responses into the shape `supplyOf` consumes.
 *
 * `declaredAvailable` is tracked separately from `available` on purpose: a
 * legacy SimCore answers `/api/k8s/payloads` (so the shelf IS reachable) but
 * returns no `declared[]`, and without that array we cannot tell an unstaged
 * declared artifact from a pack that never declared one. That is exactly an
 * UNKNOWN, not a default.
 */
export function normalizeShelf(payloadsBody, artifactsBody) {
  if (!payloadsBody) {
    return {
      available: false, declaredAvailable: false, payloads: [], declared: [],
      stagedNames: new Set(), byAdapter: {}, unbound: [], distDir: null,
      auth: null, writable: null, via: null,
    }
  }
  const payloads = Array.isArray(payloadsBody.payloads) ? payloadsBody.payloads : []
  const declaredRaw = Array.isArray(payloadsBody.declared) ? payloadsBody.declared : null
  // /artifacts carries pin_type/pin_ref/used_by that /payloads does not; when
  // it is reachable it WINS, because a richer provenance row is strictly better
  // and the two are derived from the same packs.
  const artifacts = artifactsBody && Array.isArray(artifactsBody.artifacts)
    ? artifactsBody.artifacts
    : null
  const declared = artifacts || declaredRaw || []
  const stagedNames = new Set(payloads.map((p) => p.name))

  const byAdapter = {}
  for (const d of declared) {
    if (!d || !d.adapter_id) continue
    byAdapter[d.adapter_id] = {
      declared: d,
      staged: payloads.find((p) => p.name === d.name) || null,
    }
  }

  // Staged bytes that NO pack claims. The shelf's own `unbound[]` migration
  // bucket, seen from the console: they can be served, but nothing can compose
  // them because no adapter_ref resolves to them. Silently listing them as
  // "2 staged" would read as coverage that does not exist.
  const claimedNames = new Set(declared.map((d) => d && d.name).filter(Boolean))
  const unbound = payloads.filter((p) => !p.adapter_id && !claimedNames.has(p.name))

  return {
    available: true,
    declaredAvailable: artifacts !== null || declaredRaw !== null,
    payloads,
    declared,
    stagedNames,
    byAdapter,
    unbound,
    distDir: payloadsBody.dist_dir || null,
    auth: payloadsBody.auth || null,
    writable: typeof payloadsBody.writable === 'boolean' ? payloadsBody.writable : null,
    via: payloadsBody._via || null,
  }
}

/**
 * Resolve one adapter's supply descriptor.
 *
 * @param {object} adapter  a row from GET /api/tools/adapters (or the full pack)
 * @param {object} shelf    normalizeShelf() output
 * @returns {{state, label, chipClass, egress, egressLabel, actionable, …}}
 */
export function supplyOf(adapter, shelf) {
  const base = {
    adapterId: adapter?.adapter_id || null,
    payloadName: null,
    sha256: null,
    pinned: null,
    pinType: null,
    license: null,
    sourceUrl: null,
    stagedAt: null,
    sizeBytes: null,
    stagePath: null,
    actionable: false,
  }

  if (!adapter) return { ...base, ...describe(SUPPLY.UNKNOWN), egress: 'unknown', egressLabel: 'egress: unknown' }

  // The shelf could not be read at all → UNKNOWN for every adapter, including
  // the tier-2 ones we could technically answer. Mixing a confident N/A with an
  // UNKNOWN from the same failed call would imply the failure was partial.
  if (!shelf || !shelf.available) {
    return {
      ...base,
      ...describe(SUPPLY.UNKNOWN),
      egress: 'unknown',
      egressLabel: 'egress: unknown — this SimCore does not report shelf state',
    }
  }

  if (adapter.tier !== 4) {
    return {
      ...base,
      ...describe(SUPPLY.NOT_APPLICABLE),
      egress: 'none',
      egressLabel: `egress: none — ${TIER_REASON[adapter.tier] || 'not a runtime-fetched tool'}`,
    }
  }

  const bound = shelf.byAdapter[adapter.adapter_id] || null

  if (!bound) {
    // Tier 4 with no artifact declared on its pack. If the response could not
    // carry a declaration at all, we do not know which of the two it is.
    if (!shelf.declaredAvailable) {
      return {
        ...base,
        ...describe(SUPPLY.UNKNOWN),
        egress: 'unknown',
        egressLabel: 'egress: unknown — this SimCore does not report declared artifacts',
      }
    }
    return {
      ...base,
      ...describe(SUPPLY.RUNTIME_FETCH),
      egress: 'target_host',
      egressLabel: 'egress: target → public internet, at run time (no artifact declared on this pack)',
    }
  }

  const d = bound.declared
  const s = bound.staged
  const host = hostOf(d.url)

  if (s) {
    return {
      ...base,
      ...describe(SUPPLY.STAGED),
      egress: 'none',
      egressLabel: 'egress: none — served from this SimCore',
      payloadName: d.name,
      // The digest is recomputed from the BYTES by the inventory endpoint, so
      // this is what a consumer would actually verify against.
      sha256: s.sha256 || null,
      sizeBytes: typeof s.size_bytes === 'number' ? s.size_bytes : null,
      pinned: typeof d.pinned === 'boolean' ? d.pinned : (s.pinned ?? null),
      pinType: d.pin_type || null,
      pinRef: d.pin_ref || null,
      license: d.license || s.license || null,
      packLicense: adapter.license || null,
      sourceUrl: d.url || s.source_url || null,
      stagedAt: s.modified_at || null,
      stagePath: d.stage_path || null,
      actionable: false,
    }
  }

  return {
    ...base,
    ...describe(SUPPLY.UNSTAGED),
    egress: 'target_host',
    egressLabel: host
      ? `egress: target → ${host}, at run time`
      : 'egress: target → public internet, at run time',
    payloadName: d.name,
    sha256: d.sha256 || null,
    pinned: typeof d.pinned === 'boolean' ? d.pinned : null,
    pinType: d.pin_type || null,
    pinRef: d.pin_ref || null,
    license: d.license || null,
    packLicense: adapter.license || null,
    sourceUrl: d.url || null,
    stagePath: d.stage_path || null,
    // Staging is only offerable when the pack declares WHERE the bytes come
    // from. We never parse a URL out of install.runtime_install_command — a
    // shell string that silently yields the wrong URL is how staging becomes a
    // no-op that still reports success.
    actionable: Boolean(d.url),
  }
}

function describe(state) {
  return { state, label: SUPPLY_LABEL[state], chipClass: SUPPLY_CHIP[state] }
}

/**
 * Scenario ids out of a declared artifact's `used_by` annotation.
 *
 * TRAP, FOUND ON A LIVE SIMCORE: `used_by` is a comma-joined list of scenario
 * ids OR, when nothing references the adapter, the literal prose
 * `"(no scenario references this adapter yet)"`. A naive `split(',')` offers
 * that sentence as a scenario id, and the composer then posts it to
 * `/api/shelf/compose` and 404s. Anything that is not shaped like a scenario id
 * is dropped, so an empty list means "none", never a fabricated one.
 */
export function scenarioIdsFrom(usedBy) {
  return String(usedBy || '')
    .split(',')
    .map((s) => s.trim())
    .filter((s) => /^[A-Z][A-Z0-9]*(-[A-Z0-9]+)+$/.test(s))
}

/** Tally supply states across a catalog. Used by the banner and the nav badge. */
export function shelfCounts(adapters = [], shelf) {
  const counts = {
    [SUPPLY.STAGED]: 0,
    [SUPPLY.UNSTAGED]: 0,
    [SUPPLY.RUNTIME_FETCH]: 0,
    [SUPPLY.NOT_APPLICABLE]: 0,
    [SUPPLY.UNKNOWN]: 0,
  }
  for (const a of adapters) counts[supplyOf(a, shelf).state] += 1
  return {
    staged: counts[SUPPLY.STAGED],
    unstaged: counts[SUPPLY.UNSTAGED],
    runtime_fetch: counts[SUPPLY.RUNTIME_FETCH],
    not_applicable: counts[SUPPLY.NOT_APPLICABLE],
    unknown: counts[SUPPLY.UNKNOWN],
    /** Everything that makes the TARGET reach the internet mid-run. The single
     *  number a DC needs before walking into a default-deny network. */
    target_egress: counts[SUPPLY.UNSTAGED] + counts[SUPPLY.RUNTIME_FETCH],
    total: adapters.length,
  }
}

/** Bytes → "1,106,683 bytes". Thousands separators because a DC reads this
 *  aloud to a customer's endpoint team. */
export function formatBytes(n) {
  if (typeof n !== 'number' || !Number.isFinite(n)) return '—'
  return `${n.toLocaleString('en-US')} bytes`
}

/** First 12 hex + ellipsis, the density this console uses everywhere else. */
export function shortDigest(hex) {
  return typeof hex === 'string' && hex.length > 12 ? `${hex.slice(0, 12)}…` : (hex || '—')
}
