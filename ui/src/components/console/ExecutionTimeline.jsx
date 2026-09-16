import React from 'react'
import { COMPOSER_LANES } from './povdata/corpus.js'
import { planeTint } from './povdata/planes.js'

/**
 * ExecutionTimeline — the chain as an ordered strip, under the canvas.
 *
 * WHY A CANVAS NEEDS A TIMELINE
 * A free-node canvas is the right tool for *structure* — what feeds what, which
 * lane a step launches through, where the chain branches. It is a poor tool for
 * *order*, because two nodes side by side may or may not run in sequence and
 * nothing on the canvas says which. So the canvas answers "what is the shape"
 * and this answers "what happens, in what order, and what state is each step
 * in" — which is the question a DC has while watching a run, and the thing they
 * said was missing ("I don't see any timeline").
 *
 * IT IS ALSO THE PER-STEP RUN CONTROL.
 * Every step carries a `run` affordance, so a single step can be executed
 * standalone without composing a whole chain around it. That was the other half
 * of the same complaint: the objects existed and there was no way to fire one.
 *
 * Clicking a card selects that node on the canvas, so the two views stay a
 * single selection rather than two parallel ones.
 *
 * Props:
 *   steps      — [{ id, label, lane, plane, state }] in execution order
 *   selectedId — currently selected step id
 *   onSelect   — (id) => void
 *   onRunStep  — (id) => void | null
 */
export default function ExecutionTimeline({
  steps = [],
  selectedId = null,
  onSelect = () => {},
  onRunStep = null,
}) {
  if (!steps.length) {
    return (
      <div className="pov-panel" data-testid="execution-timeline">
        <div className="pov-panel__body">
          <p style={{ font: '400 11.5px/1.6 var(--font-ui)', color: 'var(--tx3)', margin: 0 }}>
            No steps yet. Click an item in the palette to drop it as the next node, then
            drag it into the lane it should launch through.
          </p>
        </div>
      </div>
    )
  }

  const laneLabel = (key) => {
    const row = COMPOSER_LANES.find((l) => l[0] === key)
    return row ? row[1] : key
  }

  return (
    <div data-testid="execution-timeline">
      <div className="pov-section" style={{ marginTop: 14 }}>
        <span className="pov-section__label">Execution order · {steps.length} steps</span>
        <span className="pov-section__hr" />
      </div>

      <div style={{ display: 'flex', gap: 0, overflowX: 'auto', paddingBottom: 6 }}>
        {steps.map((s, i) => {
          const on = s.id === selectedId
          const tone = s.state === 'CONFIRMED' ? 'pos'
            : s.state === 'BROKEN' ? 'crit'
              : s.state === 'EXPECTED' ? 'warn' : 'dim'
          return (
            <React.Fragment key={s.id}>
              {/* The connector IS the ordering claim — without it two adjacent
                  cards read as "these two exist", not "this one then that one". */}
              {i > 0 && (
                <span
                  aria-hidden="true"
                  style={{ alignSelf: 'center', width: 18, height: 1, background: 'var(--bd2)', flex: 'none' }}
                />
              )}
              <div
                className={'pov-card' + (on ? ' pov-card--on' : '')}
                style={{ minWidth: 194, flex: 'none', padding: 11 }}
              >
                <button
                  type="button"
                  onClick={() => onSelect(s.id)}
                  aria-pressed={on}
                  style={{
                    display: 'block', width: '100%', textAlign: 'left',
                    background: 'transparent', border: 0, padding: 0, cursor: 'pointer', color: 'inherit',
                  }}
                >
                  <span className="pov-card__head" style={{ marginBottom: 7 }}>
                    <span className="pov-step__n">{i + 1}</span>
                    <span className="pov-card__code">{s.id}</span>
                    <span className="pov-card__spacer" />
                    <span className={`pov-dot pov-dot--${tone}`} title={s.state || 'not run'} />
                  </span>
                  <span
                    className="pov-card__title"
                    style={{ display: 'block', marginBottom: 7, whiteSpace: 'normal' }}
                  >
                    {s.label}
                  </span>
                  {/* The lane is the door this step launches through, which is
                      the same lane the Runs topology will band it into. */}
                  <span
                    className="pov-chip-plane"
                    style={{ borderLeftColor: s.plane ? planeTint(s.plane) : 'var(--bd2)' }}
                  >
                    {laneLabel(s.lane)}
                  </span>
                </button>
                {onRunStep && (
                  <button
                    type="button"
                    className="pov-btn"
                    style={{ marginTop: 9, width: '100%', padding: '6px 8px' }}
                    onClick={() => onRunStep(s.id)}
                    data-testid={`run-step-${s.id}`}
                  >
                    Run this step
                  </button>
                )}
              </div>
            </React.Fragment>
          )
        })}
      </div>
    </div>
  )
}
