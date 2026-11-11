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
 *   block   — SimCore's own guard will refuse this launch, OR the launch would
 *             be accepted and cannot execute as authored: no beacon is polling
 *             (offline, or enrolled and never polled), or no step is authored
 *             for the beacon's OS. A named dead button beats a 409 mid-demo,
 *             and beats a run that reads as the customer's missed detections.
 *   unknown — we could not check. NEVER rendered as a pass. An older SimCore
 *             that does not report shelf state is not a SimCore whose tools are
 *             staged.
 */

import { SUPPLY, supplyOf } from '../supplyState.js'
import { agentLiveness, LIVENESS } from '../setupProgress.js'
import { installerOS } from '../deployCommands.js'

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

/**
 * How often the launch surface re-reads the agent list in pull mode (seconds).
 * useLaunchScenario polls on this; the Target row quotes it, because a BLOCK
 * that clears itself has to say so or the operator reloads the page instead.
 */
export const AGENT_REFRESH_HINT_S = 10

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
 * @param {string}      input.pushFormat    'auto' | 'bash' | 'powershell' | 'k8s'
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
  const platform = platformChecks(scenario, target, agents, pushFormat)
  checks.push(platform.primary)
  if (platform.labels) checks.push(platform.labels)
  checks.push(identityCheck(scenario, target, agents, identity, pushFormat))
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

  // ONE liveness model (setupProgress.js::agentLiveness), the same one the
  // Agents cards, the header switcher and Get started read. This row used to
  // read `agent.status` itself, so a row the installer had only enrolled —
  // which SimCore stamps 'online' for 30 s before any beacon has polled — read
  // PASS "online · 12s since last poll" here while the Agents card for the
  // same row said "do not launch onto it".
  const live = agentLiveness(agent)
  const host = agent.hostname || target.id
  const age = live.ageSeconds !== null
    ? `${Math.round(live.ageSeconds)}s since last poll`
    : 'last poll unknown'

  if (live.state === LIVENESS.ENROLLED) {
    // No "since last poll" figure: there has been no poll to measure from.
    return mk('target', 'Target', PF.BLOCK, `${host} ${live.label}`,
      'The installer enrolled this agent, but no beacon has polled SimCore since. SimCore stamps a '
      + 'newly enrolled row `online` for its first 30 s, so that status is not a poll. A pull-mode '
      + 'task queued now sits undelivered until a beacon collects it; the run stays `running` and '
      + 'nothing executes.',
      'Wait for the first poll — this check re-reads the agent list every '
      + `${Math.round(AGENT_REFRESH_HINT_S)} s and clears itself. If it does not, the beacon did not `
      + `start: ${beaconCheckHint(agent.os)} Deploy agent lists what the installer reported.`)
  }
  if (live.state === LIVENESS.OFFLINE) {
    return mk('target', 'Target', PF.BLOCK, `beacon offline (${age})`,
      `${host} has not polled recently. A pull-mode task queued now sits `
      + 'undelivered; the run stays `running` and nothing executes.',
      `${beaconCheckHint(agent.os)} Or use push mode.`)
  }
  if (live.state === LIVENESS.STALE) {
    return mk('target', 'Target', PF.WARN, `beacon stale (${age})`,
      'The beacon has missed poll intervals. The task will be delivered whenever it next polls — '
      + 'which may be after the customer has stopped watching.',
      'Check the beacon\'s connectivity back to this SimCore before launching.')
  }
  if (live.state !== LIVENESS.ONLINE) {
    // An unrecognised status, or a row SimCore has never seen. Not a pass:
    // "we could not tell" is not "online".
    return mk('target', 'Target', PF.UNKNOWN, `${host}: beacon liveness unknown — ${live.label}`,
      'SimCore did not report a liveness this console recognises for this beacon, so it cannot say '
      + 'whether a queued task would be collected.',
      'Refresh Agents. If the row still reads unknown, check the beacon on the target before launching.')
  }

  const caps = Array.isArray(agent.capabilities) ? agent.capabilities : []
  return mk('target', 'Target', PF.PASS,
    `${host} online · ${agent.os || 'os?'}`,
    `Capabilities: ${caps.length ? caps.join(', ') : 'none advertised'}. ${age}.`,
    '', { agent })
}

