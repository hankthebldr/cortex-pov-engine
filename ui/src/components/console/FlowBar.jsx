import React from 'react'
import { PHASES, flowFor } from '../../app/povflow.js'

/**
 * FlowBar — the wizard, made visible.
 *
 * Replaces BOTH the old `PhaseBar` (which sat above the workspace and competed
 * with the rail for the same job) and the old `CommandStrip` ticker (which
 * spent a whole shell row restating the most recent run's status in a place
 * nobody looked). One bar, at the foot, that always answers two questions:
 * where am I in the POV, and what is the next action.
 *
 * Three things it deliberately does NOT do:
 *
 *  - It does not invent a Launch destination. Phase 4's pill points back at the
 *    Launch Gate, because Launch is a state the Composer enters after
 *    preflight, not a place.
 *  - It does not show a phase as active on the Overview. Overview's flow index
 *    is -1 (see povflow.js): it is the front door, and an earlier revision that
 *    let it share the preflight destination had the app's entry page claiming
 *    Scope and Compose were already complete.
 *  - It does not phrase its own CTA. `flowFor` owns the one rule —
 *    `Next: <destination as the rail names it>`, with `Export report` as the
 *    single terminal action — so the copy cannot drift per screen.
 *
 * Props:
 *   destination — current destination id
 *   ctx         — derived tallies passed straight to flowFor, so the bar quotes
 *                 the same numbers the surface does
 *   onNavigate  — (destinationId) => void
 */
export default function FlowBar({ destination, ctx = {}, onNavigate = () => {} }) {
  const flow = flowFor(destination, ctx)

  return (
    <footer className="pov-flow" data-testid="flow-bar" data-phase={flow.phase}>
      <div className="pov-flow__where">
        <span className="pov-flow__title">{flow.title}</span>
        <span className="pov-flow__sub">{flow.sub}</span>
      </div>

      <nav className="pov-flow__phases" aria-label="POV phases">
        {PHASES.map((p, i) => {
          const on = i === flow.phase
          const done = flow.phase >= 0 && i < flow.phase
          return (
            <button
              key={p.label}
              type="button"
              // Same selector convention the old phase bar used, kept so the
              // guards that watched wayfinding did not have to be rewritten
              // around a regex that also matches the CTA ("Compose" vs
              // "Next: Composer").
              data-testid={`phase-button-${p.label.toLowerCase()}`}
              className={
                'pov-flow__phase'
                + (on ? ' pov-flow__phase--on' : '')
                + (done ? ' pov-flow__phase--done' : '')
              }
              aria-current={on ? 'step' : undefined}
              title={p.caption}
              onClick={() => onNavigate(p.dest)}
            >
              {/* A tick for a completed phase, the numeral otherwise. The
                  numerals are the run order (1..6), NOT the rail's group
                  numbers — the rail skips 4 because Launch has no destination,
                  and a bar that skipped it too would read as a missing step. */}
              <span className="pov-flow__glyph" aria-hidden="true">{done ? '✓' : i + 1}</span>
              <span className="pov-flow__plabel">{p.label}</span>
            </button>
          )
        })}
      </nav>

      {flow.ctaDest && (
        <button
          type="button"
          className="pov-flow__cta"
          data-testid="flow-cta"
          onClick={() => onNavigate(flow.ctaDest)}
        >
          {flow.cta}
        </button>
      )}
    </footer>
  )
}
