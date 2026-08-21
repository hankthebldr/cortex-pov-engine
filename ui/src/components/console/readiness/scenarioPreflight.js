/**
 * scenarioPreflight.js — "can this scenario actually run, on THIS target, right
 * now?" answered BEFORE the operator clicks Launch.
 *
 * Pure. No React, no fetch.
 *
 * WHY IT LIVES AT THE LAUNCH BUTTON
 * ---------------------------------
 * Every failure this checks for has already shipped in this product, and each
 * one manufactures the same artefact: a run that did not execute as authored,
 * whose absent detections read in a POV report as a gap in the CUSTOMER's
 * coverage. A step whose tool never downloaded, a step that never ran because
 * the service account has a nologin shell, a Windows step executed as bash — all
 * of them produce "0/14 detections confirmed" and none of them is the customer's
 * fault. The console has to say so before the run, not after.
 *
 * A separate readiness page would be visited once, on a good day. So this
 * renders where the DC already is.
 *
 * FOUR SEVERITIES, AND `unknown` IS ONE OF THEM
 * ---------------------------------------------
 *   pass    — checked, and it holds.
 *   warn    — checked, and it will degrade something. Launch stays enabled;
 *             the DC may know egress is fine, and ceremony is not safety.
 *   block   — SimCore's own guard will refuse this launch. A named dead button
 *             beats a 409 mid-demo.
 *   unknown — we could not check. NEVER rendered as a pass. An older SimCore
 *             that does not report shelf state is not a SimCore whose tools are
 *             staged.
 */

import { SUPPLY, supplyOf } from '../supplyState.js'

export const PF = { PASS: 'pass', WARN: 'warn', BLOCK: 'block', UNKNOWN: 'unknown' }

export const PF_CHIP = {
  [PF.PASS]: 'chip chip--detected',
  [PF.WARN]: 'chip chip--pending',
  [PF.BLOCK]: 'chip chip--missed',
  [PF.UNKNOWN]: 'chip',
}

export const PF_LABEL = {
  [PF.PASS]: 'READY',
  [PF.WARN]: 'DEGRADED',
  [PF.BLOCK]: 'BLOCKED',
  [PF.UNKNOWN]: 'UNKNOWN',
}

const SEVERITY = { [PF.PASS]: 0, [PF.UNKNOWN]: 1, [PF.WARN]: 2, [PF.BLOCK]: 3 }

/** Identities that need no wrapper — they cannot fail on account grounds. */
const NO_WRAPPER_IDENTITIES = new Set(['direct', 'root', 'container-runtime', '']) // '' = unset

/**
 * Run the preflight.
 *
 * Everything is optional; an absent input yields `unknown` on the checks it
 * feeds, never a pass.
 *
 * @param {object} input
 * @param {object|null} input.scenario      full scenario detail
 * @param {object|null} input.target        {kind:'agent'|'push'|'iac', id, label}
 * @param {Array}       input.agents        /api/agents rows
 * @param {object|null} input.shelf         normalizeShelf() output
 * @param {object}      input.adapterIndex  adapter_id → adapter row
 * @param {Array|null}  input.integrations  /api/credentials/integrations rows
 * @param {string}      input.identity      the identity the operator selected
 * @param {string}      input.pushFormat    'bash' | 'k8s'
 * @returns {{verdict, checks, blocking, warnings, unknowns}}
 */
export function preflightScenario({
  scenario = null,
  target = null,
  agents = [],
  shelf = null,
  adapterIndex = {},
  integrations = null,
  identity = '',
  pushFormat = 'bash',
} = {}) {
  const checks = []
  if (!scenario) {
    return { verdict: PF.UNKNOWN, checks: [], blocking: [], warnings: [], unknowns: [] }
  }

  checks.push(toolCheck(scenario, shelf, adapterIndex))
  checks.push(targetCheck(scenario, target, agents))
  checks.push(platformCheck(scenario, target, agents, pushFormat))
  checks.push(identityCheck(scenario, target, agents, identity))
  checks.push(measurementCheck(scenario, integrations))

  const verdict = checks.reduce(
    (acc, c) => (SEVERITY[c.status] > SEVERITY[acc] ? c.status : acc),
    PF.PASS,
  )

  return {
    verdict,
    checks,
    blocking: checks.filter((c) => c.status === PF.BLOCK),
    warnings: checks.filter((c) => c.status === PF.WARN),
    unknowns: checks.filter((c) => c.status === PF.UNKNOWN),
  }
}

