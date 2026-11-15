/**
 * navHistory.js — the console's own Back / Forward history (design C9).
 *
 * The browser's history cannot drive the header's Back / Forward: only
 * `navigate()` adds a browser entry, while `setParams()` rewrites the current
 * one with `history.replaceState`, so opening a run, switching a tab or opening
 * a card would be invisible to `history.back()`. The router keeps this list
 * instead, one entry per place the DC has been, and walks it.
 *
 * Pure: the router owns the state, these functions only compute the next list.
 */

/**
 * Params that say WHAT is open: a page's tab, the open run, scenario, card,
 * test case, use case or package, a component's setup step, Runs · Compare.
 * Every other param narrows a list (`q`, `status`, `page`, `plane`, `sort`,
 * …); changing one rewrites the current entry instead of adding a step, so
 * Back never replays a search box keystroke by keystroke.
 */
export const NAV_PARAMS = Object.freeze([
  'open', 'run', 'tab', 'compare', 'arm', 'from', 'ttp', 'uc', 'tc', 'tool', 'panel', 'step',
])

/** How many places Back can return through. */
export const HISTORY_LIMIT = 50

/** Two locations with the same key are the same place. */
export function navKey(loc) {
  if (!loc) return ''
  const params = loc.params || {}
  const parts = NAV_PARAMS
    .filter((k) => params[k] != null && params[k] !== '')
    .map((k) => `${k}=${params[k]}`)
  return [loc.destination, ...parts].join('|')
}

export function startHistory(loc) {
  return { entries: [loc], index: 0 }
}

/**
 * The history after the router has moved to `loc`.
 *
 * - The same place (only list filters changed): the current entry is
 *   rewritten, so Back returns to the list as it was last filtered.
 * - A place the DC came from a redirect stub: the stub is replaced, so Back
 *   never lands on a page whose only job is to forward again.
 * - `traversal`: the move came from outside the router (the browser's own
 *   Back / Forward, a plain `href` link). Landing on the entry just behind or
 *   ahead moves there instead of adding a step, so the two sets of buttons
 *   agree.
 * - Anywhere else: a new entry, and everything ahead of the current one is
 *   dropped, as in a browser.
 *
 * @param {{entries:Array, index:number}} history
 * @param {{destination:string, params:Object}} loc
 * @param {{isRedirect?:(id:string)=>boolean, traversal?:boolean}} [opts]
 */
export function recordLocation(history, loc, { isRedirect = () => false, traversal = false } = {}) {
  const { entries, index } = history
  const key = navKey(loc)
  const sameAt = (i) => i >= 0 && i < entries.length && navKey(entries[i]) === key
  const putAt = (list, i) => {
    const next = list.slice()
    next[i] = loc
    return { entries: next, index: i }
  }

  if (sameAt(index)) return putAt(entries, index)
  if (traversal) {
    if (sameAt(index - 1)) return putAt(entries, index - 1)
    if (sameAt(index + 1)) return putAt(entries, index + 1)
  }

  const current = entries[index]
  const kept = current && isRedirect(current.destination)
    ? entries.slice(0, index)
    : entries.slice(0, index + 1)
  if (kept.length && navKey(kept[kept.length - 1]) === key) return putAt(kept, kept.length - 1)

  const next = [...kept, loc].slice(-HISTORY_LIMIT)
  return { entries: next, index: next.length - 1 }
}

/** True when `delta` steps from the current entry lands on an entry. */
export function canStep(history, delta) {
  const i = history.index + delta
  return i >= 0 && i < history.entries.length
}
