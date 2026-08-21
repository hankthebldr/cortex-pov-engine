/**
 * connectorState.js — the FOUR states of Cortex-connector proof, never collapsed
 * into one badge.
 *
 * Pure. No React, no fetch.
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * Across this whole product `tenant-verified` is **0**. No assertion and no run
 * has ever been executed against a live Cortex tenant. Every number the console
 * renders about detection efficacy is therefore either *authored* (someone wrote
 * a claim) or *measured offline* (a DC pasted observations in) — and a console
 * that shows one green "Connected" pill lets a DC stand in front of a customer
 * and say "your detection fired", which nothing in this system has established.
 *
 * So the ladder has four rungs and each rung answers a different question:
 *
 *   AUTHORED    Does the corpus even CLAIM something measurable here?
 *               (a scenario with a threshold, an assertion artifact)
 *               Evidence: the corpus. Proves: nothing about the customer.
 *
 *   CONFIGURED  Is a credential registered in the vault?
 *               Evidence: /api/credentials/integrations.
 *               Proves: a key exists. NOT that it works, NOT that it is scoped.
 *
 *   REACHABLE   Did the tenant ANSWER an explicit operator probe?
 *               Evidence: last_verified_ok === true from POST /test|/preflight.
 *               Proves: auth + network at that moment. NOT that any detection
 *               was queried, NOT that the key has alert-read or XQL scope.
 *
 *   VERIFIED    Did a query against that tenant produce a terminal verdict?
 *               Evidence: a Run/AssertionRun with tc_verdict pass|fail whose
 *               detail shows a tenant query was issued.
 *               Proves: the loop closed. This is the only rung that means
 *               "we proved something about the customer's stack".
 *
 * RUNG 3 DOES NOT IMPLY RUNG 4, AND THE UI MUST NEVER DRAW IT THAT WAY.
 * A tenant that answers /healthcheck with a key that lacks XQL scope is
 * REACHABLE forever and VERIFIED never — and that role-scoping miss is the
 * single most common first-POV failure.
 *
 * A rung we cannot evaluate is `unknown`, never `no` and never `yes`. An
 * endpoint that 404s on an older SimCore is a capability gap, not zero coverage.
 */

export const RUNG = {
  AUTHORED: 'authored',
  CONFIGURED: 'configured',
  REACHABLE: 'reachable',
  VERIFIED: 'verified',
}

export const RUNG_ORDER = [RUNG.AUTHORED, RUNG.CONFIGURED, RUNG.REACHABLE, RUNG.VERIFIED]

/** Tri-state per rung. `unknown` is first-class and never coerced. */
export const RS = { YES: 'yes', NO: 'no', UNKNOWN: 'unknown' }

export const RS_CHIP = {
  [RS.YES]: 'chip chip--detected',
  [RS.NO]: 'chip chip--pending',
  [RS.UNKNOWN]: 'chip',
}

/**
 * The two credential kinds, kept apart deliberately.
 *
 * Configuring alert read-back must not silently authorise XQL against the
 * tenant's metered query quota. A DC has to be able to see WHICH of the two
 * they hold, because they buy different things.
 */
export const CREDENTIAL_KINDS = {
  xsiam: {
    label: 'Reconcile (alert read-back)',
    buys: 'pulls observed alerts for a run window → evidence-backed MTTD',
    doesNotBuy: 'does NOT authorise XQL; assertions and Tier-2 verification stay `pending`',
  },
  xsiam_tenant: {
    label: 'Tenant (XQL + operations)',
    buys: 'Tier-2 verification queries, assertion probes, ingestion metrics',
    doesNotBuy: 'does not by itself reconcile a run — that reads the alert API',
  },
}

const isTerminal = (v) => v === 'pass' || v === 'fail'

/**
 * Did THIS verdict come from a tenant query, or from an offline measurement?
 *
 * Returns 'tenant' | 'local' | 'none'.
 *
 * Deliberately conservative: when the run record does not carry positive
 * evidence that a query was issued, the answer is 'local', never 'tenant'.
 * Understating verification costs a DC a re-run. Overstating it puts an
 * unproven claim in a customer-facing report, and that is the failure this
 * whole ladder exists to prevent.
 */
