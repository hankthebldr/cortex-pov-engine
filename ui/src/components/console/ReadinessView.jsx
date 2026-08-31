import React, { useMemo, useState } from 'react'
import { useEnvironment } from '../../context/EnvironmentContext.jsx'
import useReadiness from './readiness/useReadiness.js'
import { HS, HS_CHIP, HS_LABEL, healthHeadline } from './readiness/healthModel.js'
import { RS, RS_CHIP, RUNG } from './readiness/connectorState.js'
import { preflightXsiamTenant, preflightConnector } from '../../api/client.js'
import Term from '../onboarding/Term.jsx'

/**
 * ReadinessView — "am I ready to run this in front of a customer?", answered
 * before the customer is watching.
 *
 * Three questions, in the order a DC asks them:
 *   1. Is this SimCore whole?          → platform components, each with a fix
 *   2. What can I prove about Cortex?  → the four-rung connector ladder
 *   3. What could I NOT check?         → the gap list
 *
 * The design constraint that shapes every line below: **nothing here may render
 * an unknown as an ok**. A component this SimCore does not report is a named
 * gap, not a zero; a tenant that answered a probe is REACHABLE, not VERIFIED;
 * and a catalog that loaded 0 entries is degraded even when the server called
 * it healthy.
 */
export default function ReadinessView({ onNavigate = () => {} }) {
  const env = useEnvironment()
  const r = useReadiness({ scenarios: env.scenarios, runs: env.runs })

  if (r.loading && !r.health) {
    return (
      <div className="readiness" data-testid="readiness-view">
        <ViewHead onRefresh={r.refresh} refreshing />
        <div className="readiness__loading" role="status" aria-live="polite">
          probing SimCore…
        </div>
      </div>
    )
  }

  const unreachable = r.health && r.health.reachable === false

  return (
    <div className="readiness" data-testid="readiness-view">
      <ViewHead
        onRefresh={r.refresh}
        model={r.health}
        ladder={r.ladder}
        lastRefreshedAt={r.lastRefreshedAt}
      />

      {unreachable ? (
        <div className="readiness__down" role="alert" data-testid="readiness-unreachable">
          <div className="readiness__down-tag">SIMCORE UNREACHABLE</div>
          <p className="readiness__down-msg">{r.health.unreachableReason}</p>
          <p className="readiness__down-hint">
            Nothing below could be checked. Every other console surface swallows this failure into
            an empty list, so the console looks <em>configured but empty</em> rather than
            disconnected — that is why this page says it once, loudly.
          </p>
        </div>
      ) : (
        <>
          <ConnectorSection ladder={r.ladder} tenants={r.tenants} onNavigate={onNavigate} />
          <ComponentSection model={r.health} onNavigate={onNavigate} />
          <GapSection model={r.health} ladder={r.ladder} />
        </>
      )}
    </div>
  )
}

// ─── Header ──────────────────────────────────────────────────────────────────

function ViewHead({ model = null, ladder = null, onRefresh, refreshing = false, lastRefreshedAt = null }) {
  const status = model ? model.overall : HS.UNKNOWN
  return (
    <header className="view-head">
      <div>
        <h1>Readiness</h1>
        <div className="view-head__meta">
          <span className={HS_CHIP[status] || 'chip'} data-testid="readiness-overall">
            {HS_LABEL[status] || 'UNKNOWN'}
          </span>{' '}
          <span className="mono">{model ? healthHeadline(model) : 'probing…'}</span>
          {ladder && (
            <>
              {' · '}
              <span className="mono" data-testid="readiness-connector-headline">
                Cortex: {ladder.headline}
              </span>
            </>
          )}
        </div>
      </div>
      <div className="readiness__head-actions">
        {lastRefreshedAt && (
          <span className="readiness__stamp mono">
            checked {new Date(lastRefreshedAt).toISOString().substring(11, 19)}Z
          </span>
        )}
        <button type="button" className="btn" onClick={onRefresh} disabled={refreshing}>
          {refreshing ? 'Checking…' : '↻ Re-check'}
        </button>
      </div>
    </header>
  )
}

// ─── 1 · Connector proof ladder ──────────────────────────────────────────────

