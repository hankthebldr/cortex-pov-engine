import React, { useCallback, useEffect, useMemo, useState } from 'react'
import '../../styles/destinations/get-started.css'
import { useEnvironment } from '../../context/EnvironmentContext.jsx'
import { agentInstallUrl, mintEnrollmentToken } from '../../api/client.js'
import TenantManager from './TenantManager.jsx'
import useSetupProgress from './useSetupProgress.js'
import { agentIsOnline } from './setupProgress.js'

/**
 * GetStartedView — the console's front door: what to do, in order, on THIS
 * instance.
 *
 * It replaces three things that each tried to be the starting point — the
 * Overview page (a description of the product), the Setup Wizard (a scoping
 * exercise about detection categories) and the auto-starting tour (a spotlight
 * over the Library). None of them answered the question a DC actually has on a
 * fresh install: is my agent talking to this, is my tenant connected, and how
 * do I make one detection happen?
 *
 * One step is open at a time — the first unfinished required step, unless the
 * DC opens another. Each open step has exactly one primary action. Everything
 * shown as done is read from SimCore, except the two infrastructure steps that
 * live in the tenant, which say "you confirmed" (see setupProgress.js).
 */

// The suggested first run. A Linux credential-access chain with no staged
// payloads — it runs on a bare beacon and maps to detections every Cortex
// tenant ships with, which is what a first run needs to be.
const FIRST_SCENARIO = 'SIM-EDR-001'

export default function GetStartedView({ onNavigate = () => {} }) {
  const { steps, requiredLeft, next, ready, setStepConfirmed } = useSetupProgress()
  const requiredTotal = steps.filter((s) => s.required).length
  const [openId, setOpenId] = useState(null)
  const current = openId || (next && next.id) || null

  return (
    <div className="pov-page gs" data-testid="get-started">
      <header className="gs__head">
        <h1 className="gs__title">Get started</h1>
        <p className="gs__lede">
          {ready
            ? 'This instance is ready. Run any scenario from Simulate, or finish the optional steps to unlock network and log-source scenarios.'
            : 'Three steps take an empty instance to a detection you can show. Two more are optional and unlock network and third-party log scenarios.'}
        </p>
        <div className="gs__progress" aria-label={`${requiredTotal - requiredLeft} of ${requiredTotal} required steps done`}>
          <div className="gs__bar"><i style={{ width: `${((requiredTotal - requiredLeft) / requiredTotal) * 100}%` }} /></div>
          <span className="gs__progress-text">{requiredTotal - requiredLeft} of {requiredTotal} required steps done</span>
        </div>
      </header>

      <ol className="gs__steps">
        {steps.map((step, i) => (
          <Step
            key={step.id}
            n={i + 1}
            step={step}
            open={current === step.id}
            onToggle={() => setOpenId(current === step.id ? '__none__' : step.id)}
          >
            {step.id === 'agent' && <AgentStep step={step} onNavigate={onNavigate} />}
            {step.id === 'tenant' && <TenantStep step={step} />}
            {step.id === 'collector' && (
              <CollectorStep step={step} onConfirm={(v) => setStepConfirmed('collector', v)} />
            )}
            {step.id === 'bvm' && <BvmStep step={step} onConfirm={(v) => setStepConfirmed('bvm', v)} />}
            {step.id === 'simulate' && <SimulateStep step={step} onNavigate={onNavigate} />}
          </Step>
        ))}
      </ol>

      <p className="gs__foot">
        New to POVengine? <button type="button" className="gs-link" onClick={() => onNavigate('overview')}>Read how it works</button>
      </p>
    </div>
  )
}

function Step({ n, step, open, onToggle, children }) {
  const state = step.done ? 'done' : open ? 'open' : 'todo'
  return (
    <li className={`gs-step gs-step--${state}`} data-testid={`gs-step-${step.id}`} data-done={step.done}>
      <button type="button" className="gs-step__row" onClick={onToggle} aria-expanded={open}>
        <span className="gs-step__n" aria-hidden="true">{step.done ? '✓' : n}</span>
        <span className="gs-step__text">
          <span className="gs-step__title">
            {step.title}
            {!step.required && <span className="gs-step__opt">optional</span>}
          </span>
          <span className="gs-step__status">{step.status}</span>
        </span>
        <span className="gs-step__chev" aria-hidden="true">{open ? '−' : '+'}</span>
      </button>
      {open && (
        <div className="gs-step__body">
          <p className="gs-step__why">{step.why}</p>
          {children}
        </div>
      )}
    </li>
  )
}

// ── 1 · Agent ────────────────────────────────────────────────────────────────

const LOOPBACK = /^https?:\/\/(localhost|127\.0\.0\.1|\[::1\]|0\.0\.0\.0)(:|$)/i

