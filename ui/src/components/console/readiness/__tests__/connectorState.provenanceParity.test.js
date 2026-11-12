// Parity oracle: the Python port (core/connectors/provenance.py) is proven
// against the same JSON fixture. If this and tests/connectors/test_provenance.py
// disagree, the fixture or the Python is wrong — never this module.
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, it, expect } from 'vitest'
import { verdictProvenance, tenantVerifiedNames } from '../connectorState.js'

const fixture = JSON.parse(
  readFileSync(resolve(__dirname, '../../../../../../tests/fixtures/provenance_parity.json'), 'utf8'),
)

describe('verdictProvenance / tenantVerifiedNames parity fixture', () => {
  it('names match expected_names', () => {
    const names = tenantVerifiedNames({
      runs: fixture.runs,
      assertionRuns: fixture.assertion_runs,
    }).sort()
    expect(names).toEqual(fixture.expected_names)
  })

  it('tenant-provenance count matches expected_tenant_count', () => {
    const n = [...fixture.runs, ...fixture.assertion_runs].filter(
      (r) => verdictProvenance(r) === 'tenant',
    ).length
    expect(n).toBe(fixture.expected_tenant_count)
  })

  it('pending and not_applicable rows are never tenant', () => {
    for (const r of [...fixture.runs, ...fixture.assertion_runs]) {
      if (r.tc_verdict === 'pending' || r.tc_verdict === 'not_applicable') {
        expect(verdictProvenance(r)).toBe('none')
      }
    }
  })
})
