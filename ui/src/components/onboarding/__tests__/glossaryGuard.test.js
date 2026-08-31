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

// Strip block comments (incl. JSDoc) and line comments before scanning source
// text for <Term k="..."> usages. This is a source-text guard, not a parser —
// a good-enough regex strip is enough to keep doc-comment prose (which is
// free to mention <Term k="..."> as an example) from tripping the scanner.
//
// The line-comment strip only treats `//` as a comment when it is at the
// start of a line (modulo leading whitespace) or preceded by whitespace and
// NOT by `:` — a bare `/\/\/.*$/` cannot tell a real comment from `//` inside
// a string or URL scheme (e.g. `"see https://example.com"`), and would eat
// everything after it on the line, including a real <Term k="...">. That is
// exactly the silent under-report this guard exists to prevent.
function stripComments(src) {
  return src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1')
}

function findTermUsages(files) {
  const usages = []
  for (const f of files) {
    const src = stripComments(readFileSync(f, 'utf8'))
    for (const m of src.matchAll(/<Term\s+k=["']([^"']+)["']/g)) {
      usages.push({ file: f, key: m[1] })
    }
  }
  return usages
}

describe('stripComments', () => {
  it('does not eat a <Term> that shares a line with a :// URL', () => {
    // Regression guard for the reviewer-caught defect: a naive `//.*$` line-
    // comment strip cannot distinguish a real comment from `//` inside a
    // string or URL scheme, so it silently truncated everything after the
    // URL — including a real <Term k="..."> later on the same line. That is
    // exactly the failure mode this whole guard exists to prevent: a clean
    // scan that is actually under-reporting.
    const src = '  console.log("see https://example.com"); <Term k="leaked">x</Term>'
    expect(stripComments(src)).toContain('<Term k="leaked">')
  })

  it('still strips a genuine line comment', () => {
    // The fix must not simply disable line-comment stripping — that would
    // pass the assertion above while silently re-breaking Part 3 (the
    // restored glossary.js doc comment would trip the guard again).
    const src = '  const x = 1 // a real comment mentioning <Term k="fake">\n'
    expect(stripComments(src)).not.toContain('<Term k="fake">')
  })
})

describe('glossary guard', () => {
  it('every <Term k="..."> in the UI resolves to a glossary entry', () => {
    const files = walk(new URL('../../..', import.meta.url).pathname)
    const usages = findTermUsages(files)
    const dangling = usages
      .filter((u) => !Object.prototype.hasOwnProperty.call(GLOSSARY, u.key))
      .map((u) => `${u.file}: k="${u.key}"`)
    expect(dangling, `dangling glossary keys:\n${dangling.join('\n')}`).toEqual([])
  })

  it('no glossary definition is a placeholder', () => {
    for (const [key, entry] of Object.entries(GLOSSARY)) {
      expect(entry.definition, key).not.toMatch(/TODO|TBD|FIXME|lorem/i)
      expect(entry.definition.length, key).toBeGreaterThan(20)
    }
  })

  it('the guard is not vacuous: at least one production file uses <Term>', () => {
    // A previous task shipped this whole guard while Term.jsx was imported by
    // zero production files — the "no dangling keys" assertion above passed
    // trivially by scanning an empty set. An empty scan and a clean scan must
    // never produce the same verdict ("a zero is degraded, not ok"). This
    // test fails loudly the moment that regresses, instead of passing silently.
    const files = walk(new URL('../../..', import.meta.url).pathname)
      .filter((f) => !f.split('/').includes('__tests__'))
    const usages = findTermUsages(files)
    expect(
      usages.length,
      'the glossary guard is vacuous: no production file (outside __tests__) contains a ' +
        '<Term k="..."> usage, so the "no dangling keys" test above is scanning an empty ' +
        'set and passing trivially. The tooltip layer is dead code — wire <Term> into a ' +
        'real console surface before trusting this guard again.'
    ).toBeGreaterThan(0)
  })
})
