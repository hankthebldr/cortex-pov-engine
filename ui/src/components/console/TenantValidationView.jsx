import React, { useState } from 'react'
import { apiCatalog, validationSheets } from './povdata/tenantApi.js'

/**
 * TenantValidationView — `6 · Prove`. Three tabs, in the order the work happens.
 *
 *   1 Credentials      onboard the key and prove it can read what we will ask
 *   2 API calls        the actual probes, with their inputs and their returns
 *   3 Validation sheets the exportable tables those returns populate
 *
 * WHY THE RESPONSES ARE SHOWN VERBATIM
 * This surface exists because a POV readout is only as defensible as the thing
 * behind each row. Showing the request body and the raw response — rather than
 * a verdict derived from them off-screen — is what lets a DC answer "how do you
 * know?" in the room. It is also the only way the three failure modes stay
 * distinguishable:
 *
 *   200 with total_count: 0   the alert did not exist in the window.
 *                             AWAITING, not a miss.
 *   403 Forbidden             the key scope excludes this dataset. A
 *                             PERMISSIONS ARTEFACT, not an absence of signal.
 *   installed: true,          the rule exists but cannot fire. NOT PRESENT,
 *   enabled: false            which is the whole difference between a content
 *                             gap and a missed detection.
 *
 * Collapsing any of those three into "not detected" is the single most
 * expensive thing this console could do, because it is the claim a customer
 * will push back on hardest and the one a DC cannot walk back.
 *
 * EVERY SHEET ROW CARRIES THE PROBE THAT PRODUCED IT, so the sheet can be
 * re-derived from the tenant after this instance is gone.
 */
