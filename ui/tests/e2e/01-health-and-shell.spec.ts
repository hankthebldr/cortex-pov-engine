import { test, expect, gotoView } from './_fixtures'

/**
 * Golden path #1 — application shell loads, health is green, header chrome
 * renders, the detection-plane rail pulls scenario counts, and every
 * primary + secondary view is reachable through the guided stepper.
 *
 * If this fails, every other E2E test is meaningless — keep it first.
 */
test.describe('app shell', () => {
  test('SimCore /api/health responds', async ({ api }) => {
    const body = await api.health()
    // `degraded` is a legitimate CI state and asserting `ok` was wrong.
    // payloads/ holds build artifacts that are gitignored, so a fresh checkout
    // has an empty shelf and the payload_shelf component correctly reports
    // SHELF_ARTIFACTS_MISSING. Forcing `ok` here would mean either weakening
    // the component until an empty shelf looked fine — hiding the exact thing
    // a DC needs to see before a customer meeting — or staging 8 real
    // offensive tools in CI. What must hold is that SimCore ANSWERS and that
    // anything not ok explains itself.
    expect(['ok', 'degraded']).toContain(body.status)
  })

  test('a degraded component names itself and how to fix it', async ({ api }) => {
    const body = await api.health()
    for (const [name, comp] of Object.entries<any>(body.components ?? {})) {
      if (comp.status === 'ok') continue
      expect(comp.code, `${name} has no machine code`).toBeTruthy()
      expect(comp.detail, `${name} has no detail`).toBeTruthy()
      // A health surface that says something is wrong and not what to do about
      // it costs the operator more than it saves.
      expect(
        /fix:/i.test(comp.detail) || /NOT a liveness probe/.test(comp.detail),
        `${name} reports "${comp.status}" with no remediation: ${comp.detail}`,
      ).toBe(true)
    }
  })

  test('UI loads and shows the POVengine header + detection-plane rail', async ({ page }) => {
    await page.goto('/')
    // The console rebranded to POVengine in-app, and the header lost its
    // "Detection Simulation Engine" subtitle with it — the redesigned bar is
    // the Cortex product mark, the POVengine wordmark and a version chip, with
    // the ten scope/run controls that used to compete with a tagline for the
    // same row. Asserting on the WORDMARK rather than the tagline is also more
    // durable: the mark is an <img alt=""> (decorative, since the wordmark
    // beside it carries the name), so there is no "cortex" text to match.
    // `.brand__wordmark` rather than a /POV/ text match: the header carries
    // "POV" twice — once as the wordmark and once as the evidence-collection
    // chip's kicker — so a text regex trips Playwright's strict mode.
    await expect(page.getByTestId('console-header')).toBeVisible()
    await expect(page.locator('.brand__wordmark')).toHaveText(/POVengine/)

    // The rail lists all 11 detection planes; each plane button carries a
    // stable data-testid (the scenario grid also surfaces plane names, so a
    // name-regex would trip strict-mode). Check the core planes render.
    for (const id of ['EDR', 'CDR', 'NDR', 'ITDR', 'CLOUD_APP', 'ANALYTICS']) {
      await expect(page.getByTestId(`plane-button-${id}`)).toBeVisible()
    }
  })

  test('touring every view leaves the workspace alive', async ({ page }) => {
    await page.goto('/')
    // The stepper and its "More ▾" overflow are long gone; navigation is the
    // phase-ordered rail, and three of these destinations are reachable by
    // route rather than by a rail button (see VIEW_ROUTES). gotoView handles
    // both, which is the reason this list did not need to change when the IA
    // did — only how each entry is addressed.
    for (const name of ['Targets', 'Library', 'Live', 'Evidence', 'ATT&CK Coverage', 'Environments', 'Launch']) {
      await gotoView(page, name)
    }
    // App is still alive after the full tour: the shell chrome is the thing
    // that survives every destination, so it is what "alive" means here.
    await expect(page.getByTestId('console-header')).toBeVisible()
    await expect(page.getByTestId('flow-bar')).toBeVisible()
  })
})
