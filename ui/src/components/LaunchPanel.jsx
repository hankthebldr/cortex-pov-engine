import React, { useState, useEffect, useCallback } from 'react'
import { postRun, getAgents, downloadScenario } from '../api/client.js'
import { agentIdOf } from '../api/ids.js'
import { PUSH_FORMATS, saveBlob, errorRecord, describeError } from './console/useLaunchScenario.js'
import LaunchErrorNotice, { DownloadOutcome } from './console/LaunchErrorNotice.jsx'
import { WINDOWS_BUNDLE_UNPROVEN, WINDOWS_BUNDLE_RESIDUE } from './console/deployCommands.js'

/** What each push format is, in one line, beside the selector. */
const FORMAT_HINT = {
  auto: 'SimCore picks the target the scenario\'s command text can satisfy — bash for POSIX '
    + 'content, PowerShell for windows-only content',
  bash: 'Self-contained bash script — runs on any Ubuntu 22.04 box',
  powershell: 'Self-contained PowerShell script for the Windows PowerShell 5.1 floor '
    + '(Server 2016+ / Windows 10+): powershell.exe -NoProfile -ExecutionPolicy Bypass -File <file>. '
    + `${WINDOWS_BUNDLE_UNPROVEN} ${WINDOWS_BUNDLE_RESIDUE}`,
  k8s: 'K8s YAML manifest — apply with kubectl apply -f <file>',
}

const DOWNLOAD_LABEL = {
  auto: 'Download bundle',
  bash: 'Download Bash Bundle',
  powershell: 'Download PowerShell (.ps1)',
  k8s: 'Download K8s YAML',
}

// ─── Component ────────────────────────────────────────────────────────────────

