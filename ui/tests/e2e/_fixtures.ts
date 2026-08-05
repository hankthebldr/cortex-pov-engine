import { test as base, expect, type Page } from '@playwright/test'

/**
 * Shared Playwright fixtures + nav helpers for the Mission Ops Console.
 * Each test gets API helpers so we can pre-seed runs/agents without leaning on
 * UI clicks for setup.
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
 * Navigate to a console view by its historical spec name.
 *
 * These specs were written against the guided STEPPER, which
 * `ui/src/app/destinations.jsx` replaced with a persistent destination rail —
 * ConsoleStepper is no longer mounted anywhere, so `.step--more` waited forever
 * and every spec timed out identically.
 *
 * The names below are kept as the specs' vocabulary rather than renamed, because
 * they describe what a DC is trying to DO ("go look at Evidence"), which the
 * redesign did not change. What changed is where each one lives:
 *
 *   - most are now first-class destinations in the rail;
 *   - "Launch" moved inside the guided flow (a hidden destination reachable
 *     from Library and ⌘K), so it is addressed by route rather than by rail;
 *   - "Live" / "Evidence" became TABS of a run, not top-level views.
 *
 * Destination-level views navigate by CLICKING the rail, so the nav itself stays
 * under test. Tab-scoped views go through the hash router, since there is no rail
 * entry to click.
 */
const VIEW_ROUTES: Record<string, { dest: string; params?: Record<string, string> }> = {
  'Targets': { dest: 'agents' },
  'Library': { dest: 'library' },
  'ATT&CK Coverage': { dest: 'coverage' },
  'Environments': { dest: 'environments' },
  'Launch': { dest: 'guided' },
  'Live': { dest: 'runs', params: { tab: 'live' } },
  'Evidence': { dest: 'runs', params: { tab: 'evidence' } },
  'EAL Plugins': { dest: 'eal' },
  // The tool-adapter catalog, relabelled "Tools & Payloads" when the payload
  // shelf landed on it. The destination id is unchanged, which is the point of
  // keeping it: routes, testids and ⌘K entries all derive from the id.
  'Tools & Payloads': { dest: 'adapters' },
  'Payload Shelf': { dest: 'adapters', params: { supply: 'unstaged' } },
}

export async function gotoView(
  page: Page,
  name: RegExp | string,
  extra: Record<string, string> = {},
): Promise<void> {
  const key = typeof name === 'string' ? name : String(name).replace(/^\/|\/[a-z]*$/g, '')
  const route = VIEW_ROUTES[key]
  if (!route) {
    throw new Error(
      `gotoView: no route for "${key}". Add it to VIEW_ROUTES in _fixtures.ts — ` +
      `an unmapped name used to fall through to a selector that silently timed out.`,
    )
  }

  const params = { ...(route.params ?? {}), ...extra }
  const hidden = route.dest === 'guided'   // not in the rail by design
  if (hidden || Object.keys(params).length) {
    const qs = new URLSearchParams(params).toString()
    await page.goto(`/#/${route.dest}${qs ? `?${qs}` : ''}`)
  } else {
    const btn = page.getByTestId(`dest-button-${route.dest}`)
    await btn.waitFor({ state: 'visible', timeout: 10_000 })
    await btn.click()
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
