/**
 * NewRunPanel — the one place a run is started (design C1, "CHANGES SINCE
 * v26"; Launch gate MVP, v33): 1 What runs · 2 Readiness · 3 Where it runs ·
 * 4 What it will do, then the
 * authorisation tick and a single primary Run. The tick starts unticked; Run
 * stays disabled until a target is chosen, no gate check blocks and the tick
 * is given, and every reason it is disabled is written under it.
 *
 * Presentational: LaunchView owns the launch hook and passes everything in.
 */
import React from 'react'
import { Button, StatusIcon } from './kit/index.js'
import '../../styles/destinations/new-run.css'

void React

export default function NewRunPanel({
  scenario,
  checks = { pass: 0, total: 0 },
  onChangeScenario = () => {},
  readiness = null,
  targets = [],
  selected = null,
  onSelect = () => {},
  targetExtra = null,
  plan = [],
  identityOptions = [],
  identity = '',
  onIdentity = () => {},
  authorised = false,
  onAuthorised = () => {},
  consentExtra = null,
  blockers = [],
  running = false,
  onRun = () => {},
}) {
  const sid = scenario?.scenario_id || scenario?.id
  const blockersId = 'new-run-blockers'
  const isSel = (t) => selected && selected.kind === t.kind && selected.id === t.id

  return (
    <section className="new-run" aria-labelledby="new-run-title" data-testid="new-run">
      <h2 id="new-run-title" className="new-run__title">New POV run</h2>
      <div className="new-run__sub">
        {checks.pass} of {checks.total} checks pass
      </div>

      <div className="new-run__step">1 · What runs</div>
      <div className="new-run__what">
        <div className="new-run__what-meta mono">
          {sid}{scenario?.mitre_technique ? ` · ${scenario.mitre_technique}` : ''}{scenario?.plane ? ` · ${scenario.plane}` : ''}
        </div>
        <div className="new-run__what-title">{scenario?.name || sid}</div>
      </div>
      <button type="button" className="linklike new-run__change" onClick={onChangeScenario}>
        Pick another scenario
      </button>

      {readiness && (
        <>
          <div className="new-run__step">2 · Readiness</div>
          <div className="new-run__readiness">{readiness}</div>
        </>
      )}

      <div className="new-run__step" id="new-run-where">{readiness ? '3' : '2'} · Where it runs</div>
      <div className="new-run__targets" role="radiogroup" aria-labelledby="new-run-where">
        {targets.map((t) => (
          <button
            key={`${t.kind}:${t.id}`}
            type="button"
            role="radio"
            aria-checked={isSel(t) ? 'true' : 'false'}
            aria-disabled={t.choosable ? undefined : 'true'}
            className={'new-run__target' + (isSel(t) ? ' new-run__target--on' : '') + (t.choosable ? '' : ' new-run__target--off')}
            data-testid={`new-run-target-${t.kind}-${t.id}`}
            onClick={() => { if (t.choosable) onSelect({ kind: t.kind, id: t.id, label: t.label }) }}
          >
            <span className="new-run__radio" aria-hidden="true" />
            <span className="new-run__target-text">
              <span className="new-run__target-label">{t.label}</span>
              <span className="new-run__target-sub">{t.sub}</span>
            </span>
            <StatusIcon status={t.status} word={t.word} />
          </button>
        ))}
      </div>
      {targetExtra}

      <div className="new-run__step">{readiness ? '4' : '3'} · What it will do</div>
      <dl className="new-run__plan">
        {plan.map((p) => (
          <div key={p.k} className="new-run__plan-row">
            <dt>{p.k}</dt>
            <dd className="mono">
              {p.k === 'Runs as' && identityOptions.length > 1 ? (
                <select
                  className="new-run__select"
                  aria-label="Runs as"
                  value={identity}
                  onChange={(e) => onIdentity(e.target.value)}
                >
                  {identityOptions.map((o) => <option key={o} value={o}>{o}</option>)}
                </select>
              ) : p.v}
            </dd>
          </div>
        ))}
      </dl>

      <label className="new-run__consent">
        <input
          type="checkbox"
          checked={authorised}
          onChange={(e) => onAuthorised(e.target.checked)}
          data-testid="new-run-authorised"
        />
        <span>I am authorised to detonate attack-shaped signal on this tenant, and the owning SOC has been told.</span>
      </label>
      {consentExtra}

      <Button
        variant="primary"
        className="new-run__run"
        disabled={running || blockers.length > 0}
        aria-describedby={blockers.length ? blockersId : undefined}
        onClick={onRun}
      >
        Run
      </Button>
      {running && <div className="new-run__note" role="status">Starting…</div>}
      {blockers.length > 0 && (
        <ul className="new-run__blockers" id={blockersId} data-testid="launch-blockers">
          {blockers.map((b) => (
            <li key={b} className="launch-blockers__item">
              <StatusIcon status="off" word="Needed" showWord={false} /> {b}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