function ConnectorSection({ ladder, tenants, onNavigate }) {
  return (
    <section className="readiness__section" aria-labelledby="rdy-connector-h">
      <h2 id="rdy-connector-h" className="readiness__h">Cortex connector — what is actually proven</h2>
      <p className="readiness__lede">
        Four different questions. A console that collapses them into one green pill lets a DC tell a
        customer their detection fired when nothing here established that.
      </p>

      <ol className="readiness__ladder" data-testid="connector-ladder">
        {ladder.rungs.map((rung, i) => (
          <li
            key={rung.id}
            className={`rdy-rung rdy-rung--${rung.state}`}
            data-testid={`rung-${rung.id}`}
            data-state={rung.state}
          >
            <div className="rdy-rung__top">
              <span className="rdy-rung__n mono">{i + 1}</span>
              <span className="rdy-rung__label">{rung.label}</span>
              <span className={RS_CHIP[rung.state]} data-testid={`rung-${rung.id}-state`}>
                {rung.state === RS.YES ? 'YES' : rung.state === RS.NO ? 'NO' : 'UNKNOWN'}
              </span>
            </div>
            <div className="rdy-rung__q">{rung.question}</div>
            <div className="rdy-rung__detail">{rung.detail}</div>
            <div className="rdy-rung__proves">
              <span className="rdy-rung__proves-yes">proves: {rung.proves}</span>
              {rung.provesNot !== '—' && (
                <span className="rdy-rung__proves-no">does NOT prove: {rung.provesNot}</span>
              )}
            </div>
          </li>
        ))}
      </ol>

      {/* The single sentence that must never drift optimistic. */}
      <p className="readiness__invariant" data-testid="tenant-verified-statement">
        <strong><Term k="tenant-verified">tenant-verified</Term>: {ladder.rungs[3].count ?? 0}</strong> — {ladder.rungs[3].detail}
      </p>

      <TenantList tenants={tenants} onNavigate={onNavigate} />
    </section>
  )
}

function TenantList({ tenants, onNavigate }) {
  if (!tenants || tenants.length === 0) {
    return (
      <div className="readiness__empty" data-testid="readiness-no-tenants">
        <p>
          No Cortex credential is registered on this SimCore. That is a legitimate configuration —
          SimCore generates signal INTO the environment and needs no tenant to do it — but every
          scoreable scenario will resolve <span className="mono">pending</span>, which means{' '}
          <strong>still owed</strong>, not <strong>failed</strong>.
        </p>
        <button type="button" className="btn" onClick={() => onNavigate('tenants')}>
          Register a tenant ▸
        </button>
      </div>
    )
  }
  return (
    <div className="readiness__tenants" data-testid="readiness-tenants">
      {tenants.map((t) => <TenantRow key={`${t.kind}:${t.name}`} tenant={t} />)}
    </div>
  )
}

