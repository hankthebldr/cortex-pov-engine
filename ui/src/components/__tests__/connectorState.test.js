import { describe, it, expect } from 'vitest'
import {
  buildConnectorLadder, describeIntegration, verdictProvenance, tenantVerifiedNames,
  RUNG, RS,
} from '../console/readiness/connectorState.js'

/**
 * tenant-verified is 0 across this entire product. These assertions exist so a
 * console change cannot quietly make it look like anything else.
 *
 * The specific lie being guarded against: a tenant that answered a `/test`
 * probe rendering as "connected / verified", which lets a DC tell a customer a
 * detection was proven when no query has ever been issued.
 */

const RECONCILE_CRED = {
  name: 'cust-prod', kind: 'xsiam',
  config: { fqdn: 'api-cust.xdr.us.paloaltonetworks.com', api_key_id: '3' },
  last_verified_ok: null, last_verified_at: null, last_verified_error: null,
}
const TENANT_CRED = {
  name: 'cust-lab', kind: 'xsiam_tenant',
  config: { base_url: 'https://api-lab.xsiam.eu.paloaltonetworks.com', region: 'eu', api_key_id: '1' },
  last_verified_ok: true, last_verified_at: '2026-08-05T09:00:00', last_verified_error: null,
}

describe('the ladder never skips a rung', () => {
  it('a REACHABLE tenant is not VERIFIED', () => {
    const l = buildConnectorLadder({
      integrations: [TENANT_CRED],
      runs: [],
      assertionRuns: [],
      scenarios: [{ threshold: { kpi: 'MTTD', op: '<=', value: 300 } }],
      assertionCount: 18,
    })
    const byId = Object.fromEntries(l.rungs.map((r) => [r.id, r]))
    expect(byId[RUNG.CONFIGURED].state).toBe(RS.YES)
    expect(byId[RUNG.REACHABLE].state).toBe(RS.YES)
    expect(byId[RUNG.VERIFIED].state).toBe(RS.NO)
    expect(byId[RUNG.VERIFIED].count).toBe(0)
    expect(l.highest).toBe(RUNG.REACHABLE)
    expect(l.headline).toBe('tenant reachable · nothing verified against it yet')
  })

  it('a locally scored pass verdict is NOT tenant verification', () => {
    // Tier-1 offline scoring: a DC pasted observations, MTTD was measured, the
    // verdict is real — and it says nothing about a tenant query.
    const runs = [{ run_id: 'r-1', tc_verdict: 'pass', tc_verdict_detail: { measured_value: 42 } }]
    const l = buildConnectorLadder({ integrations: [TENANT_CRED], runs, assertionRuns: [] })
    const verified = l.rungs.find((r) => r.id === RUNG.VERIFIED)
    expect(verified.state).toBe(RS.NO)
    expect(verified.count).toBe(0)
    expect(verified.localScored).toBe(1)
    expect(verified.detail).toMatch(/NOT\s+proof/)
    // Found by driving the real console: the count interpolated an ARRAY, so a
    // DC read "verdicts were scored from local evidence" with no number.
    expect(verified.detail).toMatch(/1 verdict was scored from local evidence/)
  })

  it('pluralises and counts the locally-scored verdicts', () => {
    const runs = [
      { run_id: 'a', tc_verdict: 'pass', tc_verdict_detail: {} },
      { run_id: 'b', tc_verdict: 'fail', tc_verdict_detail: {} },
    ]
    const l = buildConnectorLadder({ integrations: [], runs, assertionRuns: [] })
    expect(l.rungs.find((r) => r.id === RUNG.VERIFIED).detail)
      .toMatch(/2 verdicts were scored from local evidence/)
  })

  it('counts a verdict that carries evidence a tenant query was issued', () => {
    const runs = [{
      run_id: 'r-9', tc_verdict: 'fail',
      tc_verdict_detail: { verification: { queries_issued: 3, tenant: 'cust-lab' } },
    }]
    const l = buildConnectorLadder({ integrations: [TENANT_CRED], runs, assertionRuns: [] })
    const verified = l.rungs.find((r) => r.id === RUNG.VERIFIED)
    expect(verified.state).toBe(RS.YES)
    expect(verified.count).toBe(1)
    expect(l.highest).toBe(RUNG.VERIFIED)
  })

  it('a pending verdict is never verification, no matter where it came from', () => {
    const runs = [{
      run_id: 'r-3', tc_verdict: 'pending',
      tc_verdict_detail: { verification: { queries_issued: 12 } },
    }]
    expect(verdictProvenance(runs[0])).toBe('none')
    const l = buildConnectorLadder({ integrations: [TENANT_CRED], runs, assertionRuns: [] })
    expect(l.rungs.find((r) => r.id === RUNG.VERIFIED).count).toBe(0)
  })
})

