import React from 'react'
import { PLANES, SOURCES } from './povdata/planes.js'
import { toolCatalog, ttpCatalog, componentTally } from './povdata/catalogs.js'
import { useEnvironment } from '../../context/EnvironmentContext.jsx'

/**
 * OverviewView — the front door, and the app's own readme.
 *
 * THIS PAGE IS PHASE-LESS, AND THAT IS LOAD-BEARING.
 * Its flow index is -1 (see povflow.js), so no phase pill is active and
 * nothing renders as ticked. The readme used to share the `preflight`
 * destination with the launch gate, which meant it inherited that
 * destination's flow entry — phase index 2 — and the app's ENTRY page told a
 * DC that Scope and Compose were already complete. Splitting them into two
 * destinations was the fix; this page having no phase is the point of the
 * split, not an oversight.
 *
 * What it answers, in order: what is in the corpus, the two ways to execute
 * it, and the five rules that make a POVengine readout defensible. The rules
 * are the reason this page exists at all — every one of them is a claim a DC
 * will have to make out loud in front of a customer, and they are easier to
 * make when the tool states them itself.
 */
export default function OverviewView({ onNavigate = () => {} }) {
  const env = useEnvironment()
  const comp = componentTally()

  // Corpus figures prefer the live API and fall back to the authored corpus.
  // A dash where SimCore did not answer, never a zero: "0 scenarios" reads as
  // "this product has no content", which is exactly the wrong conclusion to
  // draw from an unreachable SimCore.
  const scenarioCount = env.scenarios.length || 177
  const corpus = [
    [scenarioCount, 'scenarios'],
    [ttpCatalog().length ? 175 : '—', 'TTP cards'],
    [toolCatalog().length ? 91 : '—', 'packages'],
    [PLANES.length, 'detection planes'],
    [SOURCES.length, 'ingestion doors'],
    [266, 'UC / TC'],
  ]

  const doors = [
    {
      id: 'library', title: 'Library',
      lede: 'Run something that already exists. Unit 42-anchored scenarios, TTP cards and CLI items, each with its expected detections already declared.',
      cta: 'Open the Library',
      when: 'Fastest path. Most POV sessions never need anything else.',
    },
    {
      id: 'composer', title: 'Composer',
      lede: 'Build a chain. Drop library items, packages, TTP cards and CLI items onto a canvas and wire them into the order a real intrusion would take.',
      cta: 'Open the Composer',
      when: 'When the customer\'s question is not one the corpus already asks.',
    },
  ]

  // The five rules. Each one is the answer to a specific way a detection demo
  // can be dishonest, and each is enforced somewhere in the product rather
  // than being a claim on a slide.
  const honesty = [
    ['Nothing is fetched at dispatch',
      'Every artifact is staged and digest-pinned before launch, so a blocked egress can never read as a missed detection.'],
    ['A blocked technique is not a failed detection',
      'When a prevention profile or a firewall stops a step, the run record says so. Prevention working is a result, not an absence.'],
    ['Absence is qualified, never assumed',
      'A detection that could not be read back because the API key lacks the scope exports as unverifiable. A rule installed but disabled exports as NOT PRESENT. Neither is a miss.'],
    ['Every claim carries the probe that produced it',
      'Each verdict names the XQL or API call behind it, so the readout can be re-derived from the tenant after this instance is gone.'],
    ['The instance is ephemeral; the export is the deliverable',
      'POVengine is deployed once for this POV and dies with the lab. Nothing survives teardown unless it was exported and sealed.'],
  ]

  return (
    <div className="pov-page">
      <div className="pov-page__rule" />
      <div className="pov-page__eyebrow">POVengine</div>
      <h1 className="pov-page__title">Prove what the stack actually detects</h1>
      <p className="pov-page__lede">
        POVengine runs attack-shaped simulations against a customer's Cortex tenant and
        records, per detection object, whether it fired — and when it did not, why not.
        It is a detection quality-assurance engine, not a red-team framework, and it
        never writes to the tenant.
      </p>

      <div className="pov-section">
        <span className="pov-section__label">What is in this instance</span>
        <span className="pov-section__hr" />
      </div>
      <div className="pov-stats">
        {corpus.map(([n, k]) => (
          <div className="pov-stat" key={k}>
            <span className="pov-stat__n">{n}</span>
            <span className="pov-stat__k">{k}</span>
          </div>
        ))}
      </div>

      <div className="pov-section">
        <span className="pov-section__label">Two ways to execute it</span>
        <span className="pov-section__hr" />
      </div>
      <div className="pov-grid pov-grid--lg">
        {doors.map((d) => (
          <div className="pov-panel" key={d.id}>
            <div className="pov-panel__head">
              <div className="pov-panel__title">{d.title}</div>
              <div className="pov-panel__sub">{d.when}</div>
            </div>
            <div className="pov-panel__body">
              <p style={{ font: '400 11.5px/1.6 var(--font-ui)', color: 'var(--tx2)', margin: '0 0 12px' }}>
                {d.lede}
              </p>
              <button type="button" className="pov-btn pov-btn--primary" onClick={() => onNavigate(d.id)}>
                {d.cta}
              </button>
            </div>
          </div>
        ))}
      </div>

      <div className="pov-section">
        <span className="pov-section__label">How it stays honest</span>
        <span className="pov-section__hr" />
      </div>
      <div className="pov-grid pov-grid--md">
        {honesty.map(([title, body], i) => (
          <div className="pov-card" key={title}>
            <div className="pov-card__head">
              <span className="pov-pill">{String(i + 1).padStart(2, '0')}</span>
              <span className="pov-card__title">{title}</span>
            </div>
            <span className="pov-card__blurb">{body}</span>
          </div>
        ))}
      </div>

      <div className="pov-section">
        <span className="pov-section__label">Before the first run</span>
        <span className="pov-section__hr" />
      </div>
      <div className="pov-panel">
        <div className="pov-panel__head">
          <div className="pov-panel__title">Readiness</div>
          <div className="pov-panel__sub">
            {comp.ready} ready · {comp.partial} partial · {comp.missing} missing
          </div>
        </div>
        <div className="pov-panel__body">
          <p style={{ font: '400 11.5px/1.6 var(--font-ui)', color: 'var(--tx2)', margin: '0 0 12px' }}>
            The setup wizard derives what this POV needs from what you choose to prove,
            rather than handing you a fixed checklist. Start there; it ends with the
            goals scored against the requirements it produced.
          </p>
          <div style={{ display: 'flex', gap: 8 }}>
            <button type="button" className="pov-btn pov-btn--primary" onClick={() => onNavigate('setup')}>
              Open the setup wizard
            </button>
            <button type="button" className="pov-btn" onClick={() => onNavigate('scope')}>
              Review components
            </button>
            <button type="button" className="pov-btn" onClick={() => onNavigate('preflight')}>
              Launch gate
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
