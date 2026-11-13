import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Button, ErrorBadge, StatusIcon } from '../kit/index.js'
import { CopyButton, CopyLive, useCopyFeedback } from '../DeployParts.jsx'
import { setConfirmedKey, useConfirmed } from '../useSetupProgress.js'

/**
 * ConfigureFrame — the one Configure checklist every component uses (design
 * v26 §4.3).
 *
 * An ordered list of steps on the left (260px), the current step on the right:
 * its title, one paragraph of why, where it happens, the fields to set, an
 * optional copyable command, and who verifies it.
 *
 *   pov     "POVengine tests this step."                       Test
 *   cortex  "Cortex shows the result — Test reads it back."    Test
 *   you     "POVengine cannot see this step — it takes your word."
 *           No Test; the DC ticks it and it reads "You confirmed".
 *
 * A Cortex step whose Test cannot read the tenant (no credential, no XQL)
 * reads Can't check and can then be ticked too — "You confirmed", never
 * "Passed". Nothing the DC ticks is ever shown as a measurement.
 *
 * A STEP is plain data:
 *   { id, title, why, where?, fields?, command?, verifier,
 *     derive?(ctx)  → result|null   live state (an agent is online): no Test needed
 *     test?(ctx, { progress }) → Promise<result>
 *     body?(ctx)    → node          inputs or an embedded surface }
 * `fields` and `command` may be functions of ctx, so a step can show a value
 * an earlier step produced (`ctx.shared`).
 *
 * A RESULT is { state, issue?, detail?, rows?, share? }:
 *   state   passed | failed | degraded | pending | cant_check
 *   issue   a short failure name for the ErrorBadge
 *   rows    per-row outcomes, in order — a Test that runs two things reports
 *           both; `progress(partial)` shows rows as they arrive
 *   share   values for later steps (a minted token)
 *
 * The step body gets `draft`/`setDraft`, and Test receives the same `draft`:
 * a step that stores values (the HTTP collector's "Add it to POVengine")
 * saves them when Test runs, then checks them. There is no Save verb.
 *
 * Results live for the session only (no history, v26 §4.2); ticks persist in
 * the same per-browser store Get started uses.
 */

export const VERIFIER_LINE = {
  pov: 'POVengine tests this step.',
  cortex: 'Cortex shows the result — Test reads it back.',
  you: 'POVengine cannot see this step — it takes your word.',
}

// A step's state as a StatusIcon. "You confirmed" and "Can't check" share the
// unknown tone: neither is a measurement.
export const STEP_STATE = {
  passed:     { status: 'up',       word: 'Passed' },
  failed:     { status: 'down',     word: 'Failed' },
  degraded:   { status: 'degraded', word: 'Warning' },
  pending:    { status: 'unknown',  word: 'Not yet' },
  cant_check: { status: 'unknown',  word: "Can't check" },
  confirmed:  { status: 'unknown',  word: 'You confirmed' },
  testing:    { status: 'unknown',  word: 'Testing' },
  todo:       { status: 'off',      word: 'To do' },
}

const DONE = new Set(['passed', 'confirmed'])
const resolve = (v, ctx) => (typeof v === 'function' ? v(ctx) : v)

