import React from 'react'
import '../../styles/destinations/flow-stepper.css'
import { useEnvironment } from '../../context/EnvironmentContext.jsx'
import { runIdOf } from '../../api/ids.js'

void React

/**
 * FlowStepper — Scenarios › Build a chain › Launch gate › Run, so composing
 * leads into simulating (design v33, handoff item 6).
 *
 * Shown on the four pages it names: Scenarios (`library`), Build a chain
 * (`composer`), Launch gate (`preflight`) and Run (`runs`). The page it is on
 * reads as the current step (`aria-current="step"`); every other step is a real
 * link to its page, carrying the chosen scenario where that page accepts it:
 *
 *   Scenarios     `?open=<id>`   opens that scenario's sheet
 *   Build a chain `?open=<id>`   loads its chain
 *   Launch gate   `?arm=<id>`    the scenario the launch is for (`launchId`,
 *                                which the Composer withholds while its chain
 *                                has unsaved edits: SimCore runs a saved chain,
 *                                never the canvas)
 *   Run           `?run=<id>`    the newest run of that scenario, when there
 *                                is one; otherwise the Runs page
 *
 * Built from the kit's tokens only; the selected step is the green wash
 * (`--ac-soft`) with the matte line (`--gr-matte`, `--ac-line` until that token
 * lands).
 */

export const FLOW_STEPS = Object.freeze([
  Object.freeze({ id: 'library', label: 'Scenarios' }),
  Object.freeze({ id: 'composer', label: 'Build a chain' }),
  Object.freeze({ id: 'preflight', label: 'Launch gate' }),
  Object.freeze({ id: 'runs', label: 'Run' }),
])

/** The page one step links to, with the id that page accepts. */
export function flowTarget(step, { scenarioId = null, launchId = scenarioId, runId = null } = {}) {
  switch (step) {
    case 'library': return { dest: 'library', params: scenarioId ? { open: scenarioId } : null }
    case 'composer': return { dest: 'composer', params: scenarioId ? { open: scenarioId } : null }
    case 'preflight': return { dest: 'preflight', params: launchId ? { arm: launchId } : null }
    case 'runs': return { dest: 'runs', params: runId ? { run: runId, tab: 'live' } : null }
    default: return null
  }
}

/** The hash the console router reads for a target (same shape it writes). */
export function flowHref({ dest, params }) {
  const keys = params ? Object.keys(params).filter((k) => params[k] != null && params[k] !== '') : []
  if (!keys.length) return `#/${dest}`
  const sp = new URLSearchParams()
  keys.forEach((k) => sp.set(k, String(params[k])))
  return `#/${dest}?${sp.toString()}`
}

/** The newest run of `scenarioId` in `runs` (the provider keeps them newest first). */
export function latestRunOf(runs, scenarioId) {
  if (!scenarioId || !Array.isArray(runs)) return null
  const run = runs.find((r) => r && r.scenario_id === scenarioId)
  return run ? runIdOf(run) : null
}

export default function FlowStepper({
  current,
  scenarioId = null,
  launchId = scenarioId,
  launchTitle = null,
  runFor = null,
  runId = undefined,
  onNavigate = () => {},
  className = '',
}) {
  const { runs } = useEnvironment()
  const resolvedRun = runId !== undefined ? runId : latestRunOf(runs, runFor || launchId || scenarioId)
  const ids = { scenarioId, launchId, runId: resolvedRun }

  const titleFor = (step, target) => {
    if (step.id === 'preflight' && !launchId && launchTitle) return launchTitle
    const carried = target.params && (target.params.open || target.params.arm || target.params.run)
    return carried ? `${step.label}: ${carried}` : step.label
  }

  return (
    <nav
      className={('flow-stepper ' + className).trim()}
      aria-label="Compose to simulate"
      data-testid="flow-stepper"
    >
      <ol className="flow-stepper__list">
        {FLOW_STEPS.map((step, i) => {
          const isCurrent = step.id === current
          const target = flowTarget(step.id, ids)
          return (
            <li
              key={step.id}
              className={'flow-stepper__step' + (isCurrent ? ' flow-stepper__step--current' : '')}
              data-step={step.id}
            >
              {i > 0 && <span className="flow-stepper__sep" aria-hidden="true">›</span>}
              {isCurrent ? (
                <span className="flow-stepper__label" aria-current="step">
                  <span className="flow-stepper__num mono" aria-hidden="true">{i + 1}</span>
                  {step.label}
                </span>
              ) : (
                <a
                  className="flow-stepper__label flow-stepper__link"
                  href={flowHref(target)}
                  title={titleFor(step, target)}
                  onClick={(e) => {
                    // A modified click opens the page in a new tab, as a link does.
                    if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return
                    e.preventDefault()
                    if (target.params) onNavigate(target.dest, target.params)
                    else onNavigate(target.dest)
                  }}
                >
                  <span className="flow-stepper__num mono" aria-hidden="true">{i + 1}</span>
                  {step.label}
                </a>
              )}
            </li>
          )
        })}
      </ol>
    </nav>
  )
}
