/**
 * ChainStage — one chain drawing, three modes (design v26 §4.1).
 *
 *   editable  the Composer's Lanes lens: every card is a button that selects
 *             its step; tenant rules sit in the Cortex Engine band.
 *   live      Run detail: every card carries a state pip AND a word
 *             (Detected · Broken · Waiting / Not seen · Ran — not read back ·
 *             Did not run); a lane header opens its component page.
 *   preview   a scenario's sheet: no state, no controls, scaled to fit its box
 *             and never below 0.62 — past that it scrolls in its own box.
 *
 * Lanes are signal paths (`composerLanes`), cards sit in execution order, and
 * edges are the declared causality (`composerGraph`) — all computed by the pure
 * `chainStage.buildChainStage`, so the three modes cannot disagree about where
 * a step sits. A chain with no steps renders the stage's empty state.
 *
 * LIVE-MODE CONTRACT (for Run detail, design v26 §4.6 / slice 7)
 *   mode="live"
 *   steps       the run's scenario steps in the Composer step model
 *               (`composerDraft.draftFromScenario(scenario).steps`)
 *   states      { [stepId]: 'detected' | 'broken' | 'waiting' | 'not_seen'
 *                          | 'ran' | 'did_not_run' } — the caller derives these
 *               from the run and its read-back. Before a read-back a step is only
 *               'ran' or 'did_not_run'; a step absent from `states` reads Waiting.
 *   onSelect    (stepId) => void — a card becomes a button (e.g. a Broken step
 *               opens its troubleshooting sheet). Omit for a read-only stage.
 *   onOpenLane  (laneKey) => void — each lane header becomes a button that opens
 *               the component page behind it (ENDPOINT → Agents, …).
 *   cgo, plane  the trigger's label and the tint fallback (`draft.cgo`, `.plane`)
 *   zoom        optional, clamped to ≥ 0.62; `compact` defaults to true.
 */
import React, { useEffect, useMemo, useRef, useState } from 'react'
import '../../styles/chain-stage.css'
import StatusIcon from './kit/StatusIcon.jsx'
import {
  buildChainStage, clampScale, doorTitle, fitScale, liveLegend, liveStateOf, LIVE_STATES, STAGE_GEOMETRY,
} from './chainStage.js'

const MODES = new Set(['editable', 'live', 'preview'])

/** What the component behind each lane is called, for the lane-header button. */
function laneButtonLabel(lane) {
  return `Open the component behind ${lane.label}`
}

/** The box's width, kept current; 0 where nothing can be measured (jsdom). */
function useBoxWidth(ref, active) {
  const [width, setWidth] = useState(0)
  useEffect(() => {
    if (!active) return undefined
    const el = ref.current
    if (!el) return undefined
    const read = () => setWidth(el.clientWidth || 0)
    read()
    if (typeof ResizeObserver === 'undefined') return undefined
    const ro = new ResizeObserver(read)
    ro.observe(el)
    return () => ro.disconnect()
  }, [ref, active])
  return width
}

function StepCardBody({ card, mode, state }) {
  const s = card.step
  return (
    <>
      <span className="chain-stage__tint" style={card.tint ? { background: card.tint } : undefined} aria-hidden="true" />
      <span className="chain-stage__card-head">
        <span className="chain-stage__plane mono">{card.plane || '—'}</span>
        <span className="chain-stage__order mono">{String(card.order).padStart(2, '0')}</span>
        <span className="chain-stage__spacer" />
        {mode === 'live' && (
          <StatusIcon status={LIVE_STATES[state].status} word={LIVE_STATES[state].word} className="chain-stage__state" />
        )}
      </span>
      <span className="chain-stage__name">{s.name || s.id}</span>
      <span className="chain-stage__card-foot">
        <span
          className={'chain-stage__door mono' + (card.signal?.unplaced ? ' chain-stage__door--unplaced' : '')}
          data-testid={`chain-door-${s.id}`}
          title={doorTitle(card.signal)}
        >
          {card.badge}
        </span>
        <span className="chain-stage__tech mono">
          {[s.technique, s.identity].filter(Boolean).join(' · ') || s.id}
        </span>
      </span>
    </>
  )
}

