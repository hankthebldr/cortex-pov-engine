/**
 * stitchContext.js — the pure model behind the Composer's Stitch Context panel.
 *
 * No React, no fetch, no randomness. Same convention (and the same reason) as
 * `composerDraft.js`, `composerLayout.js`, `runStatus.js` and `healthModel.js`:
 * a stitch spec that the panel, the canvas overlay and the serializer each
 * re-derive is a spec that can disagree with itself, and the direction that
 * disagreement takes is always "looks like it will stitch".
 *
 * WHAT THIS MODULE IS — AND IS NOT
 * --------------------------------
 * This is the AUTHORING side of the Stitch Context. It lists the nine entity
 * keys, groups them by NICE, knows which of the six directives may resolve each
 * key, and parses / emits / validates the flat `stitch_context` block that
 * `draftToApi` attaches to a draft. It NEVER resolves a value — turning
 * `{resolve: auto_ip}` into a concrete `10.x.y.z`, or `{resolve: canary_principal}`
 * into `token@cortexsim-canary.invalid`, is the RESOLVER's job
 * (`core/engine/stitch_context.py`, deterministic from the run id) and happens
 * server-side at launch. This module only decides what is a well-formed INTENT.
 *
 * THE HONESTY RULE
 * ----------------
 * The panel this feeds shows directive NAMES, never a fabricated resolved value
 * — a DC must not read a made-up 5-tuple on the design lens and quote it as the
 * value the run used. Real values surface only post-launch, from the run's
 * persisted `stitch_binding`. So every function here is over the SPEC; none
 * invents an address, a port, a UPN or a container id.
 *
 * The constants below MIRROR the backend byte-for-byte (`StitchContextSchema` /
 * `_STITCH_PLACEHOLDER_RE` in `core/engine/stitch_context.py`): a picker that
 * offered a key or directive the backend rejects would let a DC author a draft
 * that 422s at save, so the two lists must not drift.
 */

// ─── Frozen vocabularies (mirror the backend) ─────────────────────────────────

/**
 * The nine canonical entity keys, in order: the eight `causality_graph._entities`
 * keys plus the Phase-2 ninth (`cloud_resource`). This order is load-bearing —
 * the panel and the tests read it.
 */
export const ENTITY_KEYS = Object.freeze([
  'host',
  'src_ip',
  'dst_ip',
  'src_port',
  'dst_port',
  'protocol',
  'container_id',
  'account',
  'cloud_resource',
])

/** The six directives Phase 2 ships. `cloud_resource` has none (literal-only). */
export const DIRECTIVES = Object.freeze([
  'auto_ip',
  'auto_port',
  'auto_5tuple',
  'canary_principal',
  'from_agent',
  'auto_container_id',
])

/**
 * The NICE grouping the panel renders (spec §4.2). The DATA is flat — this only
 * shapes the UI. `container_id` is CLOUD (a stitch by entity, not by CGO) and
 * `host` is ENDPOINT; do not "tidy" them into the same group.
 */
export const NICE_GROUPS = Object.freeze([
  Object.freeze({ nice: 'Network', keys: Object.freeze(['src_ip', 'dst_ip', 'src_port', 'dst_port', 'protocol']) }),
  Object.freeze({ nice: 'Identity', keys: Object.freeze(['account']) }),
  Object.freeze({ nice: 'Cloud', keys: Object.freeze(['container_id', 'cloud_resource']) }),
  Object.freeze({ nice: 'Endpoint', keys: Object.freeze(['host']) }),
])

/**
 * Directive → the keys it may resolve. `auto_5tuple` is declarable on any of the
 * five tuple keys (and REJECTED on host/container_id/account/cloud_resource);
 * `cloud_resource` appears in no list, so it is literal-only.
 */
export const DIRECTIVE_COMPAT = Object.freeze({
  auto_ip: Object.freeze(['src_ip', 'dst_ip']),
  auto_port: Object.freeze(['src_port', 'dst_port']),
  auto_5tuple: Object.freeze(['src_ip', 'src_port', 'dst_ip', 'dst_port', 'protocol']),
  canary_principal: Object.freeze(['account']),
  from_agent: Object.freeze(['host', 'src_ip']),
  auto_container_id: Object.freeze(['container_id']),
})

/** Human labels for each key — the panel's row headers. */
export const KEY_LABEL = Object.freeze({
  host: 'Host',
  src_ip: 'Source IP',
  dst_ip: 'Destination IP',
  src_port: 'Source port',
  dst_port: 'Destination port',
  protocol: 'Protocol',
  container_id: 'Container ID',
  account: 'Account',
  cloud_resource: 'Cloud resource',
})

/** key → its NICE group name, derived from NICE_GROUPS so there is one source. */
export const KEY_NICE = Object.freeze(
  NICE_GROUPS.reduce((acc, g) => {
    for (const k of g.keys) acc[k] = g.nice
    return acc
  }, {}),
)

