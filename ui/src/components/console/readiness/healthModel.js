/**
 * healthModel.js — the ONE mapping from a raw `/api/health` body to what the
 * console shows a DC before a customer is watching.
 *
 * Pure. No React, no fetch. Same convention (and the same reason) as
 * `supplyState.js` and `runStatus.js`: readiness that is re-derived in three
 * components drifts in three directions, and the direction it drifts in is
 * always "green".
 *
 * THREE RULES NO COMPONENT MAY BREAK
 * ----------------------------------
 * 1. **A catalog that exists to be non-empty and is empty is NOT ok.**
 *    A live SimCore booted without `tools/` answers
 *    `adapter_catalog: {status: "ok", count: 0}` and an overall `status: "ok"`,
 *    while every `adapter_ref` in the corpus is unresolvable and the payload
 *    shelf can stage nothing. The console down-grades that here, client-side,
 *    rather than waiting for the server to be fixed — because the deployment
 *    that is broken is exactly the deployment whose server has not been fixed.
 *    `serverSaidOk` is preserved on the row so the disagreement is visible and
 *    nobody has to guess which side is lying.
 *
 * 2. **A component the readiness contract expects and the server does not
 *    report is `unreported` — never `ok`, never a zero.** An older SimCore has
 *    no `payload_shelf` / `agent_binaries` / `integrations` component. Rendering
 *    those as "0 staged" would be a measurement the console never took. They
 *    render as a NAMED GAP with the reason, which is the honest answer and also
 *    tells the operator which SimCore build they are on.
 *
 * 3. **Nothing in here implies a tenant was reached.** `/api/health` must not
 *    call a customer's tenant (it would spend their metered quota to colour a
 *    pixel), so no row produced here may claim reachability. Tenant proof lives
 *    in `connectorState.js` and is sourced from an explicit operator probe.
 */

// ─── Status vocabulary ───────────────────────────────────────────────────────
// Five values. `unreported` and `unknown` are first-class and are NEVER
// coerced into `ok` — that coercion is the whole failure mode this file exists
// to prevent.
export const HS = {
  OK: 'ok',
  DEGRADED: 'degraded',
  ERROR: 'error',
  UNREPORTED: 'unreported',
  UNKNOWN: 'unknown',
}

export const HS_LABEL = {
  [HS.OK]: 'OK',
  [HS.DEGRADED]: 'DEGRADED',
  [HS.ERROR]: 'ERROR',
  [HS.UNREPORTED]: 'NOT REPORTED',
  [HS.UNKNOWN]: 'UNKNOWN',
}

/** Colour is never the only signal — a projector flattens hue and WCAG does not
 *  accept it either, so every chip also renders HS_LABEL. */
export const HS_CHIP = {
  [HS.OK]: 'chip chip--detected',
  [HS.DEGRADED]: 'chip chip--pending',
  [HS.ERROR]: 'chip chip--missed',
  [HS.UNREPORTED]: 'chip',
  [HS.UNKNOWN]: 'chip',
}

const RANK = { [HS.OK]: 0, [HS.UNREPORTED]: 1, [HS.UNKNOWN]: 1, [HS.DEGRADED]: 2, [HS.ERROR]: 3 }

/**
 * The components a DC's readiness depends on, in the order they are read.
 *
 * `countKey`   — which numeric field carries the population.
 * `zeroIsBad`  — a count of 0 means the deployment cannot do its job.
 * `required`   — the contract expects this component; its ABSENCE is a gap
 *                worth naming (an older SimCore, or a probe orphaned by a merge).
 */
