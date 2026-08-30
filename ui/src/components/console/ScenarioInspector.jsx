import React from 'react'
import PinButton from './PinButton.jsx'
import { formatAgo } from './useScenarioRunHistory.js'
import { runStatusToken, runStatusGlyph } from './runStatus.js'
import { agentIdOf, runIdOf } from '../../api/ids.js'
import { getToolAdapters } from '../../api/client.js'
import useShelf from './useShelf.js'
import { SUPPLY, supplyOf } from './supplyState.js'

/**
 * ScenarioInspector — right-side 420px drawer with pinned launch CTA at top,
 * metadata, expected-detection matrix, and footer.
 *
 * Launch state is owned by the parent (OperationsView) so the same hook
 * instance powers both the drawer button AND the global ⌘L shortcut.
 *
 * Launch convergence (redesign v2): the canonical launch surface is the
 * dedicated ③ Launch step. The drawer's primary CTA "Continue to Launch ▸"
 * arms the scenario and hands off there (target picker + identity + consent +
 * fire). The inline launch-config below is a power-user QUICK-LAUNCH path that
 * the ⌘L shortcut drives — kept but visually demoted so the two paths don't
 * read as redundant.
 *
 * Props:
 *   scenario          — scenario detail object
 *   open              — boolean
 *   launch            — useLaunchScenario() return value (controlled from parent)
 *   pinned            — boolean — whether this scenario is currently pinned
 *   onTogglePin       — () => void
 *   onClose           — () => void
 *   onContinueToLaunch— () => void  hand off to the canonical ③ Launch step
 */
export default function ScenarioInspector({
  scenario,
  open,
  launch,
  pinned = false,
  onTogglePin = () => {},
  onClose = () => {},
  runHistory = [],
  onOpenRunEvidence = () => {},
  onContinueToLaunch = null,
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
          {onContinueToLaunch ? (
            // Canonical path — hand off to the dedicated ③ Launch step where
            // the target picker + identity + consent gate live.
            <button
              type="button"
              className="btn btn--primary"
              onClick={onContinueToLaunch}
              title="Arm this scenario and continue to the Launch step"
            >
              <span>Continue to Launch</span>
              <span aria-hidden="true" className="insp-launch__arrow">▸</span>
            </button>
          ) : (
            // Fallback (no handoff wired, e.g. isolated render): inline launch
            // so the drawer is still functional on its own.
            <button
              type="button"
              className="btn btn--primary"
              disabled={launch.launchDisabled}
              onClick={launch.launch}
              title="Launch this scenario in the selected mode"
            >
              <span>{launch.launching ? 'Launching…' : 'Launch'}</span>
              <span
                className="kbd insp-launch__kbd"
              >⌘L</span>
            </button>
          )}
          <PinButton
            pinned={pinned}
            onToggle={onTogglePin}
            variant="inspector"
          />
          <button type="button" className="btn" onClick={onClose}>Close</button>
        </div>

        {launch.lastRun && (
          <div
            className={'mono insp-launch__status' + (launch.lastRun.status === 'success'
              ? ' insp-launch__status--success'
              : ' insp-launch__status--missed')}
          >
            {launch.lastRun.status === 'success' ? '\u2713 ' : '\u2717 '}
            {launch.lastRun.message}
          </div>
        )}
      </div>

      {/* ── Mode + identity + agent (compact) ───────────────────────────── */}
      {/* Quick-launch power path. The full guided flow (target picker +
          consent) lives in the ③ Launch step; this stays for ⌘L muscle
          memory and one-keystroke re-runs. */}
      <div className="insp-section insp-section--launch-config">
        <div className="insp-section__title">
          Quick launch
          <span className="mono insp-section__title-hint">⌘L · advanced</span>
        </div>
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
                <span className="mono insp-config__no-agents">
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
                      key={agentIdOf(a)}
                      value={agentIdOf(a)}
                    >
                      {(a.hostname || agentIdOf(a)) +
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

          {/* Quick-launch actions. Quick-launch is the secondary one-keystroke
              path when the canonical ③ Launch handoff is wired (the top CTA
              becomes "Continue to Launch"); the push-bundle download is always
              available here. */}
          <div className="insp-config__row insp-config__row--actions">
            {onContinueToLaunch && (
              <button
                type="button"
                className="btn btn--xs"
                disabled={launch.launchDisabled}
                onClick={launch.launch}
                title="Quick-launch in the selected mode (skips the guided Launch step)"
              >
                {launch.launching ? 'Launching…' : 'Quick launch'}
              </button>
            )}
            <button
              type="button"
              className="btn btn--xs"
              onClick={launch.downloadPushBundle}
              disabled={launch.downloading || !launch.supportsPush}
            >
              {launch.downloading ? 'Preparing…' : 'Push bundle'}
            </button>
          </div>
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
              <dd className="mono insp-tags">
                {scenario.tags.join(' · ')}
              </dd>
            </>
          )}
        </dl>
      </div>

      {/* ── Tools & payload supply ─────────────────────────────────────── */}
      <ScenarioToolsSection scenario={scenario} scenarioId={id} />

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
          <div className="mono insp-matrix-empty">
            no steps loaded — fetch detail
          </div>
        )}
      </div>

      {/* ── Run history ───────────────────────────────────────────────── */}
      <RunHistorySection runs={runHistory} onOpenRunEvidence={onOpenRunEvidence} />

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