// ─── 1 · Tool supply ─────────────────────────────────────────────────────────

function toolCheck(scenario, shelf, adapterIndex) {
  const refs = Array.from(new Set(
    (scenario.external_tools || []).map((t) => t && t.adapter_ref).filter(Boolean),
  ))

  const declaredPayloads = (scenario.cluster_posture && scenario.cluster_posture.payloads) || []

  if (!refs.length && !declaredPayloads.length) {
    return mk('tools', 'Tooling', PF.PASS, 'no external tools declared',
      'This scenario runs entirely on the target\'s own binaries.')
  }

  if (!shelf || shelf.available !== true) {
    return mk('tools', 'Tooling', PF.UNKNOWN,
      `${refs.length} tool${refs.length === 1 ? '' : 's'} declared · shelf state unreadable`,
      'This SimCore does not report payload-shelf state, so we cannot tell whether these tools '
      + 'are served from here or fetched by the target from the public internet. UNKNOWN is not '
      + '"fine": if the target has no egress, the step runs WITHOUT its tool and produces no '
      + 'detection.',
      'Open Tools & Payloads, or upgrade this SimCore to one that serves /api/shelf.')
  }

  // A scenario that DECLARES a payload and is missing it is a hard stop —
  // SimCore's own guard answers 409 PAYLOAD_NOT_STAGED.
  const missing = declaredPayloads.filter((n) => !shelf.stagedNames.has(n))
  if (missing.length) {
    return mk('tools', 'Tooling', PF.BLOCK,
      `${missing.length} declared payload${missing.length === 1 ? '' : 's'} not staged`,
      `This scenario declares ${missing.join(', ')} and ${missing.length === 1 ? 'it is' : 'they are'} `
      + 'not on the shelf. SimCore will refuse the launch with PAYLOAD_NOT_STAGED.',
      'Stage them in Tools & Payloads.',
      { payloads: missing })
  }

  const supplies = refs.map((r) => ({ ref: r, adapter: adapterIndex[r] || null }))
  const unresolved = supplies.filter((s) => !s.adapter).map((s) => s.ref)
  if (unresolved.length === refs.length && refs.length > 0) {
    return mk('tools', 'Tooling', PF.UNKNOWN,
      `${refs.length} adapter_ref${refs.length === 1 ? '' : 's'} unresolved`,
      `None of ${unresolved.join(', ')} resolves against this SimCore's adapter catalog. That is `
      + 'the signature of a deployment booted without tools/ — the catalog is empty and nothing '
      + 'can be staged.',
      'Check /api/health → adapter_catalog. If its count is 0, the image is missing tools/.')
  }

  const resolved = supplies.filter((s) => s.adapter).map((s) => ({
    ref: s.ref, name: s.adapter.name || s.ref, supply: supplyOf(s.adapter, shelf),
  }))
  const egress = resolved.filter(
    (r) => r.supply.state === SUPPLY.UNSTAGED || r.supply.state === SUPPLY.RUNTIME_FETCH,
  )
  const unknown = resolved.filter((r) => r.supply.state === SUPPLY.UNKNOWN)
  const staged = resolved.filter((r) => r.supply.state === SUPPLY.STAGED)

  if (unknown.length) {
    return mk('tools', 'Tooling', PF.UNKNOWN,
      `${unknown.length} of ${resolved.length} tools: supply unknown`,
      `Supply state could not be determined for ${unknown.map((u) => u.name).join(', ')}.`,
      'Open Tools & Payloads.')
  }

  if (egress.length) {
    return mk('tools', 'Tooling', PF.WARN,
      `${egress.length} of ${resolved.length} tools fetched by the TARGET at run time`,
      `${egress.map((e) => e.name).join(' · ')} ${egress.length === 1 ? 'is' : 'are'} not served `
      + 'from this SimCore. On a default-deny network the fetch fails and the step runs without '
      + 'its tool — producing no detection, which then reads in the report as a coverage gap.',
      'Stage them in Tools & Payloads, or confirm this target has egress to those origins.',
      { tools: egress.map((e) => ({ name: e.name, ref: e.ref, egress: e.supply.egressLabel })) })
  }

  return mk('tools', 'Tooling', PF.PASS,
    `${staged.length ? `${staged.length} staged · ` : ''}no target egress required`,
    resolved.length
      ? `${resolved.map((r) => r.name).join(' · ')} — served from this SimCore or already present `
        + 'on the target.'
      : 'No runtime-fetched tools.')
}