const SPEC = [
  {
    key: 'db', label: 'Database', countKey: null, zeroIsBad: false, required: true,
    purpose: 'run history — scenarios, cards and adapters load from disk, not from here',
    remediation:
      'SimCore cannot reach its SQLite store. In Docker check for `:ro` on the data volume; '
      + 'on a host, chown the data directory to the uid SimCore runs as.',
  },
  {
    key: 'scenario_catalog', label: 'Scenario catalog', countKey: 'count', zeroIsBad: true, required: true,
    purpose: 'the corpus the Library launches from',
    remediation:
      '0 scenarios loaded — scenarios/ is missing from this deployment or every file was '
      + 'rejected by the schema validator. Check the boot log for rejections.',
  },
  {
    key: 'ttp_catalog', label: 'TTP cards', countKey: 'count', zeroIsBad: true, required: true,
    purpose: 'the detection objects each scenario step claims',
    remediation:
      '0 TTP cards loaded — detection_scanner/ttps/ is missing, so no scenario can resolve '
      + 'a detection_id and the proof surfaces will render empty.',
  },
  {
    key: 'adapter_catalog', label: 'Tool adapters', countKey: 'count', zeroIsBad: true, required: true,
    purpose: 'every scenario external_tools[].adapter_ref resolves through this',
    remediation:
      '0 adapter packs loaded — tools/ is missing from this deployment (core/Dockerfile must '
      + 'COPY tools/). Every adapter_ref will be unresolvable and the payload shelf can stage nothing.',
  },
  {
    key: 'uctc_registry', label: 'UC / TC index', countKey: 'count', zeroIsBad: true, required: true,
    purpose: 'the sales-motion index scenario refs are a foreign key into',
    remediation:
      'The FY27 index snapshot is absent, so ref validation is advisory and the UC/TC surface '
      + 'is degraded. This is 200-with-index_loaded:false, NOT "0 test cases".',
  },
  {
    key: 'eal', label: 'EAL plugins', countKey: 'plugins', zeroIsBad: true, required: true,
    purpose: 'the signal shapes the identity harness cannot produce',
    remediation: '0 EAL plugins registered — the Traffic/EAL destination cannot run a campaign.',
  },
  {
    key: 'payload_shelf', label: 'Payload shelf', countKey: 'staged', zeroIsBad: false, required: true,
    purpose: 'tools this SimCore serves to the target instead of the public internet',
    remediation:
      'Adapter packs declare staged artifacts that are not on the shelf. Scenarios binding them '
      + 'refuse at compose with PAYLOAD_NOT_STAGED. Fix: ./scripts/build-payloads.sh, then rebuild.',
  },
  {
    key: 'agent_binaries', label: 'Beacon binaries', countKey: null, zeroIsBad: false, required: true,
    purpose: 'what GET /api/agents/binary can hand an enrolling target',
    remediation:
      'The agent-dist matrix is short. GET /api/agents/binary?os=… will 404 for the missing '
      + 'platform and its installer refuses before enrolling.',
  },
  {
    key: 'integrations', label: 'Tenant credentials', countKey: null, zeroIsBad: false, required: true,
    purpose: 'which Cortex credentials are CONFIGURED — not whether any tenant answered',
    remediation:
      'No Cortex tenant credential is registered, so every measured verdict resolves `pending`. '
      + 'Register one under Tenants.',
  },
  {
    key: 'xsiam_operation_catalog', label: 'XSIAM operations', countKey: 'count', zeroIsBad: true, required: true,
    purpose: 'the read-only Cortex API operation packs',
    remediation:
      '0 XSIAM operation packs loaded — the operation catalog is empty and every typed tenant '
      + 'call will 404 in-process.',
  },
  // ── Components a current SimCore adds. `required: false` deliberately: an
  // older build not reporting them is not a gap worth naming (the console has
  // its own sources for agents and campaigns), but when they ARE reported they
  // must be labelled rather than dumped as raw keys.
  {
    key: 'agents', label: 'Enrolled beacons', countKey: 'registered', zeroIsBad: false, required: false,
    purpose: 'derived from last_seen age — SimCore never dials a target',
    remediation:
      'No beacon is enrolled, so a pull-mode launch has nowhere to go. Push mode needs none.',
  },
  {
    key: 'task_queue', label: 'Task queue', countKey: 'queued', zeroIsBad: false, required: false,
    purpose: 'undelivered work; a stranded task is a run that will never execute',
    remediation:
      'Tasks are stranded — an agent was queued work it never collected. Check that beacon is '
      + 'polling, or abort the run rather than leaving it `running` forever.',
  },
  {
    key: 'eal_collectors', label: 'EAL collectors', countKey: 'campaigns', zeroIsBad: false, required: false,
    purpose: 'where campaign records are POSTed — reachability is NOT probed here',
    remediation: 'A campaign collector is misconfigured. Run the campaign preflight before launching.',
  },
]

const SPEC_BY_KEY = new Map(SPEC.map((s) => [s.key, s]))

/** Why a required component might be missing from THIS SimCore's response. */
const GAP_REASON = {
  payload_shelf:
    'This SimCore\'s /api/health does not report a payload_shelf component, so the console '
    + 'cannot tell you from here whether declared artifacts are staged. Tools & Payloads reads '
    + '/api/shelf directly and is authoritative.',
  agent_binaries:
    'This SimCore\'s /api/health does not report an agent_binaries component, so the console '
    + 'cannot tell you which OS a target can enrol from until you try.',
  integrations:
    'This SimCore\'s /api/health does not report an integrations component. The Connector '
    + 'ladder below reads /api/credentials/integrations directly instead.',
  xsiam_operation_catalog:
    'This SimCore\'s /api/health does not report xsiam_operation_catalog. On builds where that '
    + 'probe sits after `return components` it has never executed — the catalog may be loaded '
    + 'and simply invisible here, or empty. Unknown, not ok.',
  uctc_registry:
    'This SimCore\'s /api/health does not report uctc_registry, so index-snapshot presence is '
    + 'unknown from here. /api/uctc/summary carries index_loaded and is authoritative.',
}

