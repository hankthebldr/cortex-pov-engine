import React from 'react'
import PinButton from './PinButton.jsx'
import { formatAgo } from './useScenarioRunHistory.js'

/**
 * ScenarioInspector — right-side 420px drawer with pinned launch CTA at top,
 * metadata, expected-detection matrix, and footer.
 *
 * Launch state is owned by the parent (OperationsView) so the same hook
 * instance powers both the drawer button AND the global ⌘L shortcut.
 *
 * Props:
 *   scenario      — scenario detail object
 *   open          — boolean
 *   launch        — useLaunchScenario() return value (controlled from parent)
 *   pinned        — boolean — whether this scenario is currently pinned
 *   onTogglePin   — () => void
 *   onClose       — () => void
 */
export default function ScenarioInspector({
  scenario,
  open,
  launch,
  pinned = false,
  onTogglePin = () => {},
  onClose = () => {},
  runHistory = [],
}) {
  if (!scenario || !launch) return <aside className="inspector" />

  const id = scenario.scenario_id || scenario.id

  return (
    <aside className={'inspector' + (open ? ' inspector--open' : '')}>
      {/* ── Pinned launch CTA ───────────────────────────────────────────── */}
      <div className="insp-launch">
        <div className="insp-launch__label mono">
          {id} · ready to launch
        </div>
        <div className="insp-launch__title">{scenario.name}</div>

        <div className="insp-launch__actions">
          <button
            type="button"
            className="btn btn--primary"
            disabled={launch.launchDisabled}
            onClick={launch.launch}
            title="Launch this scenario in the selected mode"
          >
            <span>{launch.launching ? 'Launching…' : 'Launch'}</span>
            <span
              className="kbd"
              style={{
                background: 'rgba(5,10,20,0.25)',
                borderColor: 'rgba(5,10,20,0.25)',
                color: 'var(--c-void)',
              }}
            >⌘L</span>
          </button>
          <button
            type="button"
            className="btn"
            onClick={launch.downloadPushBundle}
            disabled={launch.downloading || !launch.supportsPush}
          >
            {launch.downloading ? 'Preparing…' : 'Push bundle'}
          </button>
          <PinButton
            pinned={pinned}
            onToggle={onTogglePin}
            variant="inspector"
          />
          <button type="button" className="btn" onClick={onClose}>Close</button>
        </div>

        {launch.lastRun && (
          <div
            className="mono"
            style={{
              marginTop: 10,
              fontSize: 10,
              letterSpacing: '0.04em',
              color: launch.lastRun.status === 'success'
                ? 'var(--c-detected)'
                : 'var(--c-missed)',
            }}
          >
            {launch.lastRun.status === 'success' ? '\u2713 ' : '\u2717 '}
            {launch.lastRun.message}
          </div>
        )}
      </div>

      {/* ── Mode + identity + agent (compact) ───────────────────────────── */}
      <div className="insp-section insp-section--launch-config">
        <div className="insp-section__title">Launch config</div>
        <div className="insp-config">
          {/* Mode */}
          <div className="insp-config__row">
            <label className="insp-config__label">Mode</label>
            <div className="insp-segmented">
              <button
                type="button"
                className={launch.mode === 'pull' ? 'is-active' : ''}
                disabled={!launch.supportsPull}
                onClick={() => launch.setMode('pull')}
              >Pull</button>
              <button
                type="button"
                className={launch.mode === 'push' ? 'is-active' : ''}
                disabled={!launch.supportsPush}
                onClick={() => launch.setMode('push')}
              >Push</button>
            </div>
          </div>

          {/* Identity */}
          {launch.identityOptions.length > 0 && (
            <div className="insp-config__row">
              <label className="insp-config__label">Identity</label>
              <select
                className="insp-select"
                value={launch.identity}
                onChange={(e) => launch.setIdentity(e.target.value)}
                disabled={launch.launching}
              >
                {launch.identityOptions.map((opt) => (
                  <option key={opt} value={opt}>{opt}</option>
                ))}
              </select>
            </div>
          )}

          {/* Pull: agent */}
          {launch.mode === 'pull' && (
            <div className="insp-config__row">
              <label className="insp-config__label">Agent</label>
              {launch.agents.length === 0 ? (
                <span className="mono" style={{
                  color: 'var(--c-pending)',
                  fontSize: 10,
                  letterSpacing: '0.04em',
                }}>
                  ! no agents connected
                </span>
              ) : (
                <select
                  className="insp-select"
                  value={launch.selectedAgent}
                  onChange={(e) => launch.setSelectedAgent(e.target.value)}
                  disabled={launch.launching}
                >
                  {launch.agents.map((a) => (
                    <option
                      key={a.id || a.agent_id}
                      value={a.id || a.agent_id}
                    >
                      {(a.hostname || a.id || a.agent_id) +
                        (a.os ? ` (${a.os})` : '')}
                    </option>
                  ))}
                </select>
              )}
            </div>
          )}

          {/* Push: format */}
          {launch.mode === 'push' && (
            <div className="insp-config__row">
              <label className="insp-config__label">Format</label>
              <div className="insp-segmented">
                <button
                  type="button"
                  className={launch.pushFormat === 'bash' ? 'is-active' : ''}
                  onClick={() => launch.setPushFormat('bash')}
                >bash</button>
                <button
                  type="button"
                  className={launch.pushFormat === 'k8s' ? 'is-active' : ''}
                  onClick={() => launch.setPushFormat('k8s')}
                >k8s</button>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* ── Metadata ───────────────────────────────────────────────────── */}
      <div className="insp-section">
        <div className="insp-section__title">Metadata</div>
        <dl className="insp-kv">
          <dt>Scenario ID</dt>     <dd>{id}</dd>
          <dt>Plane</dt>           <dd>{scenario.plane || '—'}</dd>
          <dt>Tactic</dt>          <dd>
            {scenario.mitre_tactic} · {scenario.mitre_tactic_name}
          </dd>
          <dt>Technique</dt>       <dd>
            {scenario.mitre_technique} · {scenario.mitre_technique_name}
          </dd>
          {scenario.threat_report && (
            <>
              <dt>Anchor</dt>
              <dd title={scenario.threat_report}>
                {truncate(scenario.threat_report, 56)}
              </dd>
            </>
          )}
          {scenario.threat_report_url && (
            <>
              <dt>Source</dt>
              <dd>
                <a href={scenario.threat_report_url} target="_blank" rel="noreferrer">
                  view ↗
                </a>
              </dd>
            </>
          )}
          <dt>Identity</dt>
          <dd>{scenario.execution_identity?.default || '—'}</dd>
          {(scenario.tags && scenario.tags.length > 0) && (
            <>
              <dt>Tags</dt>
              <dd className="mono" style={{ fontSize: 10, color: 'var(--c-text-secondary)' }}>
                {scenario.tags.join(' · ')}
              </dd>
            </>
          )}
        </dl>
      </div>

      {/* ── Expected detection matrix ──────────────────────────────────── */}
      <div className="insp-detection-matrix">
        <div className="insp-section__title">Expected detection matrix</div>
        {(scenario.steps || []).map((step, idx) => (
          <div className="dmx-row" key={step.id || idx}>
            <div className="dmx-step">{String(idx + 1).padStart(2, '0')}</div>
            <div className="dmx-body">
              <div className="dmx-body__tid">
                {step.mitre_technique || '—'} · {step.identity || '—'}
              </div>
              <div className="dmx-body__desc">{step.name || '(unnamed step)'}</div>
            </div>
            <div className="dmx-planes">
              {(step.expected_detections || []).map((d, i) => (
                <div
                  key={i}
                  className="plane-dot plane-dot--on"
                  title={`${d.plane || '?'} · ${d.type || '?'} — ${d.description || ''}`}
                />
              ))}
            </div>
          </div>
        ))}
        {(!scenario.steps || scenario.steps.length === 0) && (
          <div className="mono" style={{
            color: 'var(--c-text-muted)',
            fontSize: 10,
            letterSpacing: '0.04em',
            textAlign: 'center',
            padding: '20px 0',
          }}>
            no steps loaded — fetch detail
          </div>
        )}
      </div>

      {/* ── Run history ───────────────────────────────────────────────── */}
      <RunHistorySection runs={runHistory} />

      {/* ── Footer ─────────────────────────────────────────────────────── */}
      <div className="insp-footer">
        <span>
          {scenario.steps?.length || 0} steps ·{' '}
          {countDetections(scenario)} expected detections ·{' '}
          {(scenario.tags || []).find((t) => /^(basic|intermediate|advanced|evasive)$/.test(t)) || 'std'}
        </span>
        <span>
          {scenario.pull_supported && 'pull'}
          {scenario.pull_supported && scenario.push_supported && ' · '}
          {scenario.push_supported && 'push'}
        </span>
      </div>
    </aside>
  )
}

function countDetections(scenario) {
  return (scenario.steps || []).reduce(
    (n, s) => n + (s.expected_detections || []).length, 0
  )
}

function truncate(s, n) {
  if (!s || s.length <= n) return s
  return s.slice(0, n - 1) + '\u2026'
}

/* ─── Run history section ─────────────────────────────────────────── */

/**
 * RunHistorySection — last 5 runs of this scenario.
 *
 * Reads from the OperationsView's run-history rollup; no extra fetch.
 * Empty state stays inline so the section never disappears (consistent
 * drawer height across selections).
 */
function RunHistorySection({ runs = [] }) {
  const shown = runs.slice(0, 5)
  return (
    <div className="insp-section insp-history">
      <div className="insp-section__title">
        Run history
        <span className="insp-history__count mono">
          {runs.length === 0 ? 'never run' : `${runs.length} total`}
        </span>
      </div>
      {shown.length === 0 ? (
        <div className="insp-history__empty mono">
          no runs on record · launch to validate
        </div>
      ) : (
        <ul className="insp-history__list">
          {shown.map((r) => (
            <RunHistoryRow key={r.id || r.run_id} run={r} />
          ))}
        </ul>
      )}
    </div>
  )
}

function RunHistoryRow({ run }) {
  const id     = run.id || run.run_id
  const status = (run.status || 'unknown').toLowerCase()
  const ts     = Date.parse(run.started_at || run.created_at || '') || 0
  const ago    = formatAgo(ts) || '—'
  const statusGlyph = status === 'completed' ? '✓'
    : status === 'failed'    ? '✗'
    : status === 'running'   ? '◐'
    : '○'
  const statusClass = 'insp-history__row--' + status
  return (
    <li className={'insp-history__row ' + statusClass} title={`Run ${id} · ${status}`}>
      <span className="insp-history__glyph" aria-hidden="true">{statusGlyph}</span>
      <span className="insp-history__id mono">{String(id).slice(0, 10)}</span>
      <span className="insp-history__when mono">{ago}</span>
      <span className="insp-history__status mono">{status}</span>
    </li>
  )
}
