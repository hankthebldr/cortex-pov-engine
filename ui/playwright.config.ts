import { defineConfig, devices } from '@playwright/test'

/**
 * Playwright E2E config for CortexSim.
 *
 * Targets a *running* SimCore — by default a docker compose stack at
 * http://localhost:8888 — and exercises the React UI it serves.
 *
 *   CORTEXSIM_BASE_URL    base URL of SimCore (default http://localhost:8888)
 *   CORTEXSIM_KEEP_BROWSER  if set, leave browser open after first failure
 *
 * Run:
 *   cd ui && npm run test:e2e
 *   cd ui && npm run test:e2e:headed       # see the browser
 */
export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: false,           // shared SimCore state — keep tests sequential
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: process.env.CI ? [['github'], ['html', { open: 'never' }]] : 'list',
  timeout: 60_000,
  expect: { timeout: 10_000 },
  use: {
    baseURL: process.env.CORTEXSIM_BASE_URL || 'http://localhost:8888',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    actionTimeout: 10_000,
    navigationTimeout: 15_000,
  },
  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        // Desktop Chrome's default is 1280x720, which sits BELOW every one of
        // the console header's progressive-disclosure breakpoints
        // (cortex-console.css: 1700 / 1600 / 1500 / 1280 / 1120). At 1280 the
        // brand subtitle, the PANW mark and the whole .brand block are
        // display:none BY DESIGN — so the shell specs were asserting on chrome
        // the stylesheet deliberately hides, and failing for a reason that has
        // nothing to do with the behaviour under test.
        //
        // 1920x1080 is above the widest breakpoint (1700), so the console
        // renders its full chrome and the specs measure the app rather than the
        // viewport. Responsive collapse is a separate concern and belongs in a
        // spec that sets its own narrow viewport explicitly.
        viewport: { width: 1920, height: 1080 },
      },
    },
  ],
})
