import React from 'react'

/**
 * PhaseBar — the six phases of a POV run, and where you currently are in them.
 *
 * The console has fourteen destinations grouped by JOB (Operate / Analyze /
 * Manage …). That grouping answers "what kind of thing is this surface?" but
 * not "where am I in the work?", which is the question a DC running their first
 * POV actually has — and the reason the redesign added this bar above the
 * workspace rather than another nav group inside it.
 *
 * Each phase maps to the destination that OWNS it. The mapping is deliberately
 * many-to-one in both directions:
 *   - Compose and Launch both own `composer` — composing a chain and pushing it
 *     to an agent are one surface, two moments.
 *   - `library`, `adapters` and `uctc` all sit in Compose, because choosing
 *     what to prove is part of composing it.
 * A destination with no phase (e.g. the guided flow) leaves the bar with no
 * active step rather than guessing one — see `phaseIndexFor` returning -1.
 *
 * Props:
 *   destination — current destination id
 *   onNavigate  — (destinationId) => void
 */

export const PHASES = [
  { num: '1', label: 'Scope',     caption: 'tenant · agent · lab',   dest: 'environments' },
  { num: '2', label: 'Compose',   caption: 'steps · payload plan',   dest: 'composer' },
  { num: '3', label: 'Preflight', caption: 'readiness · egress',     dest: 'readiness' },
  { num: '4', label: 'Launch',    caption: 'push to agent',          dest: 'composer' },
  { num: '5', label: 'Observe',   caption: 'live steps · events',    dest: 'runs' },
  { num: '6', label: 'Prove',     caption: 'evidence · export',      dest: 'coverage' },
]

/**
 * Which phase a destination belongs to, as an index into PHASES.
 *
 * `composer` resolves to Compose (1), never Launch (3): Launch is a state the
 * Composer enters after preflight, not a place you navigate to, so highlighting
 * it on arrival would claim progress the DC has not made.
 */
const PHASE_BY_DEST = {
  environments: 0, tenants: 0, agents: 0,
  composer: 1, library: 1, adapters: 1, uctc: 1,
  readiness: 2,
  eal: 4, runs: 4,
  coverage: 5, ttps: 5,
}

export function phaseIndexFor(destination) {
  const i = PHASE_BY_DEST[destination]
  return i === undefined ? -1 : i
}

export default function PhaseBar({ destination = null, onNavigate = () => {} }) {
  const active = phaseIndexFor(destination)

  return (
    <div className="phase-bar" data-tour-id="phase-bar" aria-label="POV run phases">
      <div className="phase-bar__eyebrow">POV run</div>
      <ol className="phase-bar__list">
        {PHASES.map((p, i) => {
          // `active === -1` (a destination outside the model) must leave every
          // step neutral — not mark them all done, which -1 would do naively.
          const isOn = i === active
          const isDone = active >= 0 && i < active
          return (
            <li key={p.num} className="phase-bar__item">
              <button
                type="button"
                data-testid={`phase-button-${p.label.toLowerCase()}`}
                className={
                  'phase-step'
                  + (isOn ? ' phase-step--on' : '')
                  + (isDone ? ' phase-step--done' : '')
                }
                aria-current={isOn ? 'step' : undefined}
                onClick={() => onNavigate(p.dest)}
                title={`Phase ${p.num} · ${p.label} — ${p.caption}`}
              >
                <span className="phase-step__num" aria-hidden="true">{p.num}</span>
                <span className="phase-step__text">
                  <span className="phase-step__label">{p.label}</span>
                  <span className="phase-step__caption">{p.caption}</span>
                </span>
              </button>
            </li>
          )
        })}
      </ol>
    </div>
  )
}
