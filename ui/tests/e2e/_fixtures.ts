import { test as base, expect } from '@playwright/test'

/**
 * Shared Playwright fixtures.
 *
 * Each test gets a small set of API helpers so we can pre-seed runs,
 * register agents, etc. without leaning on UI clicks for setup (faster
 * and less flaky).  The fixtures use baseURL from playwright.config.ts.
 *
 * The `page` fixture is also extended to seed `cortexsim.theme=legacy`
 * into localStorage before any page script runs. Reason: the Mission Ops
 * Console is now the default UI (see ui/src/main.jsx::resolveTheme), but
 * these specs were written against the legacy light-themed App.jsx and
 * still assert legacy selectors ("MITRE", "Deploy", "EAL", "Runs"
 * buttons; "Scenario Library" heading). Until the specs are migrated to
 * the console UI in a follow-up PR, the legacy escape hatch keeps them
 * green. addInitScript runs the snippet on every navigation before the
 * React bundle boots, so the theme router picks up the pinned legacy
 * choice immediately.
 */

type Helpers = {
  api: {
    health: () => Promise<{ status: string; version: string }>
    launchPush: (scenarioId: string) => Promise<string>
    seedAgent: (agentId: string) => Promise<void>
    validateAllResults: (runId: string) => Promise<void>
  }
}

export const test = base.extend<Helpers>({
  page: async ({ page }, use) => {
    await page.addInitScript(() => {
      try { window.localStorage.setItem('cortexsim.theme', 'legacy') } catch {}
    })
    await use(page)
  },
  api: async ({ request, baseURL }, use) => {
    const helpers = {
      async health() {
        const r = await request.get(`${baseURL}/api/health`)
        if (!r.ok()) throw new Error(`SimCore not reachable at ${baseURL}`)
        return r.json()
      },
      async launchPush(scenarioId: string) {
        const r = await request.post(`${baseURL}/api/run`, {
          data: { scenario_id: scenarioId, mode: 'push' },
        })
        if (!r.ok()) throw new Error(`launch failed: ${await r.text()}`)
        const body = await r.json()
        return body.run_id as string
      },
      async seedAgent(agentId: string) {
        const r = await request.post(`${baseURL}/api/agents/register`, {
          data: { agent_id: agentId, hostname: 'e2e-agent', os: 'linux', capabilities: [] },
        })
        if (!r.ok()) throw new Error(`agent register failed: ${await r.text()}`)
      },
      async validateAllResults(runId: string) {
        const r = await request.get(`${baseURL}/api/results/${runId}`)
        const body = await r.json()
        for (const result of body.results || []) {
          await request.put(`${baseURL}/api/results/${result.id}/validate`, {
            data: { observed: true, notes: 'e2e' },
          })
        }
      },
    }
    await use(helpers)
  },
})

export { expect }