function AgentStep({ step, onNavigate }) {
  const { agents, refreshAgents, healthModel } = useEnvironment()
  const [os, setOs] = useState('linux')
  const origin = typeof window !== 'undefined' ? window.location.origin : ''
  const [server, setServer] = useState(origin)
  const [token, setToken] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [copied, setCopied] = useState(false)

  // Poll while waiting: the provider polls runs and health, not agents, so
  // without this the step would sit at "waiting" after the beacon checked in.
  useEffect(() => {
    if (step.done) return undefined
    const t = setInterval(() => { refreshAgents && refreshAgents() }, 4000)
    return () => clearInterval(t)
  }, [step.done, refreshAgents])

  const noBinaries = (healthModel?.degraded || []).includes('agent_binaries')

  const mint = useCallback(async () => {
    setBusy(true); setError(null)
    try {
      setToken(await mintEnrollmentToken({ label: 'get-started', ttl_seconds: 3600, max_uses: 1 }))
    } catch (err) {
      setError(err.message || 'Could not create an install token')
    } finally {
      setBusy(false)
    }
  }, [])

  const base = server.replace(/\/+$/, '')
  const installUrl = `${base}${agentInstallUrl({ os })}`
  const command = token
    ? (os === 'windows'
        ? `$env:CORTEXSIM_TOKEN='${token.token}'; iwr -useb "${installUrl}" | iex`
        : `curl -fsSL "${installUrl}" | CORTEXSIM_TOKEN='${token.token}' bash`)
    : null

  const copy = () => {
    try { navigator.clipboard.writeText(command); setCopied(true); setTimeout(() => setCopied(false), 1600) } catch { /* select manually */ }
  }

  if (step.done) {
    const online = agents.filter(agentIsOnline)
    return (
      <div className="gs-done">
        <ul className="gs-list">
          {online.map((a) => (
            <li key={a.agent_id}><span className="gs-dot gs-dot--ok" /> <b className="mono">{a.agent_id}</b> · {a.hostname} · {a.os}</li>
          ))}
        </ul>
        <button type="button" className="pov-btn" onClick={() => onNavigate('agents')}>Manage agents</button>
      </div>
    )
  }

  return (
    <div className="gs-form">
      <div className="gs-field">
        <span className="gs-label">Target host</span>
        <div className="gs-seg" role="radiogroup">
          {[['linux', 'Linux'], ['darwin', 'macOS'], ['windows', 'Windows']].map(([v, l]) => (
            <button key={v} type="button" role="radio" aria-checked={os === v}
              className={'gs-seg__opt' + (os === v ? ' is-on' : '')} onClick={() => { setOs(v); setToken(null) }}>{l}</button>
          ))}
        </div>
      </div>

      <label className="gs-field">
        <span className="gs-label">POVengine address the host can reach</span>
        <input className="gs-input mono" value={server} onChange={(e) => { setServer(e.target.value); setToken(null) }} />
        {LOOPBACK.test(base) && (
          <span className="gs-hint gs-hint--warn">
            This is a loopback address. It only works if the agent runs on this same machine — for another host, use this machine&apos;s LAN IP or DNS name.
          </span>
        )}
      </label>

      {noBinaries && (
        <p className="gs-hint gs-hint--warn">
          This server has no prebuilt agents, so the installer will build from source and needs Go on the host.
          Run <code>make agent-dist</code> on the POVengine host to fix that.
        </p>
      )}

      {!command ? (
        <button type="button" className="pov-btn pov-btn--primary" onClick={mint} disabled={busy} data-testid="gs-agent-generate">
          {busy ? 'Creating…' : 'Generate install command'}
        </button>
      ) : (
        <div className="gs-cmd">
          <span className="gs-label">Run this on the host (token works once, for 1 hour)</span>
          <pre className="gs-cmd__text mono" data-testid="gs-agent-command">{command}</pre>
          <div className="gs-row">
            <button type="button" className="pov-btn pov-btn--primary" onClick={copy}>{copied ? 'Copied' : 'Copy command'}</button>
            <span className="gs-wait"><span className="gs-dot gs-dot--pulse" /> Waiting for the agent to check in…</span>
          </div>
        </div>
      )}
      {error && <p className="gs-hint gs-hint--err">{error}</p>}
    </div>
  )
}

// ── 2 · Tenant ───────────────────────────────────────────────────────────────

function TenantStep({ step }) {
  const { tenants } = useEnvironment()
  if (step.done) {
    return (
      <ul className="gs-list">
        {tenants.map((t) => (
          <li key={t.name || t.id}><span className="gs-dot gs-dot--ok" /> <b>{t.name || t.id}</b> <span className="mono gs-muted">{t.base_url || t.config?.base_url || ''}</span></li>
        ))}
      </ul>
    )
  }
  return (
    <div className="gs-embed">
      <p className="gs-hint">
        Create a <b>read-only</b> API key in Cortex (Settings → Configurations → Integrations → API Keys, Standard key, role <i>Viewer</i>) and paste the API URL, key ID and key below.
      </p>
      <TenantManager embedded />
    </div>
  )
}