// ─── 2 · Target ──────────────────────────────────────────────────────────────

function targetCheck(scenario, target, agents) {
  if (!target) {
    return mk('target', 'Target', PF.BLOCK, 'no target selected',
      'Pick an enrolled beacon (pull) or the offline bundle (push).')
  }

  if (target.kind !== 'agent') {
    const supportsPush = scenario.push_supported !== false
    if (!supportsPush) {
      return mk('target', 'Target', PF.BLOCK, 'this scenario does not support push',
        'The scenario declares push_supported: false — it must run through an enrolled beacon.',
        'Enrol a beacon from Agents and pick it as the target.')
    }
    return mk('target', 'Target', PF.PASS, 'offline bundle (push)',
      'SimCore generates a self-contained bundle. Nothing about the destination host is known '
      + 'to this console, so tool egress and account preconditions are the operator\'s to verify.')
  }

  const agent = (agents || []).find((a) => (a.agent_id || a.id) === target.id) || null
  if (!agent) {
    return mk('target', 'Target', PF.UNKNOWN, `beacon ${target.id} not in the registry`,
      'The selected beacon is not in this SimCore\'s agent list. It may have been pruned, or the '
      + 'list is stale.',
      'Refresh Agents and re-pick the target.')
  }

  const status = String(agent.status || '').toLowerCase()
  const age = typeof agent.last_seen_age_seconds === 'number'
    ? `${Math.round(agent.last_seen_age_seconds)}s since last poll`
    : 'last poll unknown'

  if (status === 'offline') {
    return mk('target', 'Target', PF.BLOCK, `beacon offline (${age})`,
      `${agent.hostname || target.id} has not polled recently. A pull-mode task queued now sits `
      + 'undelivered; the run stays `running` and nothing executes.',
      'Restart the beacon on the target (systemctl status cortexsim-agent), or use push mode.')
  }
  if (status === 'stale') {
    return mk('target', 'Target', PF.WARN, `beacon stale (${age})`,
      'The beacon has missed poll intervals. The task will be delivered whenever it next polls — '
      + 'which may be after the customer has stopped watching.',
      'Check the beacon\'s connectivity back to this SimCore before launching.')
  }

  const caps = Array.isArray(agent.capabilities) ? agent.capabilities : []
  return mk('target', 'Target', PF.PASS,
    `${agent.hostname || target.id} online · ${agent.os || 'os?'}`,
    `Capabilities: ${caps.length ? caps.join(', ') : 'none advertised'}. ${age}.`,
    '', { agent })
}

// ─── 3 · Mode / platform ─────────────────────────────────────────────────────

