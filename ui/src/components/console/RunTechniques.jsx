import React, { useMemo } from 'react'
import { SCENARIOS, WORKFLOWS } from './povdata/corpus.js'
import { planeTint, planeLabel } from './povdata/planes.js'

/**
 * RunTechniques — what this run actually exercised, as techniques.
 *
 * WHY THIS IS A SEPARATE VIEW FROM THE STORYLINE
 * The Storyline answers "what happened, as a narrative". This answers "which
 * techniques did we exercise, and did each one produce a verdict" — the
 * question a customer's own detection engineer asks, and the one a POV readout
 * is graded on. They are the same run described in two vocabularies, and
 * collapsing them produced a page that was too narrative to audit and too
 * tabular to follow.
 *
 * THE KILL-CHAIN STRIP IS COVERAGE, NOT DECORATION.
 * A stage with no step is a stage this run never touched, which is a real and
 * useful gap: a chain that proves execution and exfiltration but never
 * persistence has a hole a customer will find. Empty stages are drawn, dimmed,
 * rather than omitted.
 *
 * Props:
 *   run       — the run record ({ scenario_id, ... }) | null
 *   onExplain — (detectionId) => void | null
 */
const KILL_CHAIN = [
  ['recon', 'Recon'],
  ['initial', 'Initial access'],
  ['execution', 'Execution'],
  ['persistence', 'Persistence'],
  ['privesc', 'Priv-esc'],
  ['credential', 'Credential access'],
  ['discovery', 'Discovery'],
  ['lateral', 'Lateral movement'],
  ['collection', 'Collection'],
  ['exfil', 'Exfiltration'],
]

/** MITRE technique → the kill-chain stage it belongs to. */
const STAGE_FOR = {
  T1592: 'recon', T1595: 'recon',
  T1078: 'initial', 'T1078.004': 'initial', T1195: 'initial', 'T1195.002': 'initial',
  T1610: 'execution', T1059: 'execution',
  T1003: 'credential', 'T1003.001': 'credential', 'T1558.003': 'credential',
  'T1552.007': 'credential', 'T1550.003': 'lateral',
  T1087: 'discovery', 'T1087.001': 'discovery',
  T1539: 'collection',
  T1567: 'exfil', 'T1567.002': 'exfil', 'T1071.004': 'exfil',
  LLM01: 'execution',
}

export default function RunTechniques({ run = null, onExplain = null }) {
  const rows = useMemo(() => {
    const scenarioId = run?.scenario_id || run?.scenarioId || null
    // Prefer the workflow whose chain this run executed; fall back to the
    // scenario's own primary technique. Never invent a technique list — an
    // empty table is honest, a plausible one is not.
    const wf = WORKFLOWS.find((w) => w.name === (SCENARIOS.find((s) => s[0] === scenarioId) || [])[1])
    if (wf) {
      return wf.chain.map(([id, label, plane, mitre, , door, dets, state]) => ({
        id, label, plane, mitre, door, dets, state,
      }))
    }
    const scenario = SCENARIOS.find((s) => s[0] === scenarioId)
    if (!scenario) return []
    return [{
      id: scenario[0], label: scenario[1], plane: scenario[2],
      mitre: scenario[3], door: scenario[4], dets: [], state: 'EXPECTED',
    }]
  }, [run])

  const touched = new Set(rows.map((r) => STAGE_FOR[r.mitre]).filter(Boolean))

  if (!rows.length) {
    return (
      <div className="pov-panel" data-testid="run-techniques">
        <div className="pov-panel__body">
          <p style={{ font: '400 11.5px/1.6 var(--font-ui)', color: 'var(--tx3)', margin: 0 }}>
            No technique detail for this run. The chain it executed is not in this
            instance's corpus, so nothing here can be attributed — which is reported
            rather than guessed at.
          </p>
        </div>
      </div>
    )
  }

  return (
    <div data-testid="run-techniques">
      <div className="pov-section" style={{ marginTop: 0 }}>
        <span className="pov-section__label">
          Kill chain · {touched.size} of {KILL_CHAIN.length} stages exercised
        </span>
        <span className="pov-section__hr" />
      </div>

      <div style={{ display: 'flex', gap: 3, overflowX: 'auto', paddingBottom: 4 }}>
        {KILL_CHAIN.map(([key, label]) => {
          const on = touched.has(key)
          return (
            <div
              key={key}
              style={{
                flex: '1 0 auto', minWidth: 96, padding: '9px 10px',
                background: on ? 'var(--ac-soft)' : 'var(--s2)',
                borderTop: `3px solid ${on ? 'var(--ac)' : 'var(--bd)'}`,
                borderRadius: 4,
                opacity: on ? 1 : .5,
              }}
              title={on ? 'exercised by this run' : 'not touched by this run'}
            >
              <span style={{
                display: 'block',
                font: '700 8.5px/1.2 var(--font-ui)',
                letterSpacing: '.1em', textTransform: 'uppercase',
                color: on ? 'var(--tx)' : 'var(--tx3)',
              }}>
                {label}
              </span>
            </div>
          )
        })}
      </div>

      <div className="pov-section">
        <span className="pov-section__label">Techniques this run exercised</span>
        <span className="pov-section__hr" />
      </div>

      <div className="pov-table__scroll">
        <table className="pov-table">
          <thead>
            <tr>
              <th>Step</th><th>Technique</th><th>Plane</th><th>Door</th>
              <th>Detections</th><th>Verdict</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td className="mono pov-td-strong">{r.id}<div style={{ color: 'var(--tx2)', fontFamily: 'var(--font-ui)' }}>{r.label}</div></td>
                <td className="mono">{r.mitre}</td>
                <td>
                  <span className="pov-chip-plane" style={{ borderLeftColor: planeTint(r.plane) }} title={planeLabel(r.plane)}>
                    {r.plane}
                  </span>
                </td>
                <td className="mono">{r.door}</td>
                <td>
                  {r.dets.length
                    ? r.dets.map((d) => (
                      <button
                        key={d}
                        type="button"
                        className="pov-chip-det"
                        style={{ marginRight: 4 }}
                        onClick={() => onExplain && onExplain(d)}
                      >
                        {d}
                      </button>
                    ))
                    // A step that declared nothing did not "fail to detect" —
                    // it made no claim. Those are different rows in a readout.
                    : <span className="pov-pill pov-pill--dim">NO CLAIM</span>}
                </td>
                <td>
                  <span className={`pov-pill pov-pill--${r.state === 'CONFIRMED' ? 'pos' : r.state === 'BROKEN' ? 'crit' : 'warn'}`}>
                    {r.state}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