export function verdictProvenance(row) {
  if (!row || !isTerminal(row.tc_verdict)) return 'none'
  const d = row.tc_verdict_detail
  if (d && typeof d === 'object') {
    const v = d.verification
    if (v && typeof v === 'object') {
      if (typeof v.queries_issued === 'number' && v.queries_issued > 0) return 'tenant'
      if (v.tenant || v.integration) return 'tenant'
    }
    if (typeof d.queries_issued === 'number' && d.queries_issued > 0) return 'tenant'
    if (d.verified_against_tenant === true) return 'tenant'
  }
  // AssertionRun carries the tenant it ran against on the row itself, and a dry
  // run can never reach a terminal verdict, so a named tenant + pass/fail is
  // real tenant evidence.
  if (row.tenant && row.dry_run !== true) return 'tenant'
  return 'local'
}

/**
 * Build the four-rung ladder.
 *
 * Every argument is optional and every absent argument yields `unknown` on the
 * rungs it feeds — never a zero.
 *
 * @param {object} input
 * @param {Array|null}  input.integrations   /api/credentials/integrations rows
 * @param {Array|null}  input.connectors     /api/connectors `connectors[]`
 * @param {Array|null}  input.runs           run rows (tc_verdict, tc_verdict_detail)
 * @param {Array|null}  input.assertionRuns  /api/assertions/runs `runs[]`
 * @param {Array|null}  input.scenarios      scenario summaries (primary_kpi/threshold)
 * @param {number|null} input.assertionCount authored assertion artifacts
 */