function platformCheck(scenario, target, agents, pushFormat) {
  const steps = Array.isArray(scenario.steps) ? scenario.steps : []
  const declaring = steps.filter((s) => Array.isArray(s.platforms) && s.platforms.length)
  if (!declaring.length) {
    return mk('platform', 'Platform', PF.UNKNOWN, 'no step declares a platform',
      'No step in this scenario declares `platforms`, so the console cannot check that the target '
      + 'can run it. The commands may still be POSIX-only or PowerShell-only — a mismatch shows up '
      + 'as exit 127 on the first step.')
  }

  const union = Array.from(new Set(declaring.flatMap((s) => s.platforms)))

  if (target && target.kind !== 'agent') {
    // Push. The bundle format has to be able to carry the steps.
    const windowsOnly = union.length === 1 && union[0] === 'windows'
    if (windowsOnly && pushFormat !== 'powershell') {
      return mk('platform', 'Platform', PF.BLOCK,
        'windows-only scenario in a non-PowerShell bundle',
        'Every step declares `windows`, and this bundle format cannot carry PowerShell. SimCore '
        + 'answers 409 BUNDLE_TARGET_UNSATISFIABLE naming the offending steps.',
        'Download the bundle as PowerShell (format=powershell), or run it through a Windows beacon.')
    }
    return mk('platform', 'Platform', PF.PASS, `declares ${union.join(' · ')}`,
      `${declaring.length} of ${steps.length} steps declare a platform. The bundle is generated for `
      + `the ${pushFormat} target.`)
  }

  const agent = (agents || []).find((a) => (a.agent_id || a.id) === (target && target.id)) || null
  if (!agent || !agent.os) {
    return mk('platform', 'Platform', PF.UNKNOWN, `declares ${union.join(' · ')}`,
      'The target\'s OS is unknown, so platform compatibility cannot be checked.')
  }

  const os = String(agent.os).toLowerCase()
  const runnable = declaring.filter((s) => s.platforms.some((p) => matchesOs(p, os)))
  if (!runnable.length) {
    return mk('platform', 'Platform', PF.BLOCK,
      `no step declares ${os}`,
      `Every platform-declaring step targets ${union.join(' / ')} and the beacon is ${os}. `
      + 'Running it here executes the wrong command interpreter — historically this passed as '
      + 'green while doing nothing the scenario claimed.',
      `Pick a ${union.join(' or ')} target, or use push mode with the matching bundle format.`)
  }
  if (runnable.length < declaring.length) {
    const skipped = declaring.length - runnable.length
    return mk('platform', 'Platform', PF.WARN,
      `${skipped} of ${declaring.length} platform-bearing steps do not declare ${os}`,
      `Those steps will run whatever their command text is on a ${os} host. A step authored for a `
      + 'different OS that runs anyway is the shape that produced "52 steps claimed Windows '
      + 'coverage while running Linux bash".',
      'Check the step list, or pick a target matching every declared platform.')
  }

  return mk('platform', 'Platform', PF.PASS, `${os} matches every declared step`,
    `${declaring.length} platform-bearing steps all declare ${os}.`)
}

function matchesOs(platform, os) {
  const p = String(platform).toLowerCase()
  if (p === os) return true
  // container/k8s steps run wherever the runtime is — a linux beacon satisfies them.
  if ((p === 'container' || p === 'k8s') && os === 'linux') return true
  if (p === 'macos' && os === 'darwin') return true
  if (p === 'darwin' && os === 'macos') return true
  return false
}

// ─── 4 · Execution identity ──────────────────────────────────────────────────

