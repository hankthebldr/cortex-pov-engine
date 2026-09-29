import React, { useMemo, useState } from 'react'
import { constraintRows, requirements, resources } from './povdata/scope.js'
import { detectionTypes, targetTypes, scoreGoals } from './povdata/wizard.js'

/**
 * SetupWizardView — `Start here`. Five steps, and the chain actually computes.
 *
 *   1 Detections    what kinds of detection must this POV prove
 *   2 Targets       what is being targeted
 *   3 Refine scope  what the environment already constrains, and what to do
 *   4 Requirements  derived from 1-3, never a fixed list
 *   5 Goals         scored from those requirements
 *
 * WHY STEPS 4 AND 5 ARE DERIVED RATHER THAN AUTHORED
 * A readiness checklist that is identical on every POV lists things this
 * engagement does not need and stays silent about things it does, so a DC
 * learns to skip it. Here, every requirement row names what requires it: pick
 * Correlation and you inherit "correlation content installed in the tenant"
 * (blocked) and "API scope covers the correlations dataset" (warn); deselect
 * network targets and the missing Broker VM relay rule disappears along with
 * the goal that depended on it.
 *
 * WHY STEP 3 IS SCOPE REFINEMENT AND NOT A POLICY EDITOR
 * It was a policy editor first — prevention profiles, NGFW profiles, tenant
 * exclusions — and that was wrong in a way worth recording: it was managing
 * Cortex, a system POVengine does not own and must never imply it can change.
 * What POVengine can legitimately do is record a DISPOSITION for each
 * constraint the environment already has, and carry that disposition into the
 * report. Prove it / work around / drop it are the only three honest answers,
 * and each moves what the POV claims.
 */