export default function TenantValidationView({ onNavigate = () => {} }) {
  const [tab, setTab] = useState(0)
  const [openProbe, setOpenProbe] = useState(null)
  const probes = apiCatalog()
  const sheets = validationSheets()

  const TABS = ['1 · Credentials', '2 · API calls', '3 · Validation sheets']

  return (
    <div className="pov-page">
      <div className="pov-page__rule" />
      <div className="pov-page__eyebrow">Phase 6 · Prove</div>
      <h1 className="pov-page__title">Tenant validation</h1>
      <p className="pov-page__lede">
        Every claim this POV makes, re-asked of the customer's own tenant and answered
        by a named API call. Nothing here is inferred from the run record alone.
      </p>

      <div className="pov-tabs" style={{ marginTop: 18 }} data-testid="validation-tabs">
        {TABS.map((t, i) => (
          <button
            key={t}
            type="button"
            className={'pov-tab' + (i === tab ? ' pov-tab--on' : '')}
            onClick={() => setTab(i)}
          >
            {t}
          </button>
        ))}
      </div>

      {tab === 0 && <CredentialsTab />}

      {tab === 1 && (
        <>
          <div className="pov-section">
            <span className="pov-section__label">{probes.length} probes · inputs and returns</span>
            <span className="pov-section__hr" />
          </div>
          <div className="pov-grid pov-grid--lg">
            {probes.map((p) => {
              const failed = String(p.status).startsWith('4')
              const open = openProbe === p.key
              return (
                <div className="pov-panel" key={p.key}>
                  <button
                    type="button"
                    className="pov-panel__head"
                    style={{ width: '100%', textAlign: 'left', border: 0, cursor: 'pointer' }}
                    onClick={() => setOpenProbe(open ? null : p.key)}
                    aria-expanded={open}
                  >
                    <div className="pov-card__head">
                      <span className="pov-pill">{p.method}</span>
                      <span className="pov-card__title">{p.name}</span>
                      <span className="pov-card__spacer" />
                      <span className={`pov-pill pov-pill--${failed ? 'crit' : 'pos'}`}>{p.status}</span>
                    </div>
                    <div className="pov-panel__sub">{p.path}</div>
                  </button>
                  <div className="pov-panel__body">
                    <div className="pov-facts" style={{ marginBottom: 10 }}>
                      <div className="pov-fact"><span className="pov-fact__k">Validates</span><span className="pov-fact__v">{p.validates}</span></div>
                      <div className="pov-fact"><span className="pov-fact__k">Returned</span><span className="pov-fact__v">{p.rows} · {p.timing}</span></div>
                      {p.inputs.map((i) => (
                        <div className="pov-fact" key={i.k}>
                          <span className="pov-fact__k">{i.k}</span>
                          <span className="pov-fact__v">{i.v}</span>
                        </div>
                      ))}
                    </div>

                    {open && (
                      <>
                        <div className="pov-section" style={{ marginTop: 4 }}>
                          <span className="pov-section__label">Request</span>
                          <span className="pov-section__hr" />
                        </div>
                        <code className="pov-code">{p.body}</code>
                        <div className="pov-section">
                          <span className="pov-section__label">Response</span>
                          <span className="pov-section__hr" />
                        </div>
                        <code className="pov-code">{p.response}</code>
                      </>
                    )}

                    {/* How to read it. This line is the point of the card. */}
                    <p style={{ font: '400 10.5px/1.6 var(--font-ui)', color: failed ? 'var(--crit)' : 'var(--tx2)', margin: '10px 0 0' }}>
                      {p.reads}
                    </p>
                  </div>
                </div>
              )
            })}
          </div>
        </>
      )}

      {tab === 2 && (
        <>
          {Object.entries(sheets).map(([key, s]) => (
            <section key={key}>
              <div className="pov-section">
                <span className="pov-section__label">{s.label} · {s.n} rows</span>
                <span className="pov-section__hr" />
                {/* Four formats because the sheet leaves with the DC. CSV and
                    XLSX go to the customer, JSON re-enters a pipeline, and
                    Markdown pastes into the readout without reformatting. */}
                {['CSV', 'XLSX', 'JSON', 'MD'].map((fmt) => (
                  <button type="button" className="pov-btn" key={fmt}>{fmt}</button>
                ))}
              </div>
              <div className="pov-table__scroll">
                <table className="pov-table">
                  <thead>
                    <tr>{s.head.map((h) => <th key={h}>{h}</th>)}</tr>
                  </thead>
                  <tbody>
                    {s.rows.map((r, i) => (
                      <tr key={i}>
                        {r.map((cell, j) => (
                          <td key={j} className={j === 0 ? 'mono pov-td-strong' : 'mono'}>{cell}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          ))}
          <div style={{ display: 'flex', gap: 8, marginTop: 18 }}>
            <button type="button" className="pov-btn pov-btn--primary" onClick={() => onNavigate('proof')}>
              Next: Proof &amp; Export
            </button>
          </div>
        </>
      )}
    </div>
  )
}

/**
 * Credentials — onboarding, then the scopes, then the proof the key works.
 *
 * The scope list says WHY each one is needed, because a customer asked to
 * grant an API scope will ask what it is for, and "the tool wants it" is not
 * an answer that gets a key issued.
 */
function CredentialsTab() {
  const steps = [
    ['Create the key', 'Settings → Configurations → API Keys → New Key, role Viewer'],
    ['Grant the scope', 'the five scopes below — read-only, never write'],
    ['Store the secret', 'held in memory for this instance only; never written to the export'],
    ['Verify', 'one healthcheck call proves the key can read before any run'],
  ]
  const scopes = [
    ['read:alerts', 'granted', 'Reads BIOC and ABIOC alerts — the bulk of every detection claim.'],
    ['read:datasets', 'granted', 'Runs the XQL probes that measure MTTD and prove a query in the customer\'s own tenant.'],
    ['read:correlations', 'granted', 'Distinguishes a correlation rule that is absent from one that is installed but disabled.'],
    ['read:incidents', 'missing', 'Without it, "was an incident opened?" is unverifiable — which the readout says, rather than reporting no incident.'],
    ['read:audits', 'optional', 'Only needed to prove the key itself never wrote to the tenant.'],
  ]

  return (
    <>
      <div className="pov-section">
        <span className="pov-section__label">Onboarding</span>
        <span className="pov-section__hr" />
      </div>
      <div className="pov-steps">
        {steps.map(([label, sub], i) => (
          <div className="pov-step" key={label}>
            <span className="pov-step__head">
              <span className="pov-step__n">{i + 1}</span>
              <span className="pov-step__label">{label}</span>
            </span>
            <span className="pov-step__sub">{sub}</span>
          </div>
        ))}
      </div>

      <div className="pov-grid pov-grid--lg" style={{ marginTop: 16 }}>
        <div className="pov-panel">
          <div className="pov-panel__head">
            <div className="pov-panel__title">Credential</div>
            <div className="pov-panel__sub">acme-prod.xdr.us</div>
          </div>
          <div className="pov-panel__body">
            <label className="pov-field">
              <span className="pov-field__k">Tenant FQDN</span>
              <input className="pov-field__input" defaultValue="acme-prod.xdr.us" />
            </label>
            <label className="pov-field">
              <span className="pov-field__k">API key id (x-xdr-auth-id)</span>
              <input className="pov-field__input" defaultValue="4" />
            </label>
            <label className="pov-field">
              <span className="pov-field__k">API key</span>
              {/* Masked, and it never leaves this instance — see the note below. */}
              <input className="pov-field__input" type="password" defaultValue="••••••••••••••••" />
            </label>
            <label className="pov-field">
              <span className="pov-field__k">Key type</span>
              <input className="pov-field__input" defaultValue="Advanced · Viewer role" />
            </label>
            <label className="pov-field">
              <span className="pov-field__k">Run window</span>
              <input className="pov-field__input" defaultValue="24h relative" />
            </label>
          </div>
        </div>

        <div className="pov-panel">
          <div className="pov-panel__head">
            <div className="pov-panel__title">Scopes · why each is needed</div>
            <div className="pov-panel__sub">3 granted · 1 missing · 1 optional</div>
          </div>
          {scopes.map(([name, state, why]) => (
            <div className="pov-panel__row" key={name}>
              <div className="pov-card__head">
                <span className="pov-card__code">{name}</span>
                <span className="pov-card__spacer" />
                <span className={`pov-pill pov-pill--${state === 'granted' ? 'pos' : state === 'missing' ? 'crit' : 'dim'}`}>
                  {state.toUpperCase()}
                </span>
              </div>
              <div className="pov-card__blurb" style={{ marginBottom: 0 }}>{why}</div>
            </div>
          ))}
        </div>
      </div>

      <div className="pov-grid pov-grid--lg" style={{ marginTop: 14 }}>
        <div className="pov-panel">
          <div className="pov-panel__head">
            <div className="pov-panel__title">Verification</div>
            <div className="pov-panel__sub">200 OK · 142ms</div>
          </div>
          <div className="pov-panel__body">
            <code className="pov-code">
              {'POST /public_api/v1/healthcheck\n  x-xdr-auth-id: 4\n  Authorization: ••••\n\n→ 200 OK  { "reply": { "status": "available" } }'}
            </code>
          </div>
        </div>
        <div className="pov-panel">
          <div className="pov-panel__head">
            <div className="pov-panel__title">Secret handling</div>
            <div className="pov-panel__sub">nothing here survives teardown</div>
          </div>
          <div className="pov-panel__body">
            <p style={{ font: '400 11.5px/1.6 var(--font-ui)', color: 'var(--tx2)', margin: 0 }}>
              The key is held for the life of this instance and dies with the lab. It is
              never written to an export, a run record or a manifest — an exported bundle
              carries the probe text and the row counts, never the credential that ran
              them. The role is read-only by construction, so POVengine cannot write to
              the tenant even if asked to.
            </p>
          </div>
        </div>
      </div>
    </>
  )
}
