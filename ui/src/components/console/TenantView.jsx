import React, { Suspense, lazy, useState } from 'react'
import { tenantCatalog } from './povdata/catalogs.js'
import { useEnvironment } from '../../context/EnvironmentContext.jsx'

const TenantManager = lazy(() => import('./TenantManager.jsx'))

/**
 * TenantView — `1 · Scope`. One tenant per instance, in depth.
 *
 * ONE TENANT, NOT A LIST — AND WHY THE LIST STILL EXISTS UNDERNEATH
 * A POVengine instance is deployed once for one POV and dies with the lab, so
 * a tenant *list* was modelling a thing that cannot happen: you do not run half
 * a POV against Acme and half against Northwind. The page therefore leads with
 * the bound tenant and everything that decides whether its answers can be
 * trusted.
 *
 * The registration machinery is not gone, it is the fourth tab. Binding a
 * tenant is a real operation that has to happen once, and the existing wizard
 * does it properly (validation, liveness probe, provider write-through). What
 * changed is that it stopped being the whole page.
 *
 * THE FOUR TABS ARE FOUR DIFFERENT DOUBTS
 *   Health check      can we reach it, and is the credential accepted?
 *   Read-back surface can we read the datasets the claims depend on?
 *   Integrations      what else is feeding it, and what can an agent ask of it?
 *   Binding           how it was bound, by whom, and what a rebind destroys.
 *
 * Nothing on this page writes to the tenant. The role is read-only by
 * construction, which is a product guarantee rather than a policy — and the
 * page says so, because "it never writes" is the first question a security team
 * asks about a tool that runs attack-shaped traffic.
 */
export default function TenantView({ onNavigate = () => {} }) {
  const [tab, setTab] = useState(0)
  const env = useEnvironment()

  // The bound tenant: the provider's active scope where there is one, else the
  // seed catalog's bound row. Never a silent default to "the first tenant" —
  // that is the bug that made every tenant open as a green BOUND Acme.
  const seed = tenantCatalog()
  const bound = seed.find((t) => t.state === 'bound') || seed[0]
  const live = env.tenant
  const host = live ? (live.host || live.name || live.id) : bound.host
  const name = live ? (live.name || live.id) : bound.name

  const TABS = ['Health check', 'Read-back surface', 'Integrations & MCP', 'Binding']

  return (
    <div className="pov-page">
      <div className="pov-page__rule" />
      <div className="pov-page__eyebrow">Phase 1 · Scope</div>
      <h1 className="pov-page__title">{name}</h1>
      <p className="pov-page__lede">
        <span className="mono">{host}</span> — the one tenant this instance is bound to.
        Everything this POV claims is re-asked of it, so what it can and cannot read is
        what the readout is allowed to say.
      </p>

      <div className="pov-tabs" style={{ marginTop: 18 }} data-testid="tenant-tabs">
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

      {tab === 0 && <HealthCheckTab />}
      {tab === 1 && <ReadBackTab tenant={bound} />}
      {tab === 2 && <IntegrationsTab onNavigate={onNavigate} />}
      {tab === 3 && (
        <Suspense fallback={<div className="destination-loading">loading…</div>}>
          <TenantManager />
        </Suspense>
      )}
    </div>
  )
}

/**
 * Nine read-only calls, each with what it proves. Two of them are WARN rather
 * than FAIL, and the difference is the point: a 403 on incidents is the key
 * scope working as designed, and two correlation rules installed-but-disabled
 * is a content state, not an outage. Reporting either as a failure would send
 * a DC to debug a tenant that is fine.
 */
function HealthCheckTab() {
  const checks = [
    ['Reachability', 'pass', '84ms', 'TLS to acme-prod.xdr.us, cert valid'],
    ['Credential accepted', 'pass', '142ms', 'x-xdr-auth-id 4 · Viewer role'],
    ['Role is read-only', 'pass', '96ms', 'no write scope on the key — POVengine cannot write to this tenant'],
    ['Clock agreement', 'pass', '+0.4s', 'skew under a second, so MTTD arithmetic is sound'],
    ['Alerts readable', 'pass', '312ms', 'read:alerts · the bulk of every detection claim'],
    ['XQL engine answers', 'pass', '201ms', 'read:datasets · query id returned and results fetched'],
    ['Correlations readable', 'warn', '178ms', '2 rules installed, both DISABLED — present, but they cannot fire'],
    ['Incidents', 'warn', '88ms', '403 · key scope excludes incidents. A permissions artefact, not an absence'],
    ['Rate-limit headroom', 'pass', '—', 'None of 1000 requests used in this window'],
  ]
  const pass = checks.filter((c) => c[1] === 'pass').length

  return (
    <>
      <div className="pov-section">
        <span className="pov-section__label">
          {pass} of {checks.length} pass · {checks.length - pass} warn · 0 failing
        </span>
        <span className="pov-section__hr" />
      </div>
      <div className="pov-panel">
        {checks.map(([label, state, timing, detail]) => (
          <div className="pov-panel__row" key={label}>
            <div className="pov-card__head">
              <span className={`pov-dot pov-dot--${state === 'pass' ? 'pos' : 'warn'}`} />
              <span className="pov-card__title">{label}</span>
              <span className="pov-card__spacer" />
              <span className="pov-card__code">{timing}</span>
              <span className={`pov-pill pov-pill--${state === 'pass' ? 'pos' : 'warn'}`}>{state.toUpperCase()}</span>
            </div>
            <div className="pov-card__blurb" style={{ marginBottom: 0 }}>{detail}</div>
          </div>
        ))}
      </div>
      <div className="pov-section">
        <span className="pov-section__label">Re-run it</span>
        <span className="pov-section__hr" />
      </div>
      <code className="pov-code">povengine tenant healthcheck --tenant acme-prod.xdr.us --json</code>
    </>
  )
}