/**
 * Where to look on the TARGET when its beacon is not polling — in that
 * target's own words. Every offline beacon used to be told to run `systemctl`,
 * Windows ones included. The Windows lines name what the installer creates;
 * that installer has run only on a CI runner, never on an offline customer
 * beacon, so they are where to look, not a tested procedure.
 */
export function beaconCheckHint(os) {
  const target = installerOS(os)
  if (target === 'windows') {
    return 'On the target, in Windows PowerShell as Administrator: `sc.exe query CortexSimAgent`, '
      + 'then `Restart-Service CortexSimAgent` (log: %ProgramData%\\CortexSim\\agent.log). A beacon '
      + 'installed without elevation is a detached process that ends at logoff — re-run the installer.'
  }
  if (target === 'darwin') {
    return 'On the target: `launchctl print gui/$(id -u)/com.paloaltonetworks.cortexsim.agent` '
      + '(`system/com.paloaltonetworks.cortexsim.agent` for a root install), and read '
      + '~/.cortexsim/cortexsim-agent.log (/var/log/cortexsim-agent.log for a root install).'
  }
  return 'On the target: `systemctl status cortexsim-agent` (root install) or '
    + '`systemctl --user status cortexsim-agent` (installed without sudo).'
}

// ─── 3 · Mode / platform ─────────────────────────────────────────────────────
//
// SERVER TRUTH FIRST. `bundle_targets` on GET /api/scenarios/{id} is computed by
// the same resolver the download route (push) and the launch target check
// (pull) gate on: it reads the command TEXT, not the `platforms` label. ~100
// corpus steps are labelled `windows` while carrying POSIX text — SIM-EDR-014
// is every step of a scenario — so a label-keyed verdict BLOCKED the one
// target that runs it (a Linux beacon) and cleared the one that cannot.
//
// The labels are still read, as a SECONDARY row (`platform-labels`): a WARN
// when they disagree with what SimCore will actually do, and — on an older
// SimCore that does not report `bundle_targets` — a WARN that the primary
// verdict is label-based and therefore not to be trusted as far as the server's.

/** Push format → the target it needs (the server's `_FORMAT_TARGET`). */
const FORMAT_TARGET = { bash: 'posix', k8s: 'posix', powershell: 'windows' }
const BUNDLE_TARGETS = new Set(['posix', 'windows'])
// Exactly the server's sets (orchestrator `_WINDOWS_AGENT_OS` / `_POSIX_AGENT_OS`).
// Any other non-empty value is one SimCore refuses (PULL_TARGET_OS_UNRECOGNISED),
// so it must not be guessed into a target here.
const WINDOWS_OS = new Set(['windows'])
const POSIX_OS = new Set(['linux', 'darwin', 'macos'])

/**
 * `bundle_targets` as server truth, or why it cannot be.
 * @returns {{targets: string[]|null, reason: 'absent'|'unrecognised'|'push_withdrawn'|null}}
 */
function serverTargets(scenario) {
  const raw = scenario.bundle_targets
  if (raw === undefined || raw === null) return { targets: null, reason: 'absent' }
  // An unrecognised shape is not read as "no targets" — that would BLOCK every
  // launch on a field this console does not understand.
  if (!Array.isArray(raw) || raw.some((t) => !BUNDLE_TARGETS.has(t))) {
    return { targets: null, reason: 'unrecognised' }
  }
  // [] for push_supported:false is the route refusing push, not a statement
  // about what the content can run — it says nothing about a pull target.
  if (raw.length === 0 && scenario.push_supported === false) {
    return { targets: null, reason: 'push_withdrawn' }
  }
  return { targets: raw, reason: null }
}

/** The execution target this launch needs, or null when it cannot be known. */
function requiredTarget(target, agents, pushFormat, serverList) {
  if (target && target.kind !== 'agent') {
    const fmt = pushFormat || 'auto'
    if (fmt === 'auto') {
      // The server's auto rule: POSIX first, PowerShell for windows-only content.
      if (!serverList) return null
      if (serverList.includes('posix')) return 'posix'
      if (serverList.includes('windows')) return 'windows'
      return null
    }
    return FORMAT_TARGET[fmt] || null
  }
  const agent = (agents || []).find((a) => (a.agent_id || a.id) === (target && target.id)) || null
  const os = String((agent && agent.os) || '').toLowerCase()
  if (WINDOWS_OS.has(os)) return 'windows'
  if (POSIX_OS.has(os)) return 'posix'
  return null
}