function pickCount(raw, spec) {
  if (!raw || typeof raw !== 'object') return null
  if (spec && spec.countKey && typeof raw[spec.countKey] === 'number') return raw[spec.countKey]
  // Fall back to the two field names the server uses across components so a
  // newly-added count is displayed rather than silently dropped.
  if (typeof raw.count === 'number') return raw.count
  if (typeof raw.plugins === 'number') return raw.plugins
  return null
}

/**
 * Normalise ONE component row.
 *
 * @param {string} key
 * @param {object|undefined} raw  the server's component object, if present
 * @returns {{key, label, status, serverStatus, serverSaidOk, count, detail,
 *            remediation, purpose, disagreement}}
 */
export function normalizeComponent(key, raw) {
  const spec = SPEC_BY_KEY.get(key) || {
    key, label: key, countKey: null, zeroIsBad: false, required: false,
    purpose: '', remediation: '',
  }

  if (raw === undefined || raw === null) {
    return {
      key,
      label: spec.label,
      status: HS.UNREPORTED,
      serverStatus: null,
      serverSaidOk: false,
      count: null,
      detail: GAP_REASON[key] || 'This SimCore does not report this component.',
      remediation: spec.remediation || '',
      purpose: spec.purpose || '',
      disagreement: null,
      extra: {},
    }
  }

  const serverStatus = typeof raw.status === 'string' ? raw.status : HS.UNKNOWN
  const count = pickCount(raw, spec)
  let status = [HS.OK, HS.DEGRADED, HS.ERROR].includes(serverStatus) ? serverStatus : HS.UNKNOWN
  let disagreement = null

  // RULE 1 — a population that must not be empty and is empty is degraded, no
  // matter what the server called it.
  if (spec.zeroIsBad && count === 0 && status === HS.OK) {
    status = HS.DEGRADED
    disagreement =
      'This SimCore reported `ok` with a count of 0. A catalog whose whole purpose is to be '
      + 'non-empty and is empty is not healthy, so the console shows it as degraded.'
  }

  // payload_shelf carries its own ok-condition: staged must cover declared and
  // nothing may be unpinned. A server that has not learned that rule is corrected here.
  if (key === 'payload_shelf' && status === HS.OK) {
    const declared = typeof raw.declared === 'number' ? raw.declared : null
    const staged = typeof raw.staged === 'number' ? raw.staged : null
    const unpinned = typeof raw.unpinned === 'number' ? raw.unpinned : 0
    if (declared !== null && staged !== null && (staged < declared || unpinned > 0)) {
      status = HS.DEGRADED
      disagreement =
        `This SimCore reported \`ok\` with ${staged}/${declared} artifacts staged`
        + `${unpinned > 0 ? ` and ${unpinned} unpinned` : ''}. Anything not on the shelf is fetched `
        + 'by the TARGET from the public internet at run time.'
    }
  }

  return {
    key,
    label: spec.label,
    status,
    serverStatus,
    serverSaidOk: serverStatus === HS.OK,
    count,
    // A current SimCore ships a stable `code` per component and a `code_hint`
    // naming the specific condition (e.g. NO_AGENTS_ENROLLED). Both are the
    // server's own words for what happened and are shown verbatim — inventing a
    // parallel client-side vocabulary is how two descriptions of one fault drift.
    code: typeof raw.code === 'string' ? raw.code : null,
    codeHint: typeof raw.code_hint === 'string' ? raw.code_hint : null,
    detail: typeof raw.detail === 'string' ? raw.detail : null,
    // THE SERVER'S WORDS WIN. A current SimCore ships a `detail` that names the
    // fix precisely for THAT deployment ("set CORTEXSIM_BASE_DIR at a checkout
    // that contains tools/packs/"); ours is a generic fallback written for
    // builds that ship nothing. Rendering both printed the same fault twice in
    // slightly different words — found by driving a real tools-less SimCore —
    // which is exactly how two descriptions of one problem start to drift.
    remediation: (status === HS.OK || typeof raw.detail === 'string')
      ? ''
      : (spec.remediation || ''),
    purpose: spec.purpose || '',
    disagreement,
    // Everything the server sent that we did not model — rendered verbatim so a
    // newly-added field reaches the operator without a console release.
    extra: Object.fromEntries(
      Object.entries(raw).filter(
        ([k]) => !['status', 'count', 'plugins', 'detail', 'code', 'code_hint'].includes(k),
      ),
    ),
  }
}