export function buildConnectorLadder({
  integrations = null,
  connectors = null,
  runs = null,
  assertionRuns = null,
  scenarios = null,
  assertionCount = null,
} = {}) {
  // ── 1 · AUTHORED ─────────────────────────────────────────────────────────
  let authored
  if (scenarios === null && assertionCount === null) {
    authored = {
      state: RS.UNKNOWN, count: null, of: null,
      detail: 'The corpus has not loaded, so we cannot say what this deployment claims.',
    }
  } else {
    const list = Array.isArray(scenarios) ? scenarios : []
    // A scenario is "measurable" only when it carries a threshold the engine can
    // score. Most do not — and a DC needs that number, because it is the ceiling
    // on what any tenant connection could ever prove.
    const measurable = list.filter((s) => s && s.threshold && typeof s.threshold === 'object').length
    const kpiOnly = list.filter(
      (s) => s && s.primary_kpi && !(s.threshold && typeof s.threshold === 'object'),
    ).length
    const unknownShape = list.length > 0
      && list.every((s) => s && s.threshold === undefined && s.primary_kpi === undefined)
    if (unknownShape) {
      authored = {
        state: RS.UNKNOWN, count: null, of: list.length,
        detail:
          'This SimCore\'s /api/scenarios does not return `primary_kpi` / `threshold`, so the '
          + 'console cannot count how many scenarios carry a measurable bar. Unknown, not zero.',
      }
    } else {
      authored = {
        state: measurable > 0 || (assertionCount || 0) > 0 ? RS.YES : RS.NO,
        count: measurable,
        of: list.length,
        detail:
          `${measurable} of ${list.length} scenarios carry a scoreable threshold`
          + `${kpiOnly ? `, ${kpiOnly} more name a KPI with no bar` : ''}`
          + `${assertionCount !== null ? ` · ${assertionCount} assertion artifacts authored` : ''}.`,
      }
    }
  }

  // ── 2 · CONFIGURED ───────────────────────────────────────────────────────
  let configured
  const integs = Array.isArray(integrations) ? integrations : null
  if (integs === null) {
    configured = {
      state: RS.UNKNOWN, count: null, of: null,
      detail: 'Could not read /api/credentials/integrations — configuration is unknown, not absent.',
      byKind: {},
    }
  } else {
    const byKind = {}
    for (const i of integs) {
      if (!i || !i.kind) continue
      byKind[i.kind] = byKind[i.kind] || []
      byKind[i.kind].push(i)
    }
    const cortex = integs.filter((i) => i && (i.kind === 'xsiam' || i.kind === 'xsiam_tenant'))
    configured = {
      state: cortex.length > 0 ? RS.YES : RS.NO,
      count: cortex.length,
      of: null,
      byKind,
      detail: cortex.length
        ? `${cortex.length} Cortex credential${cortex.length === 1 ? '' : 's'} registered `
          + `(${Object.entries(byKind)
            .filter(([k]) => k === 'xsiam' || k === 'xsiam_tenant')
            .map(([k, v]) => `${v.length}× ${k}`).join(' · ')}).`
        : 'No Cortex credential is registered. Every measured verdict on this SimCore resolves '
          + '`pending` — which is correct, and is not the same as a detection failing.',
    }
  }

  // ── 3 · REACHABLE ────────────────────────────────────────────────────────
  // Sourced ONLY from an explicit operator probe. /api/health does not call a
  // tenant (it would spend the customer's quota to colour a pixel) and nothing
  // here may imply it did.
  let reachable
  if (integs === null) {
    reachable = {
      state: RS.UNKNOWN, count: null, of: null,
      detail: 'Unknown — the credential list could not be read.',
      never: [], failing: [],
    }
  } else {
    const cortex = integs.filter((i) => i && (i.kind === 'xsiam' || i.kind === 'xsiam_tenant'))
    const answered = cortex.filter((i) => i.last_verified_ok === true)
    const failing = cortex.filter((i) => i.last_verified_ok === false)
    const never = cortex.filter((i) => i.last_verified_ok === null || i.last_verified_ok === undefined)
    reachable = {
      state: cortex.length === 0 ? RS.NO : (answered.length > 0 ? RS.YES : RS.NO),
      count: answered.length,
      of: cortex.length,
      never: never.map((i) => i.name),
      failing: failing.map((i) => ({ name: i.name, error: i.last_verified_error || null })),
      detail: cortex.length === 0
        ? 'Nothing to reach — no credential registered.'
        : `${answered.length} of ${cortex.length} answered a probe`
          + `${never.length ? ` · ${never.length} never tested` : ''}`
          + `${failing.length ? ` · ${failing.length} failing` : ''}. `
          + 'A tenant that answers a health probe has proved auth and network at that moment — '
          + 'not that the key carries alert-read or XQL scope.',
    }
  }

  // ── 4 · VERIFIED ─────────────────────────────────────────────────────────
  let verified
  const runRows = Array.isArray(runs) ? runs : null
  const aRows = Array.isArray(assertionRuns) ? assertionRuns : null
  if (runRows === null && aRows === null) {
    verified = {
      state: RS.UNKNOWN, count: 0, of: null,
      detail: 'No run or assertion-run history could be read, so tenant verification is unknown.',
      runIds: [],
    }
  } else {
    const all = [...(runRows || []), ...(aRows || [])]
    const tenantProven = all.filter((r) => verdictProvenance(r) === 'tenant')
    const localScored = all.filter((r) => verdictProvenance(r) === 'local')
    verified = {
      state: tenantProven.length > 0 ? RS.YES : RS.NO,
      count: tenantProven.length,
      of: all.length,
      localScored: localScored.length,
      runIds: tenantProven.map((r) => r.run_id || r.id).filter(Boolean).slice(0, 5),
      detail: tenantProven.length > 0
        ? `${tenantProven.length} verdict${tenantProven.length === 1 ? '' : 's'} carry evidence of a `
          + 'tenant query.'
        : 'No verdict on this SimCore carries evidence that a query was issued against a tenant. '
          + `${localScored.length} verdict${localScored.length === 1 ? ' was' : 's were'} scored `
          + 'from local evidence (seeded results / pasted observations) — real measurement, but NOT '
          + 'proof against the customer\'s tenant.',
    }
  }

  const rungs = [
    {
      id: RUNG.AUTHORED,
      label: 'Authored',
      question: 'Does this deployment claim something measurable?',
      proves: 'a claim exists in the corpus',
      provesNot: 'nothing about the customer\'s environment',
      ...authored,
    },
    {
      id: RUNG.CONFIGURED,
      label: 'Configured',
      question: 'Is a Cortex credential registered?',
      proves: 'a key is in the vault',
      provesNot: 'that it works, or that its role is scoped for what you need',
      ...configured,
    },
    {
      id: RUNG.REACHABLE,
      label: 'Reachable',
      question: 'Did a tenant answer an explicit probe?',
      proves: 'auth + network reached the tenant at that moment',
      provesNot: 'that any detection was queried, or that the key has XQL scope',
      ...reachable,
    },
    {
      id: RUNG.VERIFIED,
      label: 'Verified',
      question: 'Did a tenant query produce a terminal verdict?',
      proves: 'the measurement loop closed against the customer\'s stack',
      provesNot: '—',
      ...verified,
    },
  ]

  // The highest rung reached, stopping at the first explicit NO. An `unknown`
  // rung does not break the chain — we could not evaluate it, and refusing to
  // report a VERIFIED verdict because the AUTHORED count was unreadable would
  // understate real evidence. An explicit NO does break it: a verified rung
  // above a not-configured one would be nonsense and means the inputs disagree.
  let highest = null
  for (const r of rungs) {
    if (r.state === RS.NO) break
    if (r.state === RS.YES) highest = r.id
  }

  return {
    rungs,
    highest,
    // The one sentence a DC may repeat to a customer. Never optimistic.
    headline: headlineFor(rungs, highest),
  }
}

