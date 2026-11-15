/**
 * GET /api/runs is paged (default 500 rows) and reports `total`. getRuns read
 * only the first page, so once a deployment passed 500 runs the Runs list,
 * the environment context and the results viewer silently dropped the oldest
 * ones, and a body without a `runs` array read as "no runs".
 */
import { describe, it, expect } from 'vitest'
import { installRoutes } from '../../test/mockFetch.js'
import { getRuns, RUNS_PAGE_LIMIT } from '../../api/client.js'

const json = (body) => new Response(JSON.stringify(body), {
  status: 200, headers: { 'content-type': 'application/json' },
})

describe('getRuns — every page, never a silent short list', () => {
  it('follows total across pages', async () => {
    const all = Array.from({ length: 7 }, (_, i) => ({ run_id: `r-${i}` }))
    const seen = []
    installRoutes({
      'GET /api/runs': (url) => {
        const q = new URL(url, 'http://localhost').searchParams
        seen.push([q.get('limit'), q.get('offset'), q.get('fields')])
        const off = Number(q.get('offset'))
        return json({ runs: all.slice(off, off + 3), total: all.length })
      },
    })
    const rows = await getRuns({ fields: 'summary' })
    expect(rows.map((r) => r.run_id)).toEqual(all.map((r) => r.run_id))
    expect(seen.map((s) => s[1])).toEqual(['0', '3', '6'])
    expect(seen.every((s) => s[0] === String(RUNS_PAGE_LIMIT) && s[2] === 'summary')).toBe(true)
  })

  it('a legacy bare array is one unpaged list', async () => {
    installRoutes({ 'GET /api/runs': () => json([{ run_id: 'a' }]) })
    expect(await getRuns()).toEqual([{ run_id: 'a' }])
  })

  it('an unrecognised body throws instead of reading as no runs', async () => {
    installRoutes({ 'GET /api/runs': () => json({ items: [] }) })
    await expect(getRuns()).rejects.toThrow(/unrecognised shape/)
  })
})