/* ─── Tools & payload supply ──────────────────────────────────────── */

/**
 * The tools this scenario needs, and WHO fetches each one at run time.
 *
 * This is the earliest point in the operator's path where "the target has to
 * reach github.com mid-run" can be surfaced — long before the launch screen and
 * long before the customer meeting. Entries with no `adapter_ref` render greyed
 * with no supply chip: the corpus has not wired them to an adapter, so
 * fabricating a state for them would be inventing coverage.
 */
function ScenarioToolsSection({ scenario, scenarioId }) {
  const tools = scenario?.external_tools || []
  const [adapters, setAdapters] = React.useState([])
  const shelf = useShelf({ adapters })

  React.useEffect(() => {
    if (!tools.length) return undefined
    let cancelled = false
    getToolAdapters()
      .then((d) => { if (!cancelled) setAdapters(Array.isArray(d?.adapters) ? d.adapters : []) })
      .catch(() => {})
    return () => { cancelled = true }
  }, [tools.length])

  if (!tools.length) return null

  const byId = Object.fromEntries(adapters.map((a) => [a.adapter_id, a]))
  const rows = tools.map((t) => {
    const adapter = t.adapter_ref ? byId[t.adapter_ref] : null
    return { tool: t, adapter, supply: adapter ? supplyOf(adapter, shelf.shelf) : null }
  })
  const egress = rows.filter(
    (r) => r.supply && (r.supply.state === SUPPLY.UNSTAGED || r.supply.state === SUPPLY.RUNTIME_FETCH),
  )

  // Direct hash write rather than prop-drilling onNavigate through
  // OperationsView — the console's router is the hash and listens for changes.
  const goShelf = (params) => {
    const sp = new URLSearchParams(params)
    window.location.hash = `#/adapters?${sp.toString()}`
  }

  return (
    <div className="insp-section" data-testid="insp-tools">
      <div className="insp-section__title">Tools</div>
      {rows.map((r, i) => (
        <div
          className={'insp-tool' + (r.adapter ? '' : ' insp-tool--unwired')}
          key={`${r.tool.adapter_ref || r.tool.name}-${i}`}
        >
          <span className="mono insp-tool__id">{r.tool.adapter_ref || r.tool.name}</span>
          {r.supply ? (
            <>
              <button
                type="button"
                className={r.supply.chipClass + ' insp-tool__chip-btn'}
                data-testid={`insp-supply-${r.tool.adapter_ref}`}
                onClick={() => goShelf({ tool: r.tool.adapter_ref })}
                title={r.supply.egressLabel}
              >
                {r.supply.label}
              </button>
              <span className="mono insp-tool__egress">{r.supply.egressLabel}</span>
            </>
          ) : (
            <span className="mono insp-tool__egress">
              no adapter_ref — this tool is inlined in the step command, so its supply is not
              managed and not knowable here
            </span>
          )}
        </div>
      ))}
      {egress.length > 0 && (
        <div className="insp-tool__warn" data-testid="insp-tools-warn">
          {egress.length} of {rows.length} tools are fetched by the target from the internet at run
          time.{' '}
          <button
            type="button"
            className="btn btn--xs"
            onClick={() => goShelf({ scenario: scenarioId, supply: SUPPLY.UNSTAGED })}
          >
            Review ▸
          </button>
        </div>
      )}
    </div>
  )
}

/* ─── Run history section ─────────────────────────────────────────── */

/**
 * RunHistorySection — last 5 runs of this scenario.
 *
 * Reads from the OperationsView's run-history rollup; no extra fetch.
 * Empty state stays inline so the section never disappears (consistent
 * drawer height across selections).
 */
function RunHistorySection({ runs = [], onOpenRunEvidence = () => {} }) {
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
            <RunHistoryRow
              key={runIdOf(r)}
              run={r}
              onOpen={() => onOpenRunEvidence(r)}
            />
          ))}
        </ul>
      )}
    </div>
  )
}

function RunHistoryRow({ run, onOpen = () => {} }) {
  const id     = runIdOf(run)
  const ts     = Date.parse(run.started_at || run.created_at || '') || 0
  const ago    = formatAgo(ts) || '—'
  // Backend terminal token is 'complete'; runStatusToken folds it (and the
  // legacy 'completed') onto the canonical 'completed' CSS state, and surfaces
  // 'aborted' as its own terminal state (Phase 2).
  const token  = runStatusToken(run.status)
  const statusGlyph = runStatusGlyph(run.status)
  const statusClass = 'insp-history__row--' + token
  return (
    <li className={'insp-history__row ' + statusClass}>
      <button
        type="button"
        className="insp-history__row-btn"
        onClick={onOpen}
        title={`Open run ${id} in Evidence`}
      >
        <span className="insp-history__glyph" aria-hidden="true">{statusGlyph}</span>
        <span className="insp-history__id mono">{String(id).slice(0, 10)}</span>
        <span className="insp-history__when mono">{ago}</span>
        <span className="insp-history__status mono">{token}</span>
        <span className="insp-history__arrow mono" aria-hidden="true">→</span>
      </button>
    </li>
  )
}