function headlineFor(rungs, highest) {
  const byId = Object.fromEntries(rungs.map((r) => [r.id, r]))
  if (byId[RUNG.VERIFIED].state === RS.YES) {
    return `${byId[RUNG.VERIFIED].count} verdict(s) proven against a tenant`
  }
  if (byId[RUNG.REACHABLE].state === RS.YES) {
    return 'tenant reachable · nothing verified against it yet'
  }
  if (byId[RUNG.CONFIGURED].state === RS.YES) {
    return 'credential configured · tenant never answered a probe'
  }
  if (byId[RUNG.CONFIGURED].state === RS.UNKNOWN) {
    return 'connector state unknown — could not read credentials'
  }
  return highest === RUNG.AUTHORED
    ? 'authored only · no Cortex credential configured'
    : 'no connector state established'
}

/**
 * Per-tenant descriptor for the tenant list.
 *
 * `rung` is the highest rung THIS credential has reached. It is capped at
 * REACHABLE unless a verdict names it, because a credential cannot verify
 * itself.
 */
export function describeIntegration(integration, { tenantVerifiedNames = [] } = {}) {
  if (!integration) return null
  const name = integration.name || integration.id
  const kind = integration.kind || 'unknown'
  const meta = CREDENTIAL_KINDS[kind] || null
  const verified = tenantVerifiedNames.includes(name)
  const ok = integration.last_verified_ok

  let rung = RUNG.CONFIGURED
  if (ok === true) rung = RUNG.REACHABLE
  if (verified) rung = RUNG.VERIFIED

  let caveat
  if (verified) caveat = null
  else if (ok === true) {
    caveat =
      'Answered a probe. No query against this tenant has produced a verdict, so nothing about '
      + 'the customer\'s detections has been proven through it.'
  } else if (ok === false) {
    caveat = integration.last_verified_error
      ? `Last probe FAILED: ${integration.last_verified_error}`
      : 'Last probe failed.'
  } else {
    caveat = 'Never probed. Registering a key proves only that a key was typed in.'
  }

  return {
    name,
    kind,
    kindLabel: meta ? meta.label : kind,
    buys: meta ? meta.buys : null,
    doesNotBuy: meta ? meta.doesNotBuy : null,
    rung,
    reachable: ok === true ? RS.YES : ok === false ? RS.NO : RS.UNKNOWN,
    lastVerifiedAt: integration.last_verified_at || null,
    lastVerifiedError: integration.last_verified_error || null,
    caveat,
    host: hostOfTenant(integration),
  }
}

/** Host of a tenant credential, from either field spelling. Never throws, and
 *  never renders a key — config carries the URL and the key id only. */
export function hostOfTenant(integration) {
  const cfg = (integration && integration.config) || {}
  const raw = cfg.base_url || cfg.fqdn || ''
  if (!raw) return null
  try {
    return new URL(raw.includes('://') ? raw : `https://${raw}`).hostname || null
  } catch {
    return String(raw).replace(/^https?:\/\//, '').split('/')[0] || null
  }
}

/** Names of tenants that a terminal, tenant-sourced verdict points at. */
export function tenantVerifiedNames({ runs = [], assertionRuns = [] } = {}) {
  const out = new Set()
  for (const r of [...(runs || []), ...(assertionRuns || [])]) {
    if (verdictProvenance(r) !== 'tenant') continue
    const d = r.tc_verdict_detail || {}
    const n = r.tenant || d.tenant || (d.verification && (d.verification.tenant || d.verification.integration))
    if (n) out.add(String(n))
  }
  return Array.from(out)
}