/** Does every platform-declaring step's LABEL claim `targetName`? */
function labelsClaim(declaring, targetName) {
  return declaring.every((s) => s.platforms.some((p) => {
    const v = String(p).toLowerCase()
    return targetName === 'windows' ? v === 'windows' : v !== 'windows'
  }))
}

function platformChecks(scenario, target, agents, pushFormat) {
  const steps = Array.isArray(scenario.steps) ? scenario.steps : []
  const declaring = steps.filter((s) => Array.isArray(s.platforms) && s.platforms.length)
  const server = serverTargets(scenario)

  if (!server.targets) {
    const primary = labelPlatformCheck(scenario, target, agents, pushFormat)
    // No labels and no server truth: the UNKNOWN already says it all.
    if (!declaring.length) return { primary, labels: null }
    const why = {
      absent: 'This SimCore does not report `bundle_targets`, so the platform verdict above is '
        + 'read from the steps\' `platforms` labels.',
      unrecognised: `This SimCore reported \`bundle_targets\` in an unrecognised shape `
        + `(${JSON.stringify(scenario.bundle_targets)}), so it was not used and the platform verdict `
        + 'above is read from the steps\' `platforms` labels.',
      push_withdrawn: 'This scenario declares push_supported: false, so SimCore reports no '
        + 'bundle targets for it and the platform verdict above is read from the steps\' '
        + '`platforms` labels.',
    }[server.reason]
    return {
      primary,
      labels: mk('platform-labels', 'Platform labels', PF.WARN, 'verdict is label-based',
        `${why} Labels are not what runs: ~100 corpus steps are labelled \`windows\` while carrying `
        + 'POSIX text, so a label can clear a target that cannot run the command — or block one that '
        + 'can.',
        'Upgrade this SimCore to one that reports bundle_targets, or read the step commands before '
        + 'launching.'),
    }
  }

  const list = server.targets
  const isPush = !!(target && target.kind !== 'agent')
  const needed = requiredTarget(target, agents, pushFormat, list)

  let primary
  if (!target) {
    primary = mk('platform', 'Platform', PF.UNKNOWN,
      list.length ? `SimCore can emit ${list.join(' · ')}` : 'SimCore can emit no target',
      'No target selected, so there is nothing to check the scenario against yet.')
  } else if (!list.length) {
    primary = mk('platform', 'Platform', PF.BLOCK, 'no target can run every step',
      'SimCore resolves no execution target — POSIX or Windows — for which every step has a '
      + `command. ${isPush ? 'The download answers 409 BUNDLE_TARGET_UNSATISFIABLE' : 'A pull launch '
      + 'answers 409 PULL_TARGET_UNSATISFIABLE'} naming the steps.`,
      'Declare platform_variants on the offending steps.')
  } else if (!needed) {
    const agent = isPush ? null
      : (agents || []).find((a) => (a.agent_id || a.id) === target.id) || null
    const os = agent && agent.os ? String(agent.os) : ''
    primary = mk('platform', 'Platform', PF.UNKNOWN, `SimCore can emit ${list.join(' · ')}`,
      isPush
        ? `The bundle format "${pushFormat}" names no target this console recognises.`
        : os
          ? `The beacon registered os="${os}", which is neither windows nor linux/darwin/macos, so `
            + 'platform compatibility cannot be checked here — and a current SimCore refuses the '
            + 'launch (PULL_TARGET_OS_UNRECOGNISED) rather than guess.'
          : 'The target\'s OS is unknown, so platform compatibility cannot be checked. (SimCore '
            + 'dispatches POSIX commands to a beacon that registered no OS.)',
      isPush ? '' : 'Re-register the beacon from a current build so it reports its OS.')
  } else if (!list.includes(needed)) {
    const other = list.find((t) => t !== needed)
    primary = mk('platform', 'Platform', PF.BLOCK,
      `SimCore has no ${needed} command for every step`,
      isPush
        ? `This scenario's command text resolves only for ${list.join(' · ')}, and the `
          + `${pushFormat} bundle needs ${needed}. SimCore answers 409 BUNDLE_TARGET_UNSATISFIABLE `
          + 'naming the offending steps.'
        : `This scenario's command text resolves only for ${list.join(' · ')}, and this beacon is `
          + `${needed}. SimCore refuses the launch with 409 PULL_TARGET_UNSATISFIABLE rather than run `
          + 'the wrong dialect — which historically passed as green while doing nothing the scenario '
          + 'claimed.',
      isPush
        ? `Download it as ${other === 'windows' ? 'PowerShell (.ps1)' : 'bash (.sh)'}, `
          + 'or use format=auto.'
        : `Pick a ${other === 'windows' ? 'Windows' : 'Linux/macOS'} beacon, or push the bundle instead.`)
  } else {
    // SimCore resolves the family. Inside POSIX it resolves nothing further,
    // so the labels decide whether THIS beacon's OS is one a step is authored for.
    const family = needed === 'posix' && !isPush
      ? posixFamilyCheck(declaring, agentOsOf(target, agents))
      : null
    if (family) {
      primary = family
    } else {
      const variants = steps.filter((s) => s && s.platform_variants
        && typeof s.platform_variants === 'object'
        && Object.keys(s.platform_variants).some((k) => (needed === 'windows'
          ? String(k).toLowerCase() === 'windows'
          : String(k).toLowerCase() !== 'windows'))).length
      primary = mk('platform', 'Platform', PF.PASS, `SimCore resolves every step for ${needed}`,
        `From bundle_targets [${list.join(', ')}] — the resolver SimCore itself gates on, reading `
        + `the command text.${variants ? ` ${variants} step${variants === 1 ? '' : 's'} declare a `
          + `${needed} platform variant; a step that runs its variant is marked PLATFORM VARIANT on `
          + 'the run, because its expected detections were authored against the primary command.' : ''}`)
    }
  }

  // Secondary: do the labels say the same thing SimCore will do?
  let labels = null
  if (needed && declaring.length) {
    const claim = labelsClaim(declaring, needed)
    const truth = list.includes(needed)
    if (claim !== truth) {
      const union = Array.from(new Set(declaring.flatMap((s) => s.platforms)))
      labels = mk('platform-labels', 'Platform labels', PF.WARN,
        `step labels say ${union.join(' · ')}; SimCore resolves ${list.join(' · ') || 'nothing'}`,
        truth
          ? `The steps are labelled ${union.join(' / ')}, but their command text resolves for `
            + `${needed}, so this run goes ahead on SimCore's reading. A label that disagrees with the `
            + 'command is a corpus defect, not a launch problem — but any detection keyed on the '
            + 'labelled OS will not fire here.'
          : `The steps are labelled ${union.join(' / ')}, but their command text has no ${needed} `
            + 'form. The label is wrong; the refusal above is correct.',
        'Fix the step `platforms` labels to match the commands they carry.')
    }
  }

  return { primary, labels }
}

