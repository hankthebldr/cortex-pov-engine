import { test as base, expect, type Page } from '@playwright/test'

/**
 * Shared Playwright fixtures + nav helpers for the Mission Ops Console
 * (redesign v2 — guided stepper). Each test gets API helpers so we can
 * pre-seed runs/agents without leaning on UI clicks for setup.
 */

type Helpers = {
  api: {
    health: () => Promise<{ status: string; version: string }>
    launchPush: (scenarioId: string) => Promise<string>
    seedAgent: (agentId: string) => Promise<void>
    validateAllResults: (runId: string) => Promise<void>
  }
}

/**
 * Navigate to a console view. Primary workflow steps (Targets / Library /
 * Launch / Live / Evidence) are stepper tabs (role=tab). Secondary views
 * (ATT&CK Coverage / Environments) live behind the "More ▾" menu
 * (role=menuitem). This helper handles both.
 */
export async function gotoView(page: Page, name: RegExp | string): Promise<void> {
  // Non-anchored: stepper tab accessible names include the step number and
  // any badge (e.g. "5Evidence0/0"), so we match on a substring.
  const re = typeof name === 'string'
    ? new RegExp(name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
    : name
  const step = page.getByRole('tab', { name: re })
  if (await step.count()) {
    await step.first().click()
  } else {
    // The More button relabels to the active secondary view once one is
    // selected, so match it structurally (aria-haspopup) not by name.
    await page.locator('.step--more').click()
    await page.getByRole('menuitem', { name: re }).first().click()
  }
  await page.waitForLoadState('networkidle')
}

export const test = base.extend<Helpers>({
  // Returning-user mode: suppress first-run overlay + pin the console theme
  // so the redesigned shell renders deterministically.
  page: async ({ page }, use) => {
    await page.addInitScript(() => {
      try {
        window.localStorage.setItem('cortexsim.helpOverlay.seenV1', 'true')
        window.localStorage.setItem('cortexsim.theme', 'console')
      } catch { /* localStorage may be blocked; app handles it */ }
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
        // Authorize gated (dual-use / c2) tool adapters so any scenario
        // launches — the consent gate itself is covered in spec 02 + unit tests.
        const r = await request.post(`${baseURL}/api/run`, {
          data: {
            scenario_id: scenarioId,
            mode: 'push',
            consent: { simulation_authorized: true, c2_authorized: true },
          },
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
