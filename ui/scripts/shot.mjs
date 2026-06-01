// Iteration screenshot helper for the design loop.
// Usage: node scripts/shot.mjs <out.png> [url] [width] [height] [full]
// Drives the system (snap) Chromium via playwright-core, dismisses the
// first-run help overlay, pins the console theme, waits for the API calls
// to settle, then captures a crisp 2x screenshot.
import { chromium } from 'playwright-core'

const [, , out = 'shot.png', url = 'http://localhost:5173/',
       w = '1440', h = '900', full = ''] = process.argv

const browser = await chromium.launch({
  executablePath: '/snap/bin/chromium',
  headless: true,
  args: ['--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage'],
})
const ctx = await browser.newContext({
  viewport: { width: Number(w), height: Number(h) },
  deviceScaleFactor: 2,
})
await ctx.addInitScript(() => {
  try {
    localStorage.setItem('cortexsim.theme', 'console')
    localStorage.setItem('cortexsim.helpOverlay.seenV1', 'true')
  } catch {}
})
const page = await ctx.newPage()
await page.goto(url, { waitUntil: 'networkidle', timeout: 30000 }).catch(() => {})
await page.waitForTimeout(1200)
await page.screenshot({ path: out, fullPage: full === 'full' })
await browser.close()
console.log('wrote', out)
