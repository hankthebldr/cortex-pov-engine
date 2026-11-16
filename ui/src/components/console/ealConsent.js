/**
 * ealConsent.js — the launch consent a scenario's EAL steps need. Pure, plus
 * one remembered name.
 *
 * A launch that carries `consent.simulation_authorized` and names who
 * authorised it (`authorized_by`) lets the run's EAL steps deliver live;
 * without both they stay dry-run and each says so (EAL_DRY_RUN_NO_CONSENT,
 * `engine/orchestrator.py::_eal_live_blockers`). A C2-shaped plugin also needs
 * `consent.c2_authorized`. The console never sent either, so every EAL step
 * launched from it ran dry.
 *
 * An EAL step is one SimCore sends itself (`channel: eal`), or an agent step
 * whose command calls the EAL CLI (`scripts.eal_simulator.cli`).
 *
 * The name of who authorised the traffic is remembered per browser
 * (localStorage), so a DC types it once. It is a convenience: the tick is
 * never remembered, so every launch is authorised on purpose.
 */

const EAL_CLI = /scripts\.eal_simulator\.cli/
const KEY = 'cortexsim.eal.authorizedBy'

function commandText(step) {
  const variants = step?.platform_variants && typeof step.platform_variants === 'object'
    ? Object.values(step.platform_variants)
    : []
  return [step?.command, ...variants].filter((c) => typeof c === 'string').join('\n')
}

/** The steps of a scenario that send EAL traffic, in order. */
export function ealSteps(scenario) {
  const steps = Array.isArray(scenario?.steps) ? scenario.steps : []
  return steps.filter((s) => s && (s.channel === 'eal' || EAL_CLI.test(commandText(s))))
}

/** The name last used to authorise live EAL traffic in this browser, or ''. */
export function rememberedAuthorizer() {
  try {
    return window.localStorage.getItem(KEY) || ''
  } catch {
    return ''   // storage blocked: the field starts empty
  }
}

export function rememberAuthorizer(name) {
  try {
    if (name) window.localStorage.setItem(KEY, name)
  } catch { /* storage blocked: nothing is remembered */ }
}

/**
 * What a launch adds for its EAL steps, or null when it adds nothing (the box
 * is not ticked, no name is given, or the scenario has no EAL step).
 *
 * @returns {{authorized_by: string, consent: Object}|null}
 */
export function ealLaunchFields(scenario, { live = false, authorizedBy = '', c2 = false } = {}) {
  const name = String(authorizedBy || '').trim()
  if (!live || !name || ealSteps(scenario).length === 0) return null
  return {
    authorized_by: name,
    consent: { simulation_authorized: true, ...(c2 ? { c2_authorized: true } : {}) },
  }
}
