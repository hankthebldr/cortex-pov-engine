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
 * The rules mirror what core/engine/scenario_loader.py enforces on YAML: no
 * forward refs, no self refs, at most one root step. A refusal ALWAYS carries a
 * reason — a silently dropped drag is indistinguishable from a broken canvas,
 * and the DC has to be able to learn the constraint from the tool.
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
 * @param {Array<{id:string, causalityParent:?string}>} steps
 * @param {string} fromId  the proposed parent
 * @param {string} toId    the step that would take `fromId` as its parent
 * @returns {{ok: true} | {ok: false, code: string, reason: string}}
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