/**
 * The `{stitch:KEY}` placeholder grammar — mirrors the backend
 * `_STITCH_PLACEHOLDER_RE = re.compile(r'\{stitch:([a-z_]+)\}')`. Global so
 * `stitchPlaceholdersIn` can walk a whole command.
 */
export const STITCH_PLACEHOLDER_RE = /\{stitch:([a-z_]+)\}/g

/** The five keys that make up the network 5-tuple. */
const TUPLE_KEYS = Object.freeze(['src_ip', 'src_port', 'dst_ip', 'dst_port', 'protocol'])

// ─── Directive ↔ key compatibility ────────────────────────────────────────────

/**
 * The directives that may resolve `key`, in DIRECTIVES order — the inverse of
 * DIRECTIVE_COMPAT. Drives the resolve `<select>` options so an incompatible
 * directive is literally unpickable (`src_ip` → auto_ip/auto_5tuple/from_agent;
 * `cloud_resource` → []).
 */
export function directivesForKey(key) {
  return DIRECTIVES.filter((d) => (DIRECTIVE_COMPAT[d] || []).includes(key))
}

/** True when directive `d` is a real directive that may resolve `key`. */
function _directiveResolves(d, key) {
  return (DIRECTIVE_COMPAT[d] || []).includes(key)
}

// ─── Parse / emit ─────────────────────────────────────────────────────────────

/**
 * Normalise a raw `stitch_context` object (from `Scenario.to_dict()` or a draft
 * row) into the panel's model: `{[key]: {literal}|{resolve}}` or `null`.
 *
 * Lossless and NEVER throws — the panel must be able to render a saved-but-
 * invalid spec (e.g. one a hand-edit left with both keys) so the DC can SEE and
 * fix it; validation is a separate call. `null`/`{}`/non-object all normalise to
 * `null` so a context-less draft round-trips as "no context", never as an empty
 * object the backend would have to special-case.
 */
export function parseStitchContext(raw) {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null
  const model = {}
  for (const key of Object.keys(raw)) {
    const entry = raw[key]
    if (entry && typeof entry === 'object' && !Array.isArray(entry)) {
      // Copy verbatim (keep both keys if the source was invalid) so validation,
      // not parsing, is where the "both-or-neither" error surfaces.
      model[key] = { ...entry }
    } else {
      // A non-object entry is preserved so validation can name it, rather than
      // silently dropped (which would read as "not declared").
      model[key] = entry
    }
  }
  return Object.keys(model).length ? model : null
}

/** Is `entry` a well-formed single-key spec — exactly one of literal|resolve? */
function _isSingleKeyEntry(entry) {
  if (!entry || typeof entry !== 'object' || Array.isArray(entry)) return false
  const hasLiteral = Object.prototype.hasOwnProperty.call(entry, 'literal')
  const hasResolve = Object.prototype.hasOwnProperty.call(entry, 'resolve')
  return hasLiteral !== hasResolve // XOR
}

/**
 * Emit the flat snake-key `stitch_context` body `draftToApi` attaches, or `null`
 * when empty. Emits ONLY known keys carrying a concrete single-key entry — an
 * invalid or unknown entry is dropped here (validation is the panel's job) so a
 * context-less draft OMITS the field entirely and serializes byte-identically to
 * the 177-scenario corpus + Phase-1 drafts.
 */
export function emitStitchContext(model) {
  if (!model || typeof model !== 'object') return null
  const out = {}
  for (const key of ENTITY_KEYS) {
    const entry = model[key]
    if (!_isSingleKeyEntry(entry)) continue
    if (Object.prototype.hasOwnProperty.call(entry, 'resolve')) {
      // Only emit a resolve the backend would accept — an incompatible one would
      // 422 at save, so it is not "concrete" and is dropped.
      if (!_directiveResolves(entry.resolve, key)) continue
      out[key] = { resolve: entry.resolve }
    } else {
      out[key] = { literal: entry.literal }
    }
  }
  return Object.keys(out).length ? out : null
}

// ─── Immutable single-key set ─────────────────────────────────────────────────

/** Shallow entry equality for the no-op check. */
function _entriesEqual(a, b) {
  if (a === b) return true
  if (!a || !b || typeof a !== 'object' || typeof b !== 'object') return false
  const ak = Object.keys(a)
  const bk = Object.keys(b)
  if (ak.length !== bk.length) return false
  return ak.every((k) => a[k] === b[k])
}

/**
 * Set (or clear, with `entry === null`) exactly one key, immutably.
 *
 * Returns the SAME model reference when the edit is a no-op, the key is unknown,
 * or the entry is a `{resolve}` the directive cannot author for this key — the
 * "picker cannot author a rejected value" convention, mirroring
 * `setCausalityParent` refusing a forward ref. A literal entry accepts any
 * scalar verbatim.
 */