/**
 * Build the full readiness model.
 *
 * @param {object|null} body    the /api/health body, or null when the probe failed
 * @param {object} [opts]
 * @param {string|null} [opts.error]  the fetch error message, when unreachable
 * @returns {{reachable, overall, version, commit, components, degraded, gaps, counts}}
 */
export function buildHealthModel(body, { error = null } = {}) {
  if (!body || typeof body !== 'object') {
    return {
      reachable: false,
      overall: HS.ERROR,
      version: null,
      commit: null,
      components: [],
      degraded: [],
      gaps: [],
      notChecked: [],
      unreachableReason:
        error
        || 'SimCore did not answer /api/health. Every other surface swallows its failure into an '
           + 'empty list, so the console would look configured-but-empty rather than disconnected.',
      counts: { ok: 0, degraded: 0, error: 0, unreported: 0 },
    }
  }

  const raw = body.components && typeof body.components === 'object' ? body.components : {}

  // Spec order first (the order a DC reads them in), then anything the server
  // reports that we have not modelled — never dropped.
  const keys = [
    ...SPEC.map((s) => s.key),
    ...Object.keys(raw).filter((k) => !SPEC_BY_KEY.has(k)),
  ]

  const components = keys
    .map((k) => normalizeComponent(k, raw[k]))
    // A component that is neither reported NOR required is not a row at all.
    .filter((c) => c.status !== HS.UNREPORTED || (SPEC_BY_KEY.get(c.key)?.required ?? false))

  const degraded = components
    .filter((c) => c.status === HS.DEGRADED || c.status === HS.ERROR)
    .map((c) => c.key)

  const gaps = components
    .filter((c) => c.status === HS.UNREPORTED)
    .map((c) => ({ key: c.key, label: c.label, why: c.detail }))

  // `not_checked[]` is the SERVER declaring what it deliberately did not
  // measure, and why, and which endpoint would. That is strictly better than
  // anything the console can infer, and it is the honest counterpart to a green
  // health check: tenant reachability, collector reachability and detection
  // efficacy are all absent from `/api/health` ON PURPOSE, and a DC must be able
  // to see that rather than assume a green page covered them.
  const notChecked = Array.isArray(body.not_checked)
    ? body.not_checked
      .filter((n) => n && typeof n === 'object')
      .map((n) => ({ what: n.what || '?', why: n.why || '', checkedBy: n.checked_by || null }))
    : []

  // Prefer the server's own list when it ships one (P0-4 adds it) but UNION it
  // with ours: the server naming a component degraded is authoritative, and our
  // zero-count rule catches the build where it does not.
  const serverDegraded = Array.isArray(body.degraded_components) ? body.degraded_components : []
  const degradedUnion = Array.from(new Set([...serverDegraded, ...degraded]))

  // Worst status among components the server ACTUALLY reported. `unreported`
  // is excluded here deliberately: it is an unknown about this build, not a
  // fault in the deployment, and letting it drive the header would paint every
  // older SimCore red and train operators to ignore the colour.
  const worst = components
    .filter((c) => c.status !== HS.UNREPORTED)
    .reduce((acc, c) => (RANK[c.status] > RANK[acc] ? c.status : acc), HS.OK)
  // A gap is not a degradation — it is an unknown. It must not turn the header
  // amber (that would make every older SimCore look broken) but it must never
  // be silent either, which is what the `gaps` list is for.
  const overall = degradedUnion.length ? (worst === HS.ERROR ? HS.ERROR : HS.DEGRADED) : worst

  const counts = components.reduce(
    (acc, c) => { acc[c.status] = (acc[c.status] || 0) + 1; return acc },
    { ok: 0, degraded: 0, error: 0, unreported: 0, unknown: 0 },
  )

  return {
    reachable: true,
    overall,
    version: body.version || null,
    commit: body.commit || null,
    components,
    degraded: degradedUnion,
    gaps,
    notChecked,
    unreachableReason: null,
    counts,
  }
}

/** One-line summary for the banner / header. Never says "ok" when it is not. */
export function healthHeadline(model) {
  if (!model) return 'readiness unknown'
  if (!model.reachable) return 'SimCore unreachable'
  if (model.overall === HS.OK && model.gaps.length === 0) return 'all components ok'
  if (model.overall === HS.OK) return `all reported components ok · ${model.gaps.length} not reported`
  const names = model.degraded.map((k) => SPEC_BY_KEY.get(k)?.label || k)
  return `${names.length} component${names.length === 1 ? '' : 's'} degraded — ${names.join(' · ')}`
}

export { SPEC as HEALTH_COMPONENT_SPEC }
