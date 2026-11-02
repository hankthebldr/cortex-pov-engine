/**
 * immersiveReturn.js — where an immersive destination's Exit goes.
 *
 * An immersive destination (`immersive: true` in destinations.jsx) hides the
 * nav rail and the task tab strip, so the shell owes it a way back. "Back"
 * means the page the DC actually came from, with the params it had (an open
 * scenario, a filtered tab), not a fixed parent: opening the Composer from a
 * run and losing the run on Exit would be a small betrayal every time.
 *
 * Pure and registry-free on purpose: the shell records a target on every
 * non-immersive render and resolves it on Exit, and both halves are testable
 * without mounting the console.
 */

/**
 * The return target to remember after rendering `dest` with `params`.
 *
 * Only an ordinary page is a place to come back to. An immersive destination
 * is never recorded (Exit from one immersive page must not land in another),
 * and neither is a redirect stub, which only forwards and has no page of its
 * own — recording one would send Exit straight back through the redirect.
 *
 * @param {{destination:string, params:Object}|null} prev  the current record
 * @param {{id:string, immersive?:boolean, redirect?:boolean}|null} dest
 * @param {Object} [params]
 * @returns {{destination:string, params:Object}|null}  the new record (or `prev`)
 */
export function nextReturnTarget(prev, dest, params) {
  if (!dest || !dest.id || dest.immersive || dest.redirect) return prev
  return { destination: dest.id, params: { ...(params || {}) } }
}

/**
 * Where Exit from the immersive destination `dest` should navigate.
 *
 * The recorded page when there is one, else the destination's `navParent`
 * (a cold deep link has no previous page), else `fallback`.
 *
 * @param {{destination:string, params:Object}|null} record
 * @param {{id:string, navParent?:string}|null} dest
 * @param {string} fallback  the console's default destination
 * @returns {{destination:string, params:Object|null}}
 */
export function exitTarget(record, dest, fallback) {
  if (record && record.destination && record.destination !== dest?.id) {
    return { destination: record.destination, params: record.params || null }
  }
  return { destination: dest?.navParent || fallback, params: null }
}
