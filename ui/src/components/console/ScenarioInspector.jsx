import React from 'react'
import PinButton from './PinButton.jsx'
import { formatAgo } from './useScenarioRunHistory.js'
import { runStatusToken, runStatusGlyph } from './runStatus.js'
import { agentIdOf, runIdOf } from '../../api/ids.js'
import { getToolAdapters } from '../../api/client.js'
import useShelf from './useShelf.js'
import { parseServerTime } from '../../api/time.js'
import { SUPPLY, supplyOf } from './supplyState.js'
import { PUSH_FORMATS } from './useLaunchScenario.js'
import LaunchErrorNotice, { DownloadOutcome, LaunchSuccessNotice } from './LaunchErrorNotice.jsx'
import ChainStage from './ChainStage.jsx'
import { draftFromScenario } from './composerDraft.js'
import { Button, PageIcon, StatusIcon } from './kit/index.js'
import WhyThisMatters from './WhyThisMatters.jsx'
import { scenarioThesis } from './sheetThesis.js'

/**
 * ScenarioInspector — the scenario's deep-dive SHEET (design v26 §4.1, §4.4).
 *
 *   header   kind icon + "Scenario" + id + run status (icon AND word), the
 *            name, and the action row: Run (primary) · Customize · Close,
 *            each verb with its icon (design v33 C10)
 *   why      "Why this matters" — Thesis · Why we simulate it · What proves
 *            it · What it needs, read off the scenario document (C10)
 *   stage    the scenario's chain — the same ChainStage the Composer edits and
 *            Run detail draws live — in preview: no state, scaled to fit
 *   tabs     Overview · Detections · Run history · Launch options
 *
 * The two verbs are the scenario card's (§4.4): Run hands the scenario to the
 * launch step with it loaded (`onRun`); Customize opens Build a chain with its
 * chain loaded (`onCustomize`). With no launch step to hand off to (an isolated
 * render), Run launches in place through the parent's launch hook.
 *
 * Launch state is owned by the parent (OperationsView) so the same hook
 * instance powers both the sheet AND the global ⌘L shortcut. The power-user
 * quick launch (mode · identity · agent · push bundle) lives on the Launch
 * options tab.
 *
 * Props:
 *   scenario          — scenario detail object
 *   open              — boolean
 *   launch            — useLaunchScenario() return value (controlled from parent)
 *   pinned            — boolean — whether this scenario is currently pinned
 *   onTogglePin       — () => void
 *   onClose           — () => void
 *   onRun             — () => void  hand off to the launch step with this scenario
 *   onCustomize       — () => void  open this scenario's chain in Build a chain
 */

const TABS = [
  ['overview', 'Overview'],
  ['detections', 'Detections'],
  ['history', 'Run history'],
  ['launch', 'Launch options'],
]

/** The run-history status as a StatusIcon tone and one word. */
function historyStatus(runs) {
  if (!runs || runs.length === 0) return { status: 'off', word: 'Never run' }
  const token = runStatusToken(runs[0]?.status)
  if (token === 'completed') return { status: 'up', word: 'Last run finished' }
  if (token === 'failed') return { status: 'down', word: 'Last run failed' }
  if (token === 'aborted') return { status: 'degraded', word: 'Last run aborted' }
  if (token === 'running' || token === 'pending') return { status: 'unknown', word: 'Running' }
  return { status: 'unknown', word: 'Last run unknown' }
}

