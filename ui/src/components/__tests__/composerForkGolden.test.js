/**
 * composerForkGolden — the JS half of the golden fork pair.
 *
 * The drafts-API tests (tests/api/test_drafts_api.py,
 * tests/engine/test_composer_draft_schema.py) POST the body the console would
 * send when a DC forks SIM-MP-031. The backend CI job runs in the prod image,
 * which has no node, so it cannot compute that body; it reads
 * tests/fixtures/composer_fork/SIM-MP-031.body.json instead, after checking
 * SIM-MP-031.doc.json still equals the live corpus (tests/_composer_fork.py).
 * THIS test is what keeps the body honest: it fails the moment
 * draftToApi(draftFromScenario(doc)) stops producing exactly the committed
 * body — a new carried field, a renamed key, a dropped refusal.
 *
 * Regenerate the body after an intended change to the fork (from the repo root):
 *
 *   node --input-type=module -e "
 *   import fs from 'node:fs';
 *   const m = await import(process.cwd() + '/ui/src/components/console/composerDraft.js');
 *   const dir = 'tests/fixtures/composer_fork/';
 *   const doc = JSON.parse(fs.readFileSync(dir + 'SIM-MP-031.doc.json', 'utf8'));
 *   const s = (v) => Array.isArray(v) ? v.map(s) : (v && typeof v === 'object')
 *     ? Object.fromEntries(Object.keys(v).sort().map((k) => [k, s(v[k])])) : v;
 *   fs.writeFileSync(dir + 'SIM-MP-031.body.json',
 *     JSON.stringify(s(m.draftToApi(m.draftFromScenario(doc))), null, 2) + '\n');"
 */
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, it, expect } from 'vitest'
import { draftFromScenario, draftToApi } from '../console/composerDraft.js'

const FIXTURES = resolve(
  dirname(fileURLToPath(import.meta.url)),
  '../../../../tests/fixtures/composer_fork',
)
const read = (name) => JSON.parse(readFileSync(resolve(FIXTURES, name), 'utf8'))

describe('golden fork pair — SIM-MP-031', () => {
  const doc = read('SIM-MP-031.doc.json')
  const body = draftToApi(draftFromScenario(doc))

  it('the console fork produces exactly the committed body the backend tests POST', () => {
    expect(body).toEqual(read('SIM-MP-031.body.json'))
  })

  // Pinned on their own so a regeneration that silently drops the refusal
  // cannot pass by rewriting the golden file alongside the code.
  it('carries the origin push refusal and its Unit 42 citation', () => {
    expect(doc.push_supported_declared).toBe(false)
    expect(body.push_supported).toBe(false)
    expect(body.threat_report_url).toBe(doc.threat_report_url)
    expect(body.threat_report_url).toMatch(/^https:\/\/unit42\.paloaltonetworks\.com\//)
  })
})