function identityCheck(scenario, target, agents, identity) {
  const chosen = identity || (scenario.execution_identity && scenario.execution_identity.default) || ''
  const stepIdentities = Array.from(new Set(
    (Array.isArray(scenario.steps) ? scenario.steps : [])
      .map((s) => s && s.identity).filter(Boolean),
  ))
  const wrapped = [chosen, ...stepIdentities].filter((i) => i && !NO_WRAPPER_IDENTITIES.has(i))

  if (!wrapped.length) {
    return mk('identity', 'Execution identity', PF.PASS, chosen ? `${chosen} — no wrapper` : 'direct',
      'No step needs a service-account wrapper, so no account precondition can stop this run.')
  }

  const uniq = Array.from(new Set(wrapped))

  if (!target || target.kind !== 'agent') {
    return mk('identity', 'Execution identity', PF.WARN, `${uniq.join(' · ')} on an unknown host`,
      `The bundle wraps steps as ${uniq.join(', ')}. If those accounts do not exist on the target, `
      + 'or their shell is /usr/sbin/nologin (the stock Debian/Ubuntu default for www-data), the '
      + 'wrapper fails and the step does not run. That is a precondition on the TARGET, NOT a gap '
      + 'in the customer\'s detection coverage — do not report the missing detections as misses.',
      `On the target: getent passwd ${uniq[0]} — confirm it exists with a login shell.`)
  }

  const agent = (agents || []).find((a) => (a.agent_id || a.id) === target.id) || null
  const caps = (agent && Array.isArray(agent.capabilities)) ? agent.capabilities : []
  const os = String((agent && agent.os) || '').toLowerCase()

  if (os === 'windows') {
    return mk('identity', 'Execution identity', PF.WARN,
      `${uniq.join(' · ')} will NOT be honoured on Windows`,
      'Windows has no credential-free unattended impersonation, so the beacon collapses every '
      + 'non-direct identity to `direct` and records an IDENTITY NOT HONOURED degradation on the '
      + 'run. The process-ancestry chain will show the beacon\'s own account, not the service '
      + 'account the scenario authored.',
      'Expect the causality chain to differ. Do not report the resulting lineage as the customer\'s.')
  }

  if (agent && !caps.includes('identity-harness')) {
    return mk('identity', 'Execution identity', PF.WARN,
      `${uniq.join(' · ')} · beacon does not advertise identity-harness`,
      `This beacon registered capabilities [${caps.join(', ') || 'none'}] and identity-harness is `
      + 'not among them, so steps may run as the beacon\'s own account.',
      'Re-enrol the beacon from a current build, or accept a direct-identity causality chain.')
  }

  // The account-runnability probe is a SERVER capability. Say plainly that we
  // have not checked it rather than implying the accounts are fine.
  const probe = agent && (agent.identity_probe || agent.identity_probe_result)
  if (probe && typeof probe === 'object') {
    const bad = uniq.filter((u) => probe[u] && probe[u].runnable === false)
    if (bad.length) {
      return mk('identity', 'Execution identity', PF.BLOCK,
        `${bad.join(', ')} cannot run on this target`,
        `SimCore probed this beacon and ${bad.join(', ')} ${bad.length === 1 ? 'is' : 'are'} not `
        + 'runnable — the account is absent, or its shell is nologin and no fallback wrapper is '
        + 'available. THESE STEPS WOULD NOT RUN.',
        `On the target: usermod -s /bin/bash ${bad[0]} (revert after the POV), or install sudo and `
        + 'grant the beacon NOPASSWD for that account, or launch with identity=direct.')
    }
    return mk('identity', 'Execution identity', PF.PASS, `${uniq.join(' · ')} runnable`,
      'SimCore probed this target and every declared identity resolves to a working wrapper.')
  }

  return mk('identity', 'Execution identity', PF.UNKNOWN,
    `${uniq.join(' · ')} — not probed on this target`,
    'This SimCore has not probed whether these accounts exist on the target with a login shell. '
    + 'On stock Debian/Ubuntu www-data has /usr/sbin/nologin, and a beacon whose wrapper chain is '
    + 'runuser-only dies at step 1 with "This account is currently not available" — the remaining '
    + 'steps never run and their detections read as missed.',
    `Before launching, on the target: getent passwd ${uniq[0]}`)
}

// ─── 5 · Measurement ─────────────────────────────────────────────────────────