/** The selected beacon's registered OS, lower-cased; '' when unknown or push. */
function agentOsOf(target, agents) {
  if (!target || target.kind !== 'agent') return ''
  const agent = (agents || []).find((a) => (a.agent_id || a.id) === target.id) || null
  return String((agent && agent.os) || '').toLowerCase()
}

/**
 * Linux versus macOS, which `bundle_targets` cannot tell apart.
 *
 * SimCore's resolver knows two targets, posix and windows. A darwin beacon is
 * `posix`, so SimCore sends it the primary command of a Linux-only scenario
 * (`cat /etc/shadow`, `runuser`, `apt`) with no marker on the run, and sends a
 * Linux beacon the macOS-only SIM-KOI-009 the same way. Nothing server-side
 * refuses either. The step labels are the only linux-versus-macOS statement
 * there is, so inside the POSIX family they are the verdict.
 *
 * A step labelled ONLY `windows` says nothing about linux versus macOS (that
 * is the mislabelled-POSIX-text shape, already a `platform-labels` WARN), so
 * it is left out rather than read as "not this OS".
 *
 * @returns {object|null} a BLOCK / WARN check, or null when the labels raise nothing
 */
function posixFamilyCheck(declaring, os) {
  if (!os) return null
  const posixDeclaring = declaring.filter(
    (s) => s.platforms.some((p) => String(p).toLowerCase() !== 'windows'),
  )
  if (!posixDeclaring.length) return null
  const other = posixDeclaring.filter((s) => !s.platforms.some((p) => matchesOs(p, os)))
  if (!other.length) return null

  const union = Array.from(new Set(
    posixDeclaring.flatMap((s) => s.platforms).filter((p) => String(p).toLowerCase() !== 'windows'),
  ))
  const noMarker = '`bundle_targets` only separates POSIX from Windows, so SimCore does not refuse '
    + `this launch: it sends that command text to the ${os} host with no marker on the run.`

  if (other.length === posixDeclaring.length) {
    return mk('platform', 'Platform', PF.BLOCK, `no step declares ${os}`,
      `Every platform-declaring step is authored for ${union.join(' / ')} and this beacon is ${os}. `
      + `${noMarker} Steps then fail or do nothing, their expected detections never fire, and the `
      + 'run reads as detections the customer\'s stack missed.',
      `Pick a ${union.join(' or ')} beacon. A scenario meant for ${os} as well needs \`${os === 'darwin' ? 'macos' : os}\` `
      + 'in its steps\' `platforms`.')
  }
  const ids = other.map((s) => s.id).filter(Boolean)
  return mk('platform', 'Platform', PF.WARN,
    `${other.length} of ${posixDeclaring.length} platform-bearing steps do not declare ${os}`,
    `${ids.length ? `${ids.slice(0, 6).join(', ')}${ids.length > 6 ? ', …' : ''} ` : 'Those steps '}`
    + `${other.length === 1 ? 'is' : 'are'} authored for another platform. ${noMarker} A step that `
    + 'runs on an OS it was not authored for is not evidence about the detection it expects.',
    'Check the step list, or pick a target matching every declared platform.')
}