describe('unknown is a first-class state', () => {
  it('an unreadable credential list is UNKNOWN, never "nothing configured"', () => {
    const l = buildConnectorLadder({ integrations: null, runs: [], assertionRuns: [] })
    const byId = Object.fromEntries(l.rungs.map((r) => [r.id, r]))
    expect(byId[RUNG.CONFIGURED].state).toBe(RS.UNKNOWN)
    expect(byId[RUNG.REACHABLE].state).toBe(RS.UNKNOWN)
    expect(l.headline).toMatch(/unknown/)
  })

  it('unreadable run history leaves VERIFIED unknown rather than zero', () => {
    const l = buildConnectorLadder({ integrations: [TENANT_CRED], runs: null, assertionRuns: null })
    expect(l.rungs.find((r) => r.id === RUNG.VERIFIED).state).toBe(RS.UNKNOWN)
  })

  it('a SimCore whose scenarios carry no KPI fields reports AUTHORED unknown, not 0', () => {
    // The 11-day-old build in compose returns scenarios with no primary_kpi /
    // threshold at all. "0 measurable" would be a measurement we never took.
    const l = buildConnectorLadder({
      scenarios: [{ scenario_id: 'SIM-EDR-001', plane: 'EDR' }],
      integrations: [], runs: [], assertionRuns: [],
    })
    const authored = l.rungs.find((r) => r.id === RUNG.AUTHORED)
    expect(authored.state).toBe(RS.UNKNOWN)
    expect(authored.detail).toMatch(/Unknown, not zero/)
  })
})

describe('the two credential kinds stay apart', () => {
  it('a reconcile credential does not claim XQL', () => {
    const d = describeIntegration(RECONCILE_CRED)
    expect(d.kind).toBe('xsiam')
    expect(d.doesNotBuy).toMatch(/does NOT authorise XQL/)
    expect(d.rung).toBe(RUNG.CONFIGURED)
    expect(d.caveat).toMatch(/Never probed/)
  })

  it('a tenant credential that answered is REACHABLE with the caveat attached', () => {
    const d = describeIntegration(TENANT_CRED)
    expect(d.rung).toBe(RUNG.REACHABLE)
    expect(d.reachable).toBe(RS.YES)
    expect(d.caveat).toMatch(/nothing about the customer's detections has been proven/)
    expect(d.host).toBe('api-lab.xsiam.eu.paloaltonetworks.com')
  })

  it('a failing probe surfaces the error, and never reads as configured-and-fine', () => {
    const d = describeIntegration({ ...TENANT_CRED, last_verified_ok: false, last_verified_error: '401 Unauthorized' })
    expect(d.rung).toBe(RUNG.CONFIGURED)
    expect(d.reachable).toBe(RS.NO)
    expect(d.caveat).toMatch(/401 Unauthorized/)
  })

  it('promotes to VERIFIED only when a verdict names that tenant', () => {
    const runs = [{
      run_id: 'r-9', tc_verdict: 'pass',
      tc_verdict_detail: { verification: { queries_issued: 1, tenant: 'cust-lab' } },
    }]
    const names = tenantVerifiedNames({ runs, assertionRuns: [] })
    expect(names).toEqual(['cust-lab'])
    expect(describeIntegration(TENANT_CRED, { tenantVerifiedNames: names }).rung).toBe(RUNG.VERIFIED)
    // The OTHER credential, on the same SimCore, is not dragged along.
    expect(describeIntegration(RECONCILE_CRED, { tenantVerifiedNames: names }).rung).toBe(RUNG.CONFIGURED)
  })

  it('reads the fqdn spelling as well as base_url', () => {
    expect(describeIntegration(RECONCILE_CRED).host).toBe('api-cust.xdr.us.paloaltonetworks.com')
  })
})

describe('verdictProvenance is conservative by construction', () => {
  it('defaults to local when nothing proves a query was issued', () => {
    expect(verdictProvenance({ tc_verdict: 'pass', tc_verdict_detail: {} })).toBe('local')
    expect(verdictProvenance({ tc_verdict: 'fail' })).toBe('local')
  })
  it('an assertion run naming a tenant is tenant evidence — a dry run cannot reach pass/fail', () => {
    expect(verdictProvenance({ tc_verdict: 'fail', tenant: 'cust-lab' })).toBe('tenant')
  })
  it('an explicitly dry run is never tenant evidence', () => {
    expect(verdictProvenance({ tc_verdict: 'pass', tenant: 'cust-lab', dry_run: true })).toBe('local')
  })
})