function measurementCheck(scenario, integrations) {
  const hasThreshold = scenario.threshold && typeof scenario.threshold === 'object'
  const kpi = scenario.primary_kpi || null
  const fieldsAbsent = scenario.threshold === undefined && scenario.primary_kpi === undefined

  if (fieldsAbsent) {
    return mk('measurement', 'Measurement', PF.UNKNOWN, 'KPI contract not reported',
      'This SimCore\'s scenario payload does not carry primary_kpi / threshold, so the console '
      + 'cannot say whether this run will produce a scored verdict. Unknown, not "no KPI".')
  }

  if (!hasThreshold) {
    return mk('measurement', 'Measurement', PF.PASS,
      kpi ? `${kpi} — no scoreable bar` : 'no scoreable threshold',
      'This scenario produces EVIDENCE, not a scored pass. Its verdict will resolve '
      + '`not_applicable`, which is the honest answer for a qualitative test case — you cannot '
      + 'machine-certify it passed, though you can still disprove it.')
  }

  // MTTD is the only KPI the engine measures natively. A threshold on Detection
  // Accuracy / Cross-Source Correlation Rate / Causality Chain Completeness has
  // nothing that produces a `measured_value`, so `score_run` returns `pending`
  // for it FOREVER — with or without a tenant. Saying "ready to measure" here
  // because a credential exists would promise a verdict that cannot arrive.
  if (!isMttdShaped(kpi, scenario.threshold)) {
    return mk('measurement', 'Measurement', PF.WARN,
      `${kpi || 'threshold'} — the engine has no producer for this KPI`,
      `This scenario's bar is on "${kpi}", and MTTD is the only KPI SimCore measures natively. `
      + 'Nothing produces a measured value for it, so the verdict resolves `pending` whether or '
      + 'not a tenant is configured. That is honest, not broken — but do not promise a scored '
      + 'pass on this scenario.',
      'Report this run as evidence, or pair it with an assertion artifact that measures the '
      + 'posture/outcome directly.')
  }

  const list = Array.isArray(integrations) ? integrations : null
  if (list === null) {
    return mk('measurement', 'Measurement', PF.UNKNOWN,
      `${kpi || 'threshold'} declared · credential state unknown`,
      'Could not read the integration list, so we cannot say whether this run can be measured.')
  }

  const cortex = list.filter((i) => i && (i.kind === 'xsiam' || i.kind === 'xsiam_tenant'))
  if (!cortex.length) {
    return mk('measurement', 'Measurement', PF.WARN,
      `${kpi || 'threshold'} declared · no Cortex credential`,
      'This scenario carries a scoreable threshold, but no tenant credential is registered, so no '
      + 'measured value can be produced. The verdict will resolve `pending` — still owed, NOT a '
      + 'failed detection. Do not report a pending verdict as a miss.',
      'Register a tenant under Tenants, or import observations manually after the run.')
  }

  const answered = cortex.filter((i) => i.last_verified_ok === true)
  if (!answered.length) {
    return mk('measurement', 'Measurement', PF.WARN,
      `${kpi || 'threshold'} declared · no tenant has answered a probe`,
      `${cortex.length} credential${cortex.length === 1 ? ' is' : 's are'} registered but none has `
      + 'answered a probe, so read-back may fail at reconcile time. A failure there resolves '
      + '`pending`, never `fail`.',
      'Test the tenant from the Tenants surface before the customer is watching.')
  }

  return mk('measurement', 'Measurement', PF.PASS,
    `${kpi || 'threshold'} · ${answered.length} credential${answered.length === 1 ? '' : 's'} answered a probe`,
    'A measured value can be produced for this run. Note that a reachable tenant is not a verified '
    + 'one — nothing is proven until a query returns a terminal verdict.')
}

/**
 * Is this threshold one the engine can actually measure?
 *
 * MTTD (mean time to detect) is derived from `observed_at - executed_at` and is
 * the only KPI with a producer. Everything else in the corpus is authored but
 * unmeasured. Matched on the KPI text rather than an allow-list of exact
 * strings, because the corpus spells it several ways ("MTTD", "Mean Time to
 * Detect", "mttd_seconds").
 */
function isMttdShaped(kpi, threshold) {
  const hay = `${kpi || ''} ${(threshold && threshold.kpi) || ''}`.toLowerCase()
  if (!hay.trim()) return false
  return hay.includes('mttd')
    || hay.includes('mean time to detect')
    || hay.includes('time to detect')
    || hay.includes('detection latency')
}

// ─── helper ──────────────────────────────────────────────────────────────────

function mk(id, label, status, summary, detail, remediation = '', extra = {}) {
  return { id, label, status, summary, detail, remediation, ...extra }
}