function labelPlatformCheck(scenario, target, agents, pushFormat) {
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
    // `auto` lets SimCore pick PowerShell for windows-only content, so only an
    // explicit POSIX format (bash / k8s) is refused for it.
    if (windowsOnly && pushFormat !== 'powershell' && pushFormat !== 'auto') {
      return mk('platform', 'Platform', PF.BLOCK,
        'windows-only scenario in a non-PowerShell bundle',
        'Every step declares `windows`, and this bundle format cannot carry PowerShell. SimCore '
        + 'answers 409 BUNDLE_TARGET_UNSATISFIABLE naming the offending steps.',
        'Pick the PowerShell (.ps1) or auto bundle format, or run it through a Windows beacon.')
    }
    return mk('platform', 'Platform', PF.PASS, `declares ${union.join(' · ')}`,
      `${declaring.length} of ${steps.length} steps declare a platform. The bundle is generated for `
      + `the ${pushFormat} format.`)
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

function identityCheck(scenario, target, agents, identity, pushFormat = 'bash') {
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
    // A .ps1 bundle has no account wrapper to fail: it cannot switch accounts
    // at all. The POSIX advice below (getent, nologin) is wrong for it.
    const bundleTarget = target
      ? requiredTarget(target, [], pushFormat, serverTargets(scenario).targets)
      : null
    if (bundleTarget === 'windows') {
      return mk('identity', 'Execution identity', PF.WARN,
        `${uniq.join(' · ')} will NOT be honoured by a .ps1 bundle`,
        'Windows has no credential-free impersonation, so a PowerShell bundle cannot switch accounts. '
        + `A step that declares ${uniq.join(' / ')} runs as whoever runs the bundle and logs `
        + '`cortexsim-identity-degraded`; a step that declares root or administrator is not run at all '
        + 'from a non-elevated session (NOT_ELEVATED in the bundle\'s outcome table). The '
        + 'process-ancestry chain will show the operator\'s account, not the one the scenario authored. '
        + 'Read from the bundle generator: no bundle has run on a Windows host.',
        'Run the bundle from Windows PowerShell as Administrator, and do not report lineage keyed on '
        + 'the authored account as the customer\'s.')
    }
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
