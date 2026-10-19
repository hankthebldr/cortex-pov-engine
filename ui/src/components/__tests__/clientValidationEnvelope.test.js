/**
 * SimCore's RequestValidationError handler (core/main.py) answers a 422 with
 * the project's TOP-LEVEL envelope:
 *
 *   { error: "mode: Field required", code: "VALIDATION_ERROR",
 *     detail: { fields: [{ field, location, message, type, loc }] } }
 *
 * request() only knew FastAPI's old `{detail: [...]}` array and the nested
 * HTTPException `{detail: {error, code, detail}}` shape. The current body fell
 * into the nested branch (detail is an object), which read `detail.error` —
 * undefined — so every launch / draft-save / credential 422 reached the
 * operator as "HTTP 422 — Unprocessable Entity" with code null. The existing
 * apiErrors tests still mock the old array shape, which is why they stayed
 * green.
 */
import { describe, it, expect } from 'vitest'
import { installRoutes } from '../../test/mockFetch.js'
import { postRun, getHealth } from '../../api/client.js'

const json = (body, status) => new Response(JSON.stringify(body), {
  status, headers: { 'content-type': 'application/json' },
})

describe('api client — the 422 envelope SimCore actually sends', () => {
  it('names the field and keeps the code and the per-field detail', async () => {
    installRoutes({
      'POST /api/run': () => json({
        error: 'mode: Field required; target_agent_id: Input should be a valid string',
        code: 'VALIDATION_ERROR',
        detail: { fields: [
          { field: 'mode', location: 'body', message: 'Field required', type: 'missing', loc: ['body', 'mode'] },
          { field: 'target_agent_id', location: 'body', message: 'Input should be a valid string', type: 'string_type', loc: ['body', 'target_agent_id'] },
        ] },
      }, 422),
    })
    const err = await postRun({ scenario_id: 'SIM-EDR-001' }).catch((e) => e)
    expect(err.message).toBe('mode: Field required; target_agent_id: Input should be a valid string')
    expect(err.message).not.toMatch(/HTTP 422/)
    expect(err.code).toBe('VALIDATION_ERROR')
    expect(err.status).toBe(422)
    expect(err.detail.fields.map((f) => f.field)).toEqual(['mode', 'target_agent_id'])
  })

  it('still prefers a top-level string detail over the generic error line', async () => {
    // XsiamError / the global 500 handler: the string detail is the specific part.
    installRoutes({
      'GET /api/health': () => json({ error: 'XSIAM integration error', code: 'XSIAM_AUTH', detail: 'HTTP 401 Unauthorized' }, 502),
    })
    const err = await getHealth().catch((e) => e)
    expect(err.message).toBe('HTTP 401 Unauthorized')
    expect(err.code).toBe('XSIAM_AUTH')
  })
})