export default function ConfigureFrame({
  steps = [],
  current = null,
  onSelect = () => {},
  pathPicker = null,
  ctx = {},
  confirmPrefix = 'step',
}) {
  const confirmed = useConfirmed()
  const [results, setResults] = useState({})
  const [testing, setTesting] = useState(null)
  const [drafts, setDrafts] = useState({})
  const [shared, setShared] = useState({})
  // Set on every mount, not only the first: StrictMode mounts, unmounts and
  // mounts again, and a ref left false by that cleanup would drop every
  // Test result on the floor ("Testing…" forever).
  const mounted = useRef(true)
  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])

  const keyOf = useCallback((step) => `${confirmPrefix}:${step.id}`, [confirmPrefix])

  const stepCtx = useCallback((step) => ({
    ...ctx,
    shared,
    draft: drafts[step.id] || {},
    setDraft: (patch) => setDrafts((d) => ({ ...d, [step.id]: { ...(d[step.id] || {}), ...patch } })),
  }), [ctx, shared, drafts])

  // The order matters: a live "passed" beats everything; a real Test pass
  // beats a tick; a tick beats a Test that could not read anything.
  const stateOf = useCallback((step) => {
    if (testing === step.id) return { state: 'testing', ...(results[step.id] || {}) }
    const r = results[step.id]
    const d = step.derive ? step.derive(stepCtx(step)) : null
    if (d && d.state === 'passed') return d
    if (r && r.state === 'passed') return r
    if (confirmed[keyOf(step)]) return { state: 'confirmed', detail: 'You confirmed this step.' }
    if (r) return r
    if (d) return d
    return { state: 'todo' }
  }, [testing, results, confirmed, keyOf, stepCtx])

  const states = useMemo(() => steps.map((s) => stateOf(s)), [steps, stateOf])

  const currentIndex = useMemo(() => {
    const i = steps.findIndex((s) => s.id === current)
    if (i >= 0) return i
    const firstOpen = states.findIndex((st) => !DONE.has(st.state))
    return firstOpen >= 0 ? firstOpen : Math.max(0, steps.length - 1)
  }, [steps, current, states])

  const step = steps[currentIndex] || null

  const runTest = useCallback(async (s) => {
    if (!s?.test) return
    setTesting(s.id)
    setResults((r) => ({ ...r, [s.id]: undefined }))
    const progress = (partial) => {
      if (mounted.current) setResults((r) => ({ ...r, [s.id]: { ...(r[s.id] || {}), ...partial } }))
    }
    let out
    try {
      out = await s.test(stepCtx(s), { progress })
    } catch (err) {
      out = { state: 'failed', issue: err?.code || 'Request failed', detail: err?.message || String(err) }
    }
    if (!mounted.current) return
    setResults((r) => ({ ...r, [s.id]: out || { state: 'cant_check', detail: 'The check returned nothing.' } }))
    if (out?.share) setShared((sh) => ({ ...sh, ...out.share }))
    setTesting(null)
  }, [stepCtx])

  if (!steps.length) return null

  return (
    <div className="cfg">
      <div className="cfg__side">
        {pathPicker && <PathPicker {...pathPicker} />}
        <ol className="cfg__steps" aria-label="Steps">
          {steps.map((s, i) => {
            const st = states[i]
            const look = STEP_STATE[st.state] || STEP_STATE.todo
            const on = i === currentIndex
            return (
              <li key={s.id} className="cfg__item">
                <button
                  type="button"
                  className={'cfg__step' + (on ? ' cfg__step--on' : '')}
                  aria-current={on ? 'step' : undefined}
                  onClick={() => onSelect(s.id)}
                >
                  <span className="cfg__step-n" aria-hidden="true">{i + 1}</span>
                  <span className="cfg__step-title">{s.title}</span>
                  <StatusIcon status={look.status} word={look.word} showWord={false} className="cfg__step-state" />
                </button>
              </li>
            )
          })}
        </ol>
      </div>
      {step && (
        <StepPanel
          key={step.id}
          step={step}
          index={currentIndex}
          total={steps.length}
          state={states[currentIndex]}
          ctx={stepCtx(step)}
          ticked={!!confirmed[keyOf(step)]}
          onTick={(v) => setConfirmedKey(keyOf(step), v)}
          onTest={() => runTest(step)}
          busy={testing === step.id}
        />
      )}
    </div>
  )
}

function PathPicker({ label, options = [], value, onChange = () => {} }) {
  return (
    <div className="cfg__path" role="radiogroup" aria-label={label}>
      <span className="cfg__path-label" aria-hidden="true">{label}</span>
      <div className="cfg__seg">
        {options.map(([v, l]) => (
          <button
            key={v}
            type="button"
            role="radio"
            aria-checked={value === v}
            className={'cfg__seg-opt' + (value === v ? ' cfg__seg-opt--on' : '')}
            onClick={() => onChange(v)}
          >
            {l}
          </button>
        ))}
      </div>
    </div>
  )
}