export default function ScenarioInspector({
  scenario,
  open,
  launch,
  pinned = false,
  onTogglePin = () => {},
  onClose = () => {},
  runHistory = [],
  onOpenRunEvidence = () => {},
  onRun = null,
  onCustomize = null,
}) {
  // Before the early return: a hook runs on every render.
  const uid = React.useId()
  const [tab, setTab] = React.useState('overview')
  const id = scenario ? (scenario.scenario_id || scenario.id) : null
  // A different scenario opens on its Overview.
  React.useEffect(() => { setTab('overview') }, [id])
  const draft = React.useMemo(() => (scenario ? draftFromScenario(scenario) : null), [scenario])
  const thesis = React.useMemo(() => (scenario ? scenarioThesis(scenario) : null), [scenario])
  // Esc closes an open sheet, unless something inside it already took the key.
  React.useEffect(() => {
    if (!open) return undefined
    const onKey = (e) => {
      if (e.key === 'Escape' && !e.defaultPrevented) onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!scenario || !launch) return <aside className="inspector scenario-sheet" />

  const status = historyStatus(runHistory)
  const run = onRun || launch.launch

  return (
    <>
      {open && <div className="scenario-sheet__scrim" aria-hidden="true" onClick={onClose} />}
      <aside
        className={'inspector scenario-sheet' + (open ? ' inspector--open' : '')}
        aria-label={`Scenario ${id}`}
      >
        {/* ── Header: kind · id · status, the name, the action row ───────── */}
        <div className="insp-launch scenario-sheet__head" data-testid="scenario-sheet-head">
          <div className="scenario-sheet__kind">
            <PageIcon icon="line-chart" />
            <span className="scenario-sheet__kind-label">Scenario</span>
            <span className="mono scenario-sheet__id">{id}</span>
            <StatusIcon status={status.status} word={status.word} className="scenario-sheet__status" />
            <span className="scenario-sheet__spacer" />
            <PinButton pinned={pinned} onToggle={onTogglePin} variant="inspector" />
          </div>
          <h2 className="scenario-sheet__title">{scenario.name}</h2>

          <div className="scenario-sheet__actions" data-testid="scenario-sheet-actions">
            <Button
              variant="primary"
              verb="Run"
              onClick={run}
              disabled={!onRun && launch.launchDisabled}
              title={onRun ? 'Go to the launch step with this scenario loaded' : 'Launch this scenario in the selected mode'}
            >
              {!onRun && launch.launching ? 'Launching…' : 'Run'}
            </Button>
            {onCustomize && (
              <Button
                variant="secondary"
                verb="Customize"
                onClick={onCustomize}
                title="Open this scenario's chain in Build a chain"
              />
            )}
            <span className="scenario-sheet__spacer" />
            <button type="button" className="btn scenario-sheet__close" onClick={onClose}>Close</button>
          </div>

          {/* A launch SimCore accepted WITH a warning (offline target, unknown
              agent OS, platform variant) is not a tick: it lists the warnings. */}
          {launch.lastRun && launch.lastRun.status !== 'error' && (
            launch.lastRun.warnings?.length ? (
              <LaunchSuccessNotice result={launch.lastRun} testId="insp-launch-success" />
            ) : (
              <div className="mono insp-launch__status insp-launch__status--success" role="status">
                {'✓ '}
                {launch.lastRun.message}
              </div>
            )
          )}
          {/* A refused launch keeps its code and structured detail (unresolved
              steps, missing agents) — the same rendering the Launch step uses. */}
          <LaunchErrorNotice
            result={launch.lastRun}
            testId="insp-launch-error"
            className="mono insp-launch__status insp-launch__status--missed"
          />
        </div>

        <div className="scenario-sheet__body">
          {/* ── Why this matters, first: the thesis before any mechanics ─── */}
          <div className="scenario-sheet__thesis">
            <WhyThisMatters parts={thesis} headingLevel={3} testId="scenario-why-matters" />
          </div>

          {/* ── The chain (preview: no state, scaled to fit) ─────────────── */}
          <section className="scenario-sheet__stage" aria-label="Chain">
            <h3 className="scenario-sheet__section-title">Chain</h3>
            <ChainStage
              mode="preview"
              steps={draft.steps}
              cgo={draft.cgo}
              plane={draft.plane}
              teardown={draft.teardown}
              label={`Chain of ${id}`}
            />
          </section>

          <div className="scenario-sheet__tabs" role="tablist" aria-label="Scenario sections">
            {TABS.map(([key, label]) => (
              <button
                key={key}
                type="button"
                role="tab"
                id={`${uid}-tab-${key}`}
                aria-selected={tab === key}
                aria-controls={`${uid}-panel`}
                className={'scenario-sheet__tab' + (tab === key ? ' scenario-sheet__tab--on' : '')}
                onClick={() => setTab(key)}
              >
                {label}
              </button>
            ))}
          </div>

          <div
            className="scenario-sheet__panel"
            role="tabpanel"
            id={`${uid}-panel`}
            aria-labelledby={`${uid}-tab-${tab}`}
          >
            {tab === 'overview' && (
              <>
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
                <ScenarioToolsSection scenario={scenario} scenarioId={id} />
              </>
            )}

            {tab === 'detections' && (
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
                      {(step.expected_detections || []).map((d, i) => (
                        <div className="mono dmx-body__det" key={i}>
                          {d.plane || '?'} · {d.type || '?'}{d.description ? ` — ${d.description}` : ''}
                        </div>
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
            )}

            {tab === 'history' && (
              <RunHistorySection runs={runHistory} onOpenRunEvidence={onOpenRunEvidence} />
            )}

            {tab === 'launch' && (
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
                      <label className="insp-config__label" htmlFor={`${uid}-identity`}>Identity</label>
                      <select
                        id={`${uid}-identity`}
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
                      <label className="insp-config__label" htmlFor={`${uid}-agent`}>Agent</label>
                      {launch.agents.length === 0 ? (
                        <span className="mono insp-config__no-agents">
                          ! no agents connected
                        </span>
                      ) : (
                        <select
                          id={`${uid}-agent`}
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

                  {/* Push: format. `auto` lets SimCore pick .sh or .ps1 from the
                      scenario's command text; PowerShell is the Windows bundle. */}
                  {launch.mode === 'push' && (
                    <div className="insp-config__row">
                      <label className="insp-config__label">Format</label>
                      <div className="insp-segmented">
                        {PUSH_FORMATS.map((f) => (
                          <button
                            key={f.value}
                            type="button"
                            className={launch.pushFormat === f.value ? 'is-active' : ''}
                            aria-pressed={launch.pushFormat === f.value}
                            title={f.label}
                            onClick={() => launch.setPushFormat(f.value)}
                          >{f.short}</button>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Quick launch skips the guided launch step; the push-bundle
                      download is always available here. */}
                  <div className="insp-config__row insp-config__row--actions">
                    {onRun && (
                      <button
                        type="button"
                        className="btn btn--xs"
                        disabled={launch.launchDisabled}
                        onClick={launch.launch}
                        title="Quick-launch in the selected mode (skips the guided launch step)"
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
                  {/* Beside the button that asked: a refused bundle names its steps. */}
                  <DownloadOutcome launch={launch} scenario={scenario} testId="insp-download-result" />
                </div>
              </div>
            )}
          </div>

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
        </div>
      </aside>
    </>
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

/** The Packages (payload shelf) route, filtered by `params`. */
function shelfHref(params) {
  return `#/adapters?${new URLSearchParams(params).toString()}`
}

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
    window.location.hash = shelfHref(params)
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
          {/* Only navigates, so it is a link (design v33 C10), not a button. */}
          <a
            className="sheet-link"
            data-testid="insp-tools-shelf-link"
            href={shelfHref({ scenario: scenarioId, supply: SUPPLY.UNSTAGED })}
          >
            Packages →
          </a>
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
  const ts     = parseServerTime(run.started_at || run.created_at || '') || 0
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
