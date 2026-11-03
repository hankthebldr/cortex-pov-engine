/**
 * The one deep-link vocabulary: `?open=<id>` opens a thing on any surface.
 *
 * Surfaces grew their own names for the same idea — Library `?open=`, Composer
 * `?from=`, TTP cards `?ttp=`, Runs and Results `?run=` — so a link that was
 * right for one page rendered another page's empty state with no error
 * (`#/composer?open=SIM-EDR-001`). `open` now wins everywhere and each
 * surface's legacy name stays as an alias, so bookmarks, the e2e specs and the
 * Composer's own `setParams({ from })` keep resolving.
 *
 * @param {Object|undefined} params   the surface's URL params
 * @param {...string}        legacy   this surface's historical key(s), in order
 * @returns {string|null}
 */
export function openParam(params, ...legacy) {
  if (!params) return null
  for (const key of ['open', ...legacy]) {
    const v = params[key]
    if (typeof v === 'string' && v.trim()) return v
  }
  return null
}

/**
 * The UC / TC index addresses two id spaces, so `open` is told apart by
 * prefix — `TC-…` is a test case, `UC-…` / `UCS-…` a use case — and the
 * explicit `uc` / `tc` pair stays as the alias.
 *
 * @param {Object|undefined} params
 * @returns {{ uc: string|null, tc: string|null }}
 */
export function uctcOpen(params) {
  const uc = openParam(params ? { open: params.uc } : null)
  const tc = openParam(params ? { open: params.tc } : null)
  const id = openParam(params)
  if (id && /^TC-/i.test(id)) return { uc, tc: id }
  if (id && /^UCS?-/i.test(id)) return { uc: id, tc }
  return { uc, tc }
}
