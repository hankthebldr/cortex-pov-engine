/**
 * payloads.js — the console's client for the PAYLOAD SHELF (`/api/shelf/*`).
 *
 * WHY A SEPARATE MODULE
 * ---------------------
 * `client.js` is 1041 lines and every surface imports it. The shelf is a
 * self-contained subsystem with its own error vocabulary
 * (PAYLOAD_NOT_STAGED / PAYLOAD_PIN_MISMATCH / SHELF_EGRESS_DISABLED / …), so
 * it gets its own file and re-uses the one `request()` wrapper rather than
 * forking the 40-line fetch/structured-error dance.
 *
 * WHAT THIS TALKS TO (verified against core/api/payloads.py, 2026-08-05)
 * ---------------------------------------------------------------------
 *   GET  /api/shelf/payloads            inventory + `declared[]` (the adapter join)
 *   GET  /api/shelf/artifacts           the DERIVED declaration (staged/unstaged/unpinned)
 *   POST /api/shelf/stage               pull a public tool onto THIS SimCore — SYNCHRONOUS
 *   POST /api/shelf/compose             resolve a digest-bound plan, or 409
 *   GET  /api/shelf/resolve/{id}        the same for one scenario, consumer=console
 *
 * Two divergences from the console design brief, both because the shipped
 * backend is what it is and pretending otherwise would build screens against
 * an API that does not exist:
 *
 *   1. **Staging is synchronous.** There is no `/jobs/{id}`, no 202, no cancel.
 *      `POST /api/shelf/stage` returns 201 with the staged artifact, or an
 *      error. The UI therefore shows a spinner and a hard client-side timeout,
 *      not a progress bar with byte counts — a determinate bar here would be a
 *      fabricated number.
 *   2. **There is no compose-PREVIEW endpoint.** `POST /api/shelf/compose` IS
 *      the preview: it is stateless, persists nothing, and returns the same
 *      plan a consumer would carry. Calling it to render the rename control is
 *      not a side effect.
 *
 * Every function below lets a 404 propagate with `err.status === 404` so the
 * caller can render UNKNOWN. It must never invent a shelf state — a fabricated
 * "staged" is the manufactured-green failure this repo keeps finding.
 */
import { request } from './client.js'

/** Bare-filename rule, copied VERBATIM from core/api/payloads.py::_NAME_RE so
 *  the console cannot offer a name the endpoints would 400 on. */
export const PAYLOAD_NAME_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/

/**
 * GET /api/shelf/payloads — what is on the shelf, and what the packs declare.
 *
 * Falls back to the K8s-namespaced mount when `/api/shelf/*` 404s. That alias
 * is the SAME handler on a current SimCore, but on an older one only the K8s
 * path exists — and there the body carries no `declared[]`, which the caller
 * detects and renders as UNKNOWN rather than guessing.
 */
export async function getShelf() {
  try {
    return await request('/api/shelf/payloads')
  } catch (err) {
    if (err?.status === 404) {
      const legacy = await request('/api/k8s/payloads')
      return { ...legacy, _via: 'k8s-alias' }
    }
    throw err
  }
}

/** GET /api/shelf/artifacts — the derived declaration incl. pin type/ref + used_by. */
export async function getShelfArtifacts() {
  return request('/api/shelf/artifacts')
}

/**
 * POST /api/shelf/stage — SimCore makes ONE outbound request, on the DC's host.
 *
 * `{adapter_id}` is the preferred form: name/url/digest/licence then come from
 * the pack's `install.artifact`, so the staged bytes are the bytes the pack was
 * authored against. The console never lets an operator type a digest.
 *
 * @param {{adapter_id?:string, name?:string, url?:string, sha256?:string,
 *          license?:string, replace?:boolean}} body
 * @param {{signal?:AbortSignal}} [opts]
 */
export async function stagePayload(body, { signal } = {}) {
  return request('/api/shelf/stage', {
    method: 'POST',
    body: JSON.stringify(body),
    ...(signal ? { signal } : {}),
  })
}

/**
 * POST /api/shelf/compose — resolve a plan, or refuse.
 *
 * `stage_as` is the rename control: `{ "TOOL-LINPEAS": "/tmp/.cache/sysinfo.sh" }`.
 * A 409 here (PAYLOAD_NOT_STAGED / PAYLOAD_PIN_MISMATCH / PAYLOAD_DEST_REFUSED)
 * is the whole point of the endpoint and must be surfaced verbatim, never
 * swallowed into an empty plan.
 */
export async function composePayloads({
  scenario_id = null, adapter_refs = [], stage_as = {}, consumer = 'console',
} = {}) {
  return request('/api/shelf/compose', {
    method: 'POST',
    body: JSON.stringify({ scenario_id, adapter_refs, stage_as, consumer }),
  })
}

/** GET /api/shelf/resolve/{scenario_id} — console preflight for one scenario. */
export async function resolveScenarioPayloads(scenarioId) {
  return request(`/api/shelf/resolve/${encodeURIComponent(scenarioId)}?consumer=console`)
}

/**
 * Does THIS SimCore accept a `payload_plan` on `POST /api/runs`?
 *
 * WHY THIS PROBE EXISTS AND WHY IT IS NOT OPTIONAL: FastAPI/Pydantic silently
 * IGNORES unknown fields on a request body. Posting a composed plan to a
 * SimCore whose `LaunchRequest` has no `payload_plan` would return 200, create
 * a Run, and execute every step with the tool under its ORIGINAL name — the
 * console would report "renamed" while the beacon ran `linpeas.sh`. That is a
 * false claim in a customer-facing report, produced by the back-compat
 * mechanism itself.
 *
 * The OpenAPI schema is the only capability surface SimCore exposes, so we read
 * it. Three outcomes, and `unknown` is NOT collapsed into either boolean:
 *   { supported: true  }  the field is in LaunchRequest
 *   { supported: false }  the schema loaded and the field is absent
 *   { unknown: true    }  the schema could not be read — say so, claim nothing
 */
export async function getLaunchCapability() {
  try {
    const doc = await request('/api/openapi.json')
    const props = doc?.components?.schemas?.LaunchRequest?.properties
    if (!props || typeof props !== 'object') {
      return { supported: false, unknown: true, reason: 'LaunchRequest is not in the OpenAPI schema' }
    }
    return { supported: Object.prototype.hasOwnProperty.call(props, 'payload_plan'), unknown: false }
  } catch (err) {
    return {
      supported: false,
      unknown: true,
      reason: err?.message || 'the OpenAPI schema could not be read',
    }
  }
}

/** URL of the served bytes, for a provenance block / a copyable curl. */
export function payloadDownloadUrl(name) {
  return `/api/shelf/payload/${encodeURIComponent(name)}`
}
