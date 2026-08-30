// @vitest-environment node
import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { GLOSSARY } from '../glossary.js'

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) { if (name !== '__tests__') walk(p, out) }
    else if (p.endsWith('.jsx') || p.endsWith('.js')) out.push(p)
  }
  return out
}

describe('glossary guard', () => {
  it('every <Term k="..."> in the UI resolves to a glossary entry', () => {
    const files = walk(new URL('../../..', import.meta.url).pathname)
    const dangling = []
    for (const f of files) {
      const src = readFileSync(f, 'utf8')
      for (const m of src.matchAll(/<Term\s+k=["']([^"']+)["']/g)) {
        if (!Object.prototype.hasOwnProperty.call(GLOSSARY, m[1])) {
          dangling.push(`${f}: k="${m[1]}"`)
        }
      }
    }
    expect(dangling, `dangling glossary keys:\n${dangling.join('\n')}`).toEqual([])
  })

  it('no glossary definition is a placeholder', () => {
    for (const [key, entry] of Object.entries(GLOSSARY)) {
      expect(entry.definition, key).not.toMatch(/TODO|TBD|FIXME|lorem/i)
      expect(entry.definition.length, key).toBeGreaterThan(20)
    }
  })
})
