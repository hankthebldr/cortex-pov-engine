import { chromium } from 'playwright-core'
const OUT = '/home/henry/Github/cortex-pov-engine/docs/design/handoff/screens'
const b = await chromium.launch({ executablePath:'/snap/bin/chromium', headless:true, args:['--no-sandbox','--disable-gpu','--disable-dev-shm-usage'] })
const ctx = await b.newContext({ viewport:{width:1440,height:900}, deviceScaleFactor:2 })
await ctx.addInitScript(()=>{try{localStorage.setItem('cortexsim.theme','console');localStorage.setItem('cortexsim.helpOverlay.seenV1','true')}catch{}})
const p = await ctx.newPage()
await p.goto('http://localhost:5173/',{waitUntil:'networkidle',timeout:30000}).catch(()=>{})
await p.waitForTimeout(1200)
const shot = (n)=>p.screenshot({path:`${OUT}/${n}.png`})
const step = async (label)=>{ await p.evaluate(l=>[...document.querySelectorAll('.stepper .step')].find(s=>s.textContent.includes(l))?.click(), label); await p.waitForTimeout(700) }
const more = async (label)=>{ await p.evaluate(()=>document.querySelector('.step--more')?.click()); await p.waitForTimeout(250); await p.evaluate(l=>[...document.querySelectorAll('.stepper__menu-item')].find(s=>s.textContent.includes(l))?.click(), label); await p.waitForTimeout(800) }

await step('Targets');  await shot('01-targets')
await step('Library');  await shot('02-library')
await step('Launch');   await shot('03-launch-gate')        // no scenario armed
// arm one + select target → armed launch
await step('Targets'); await p.evaluate(()=>document.querySelector('.target-card--push')?.click()); await p.waitForTimeout(300)
await step('Library'); await p.evaluate(()=>document.querySelector('.scenario-card')?.click()); await p.waitForTimeout(900); await p.keyboard.press('Escape'); await p.waitForTimeout(300)
await shot('02b-library-inspector-open') // capture inspector before escape? (already escaped) — capture armed launch instead
await step('Launch');   await shot('03b-launch-armed')
await step('Live');     await shot('04-live')
await step('Evidence'); await shot('05-evidence')
await more('ATT&CK Coverage'); await shot('06-coverage')
await more('Environments');    await shot('07-environments')
// collapsed rail variant
await step('Targets'); await p.evaluate(()=>document.querySelector('.rail__toggle')?.click()); await p.waitForTimeout(500); await shot('08-rail-collapsed')
await b.close()
console.log('captured handoff screens')