function TenantRow({ tenant }) {
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState(null)

  const runPreflight = async () => {
    setBusy(true)
    setResult(null)
    try {
      const body = tenant.kind === 'xsiam_tenant'
        ? await preflightXsiamTenant(tenant.name)
        : await preflightConnector(tenant.kind)
      setResult(body)
    } catch (err) {
      setResult({ _error: err?.message || 'preflight failed', _code: err?.code || null })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="rdy-tenant" data-testid={`rdy-tenant-${tenant.name}`}>
      <div className="rdy-tenant__head">
        <span className="rdy-tenant__name mono">{tenant.name}</span>
        <span className="chip" data-testid={`rdy-tenant-kind-${tenant.name}`}>{tenant.kind}</span>
        <span className={RS_CHIP[tenant.reachable]} data-testid={`rdy-tenant-rung-${tenant.name}`}>
          {tenant.rung.toUpperCase()}
        </span>
        <button type="button" className="btn btn--xs" onClick={runPreflight} disabled={busy}>
          {busy ? 'Checking…' : 'Preflight'}
        </button>
      </div>
      <div className="rdy-tenant__kind">{tenant.kindLabel}</div>
      {tenant.host && <div className="rdy-tenant__host mono">{tenant.host}</div>}
      <div className="rdy-tenant__buys">
        {tenant.buys && <div>✓ buys: {tenant.buys}</div>}
        {tenant.doesNotBuy && <div className="rdy-tenant__notbuys">✗ {tenant.doesNotBuy}</div>}
      </div>
      {tenant.caveat && (
        <div className="rdy-tenant__caveat" data-testid={`rdy-tenant-caveat-${tenant.name}`}>
          {tenant.caveat}
        </div>
      )}
      {result && <PreflightResult body={result} />}
    </div>
  )
}

/**
 * Preflight stages, rendered as a checklist.
 *
 * A SimCore without the endpoint gets an explicit "not available on this build"
 * — it has not FAILED preflight, it cannot RUN one, and those must not look the
 * same. `queries_issued` is printed verbatim: a preflight that issued zero
 * tenant queries proves the stage logic, not the tenant.
 */
function PreflightResult({ body }) {
  if (body._error) {
    return (
      <div className="rdy-preflight rdy-preflight--err" role="alert">
        preflight failed: {body._error}{body._code ? ` [${body._code}]` : ''}
      </div>
    )
  }
  if (body._unavailable) {
    return (
      <div className="rdy-preflight rdy-preflight--gap" data-testid="preflight-unavailable">
        This SimCore has no <span className="mono">/preflight</span> endpoint. The connection has
        NOT been checked — the only exercise this credential gets is a live run, mid-demo.
      </div>
    )
  }
  const stages = Array.isArray(body.stages) ? body.stages : []
  return (
    <div className="rdy-preflight" data-testid="preflight-result">
      <div className="rdy-preflight__head">
        <span className="chip">{String(body.overall || 'unknown').toUpperCase()}</span>
        <span className="mono">
          {typeof body.queries_issued === 'number'
            ? `${body.queries_issued} tenant queries issued`
            : 'queries_issued not reported — cannot tell whether a tenant was actually contacted'}
        </span>
      </div>
      {stages.length === 0 && <div className="rdy-preflight__empty">no stages reported</div>}
      {stages.map((s) => (
        <div key={s.id} className={`rdy-stage rdy-stage--${s.status}`}>
          <span className="rdy-stage__id mono">{s.id}</span>
          <span className="chip">{String(s.status || '?').toUpperCase()}</span>
          <span className="rdy-stage__detail">{s.detail || s.code || ''}</span>
          {s.remediation && <div className="rdy-stage__fix">→ {s.remediation}</div>}
        </div>
      ))}
    </div>
  )
}

// ─── 2 · Platform components ─────────────────────────────────────────────────

function ComponentSection({ model, onNavigate }) {
  const rows = useMemo(
    () => (model?.components || []).filter((c) => c.status !== HS.UNREPORTED),
    [model],
  )
  if (!rows.length) {
    return (
      <section className="readiness__section">
        <h2 className="readiness__h">This SimCore</h2>
        <div className="readiness__empty">
          /api/health reported no components. Nothing about this deployment can be verified from
          here.
        </div>
      </section>
    )
  }
  return (
    <section className="readiness__section" aria-labelledby="rdy-comp-h">
      <h2 id="rdy-comp-h" className="readiness__h">
        This SimCore{' '}
        <span className="readiness__h-meta mono">
          {model.version ? `v${model.version}` : ''} {model.commit ? `· ${model.commit}` : ''}
        </span>
      </h2>
      <div className="readiness__grid" data-testid="health-components">
        {rows.map((c) => (
          <div
            key={c.key}
            className={`rdy-comp rdy-comp--${c.status}`}
            data-testid={`health-${c.key}`}
            data-status={c.status}
          >
            <div className="rdy-comp__head">
              <span className="rdy-comp__label">{c.label}</span>
              <span className={HS_CHIP[c.status]}>{HS_LABEL[c.status]}</span>
            </div>
            {c.count !== null && (
              <div className="rdy-comp__count mono" data-testid={`health-${c.key}-count`}>
                {c.count}
              </div>
            )}
            {c.purpose && <div className="rdy-comp__purpose">{c.purpose}</div>}
            {c.disagreement && (
              <div className="rdy-comp__disagree" data-testid={`health-${c.key}-disagreement`}>
                {c.disagreement}
              </div>
            )}
            {c.codeHint && (
              <div className="rdy-comp__code mono" data-testid={`health-${c.key}-hint`}>
                {c.codeHint}
              </div>
            )}
            {c.detail && <div className="rdy-comp__detail mono">{c.detail}</div>}
            {c.remediation && <div className="rdy-comp__fix">→ {c.remediation}</div>}
            {c.key === 'payload_shelf' && (
              <button type="button" className="btn btn--xs" onClick={() => onNavigate('adapters')}>
                Tools &amp; Payloads ▸
              </button>
            )}
            {c.key === 'agent_binaries' && (
              <button type="button" className="btn btn--xs" onClick={() => onNavigate('agents')}>
                Agents ▸
              </button>
            )}
          </div>
        ))}
      </div>
    </section>
  )
}

// ─── 3 · What could not be checked ───────────────────────────────────────────

function GapSection({ model, ladder }) {
  const gaps = model?.gaps || []
  const notChecked = model?.notChecked || []
  const unknownRungs = (ladder?.rungs || []).filter((r) => r.state === RS.UNKNOWN)
  if (!gaps.length && !unknownRungs.length && !notChecked.length) return null
  return (
    <section className="readiness__section readiness__section--gaps" aria-labelledby="rdy-gap-h">
      <h2 id="rdy-gap-h" className="readiness__h">Not checked</h2>
      <p className="readiness__lede">
        These are UNKNOWNS, not passes. A green page above does not cover anything listed here.
      </p>

      {/* The SERVER's own declaration of what it deliberately did not measure.
          Strictly better than anything the console can infer, and it is what
          stops a green health check from reading as "the tenant is fine". */}
      {notChecked.length > 0 && (
        <ul className="readiness__gaps" data-testid="readiness-not-checked">
          {notChecked.map((n) => (
            <li key={n.what} data-testid={`not-checked-${n.what}`}>
              <span className="mono">{n.what}</span> — {n.why}
              {n.checkedBy && <> <span className="mono">({n.checkedBy})</span></>}
            </li>
          ))}
        </ul>
      )}

      <ul className="readiness__gaps" data-testid="readiness-gaps">
        {gaps.map((g) => (
          <li key={g.key} data-testid={`gap-${g.key}`}>
            <span className="mono">{g.key}</span> — {g.why}
          </li>
        ))}
        {unknownRungs.map((r) => (
          <li key={`rung-${r.id}`} data-testid={`gap-rung-${r.id}`}>
            <span className="mono">connector.{r.id}</span> — {r.detail}
          </li>
        ))}
      </ul>
      <p className="readiness__invariant">
        No check on this page contacts a customer tenant. <span className="mono">/api/health</span>{' '}
        must not spend a customer&apos;s metered API quota to colour a pixel, so reachability is
        only ever reported from an explicit operator probe — the{' '}
        <strong>Preflight</strong> button above.
      </p>
    </section>
  )
}

export { RUNG }
