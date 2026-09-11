/**
 * composerSpine — the connection-legality predicate for the Composer canvas.
 *
 * WHY THIS IS A SEPARATE PURE MODULE
 * ----------------------------------
 * The 2026-09-04 spec's D2 fixed the topology as a spine-constrained causality
 * tree (one root, acyclic) and enforced it by withholding the drag affordance.
 * The 2026-09-08 amendment adds the affordance and moves enforcement HERE, to
 * connect time. Keeping it pure means the rules are provable without mounting a
 * canvas, and the canvas cannot drift from them.
 *
 * TWO INVARIANTS, NOT ONE — READ BEFORE TRUSTING `canConnect` ALONE
 * -------------------------------------------------------------------
 * `core/engine/scenario_loader.py:394` rejects a scenario on TWO independent
 * grounds: the causality PARENT GRAPH must be acyclic with a single root, AND
 * a parent must sit at an EARLIER index in `steps[]` than its child
 * (`if index_of[parent] >= i: raise` — no forward refs by array position).
 * `canConnect` below proves only the FIRST of those — it walks parent
 * pointers (`ancestorsOf`) and has no notion of array position at all, so an
 * edge it approves can still be one the loader would reject if drawing it
 * out of visual order would make the array illegal (e.g. re-parenting a step
 * onto a sibling that currently sits AFTER it). This is NOT a gap in
 * `canConnect`'s contract — the two invariants are independently checkable,
 * and array order is squarely `steps[]`'s own concern, not the causality
 * graph's. The ordering invariant is maintained on the OTHER side of the
 * connect: `topologicallySortSteps` (below) re-sorts `steps[]` immediately
 * after a canvas-approved edge is applied (`ComposerView.jsx`'s
 * `handleConnectSteps`, direct-manipulation Task 10), so the two invariants
 * stay reconciled without ever asking `canConnect` to reason about position.
 *
 * A refusal from `canConnect` ALWAYS carries a reason — a silently dropped
 * drag is indistinguishable from a broken canvas, and the DC has to be able
 * to learn the constraint from the tool. The codes it can return are
 * `SELF_REF` · `CYCLE` · `SECOND_ROOT`. There is deliberately no
 * `ALREADY_PARENTED` code: re-asserting a step's EXISTING parent is an
 * idempotent `{ok: true}` no-op (see the early-return below), and re-parenting
 * an already-parented step onto a DIFFERENT, valid parent is the ordinary
 * case this predicate exists to allow — there is no distinct illegal state
 * "already having a parent" would name. An earlier draft of this interface
 * documented that code; it was never emitted anywhere and has been removed
 * rather than left as a code a caller could dead-branch on.
 */

/** Walk ancestors of `id`, returning the set of step ids above it. */
function ancestorsOf(steps, id) {
  const byId = new Map(steps.map((s) => [s.id, s]))
  const seen = new Set()
  let cur = byId.get(id)
  while (cur && cur.causalityParent && !seen.has(cur.causalityParent)) {
    seen.add(cur.causalityParent)
    cur = byId.get(cur.causalityParent)
  }
  return seen
}

/**
 * Can `toId` take `fromId` as its causality parent?
 *
 * Checks the causality PARENT GRAPH only (acyclic, single root) — NOT array
 * position; see the module header for why that is a second, independent
 * invariant maintained elsewhere (`topologicallySortSteps`, below).
 *
 * @param {Array<{id:string, causalityParent:?string}>} steps
 * @param {string} fromId  the proposed parent
 * @param {string} toId    the step that would take `fromId` as its parent
 * @returns {{ok: true} | {ok: false, code: 'SELF_REF'|'CYCLE'|'SECOND_ROOT', reason: string}}
 */
export function canConnect(steps, fromId, toId) {
  if (fromId === toId) {
    return {
      ok: false,
      code: 'SELF_REF',
      reason: 'A step cannot descend from itself — the causality spine is acyclic.',
    }
  }

  // A step's existing parent being re-asserted is a no-op, not a violation.
  const to = steps.find((s) => s.id === toId)
  if (to && to.causalityParent === fromId) return { ok: true }

  if (ancestorsOf(steps, fromId).has(toId)) {
    return {
      ok: false,
      code: 'CYCLE',
      reason: `${toId} already descends from ${fromId}; this edge would close a cycle.`,
    }
  }

  // Parenting the only root leaves nothing linking from the CGO anchor.
  const roots = steps.filter((s) => !s.causalityParent)
  if (roots.length === 1 && roots[0].id === toId) {
    return {
      ok: false,
      code: 'SECOND_ROOT',
      reason: `${toId} is the chain's only root step — it links from the CGO anchor and cannot take a parent.`,
    }
  }

  return { ok: true }
}

/**
 * Topologically re-sort `steps` so every step's `causalityParent` (when set)
 * sits at an EARLIER array index than the step itself — the array-order half
 * of the spine invariant (see the module header); the one `canConnect` above
 * does not check.
 *
 * The intended caller is `ComposerView.jsx`'s `handleConnectSteps`: apply a
 * `canConnect`-approved edge with `setCausalityParent(..., { skipOrderCheck:
 * true })`, then pass the result through here. Rather than have
 * `setCausalityParent`'s own forward-ref guard refuse an edge `canConnect`
 * already approved — a refusal with no visible cause, since the canvas
 * already said yes — the array is repaired to fit the edge instead of the
 * edge being rejected to fit the array.
 *
 * Stable Kahn's-algorithm sort: a step is emitted once its parent has
 * already been emitted; a step with no parent, or whose declared parent is
 * not present in `steps` (a dangling reference — data that is already
 * inconsistent, which a genuine cycle can never produce because `canConnect`
 * refuses one at connect time), is immediately eligible. Ties are broken by
 * original array order, so an array that already satisfies the invariant is
 * returned UNCHANGED (the same reference) — the same no-op-returns-same-ref
 * contract every other op in `composerDraft.js` follows.
 *
 * @param {Array<{id:string, causalityParent:?string}>} steps
 * @returns {Array} `steps`, re-sorted if (and only if) it needed it
 */
export function topologicallySortSteps(steps) {
  const byId = new Map(steps.map((s) => [s.id, s]))
  const placed = new Set()
  const out = []
  let remaining = steps

  while (remaining.length) {
    const ready = remaining.filter(
      (s) => !s.causalityParent || placed.has(s.causalityParent) || !byId.has(s.causalityParent),
    )
    // A genuine cycle cannot reach here (canConnect refuses one at connect
    // time) — but rather than drop steps if the data is already
    // inconsistent, emit whatever is left in its existing order.
    const batch = ready.length ? ready : remaining
    for (const s of batch) {
      out.push(s)
      placed.add(s.id)
    }
    remaining = remaining.filter((s) => !placed.has(s.id))
  }

  const unchanged = out.length === steps.length && out.every((s, i) => s === steps[i])
  return unchanged ? steps : out
}