function StepPanel({ step, index, total, state, ctx, ticked, onTick, onTest, busy }) {
  const resultRef = useRef(null)
  const copier = useCopyFeedback()
  const cmdRef = useRef(null)
  const fields = resolve(step.fields, ctx) || []
  const command = resolve(step.command, ctx) || null
  const where = resolve(step.where, ctx) || null
  const look = STEP_STATE[state.state] || STEP_STATE.todo
  const canTest = step.verifier !== 'you' && typeof step.test === 'function'
  // Ticking is for what POVengine cannot read: every `you` step, and a Cortex
  // step that has no read or whose read came back Can't check.
  const tickable = step.verifier === 'you'
    || (step.verifier === 'cortex' && (!canTest || state.state === 'cant_check' || ticked))
  const passed = state.state === 'passed'
  const problem = state.state === 'failed' || state.state === 'degraded'
  const rows = Array.isArray(state.rows) ? state.rows : null

  return (
    <section className="cfg__panel" aria-label={`Step ${index + 1}: ${step.title}`}>
      <div className="cfg__panel-head">
        <span className="cfg__panel-n">Step {index + 1} of {total}</span>
        <h2 className="cfg__panel-title">{step.title}</h2>
      </div>
      <p className="cfg__why">{step.why}</p>
      {where && (
        <p className="cfg__where"><span className="cfg__k">Where</span> {where}</p>
      )}
      {fields.length > 0 && (
        <dl className="cfg__fields">
          {fields.map(([k, v]) => (
            <div className="cfg__field" key={k}>
              <dt className="cfg__k">{k}</dt>
              <dd className="cfg__v mono">{v}</dd>
            </div>
          ))}
        </dl>
      )}
      {step.body && <div className="cfg__body">{step.body(ctx)}</div>}
      {command && (
        <div className="cfg__cmd">
          <pre className="cfg__cmd-text mono" ref={cmdRef}>{command}</pre>
          <CopyButton copier={copier} id="cmd" text={command} selectRef={cmdRef} what="command" className="kit-btn kit-btn--secondary cfg__copy" />
          <CopyLive feedback={copier.feedback} />
        </div>
      )}

      <div className="cfg__verify">
        <p className="cfg__verifier">{VERIFIER_LINE[step.verifier] || VERIFIER_LINE.you}</p>
        <div className="cfg__actions">
          {canTest && (
            <Button variant="primary" onClick={onTest} disabled={busy}>
              {busy ? 'Testing…' : passed ? 'Passed' : 'Test'}
            </Button>
          )}
          {problem && state.issue && (
            <ErrorBadge
              tone={state.state === 'failed' ? 'crit' : 'warn'}
              onClick={() => resultRef.current && resultRef.current.focus()}
            >
              {state.issue}
            </ErrorBadge>
          )}
          {!problem && !busy && state.state !== 'todo' && (
            <StatusIcon status={look.status} word={look.word} />
          )}
        </div>
        {tickable && (
          <label className="cfg__tick">
            <input type="checkbox" checked={ticked} onChange={(e) => onTick(e.target.checked)} />
            <span>I did this — POVengine takes my word for it</span>
          </label>
        )}
        <div className="cfg__result" ref={resultRef} tabIndex={-1} aria-live="polite">
          {state.detail && state.state !== 'todo' && <p className="cfg__detail">{state.detail}</p>}
          {rows && rows.length > 0 && (
            <ul className="cfg__rows" aria-label="Test result">
              {rows.map((r, i) => {
                const rl = STEP_STATE[r.state] || STEP_STATE.cant_check
                return (
                  <li key={`${r.label}-${i}`} className="cfg__row">
                    <StatusIcon status={rl.status} word={rl.word} showWord={false} />
                    <span className="cfg__row-label mono">{r.label}</span>
                    {r.detail && <span className="cfg__row-detail">{r.detail}</span>}
                  </li>
                )
              })}
            </ul>
          )}
        </div>
      </div>
    </section>
  )
}