export function setEntity(model, key, entry) {
  if (!ENTITY_KEYS.includes(key)) return model
  const cur = model && typeof model === 'object' ? model : null

  // Clear.
  if (entry == null) {
    if (!cur || !Object.prototype.hasOwnProperty.call(cur, key)) return model
    const next = { ...cur }
    delete next[key]
    return Object.keys(next).length ? next : null
  }

  if (!_isSingleKeyEntry(entry)) return model // both-or-neither → picker refuses
  if (Object.prototype.hasOwnProperty.call(entry, 'resolve')
    && !_directiveResolves(entry.resolve, key)) {
    return model // incompatible directive → picker refuses
  }

  const clean = Object.prototype.hasOwnProperty.call(entry, 'resolve')
    ? { resolve: entry.resolve }
    : { literal: entry.literal }
  if (cur && _entriesEqual(cur[key], clean)) return model // no-op
  return { ...(cur || {}), [key]: clean }
}

// ─── Validation ───────────────────────────────────────────────────────────────

/**
 * What is wrong with this stitch spec, named — `validateDraft`-style.
 *
 * Catches the three authoring failures: an unknown key, an unknown or
 * incompatible directive, and the both-or-neither `{literal|resolve}` violation.
 * Also returns the planted keys and their NICE grouping so the panel footer can
 * render a count and the overlay knows what to draw.
 */
export function validateStitchContext(model) {
  const problems = []
  const planted = plantedKeys(model)

  if (model && typeof model === 'object') {
    for (const key of Object.keys(model)) {
      const entry = model[key]
      if (!ENTITY_KEYS.includes(key)) {
        problems.push(`Unknown entity key "${key}" — not one of the nine.`)
        continue
      }
      if (!entry || typeof entry !== 'object' || Array.isArray(entry)) {
        problems.push(`${KEY_LABEL[key]} must declare exactly one of a literal or a resolve directive.`)
        continue
      }
      const hasLiteral = Object.prototype.hasOwnProperty.call(entry, 'literal')
      const hasResolve = Object.prototype.hasOwnProperty.call(entry, 'resolve')
      if (hasLiteral && hasResolve) {
        problems.push(`${KEY_LABEL[key]} declares both a literal and a resolve — choose one.`)
        continue
      }
      if (!hasLiteral && !hasResolve) {
        problems.push(`${KEY_LABEL[key]} declares neither a literal nor a resolve value.`)
        continue
      }
      if (hasResolve) {
        const d = entry.resolve
        if (!DIRECTIVES.includes(d)) {
          problems.push(`${KEY_LABEL[key]} resolves with unknown directive "${d}".`)
        } else if (!_directiveResolves(d, key)) {
          problems.push(`Directive "${d}" cannot resolve ${KEY_LABEL[key]}.`)
        }
      }
    }
  }

  const byNice = {}
  for (const key of planted) {
    const nice = KEY_NICE[key] || 'Other'
    ;(byNice[nice] = byNice[nice] || []).push(key)
  }

  return { ok: problems.length === 0, problems, plantedKeys: planted, byNice }
}

// ─── Projections over the spec ────────────────────────────────────────────────

/** The keys this model declares, in ENTITY_KEYS order. */
export function plantedKeys(model) {
  if (!model || typeof model !== 'object') return []
  return ENTITY_KEYS.filter((k) => Object.prototype.hasOwnProperty.call(model, k))
}

/**
 * Is the 5-tuple fully declared? True when all five tuple keys are present, OR
 * any single one carries `auto_5tuple` (which fills all five coherently
 * server-side from one seed derivation).
 */
export function fiveTupleComplete(model) {
  if (!model || typeof model !== 'object') return false
  const anyFiveTuple = TUPLE_KEYS.some((k) => {
    const e = model[k]
    return e && typeof e === 'object' && e.resolve === 'auto_5tuple'
  })
  if (anyFiveTuple) return true
  return TUPLE_KEYS.every((k) => Object.prototype.hasOwnProperty.call(model, k))
}

// ─── The {stitch:*} grammar ───────────────────────────────────────────────────

/**
 * The `{stitch:KEY}` keys a command references, unique and in first-appearance
 * order. This is the HONEST consume set — read from the real command text, not
 * inferred — so the inspector can badge each as planted/unplanted against the
 * context and warn that an unplanted one is left verbatim at launch.
 */
export function stitchPlaceholdersIn(command) {
  if (typeof command !== 'string' || !command) return []
  const out = []
  const seen = new Set()
  // Fresh RegExp so the shared global's lastIndex is never a hidden dependency.
  const re = new RegExp(STITCH_PLACEHOLDER_RE.source, 'g')
  let m
  while ((m = re.exec(command)) !== null) {
    const key = m[1]
    if (!seen.has(key)) {
      seen.add(key)
      out.push(key)
    }
  }
  return out
}

/** The placeholder token for a key — what an insert button appends to a command. */
export function stitchInsertToken(key) {
  return `{stitch:${key}}`
}
