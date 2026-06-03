// Tour every step: measure shell/view/document heights (to catch layout
// jumps) and screenshot. A healthy fixed-viewport shell keeps doc height ==
// viewport on every view; any view where doc scrollHeight > viewport is
// breaking out of the shell.
import { chromium } from 'playwright-core'

const VW = 1440, VH = 900
const browser = await chromium.launch({ executablePath: '/snap/bin/chromium', headless: true, args: ['--no-sandbox','--disable-gpu','--disable-dev-shm-usage'] })
const ctx = await browser.newContext({ viewport: { width: VW, height: VH }, deviceScaleFactor: 2 })
await ctx.addInitScript(() => { try { localStorage.setItem('cortexsim.theme','console'); localStorage.setItem('cortexsim.helpOverlay.seenV1','true') } catch {} })
const page = await ctx.newPage()
await page.goto('http://localhost:5173/', { waitUntil: 'networkidle', timeout: 30000 }).catch(()=>{})
await page.waitForTimeout(1200)

const steps = ['Targets','Library','Launch','Live','Evidence']
const measure = () => page.evaluate(({ VW, VH }) => {
  const h = (sel) => { const e = document.querySelector(sel); return e ? Math.round(e.getBoundingClientRect().height) : 0 }
  const doc = document.documentElement.scrollHeight
  return { doc, viewport: VH, shell: h('.shell'), view: h('.view'),
    overflow: doc > VH + 2 ? `BREAKOUT +${doc-VH}px` : 'ok',
    hScroll: document.documentElement.scrollWidth > VW + 2 ? `HSCROLL +${document.documentElement.scrollWidth-VW}` : 'ok' }
}, { VW, VH })

for (const label of steps) {
  const ok = await page.evaluate((label) => {
    const btn = [...document.querySelectorAll('.stepper .step')].find(b => b.textContent.includes(label))
    if (btn) { btn.click(); return true } return false
  }, label)
  await page.waitForTimeout(700)
  const m = await measure()
  console.log(`${label.padEnd(9)} click=${ok} doc=${m.doc} shell=${m.shell} view=${m.view} | vertical=${m.overflow} horizontal=${m.hScroll}`)
  await page.screenshot({ path: `/home/henry/cortexsim-shots/step-${label.toLowerCase()}.png` })
}
await browser.close()