export default function ChainStage({
  mode = 'preview',
  steps = [],
  cgo = null,
  plane = null,
  teardown = null,
  rules = [],
  states = {},
  selectedId = null,
  onSelect = null,
  onSelectRule = null,
  onOpenLane = null,
  zoom = 1,
  compact,
  label = 'Chain',
  emptyText = 'No authored chain — this scenario has no steps to draw.',
}) {
  const m = MODES.has(mode) ? mode : 'preview'
  const isCompact = compact === undefined ? m !== 'editable' : !!compact
  const model = useMemo(
    () => buildChainStage(steps, { compact: isCompact, cgo, plane, rules: m === 'live' ? [] : rules }),
    [steps, isCompact, cgo, plane, rules, m],
  )
  const boxRef = useRef(null)
  const boxWidth = useBoxWidth(boxRef, m === 'preview' && !model.empty)
  const scale = m === 'preview' ? fitScale(boxWidth, model.width) : clampScale(zoom)

  if (model.empty) {
    return (
      <div className="chain-stage chain-stage--empty" data-testid="chain-stage" data-mode={m} role="group" aria-label={label}>
        <p className="chain-stage__empty" data-testid="chain-stage-empty">{emptyText}</p>
      </div>
    )
  }

  const stateOf = (id) => liveStateOf(states, id)
  const edgeTone = (e) => {
    if (m !== 'live' || e.kind === 'root') return ''
    const st = stateOf(e.target === 'proof' ? e.source : e.target)
    return st === 'detected' ? ' chain-stage__edge--pos' : st === 'broken' ? ' chain-stage__edge--crit' : ''
  }
  const G = STAGE_GEOMETRY
  const stepCards = model.cards.filter((c) => c.kind === 'step')
  const legend = m === 'live' ? liveLegend(stepCards.map((c) => c.step), states) : []
  const teardownCount = Array.isArray(teardown) ? teardown.length : null

  return (
    <div
      className={`chain-stage chain-stage--${m}`}
      data-testid="chain-stage"
      data-mode={m}
      data-scale={String(scale)}
      role="group"
      aria-label={label}
    >
      <div className="chain-stage__bar">
        <span className="chain-stage__summary" data-testid="chain-stage-summary">{model.summary}</span>
        {legend.length > 0 && (
          <span className="chain-stage__legend" data-testid="chain-stage-legend">
            {legend.map((l) => (
              <span key={l.key} className="chain-stage__legend-item">
                <StatusIcon status={LIVE_STATES[l.key].status} word={`${l.count} ${l.word.toLowerCase()}`} />
              </span>
            ))}
          </span>
        )}
      </div>

      <div className="chain-stage__scroll" ref={boxRef}>
        <div style={{ width: model.width * scale, height: model.height * scale }}>
          <div
            className="chain-stage__canvas"
            style={{ width: model.width, height: model.height, transform: `scale(${scale})` }}
          >
            {model.lanes.map((b) => (
              <div
                key={b.key}
                className={'chain-stage__lane' + (b.terminal ? ' chain-stage__lane--terminal' : '')}
                data-lane={b.key}
                style={{ top: b.top, height: b.h, background: b.fill }}
              >
                {m === 'live' && onOpenLane && !['LAUNCH', 'PROOF'].includes(b.key) ? (
                  <button
                    type="button"
                    className="chain-stage__lane-head chain-stage__lane-head--btn"
                    aria-label={laneButtonLabel(b)}
                    title={laneButtonLabel(b)}
                    onClick={() => onOpenLane(b.key)}
                  >
                    <span className="chain-stage__lane-label mono">{b.label}</span>
                    <span className="chain-stage__lane-sub">{b.sub}</span>
                  </button>
                ) : (
                  <div className="chain-stage__lane-head">
                    <span className="chain-stage__lane-label mono">{b.label}</span>
                    <span className="chain-stage__lane-sub">{b.sub}</span>
                  </div>
                )}
              </div>
            ))}

            <svg
              className="chain-stage__edges"
              width={model.width}
              height={model.height}
              aria-hidden="true"
            >
              {model.edges.map((e) => (
                <path
                  key={e.id}
                  data-edge={e.id}
                  data-edge-kind={e.kind}
                  className={`chain-stage__edge chain-stage__edge--${e.kind}${edgeTone(e)}`}
                  d={e.d}
                  fill="none"
                  {...(e.typed || e.kind !== 'causality' ? { strokeDasharray: e.typed ? '6 4' : '2 4' } : {})}
                >
                  <title>{e.pivot ? `${e.source} → ${e.target} · ${e.pivot}` : `${e.source} → ${e.target}`}</title>
                </path>
              ))}
            </svg>

            {model.cards.map((c) => {
              const box = { left: c.x, top: c.y, width: G.cardW, height: c.h }
              if (c.kind === 'trigger' || c.kind === 'proof') {
                return (
                  <div
                    key={c.id}
                    className={`chain-stage__card chain-stage__card--anchor chain-stage__card--${c.kind}`}
                    data-testid={`stage-card-${c.id}`}
                    data-lane={c.lane}
                    style={box}
                  >
                    <span className="chain-stage__kicker">{c.kind === 'trigger' ? 'Trigger · CGO' : 'Proof'}</span>
                    <span className="chain-stage__anchor-label mono">
                      {c.kind === 'trigger'
                        ? c.label
                        : teardownCount != null
                          ? `Teardown · ${teardownCount} command${teardownCount === 1 ? '' : 's'}`
                          : 'Teardown & results'}
                    </span>
                  </div>
                )
              }
              const state = m === 'live' ? stateOf(c.id) : null
              const attrs = {
                className: 'chain-stage__card chain-stage__card--step'
                  + (m === 'editable' && selectedId === c.id ? ' chain-stage__card--selected' : '')
                  + (state ? ` chain-stage__card--${state}` : ''),
                'data-testid': `stage-card-${c.id}`,
                'data-step-id': c.id,
                'data-lane': c.lane,
                ...(state ? { 'data-state': state } : {}),
                style: box,
              }
              const body = <StepCardBody card={c} mode={m} state={state} />
              const clickable = (m === 'editable' || m === 'live') && typeof onSelect === 'function'
              if (clickable || m === 'editable') {
                const name = [c.id, c.step.name, state ? LIVE_STATES[state].word : null].filter(Boolean).join(' · ')
                return (
                  <button
                    key={c.id}
                    type="button"
                    {...attrs}
                    aria-label={name}
                    {...(m === 'editable' ? { 'aria-pressed': selectedId === c.id } : {})}
                    onClick={() => onSelect && onSelect(c.id)}
                  >
                    {body}
                  </button>
                )
              }
              return <div key={c.id} {...attrs}>{body}</div>
            })}

            {model.rules.map((r) => (
              <div
                key={r.key}
                className="chain-stage__card chain-stage__card--rule"
                data-testid={`stage-rule-${r.key}`}
                data-lane="ENG"
                style={{ left: r.x, top: r.y, width: G.cardW, height: r.h }}
              >
                <button
                  type="button"
                  className="chain-stage__rule-btn"
                  aria-label={`Tenant rule ${r.label}`}
                  onClick={() => onSelectRule && onSelectRule(r.key)}
                  disabled={!onSelectRule}
                >
                  <span className="chain-stage__kicker">Tenant rule</span>
                  <span className="chain-stage__name mono">{r.label}</span>
                  {r.sub && <span className="chain-stage__tech">{r.sub}</span>}
                </button>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