export default function LaunchPanel({ scenario, onRunComplete, onError }) {
  // ── State ──────────────────────────────────────────────────────────────────
  const [mode, setMode]               = useState('pull')      // 'pull' | 'push'
  const [identity, setIdentity]       = useState('')
  const [agents, setAgents]           = useState([])
  const [selectedAgent, setSelectedAgent] = useState('')
  const [pushFormat, setPushFormat]   = useState('auto')      // PUSH_FORMATS value
  const [launching, setLaunching]     = useState(false)
  const [downloading, setDownloading] = useState(false)
  const [lastRun, setLastRun]         = useState(null)        // { status, message }
  const [downloadResult, setDownloadResult] = useState(null)  // shown beside the button

  // ── Populate identity options from scenario ────────────────────────────────
  const identityOptions = scenario?.execution_identity?.options || []
  const defaultIdentity = scenario?.execution_identity?.default || ''

  useEffect(() => {
    setIdentity(defaultIdentity || (identityOptions[0] ?? ''))
    setLastRun(null)
  }, [scenario?.scenario_id])

  // ── Fetch agents when in Pull mode ────────────────────────────────────────
  useEffect(() => {
    if (mode !== 'pull') return
    getAgents()
      .then(data => {
        const list = Array.isArray(data) ? data : []
        setAgents(list)
        if (list.length > 0 && !selectedAgent) {
          setSelectedAgent(agentIdOf(list[0]) || '')
        }
      })
      .catch(() => setAgents([]))
  }, [mode])

  // ── Launch ────────────────────────────────────────────────────────────────
  const handleLaunch = useCallback(async () => {
    if (!scenario) return
    setLaunching(true)
    setLastRun(null)
    try {
      const body = {
        scenario_id: scenario.scenario_id || scenario.id,
        mode,
        identity: identity || undefined,
      }
      if (mode === 'pull' && selectedAgent) {
        body.target_agent_id = selectedAgent
      }
      const run = await postRun(body)
      setLastRun({ status: 'success', message: `Run ${run?.id || ''} started` })
      if (onRunComplete) onRunComplete(run)
    } catch (err) {
      const msg = err.message || 'Launch failed'
      setLastRun(errorRecord(err, 'Launch failed'))
      if (onError) onError(msg)
    } finally {
      setLaunching(false)
    }
  }, [scenario, mode, identity, selectedAgent, onRunComplete, onError])

  // ── Download push script ──────────────────────────────────────────────────
  // The filename is the server's (Content-Disposition), so a PowerShell bundle
  // is saved as .ps1 rather than the .sh this used to hard-code. A refusal is
  // rendered beside the button, not only handed to onError.
  const handleDownload = useCallback(async (formatOverride) => {
    if (!scenario) return
    const fmt = typeof formatOverride === 'string' && formatOverride ? formatOverride : pushFormat
    if (fmt !== pushFormat) setPushFormat(fmt)
    setDownloading(true)
    setDownloadResult(null)
    try {
      const id = scenario.scenario_id || scenario.id
      const res = await downloadScenario(id, fmt)
      saveBlob(res.blob, res.filename)
      setDownloadResult({ status: 'success', filename: res.filename, target: res.target, format: res.format })
    } catch (err) {
      setDownloadResult({ ...errorRecord(err, 'Download failed'), format: fmt })
      if (onError) onError(describeError(err))
    } finally {
      setDownloading(false)
    }
  }, [scenario, pushFormat, onError])

  useEffect(() => { setDownloadResult(null) }, [scenario?.scenario_id])

  const disabled = !scenario || launching

  return (
    <div className="panel-card">
      <div className="panel-card-header">
        <h3>Launch Panel</h3>
        {scenario && (
          <span className="badge badge-teal text-mono launch-panel__badge">
            {scenario.scenario_id || scenario.id}
          </span>
        )}
      </div>

      <div className="panel-card-body">
        {!scenario ? (
          <div className="empty-state launch-panel__empty">
            <div className="empty-state-icon">&#9654;</div>
            <p>Select a scenario to configure and launch a simulation.</p>
          </div>
        ) : (
          <div className="launch-panel__body">

            {/* Mode toggle */}
            <div className="form-group">
              <label className="form-label">Execution Mode</label>
              <div className="segmented-control">
                <button
                  className={mode === 'pull' ? 'active' : ''}
                  onClick={() => setMode('pull')}
                  disabled={!scenario?.pull_supported}
                  title={!scenario?.pull_supported ? 'Pull mode not supported for this scenario' : 'Pull mode — execute via beacon agent'}
                >
                  &#9660; Pull
                </button>
                <button
                  className={mode === 'push' ? 'active' : ''}
                  onClick={() => setMode('push')}
                  disabled={!scenario?.push_supported}
                  title={!scenario?.push_supported ? 'Push mode not supported for this scenario' : 'Push mode — generate self-contained script'}
                >
                  &#9650; Push
                </button>
              </div>
              <span className="launch-panel__hint launch-panel__hint--top">
                {mode === 'pull'
                  ? 'Agent polls SimCore and executes steps via Identity Harness'
                  : 'Generate a self-contained script for manual deployment'}
              </span>
            </div>

            {/* Identity selector */}
            {identityOptions.length > 0 && (
              <div className="form-group">
                <label className="form-label" htmlFor="identity-select">
                  Execution Identity
                </label>
                <select
                  id="identity-select"
                  value={identity}
                  onChange={e => setIdentity(e.target.value)}
                  disabled={disabled}
                >
                  {identityOptions.map(opt => (
                    <option key={opt} value={opt}>{opt}</option>
                  ))}
                </select>
                <span className="launch-panel__hint">
                  Service account used for process causality chain generation
                </span>
              </div>
            )}

            {/* Pull mode: agent selector */}
            {mode === 'pull' && (
              <div className="form-group">
                <label className="form-label" htmlFor="agent-select">Target Agent</label>
                {agents.length === 0 ? (
                  <div className="launch-panel__agent-warning">
                    &#9888; No agents connected. Start an agent with:{' '}
                    <code className="launch-panel__code">
                      ./bin/cortexsim-agent --server http://localhost:8888
                    </code>
                  </div>
                ) : (
                  <select
                    id="agent-select"
                    value={selectedAgent}
                    onChange={e => setSelectedAgent(e.target.value)}
                    disabled={disabled}
                  >
                    {agents.map(agent => (
                      <option key={agentIdOf(agent)} value={agentIdOf(agent)}>
                        {agent.hostname || agentIdOf(agent)}
                        {agent.os ? ` (${agent.os})` : ''}
                      </option>
                    ))}
                  </select>
                )}
              </div>
            )}

            {/* Push mode: format + download */}
            {mode === 'push' && (
              <div className="form-group">
                <label className="form-label">Script Format</label>
                <div className="launch-panel__push-row">
                  <div className="segmented-control">
                    {PUSH_FORMATS.map((f) => (
                      <button
                        key={f.value}
                        className={pushFormat === f.value ? 'active' : ''}
                        aria-pressed={pushFormat === f.value}
                        title={f.label}
                        onClick={() => setPushFormat(f.value)}
                      >
                        {f.short}
                      </button>
                    ))}
                  </div>

                  <button
                    className="btn btn-secondary"
                    onClick={handleDownload}
                    disabled={downloading || !scenario}
                    title={DOWNLOAD_LABEL[pushFormat] || 'Download bundle'}
                  >
                    {downloading ? (
                      <><span className="spinner" /> Preparing…</>
                    ) : (
                      <> &#8595; {DOWNLOAD_LABEL[pushFormat] || 'Download bundle'}</>
                    )}
                  </button>
                </div>
                <span className="launch-panel__hint">
                  {FORMAT_HINT[pushFormat] || FORMAT_HINT.auto}
                </span>
                <DownloadOutcome
                  result={downloadResult}
                  scenario={scenario}
                  onDownload={handleDownload}
                  busy={downloading}
                  testId="launch-panel-download-result"
                />
              </div>
            )}

            <hr className="divider" />

            {/* Launch button row */}
            <div className="launch-panel__actions">
              <button
                className="btn btn-primary btn-lg launch-panel__launch-btn"
                onClick={handleLaunch}
                disabled={disabled || (mode === 'pull' && agents.length === 0)}
              >
                {launching ? (
                  <><span className="spinner" /> Launching…</>
                ) : (
                  <> &#9654; Launch Run</>
                )}
              </button>

              {/* Status feedback */}
              {lastRun && lastRun.status === 'success' && (
                <div className="launch-panel__status launch-panel__status--success" role="status">
                  <span>✓</span>
                  {lastRun.message}
                </div>
              )}
              {/* A refusal keeps its code and structured detail. */}
              <LaunchErrorNotice
                result={lastRun}
                className="launch-panel__status launch-panel__status--error"
                testId="launch-panel-error"
              />
            </div>

          </div>
        )}
      </div>
    </div>
  )
}