// ── 3 · Collector ────────────────────────────────────────────────────────────

function CollectorStep({ step, onConfirm }) {
  return (
    <div className="gs-guide">
      <ol className="gs-howto">
        <li>In Cortex, go to <b>Settings → Data Sources</b> and add a <b>Custom – HTTP based Collector</b> (for JSON events) or a <b>Syslog</b> source on the Broker VM.</li>
        <li>Copy the collector&apos;s URL and API key — data-stream scenarios ask for them when you launch.</li>
        <li>Send one test event and confirm it appears in the dataset before the POV session.</li>
      </ol>
      <ConfirmRow done={step.done} onConfirm={onConfirm} />
    </div>
  )
}

// ── 4 · Broker VM ────────────────────────────────────────────────────────────

function BvmStep({ step, onConfirm }) {
  return (
    <div className="gs-guide">
      <ol className="gs-howto">
        <li>In Cortex, open <b>Settings → Configurations → Data Broker → Broker VMs</b>, download the image and deploy it on the customer network.</li>
        <li>Register it with the token Cortex shows, then activate the <b>Syslog Collector</b> applet (UDP/TCP 514).</li>
        <li>Make sure the agent host can reach the Broker VM on 514 — network-plane scenarios send their traffic there.</li>
      </ol>
      <ConfirmRow done={step.done} onConfirm={onConfirm} />
    </div>
  )
}

function ConfirmRow({ done, onConfirm }) {
  return (
    <div className="gs-row">
      {done ? (
        <>
          <span className="gs-muted">Marked as set up by you — POVengine cannot check this one.</span>
          <button type="button" className="gs-link" onClick={() => onConfirm(false)}>Undo</button>
        </>
      ) : (
        <>
          <button type="button" className="pov-btn" onClick={() => onConfirm(true)}>I&apos;ve set this up</button>
          <span className="gs-muted">POVengine cannot see this from here, so it takes your word for it.</span>
        </>
      )}
    </div>
  )
}

// ── 5 · Simulate ─────────────────────────────────────────────────────────────

function SimulateStep({ step, onNavigate }) {
  const { scenarios, agent, tenants, lastRun } = useEnvironment()
  const first = useMemo(() => {
    const id = (s) => s.scenario_id || s.id
    return scenarios.find((s) => id(s) === FIRST_SCENARIO)
      || scenarios.find((s) => s.plane === 'EDR')
      || scenarios[0]
      || null
  }, [scenarios])

  if (step.done) {
    return (
      <div className="gs-row">
        <button type="button" className="pov-btn pov-btn--primary" onClick={() => onNavigate('proof')}>See results</button>
        <button type="button" className="pov-btn" onClick={() => onNavigate('library')}>Run another scenario</button>
      </div>
    )
  }

  const failed = step.failedRun
  return (
    <div className="gs-guide">
      {failed && (
        <div className="gs-alert" data-testid="gs-simulate-failed">
          <b>Your last run stopped early.</b> The usual causes are an execution identity that does not
          exist on the host (the launch preflight marks it UNKNOWN) or a step that needs a tool the host
          lacks. The run shows the step and its output.
          <div className="gs-row" style={{ marginTop: 10 }}>
            <button type="button" className="pov-btn" onClick={() => onNavigate('runs', { run: failed.run_id || failed.id, tab: 'live' })}>
              Open the failed run
            </button>
          </div>
        </div>
      )}
      <ol className="gs-howto">
        <li><b>Pick a scenario.</b> Each one is a short chain of attacker steps plus the detections Cortex should raise for them.</li>
        <li><b>Launch it on {agent ? <span className="mono">{agent.agent_id || agent.hostname}</span> : 'your agent'}.</b> The agent runs each step and reports its output.</li>
        <li><b>Watch it in Runs.</b> {tenants.length
          ? 'POVengine then queries your tenant and marks each expected detection as observed or missing.'
          : 'Connect a tenant (step 2) to have each expected detection checked against real alerts.'}</li>
      </ol>
      {first && (
        <div className="gs-pick">
          <span className="gs-label">Suggested first run</span>
          <div className="gs-pick__card">
            <span className="mono gs-muted">{first.scenario_id || first.id}</span>
            <b>{first.name}</b>
          </div>
        </div>
      )}
      <div className="gs-row">
        {first && (
          <button type="button" className="pov-btn pov-btn--primary" data-testid="gs-simulate-launch"
            onClick={() => onNavigate('guided', { arm: first.scenario_id || first.id })}>
            Set up this run
          </button>
        )}
        <button type="button" className="pov-btn" onClick={() => onNavigate('library')}>Browse all scenarios</button>
        {lastRun && <button type="button" className="gs-link" onClick={() => onNavigate('runs')}>Open Runs</button>}
      </div>
    </div>
  )
}