export default function SetupWizardView({ onNavigate = () => {} }) {
  const [step, setStep] = useState(0)
  const [wizDets, setWizDets] = useState(['BIOC', 'ABIOC', 'XQL', 'Correlation'])
  const [wizTargets, setWizTargets] = useState(['host', 'cloud', 'net', 'stream'])
  const [scopeChoice, setScopeChoice] = useState({})

  const state = useMemo(() => ({ wizDets, wizTargets, scopeChoice }), [wizDets, wizTargets, scopeChoice])
  const constraints = useMemo(() => constraintRows(state), [state])
  const reqs = useMemo(() => requirements(state), [state])
  const resGroups = useMemo(() => resources(state), [state])
  const goals = useMemo(() => scoreGoals(wizDets, wizTargets, reqs), [wizDets, wizTargets, reqs])

  const toggle = (set, value) => set((prev) =>
    (prev.includes(value) ? prev.filter((x) => x !== value) : prev.concat([value])))

  const reqCount = {
    met: reqs.filter((r) => r.state === 'met').length,
    warn: reqs.filter((r) => r.state === 'warn').length,
    blocked: reqs.filter((r) => r.state === 'blocked').length,
  }

  const STEPS = [
    ['Detections', 'what must be proven', `${wizDets.length} selected`],
    ['Targets', 'what is targeted', `${wizTargets.length} classes`],
    ['Refine scope', 'constraints and dispositions', `${constraints.filter((c) => c.needed).length} in play`],
    ['Requirements', 'derived from your choices', `${reqCount.met} met · ${reqCount.warn} warn · ${reqCount.blocked} blocked`],
    ['Goals', 'scored from the requirements', `${goals.filter((g) => g.state === 'provable').length} provable`],
  ]

  return (
    <div className="pov-page">
      <div className="pov-page__rule" />
      <div className="pov-page__eyebrow">Phase 1 · Scope</div>
      <h1 className="pov-page__title">Set up the POV</h1>
      <p className="pov-page__lede">
        Choose what has to be proven and what it will be proven against. Everything
        below those two choices — the requirements, the bill of materials, and whether
        each goal can close — is derived from them, not from a fixed checklist.
      </p>

      <div className="pov-steps" style={{ marginTop: 18 }} data-testid="wizard-steps">
        {STEPS.map(([label, sub, tally], i) => (
          <button
            key={label}
            type="button"
            className={'pov-step' + (i === step ? ' pov-step--on' : '') + (i < step ? ' pov-step--done' : '')}
            onClick={() => setStep(i)}
            aria-current={i === step ? 'step' : undefined}
          >
            <span className="pov-step__head">
              <span className="pov-step__n">{i < step ? '✓' : i + 1}</span>
              <span className="pov-step__label">{label}</span>
            </span>
            <span className="pov-step__sub">{tally || sub}</span>
          </button>
        ))}
      </div>

      {step === 0 && (
        <>
          <div className="pov-section">
            <span className="pov-section__label">What kinds of detection must this POV prove?</span>
            <span className="pov-section__hr" />
          </div>
          <div className="pov-grid pov-grid--md">
            {detectionTypes().map((d) => {
              const on = wizDets.includes(d.type)
              return (
                <button
                  key={d.type}
                  type="button"
                  className={'pov-card' + (on ? ' pov-card--on' : '')}
                  onClick={() => toggle(setWizDets, d.type)}
                  aria-pressed={on}
                >
                  <span className="pov-card__head">
                    <span className={'pov-box' + (on ? ' pov-box--on' : '')}><i /></span>
                    <span className="pov-pill">{d.type}</span>
                    <span className="pov-card__spacer" />
                    <span className="pov-fact__k" style={{ minWidth: 0 }}>{d.count}</span>
                  </span>
                  <span className="pov-card__title" style={{ display: 'block', marginBottom: 6 }}>{d.name}</span>
                  <span className="pov-card__blurb">{d.blurb}</span>
                  <span className="pov-card__foot">
                    <span className="pov-fact__k" style={{ minWidth: 0 }}>needs</span>
                    {d.needs.map((n) => <span className="pov-chip-det" key={n}>{n}</span>)}
                  </span>
                </button>
              )
            })}
          </div>
        </>
      )}

      {step === 1 && (
        <>
          <div className="pov-section">
            <span className="pov-section__label">What is being targeted?</span>
            <span className="pov-section__hr" />
          </div>
          <div className="pov-grid pov-grid--sm">
            {targetTypes().map((t) => {
              const on = wizTargets.includes(t.key)
              return (
                <button
                  key={t.key}
                  type="button"
                  className={'pov-card pov-card--tinted' + (on ? ' pov-card--on' : '')}
                  style={{ borderTopColor: t.tint }}
                  onClick={() => toggle(setWizTargets, t.key)}
                  aria-pressed={on}
                >
                  <span className="pov-card__head">
                    <span className={'pov-box' + (on ? ' pov-box--on' : '')}><i /></span>
                    <span className="pov-card__title">{t.name}</span>
                  </span>
                  <span className="pov-card__meta" style={{ display: 'block', marginBottom: 8 }}>{t.door}</span>
                  <span className="pov-card__foot" style={{ display: 'block' }}>
                    <span className="pov-card__blurb" style={{ marginBottom: 0 }}>{t.inScope}</span>
                  </span>
                </button>
              )
            })}
          </div>
        </>
      )}

      {step === 2 && (
        <>
          <div className="pov-section">
            <span className="pov-section__label">What the environment already constrains</span>
            <span className="pov-section__hr" />
          </div>
          <p className="pov-page__lede" style={{ marginBottom: 14 }}>
            POVengine does not change customer policy. It records which disposition you
            chose for each constraint, so the readout can be read honestly later.
          </p>
          <div className="pov-panel">
            {constraints.map((c) => {
              const choice = scopeChoice[c.id] || 'prove'
              return (
                <div
                  className="pov-panel__row"
                  key={c.id}
                  style={{ opacity: c.needed ? 1 : .45 }}
                >
                  <div className="pov-card__head">
                    <span className="pov-card__code">{c.id}</span>
                    <span className="pov-card__title">{c.label}</span>
                    <span className="pov-card__spacer" />
                    {!c.needed && <span className="pov-pill pov-pill--dim">NOT IN SCOPE</span>}
                  </div>
                  <div className="pov-card__meta" style={{ marginBottom: 9 }}>{c.found}</div>
                  {c.needed && (
                    <>
                      <div style={{ display: 'flex', gap: 6, marginBottom: 9 }}>
                        {[['prove', 'Prove it'], ['around', 'Work around'], ['drop', 'Drop it']].map(([k, label]) => (
                          <button
                            key={k}
                            type="button"
                            className={'pov-btn' + (choice === k ? ' pov-btn--primary' : '')}
                            onClick={() => setScopeChoice((p) => ({ ...p, [c.id]: k }))}
                            aria-pressed={choice === k}
                          >
                            {label}
                          </button>
                        ))}
                      </div>
                      {/* The consequence of the choice, written out. A control
                          whose effect you have to infer is a control that gets
                          set once and never revisited. */}
                      <div className="pov-card__blurb" style={{ marginBottom: 0 }}>{c.effects[choice]}</div>
                    </>
                  )}
                </div>
              )
            })}
          </div>
        </>
      )}

      {step === 3 && (
        <>
          <div className="pov-section">
            <span className="pov-section__label">Requirements · {reqCount.met} met · {reqCount.warn} warn · {reqCount.blocked} blocked</span>
            <span className="pov-section__hr" />
          </div>
          <div className="pov-panel" data-testid="wizard-requirements">
            {reqs.map((r, i) => (
              <div className="pov-panel__row" key={`${r.label}-${i}`}>
                <div className="pov-card__head">
                  <span className={`pov-dot pov-dot--${r.state === 'met' ? 'pos' : r.state === 'warn' ? 'warn' : 'crit'}`} />
                  <span className="pov-card__title">{r.label}</span>
                  <span className="pov-card__spacer" />
                  <span className={`pov-pill pov-pill--${r.state === 'met' ? 'pos' : r.state === 'warn' ? 'warn' : 'crit'}`}>
                    {r.state.toUpperCase()}
                  </span>
                </div>
                <div className="pov-card__blurb" style={{ marginBottom: 6 }}>{r.detail}</div>
                {/* Attribution is the feature: it is what makes this a derived
                    list rather than a checklist wearing one. */}
                <div className="pov-fact__k" style={{ minWidth: 0 }}>required by · {r.because}</div>
              </div>
            ))}
          </div>

          <div className="pov-section">
            <span className="pov-section__label">Resources to stand up</span>
            <span className="pov-section__hr" />
          </div>
          {resGroups.map((g) => (
            <div className="pov-panel" key={g.label} style={{ marginBottom: 14 }}>
              <div className="pov-panel__head">
                <div className="pov-panel__title">{g.label}</div>
                <div className="pov-panel__sub">{g.sub}</div>
              </div>
              {g.rows.map((r) => (
                <div
                  className="pov-panel__row"
                  key={r.name}
                  // Resources dim rather than disappear when their target class
                  // is not selected, so the list always reads as "this POV's
                  // list" without hiding that a thing exists at all.
                  style={{ opacity: r.needed ? 1 : .45 }}
                >
                  <div className="pov-card__head">
                    <span className="pov-card__title">{r.name}</span>
                    <span className="pov-fact__k" style={{ minWidth: 0 }}>{r.kind}</span>
                    <span className="pov-card__spacer" />
                    <span className="pov-card__meta">{r.needed ? r.instance : '—'}</span>
                    <span className={`pov-pill pov-pill--${!r.needed ? 'dim' : r.state === 'ready' ? 'pos' : r.state === 'missing' ? 'crit' : 'warn'}`}>
                      {(r.needed ? r.state : 'not needed').toUpperCase()}
                    </span>
                  </div>
                  <div className="pov-card__blurb" style={{ marginBottom: 0 }}>{r.purpose}</div>
                </div>
              ))}
            </div>
          ))}
        </>
      )}

      {step === 4 && (
        <>
          <div className="pov-section">
            <span className="pov-section__label">Goals</span>
            <span className="pov-section__hr" />
          </div>
          <div className="pov-grid pov-grid--lg" data-testid="wizard-goals">
            {goals.map((g) => (
              <div className="pov-panel" key={g.id}>
                <div className="pov-panel__head">
                  <div className="pov-card__head">
                    <span className="pov-card__code">{g.id}</span>
                    <span className="pov-card__spacer" />
                    <span className={`pov-pill pov-pill--${g.tone}`}>{g.state.toUpperCase()}</span>
                  </div>
                  <div className="pov-panel__sub" style={{ fontFamily: 'var(--font-ui)' }}>{g.label}</div>
                </div>
                <div className="pov-panel__body">
                  <div className="pov-card__blurb" style={{ marginBottom: 0 }}>{g.note}</div>
                </div>
              </div>
            ))}
          </div>
          <div style={{ display: 'flex', gap: 8, marginTop: 18 }}>
            <button type="button" className="pov-btn pov-btn--primary" onClick={() => onNavigate('library')}>
              Next: Library
            </button>
            <button type="button" className="pov-btn" onClick={() => onNavigate('scope')}>
              Review components
            </button>
          </div>
        </>
      )}
    </div>
  )
}