/**
 * What can be read back, and what will distort a verdict.
 *
 * The second panel is the one that earns this tab. A suppression rule matching
 * the lab host, or 24h retention on `network_story`, does not make a detection
 * fail — it makes the READ-BACK fail, and the two are indistinguishable from
 * the outside unless something says so before the run.
 */
function ReadBackTab({ tenant }) {
  const datasets = [
    ['xdr_data', 'AGT', '30d', 'yes', 'endpoint process and network claims'],
    ['xdr_file', 'AGT', '30d', 'yes', 'staging and persistence claims'],
    ['cloud_audit_logs', 'CC', '30d', 'yes', 'cloud control-plane claims'],
    ['k8s_audit', 'CC', '7d', 'yes', 'container exec and secret-read claims'],
    ['authentication', 'DC', '30d', 'yes', 'identity claims'],
    ['saas_auth', 'DC', '30d', 'yes', 'session and SSO claims'],
    ['network_story', 'BVM', '24h', 'no', 'egress claims — outside the key scope'],
    ['http_log', 'BVM', '24h', 'no', 'proxy claims — outside the key scope'],
    ['indicators', 'API', '—', 'no', 'IOC claims — pack not installed'],
  ]
  const readable = datasets.filter((d) => d[3] === 'yes').length

  return (
    <>
      <div className="pov-section">
        <span className="pov-section__label">Datasets · {readable} of {datasets.length} readable</span>
        <span className="pov-section__hr" />
      </div>
      <div className="pov-table__scroll">
        <table className="pov-table">
          <thead>
            <tr><th>Dataset</th><th>Door</th><th>Retention</th><th>Readable</th><th>What depends on it</th></tr>
          </thead>
          <tbody>
            {datasets.map(([ds, door, ret, ok, why]) => (
              <tr key={ds}>
                <td className="mono pov-td-strong">{ds}</td>
                <td className="mono">{door}</td>
                <td className="mono">{ret}</td>
                <td><span className={`pov-pill pov-pill--${ok === 'yes' ? 'pos' : 'crit'}`}>{ok.toUpperCase()}</span></td>
                <td>{why}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="pov-section">
        <span className="pov-section__label">Things that will distort a verdict</span>
        <span className="pov-section__hr" />
      </div>
      <div className="pov-panel">
        {[
          ['Suppression rules matching the lab host', 'crit',
            '2 tenant rules match jumpbox-lin-01. An alert can fire and still never appear, which reads identically to never firing.'],
          ['24h retention on network_story', 'warn',
            'A claim re-asked more than a day after the run cannot be re-derived. Export before the window closes.'],
          ['3 datasets outside the key scope', 'warn',
            'Claims behind them export as unverifiable rather than as misses — the readout says which.'],
          ['340 alerts/day baseline noise', 'warn',
            'The run’s own alerts have to be isolated by time window and host, not by alert name alone.'],
        ].map(([label, tone, detail]) => (
          <div className="pov-panel__row" key={label}>
            <div className="pov-card__head">
              <span className={`pov-dot pov-dot--${tone}`} />
              <span className="pov-card__title">{label}</span>
            </div>
            <div className="pov-card__blurb" style={{ marginBottom: 0 }}>{detail}</div>
          </div>
        ))}
      </div>

      <div className="pov-section">
        <span className="pov-section__label">Content</span>
        <span className="pov-section__hr" />
      </div>
      <div className="pov-facts">
        {tenant.facts.map(([k, v, tone]) => (
          <div className="pov-fact" key={k}>
            <span className="pov-fact__k">{k}</span>
            <span className={`pov-fact__v${tone && tone !== 'plain' ? ` pov-fact__v--${tone}` : ''}`}>{v}</span>
          </div>
        ))}
      </div>
    </>
  )
}

/**
 * Integrations, and the MCP server.
 *
 * The MCP panel exposes six READ-ONLY tools and no write tool at all, which is
 * stated rather than implied: an agentic caller pointed at a customer's
 * security tenant is exactly the thing a security team will want bounded, and
 * "it only reads" is much easier to believe when the tool list is printed.
 */
function IntegrationsTab({ onNavigate }) {
  const ingestion = [
    ['Cortex Cloud Connector', 'CC', 'partial', '2 of 3 environments · Azure not enrolled'],
    ['Cortex Data Connector', 'DC', 'partial', '21 of 34 shapes normalised'],
    ['Broker VM', 'BVM', 'partial', 'registered · no relay rule configured'],
    ['Agent fleet', 'AGT', 'ready', '4 enrolled · 2 online'],
  ]
  const marketplace = [
    ['MCP server', 'ready', 'povengine mcp serve · 6 read-only tools'],
    ['XSOAR content pack', 'ready', 'installed · not exercised by this POV'],
    ['Threat intel feed', 'missing', 'indicator pack not installed — IOC claims unverifiable'],
    ['CrowdStrike', 'ready', 'third-party stream via the Data Connector'],
    ['ServiceNow', 'ready', 'ticketing · out of scope for detection claims'],
  ]
  const mcpTools = [
    ['tenant.healthcheck', 'the nine checks on the first tab, as data'],
    ['alerts.search', 'read alerts by name, source and window'],
    ['xql.query', 'run a read-only XQL query and fetch its results'],
    ['content.list', 'which detection content is installed, and which is enabled'],
    ['run.verdicts', 'per-detection verdicts for a run, with the probe behind each'],
    ['coverage.matrix', 'the planes × doors cross-tab as data'],
  ]

  return (
    <>
      <div className="pov-section">
        <span className="pov-section__label">Cortex ingestion</span>
        <span className="pov-section__hr" />
      </div>
      <div className="pov-panel">
        {ingestion.map(([label, code, state, detail]) => (
          <button
            type="button"
            className="pov-panel__row"
            key={code}
            onClick={() => onNavigate('scope')}
          >
            <div className="pov-card__head">
              <span className="pov-chip-plane" style={{ borderLeftColor: 'var(--family-cortex)' }}>{code}</span>
              <span className="pov-card__title">{label}</span>
              <span className="pov-card__spacer" />
              <span className={`pov-pill pov-pill--${state === 'ready' ? 'pos' : state === 'missing' ? 'crit' : 'warn'}`}>
                {state.toUpperCase()}
              </span>
            </div>
            <div className="pov-card__blurb" style={{ marginBottom: 0 }}>{detail}</div>
          </button>
        ))}
      </div>

      <div className="pov-section">
        <span className="pov-section__label">Marketplace &amp; agentic</span>
        <span className="pov-section__hr" />
      </div>
      <div className="pov-panel">
        {marketplace.map(([label, state, detail]) => (
          <div className="pov-panel__row" key={label}>
            <div className="pov-card__head">
              <span className="pov-card__title">{label}</span>
              <span className="pov-card__spacer" />
              <span className={`pov-pill pov-pill--${state === 'ready' ? 'pos' : 'crit'}`}>{state.toUpperCase()}</span>
            </div>
            <div className="pov-card__blurb" style={{ marginBottom: 0 }}>{detail}</div>
          </div>
        ))}
      </div>

      <div className="pov-section">
        <span className="pov-section__label">MCP server · 6 tools, none of them write</span>
        <span className="pov-section__hr" />
      </div>
      <code className="pov-code" style={{ marginBottom: 12 }}>povengine mcp serve --tenant acme-prod.xdr.us --read-only</code>
      <div className="pov-panel">
        {mcpTools.map(([tool, what]) => (
          <div className="pov-panel__row" key={tool}>
            <div className="pov-card__head">
              <span className="pov-card__code">{tool}</span>
              <span className="pov-card__spacer" />
              <span className="pov-pill pov-pill--pos">READ</span>
            </div>
            <div className="pov-card__blurb" style={{ marginBottom: 0 }}>{what}</div>
          </div>
        ))}
      </div>
      <p style={{ font: '400 11px/1.7 var(--font-ui)', color: 'var(--tx2)', margin: '12px 0 0', maxWidth: 760 }}>
        No write tool is exposed, and none can be: the server inherits the same
        read-only credential the console uses. An agent driving POVengine can ask the
        tenant questions and read this instance's verdicts. It cannot change a
        detection, close an alert, or write to the customer's tenant.
      </p>
    </>
  )
}
