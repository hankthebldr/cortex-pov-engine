import React, { useState, useEffect, useCallback } from 'react'
import { postRun, getAgents, downloadScenario } from '../api/client.js'

// ─── Component ────────────────────────────────────────────────────────────────

export default function LaunchPanel({ scenario, onRunComplete, onError }) {
  // ── State ──────────────────────────────────────────────────────────────────
  const [mode, setMode]               = useState('pull')      // 'pull' | 'push'
  const [identity, setIdentity]       = useState('')
  const [agents, setAgents]           = useState([])
  const [selectedAgent, setSelectedAgent] = useState('')
  const [pushFormat, setPushFormat]   = useState('bash')      // 'bash' | 'k8s'
  const [launching, setLaunching]     = useState(false)
  const [downloading, setDownloading] = useState(false)
  const [lastRun, setLastRun]         = useState(null)        // { status, message }

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
          setSelectedAgent(list[0].id || list[0].agent_id || '')
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
      setLastRun({ status: 'error', message: msg })
      if (onError) onError(msg)
    } finally {
      setLaunching(false)
    }
  }, [scenario, mode, identity, selectedAgent, onRunComplete, onError])

  // ── Download push script ──────────────────────────────────────────────────
  const handleDownload = useCallback(async () => {
    if (!scenario) return
    setDownloading(true)
    try {
      const id = scenario.scenario_id || scenario.id
      const blob = await downloadScenario(id, pushFormat)
      const ext  = pushFormat === 'k8s' ? 'yml' : 'sh'
      const filename = `cortexsim-${id}-${pushFormat}.${ext}`
      const url  = URL.createObjectURL(blob)
      const a    = document.createElement('a')
      a.href     = url
      a.download = filename
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
    } catch (err) {
      if (onError) onError(err.message || 'Download failed')
    } finally {
      setDownloading(false)
    }
  }, [scenario, pushFormat, onError])

  const disabled = !scenario || launching

  return (
    <div className="panel-card">
      <div className="panel-card-header">
        <h3>Launch Panel</h3>
        {scenario && (
          <span className="badge badge-teal text-mono" style={{ textTransform: 'none' }}>
            {scenario.scenario_id || scenario.id}
          </span>
        )}
      </div>

      <div className="panel-card-body">
        {!scenario ? (
          <div className="empty-state" style={{ padding: '24px 0' }}>
            <div className="empty-state-icon">&#9654;</div>
            <p>Select a scenario to configure and launch a simulation.</p>
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>

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
              <span style={{ fontSize: '11px', color: 'var(--cortex-steel)', marginTop: '2px' }}>
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
                <span style={{ fontSize: '11px', color: 'var(--cortex-steel)' }}>
                  Service account used for process causality chain generation
                </span>
              </div>
            )}

            {/* Pull mode: agent selector */}
            {mode === 'pull' && (
              <div className="form-group">
                <label className="form-label" htmlFor="agent-select">Target Agent</label>
                {agents.length === 0 ? (
                  <div style={{
                    padding: '10px 12px',
                    background: 'rgba(243,156,18,0.08)',
                    border: '1px solid rgba(243,156,18,0.3)',
                    borderRadius: 'var(--radius-md)',
                    fontSize: '12px',
                    color: '#c47d00',
                  }}>
                    &#9888; No agents connected. Start an agent with:{' '}
                    <code style={{ fontFamily: 'var(--font-mono)' }}>
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
                      <option key={agent.id || agent.agent_id} value={agent.id || agent.agent_id}>
                        {agent.hostname || agent.id || agent.agent_id}
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
                <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
                  <div className="segmented-control">
                    <button
                      className={pushFormat === 'bash' ? 'active' : ''}
                      onClick={() => setPushFormat('bash')}
                    >
                      bash
                    </button>
                    <button
                      className={pushFormat === 'k8s' ? 'active' : ''}
                      onClick={() => setPushFormat('k8s')}
                    >
                      k8s
                    </button>
                  </div>

                  <button
                    className="btn btn-secondary"
                    onClick={handleDownload}
                    disabled={downloading || !scenario}
                    title={`Download ${pushFormat === 'k8s' ? 'K8s YAML manifest' : 'Bash bundle'}`}
                  >
                    {downloading ? (
                      <><span className="spinner" /> Preparing…</>
                    ) : (
                      <> &#8595; Download {pushFormat === 'k8s' ? 'K8s YAML' : 'Bash Bundle'}</>
                    )}
                  </button>
                </div>
                <span style={{ fontSize: '11px', color: 'var(--cortex-steel)' }}>
                  {pushFormat === 'bash'
                    ? 'Self-contained bash script — runs on any Ubuntu 22.04 box'
                    : 'K8s YAML manifest — apply with kubectl apply -f <file>'}
                </span>
              </div>
            )}

            <hr className="divider" />

            {/* Launch button row */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
              <button
                className="btn btn-primary btn-lg"
                onClick={handleLaunch}
                disabled={disabled || (mode === 'pull' && agents.length === 0)}
                style={{ minWidth: '140px' }}
              >
                {launching ? (
                  <><span className="spinner" /> Launching…</>
                ) : (
                  <> &#9654; Launch Run</>
                )}
              </button>

              {/* Status feedback */}
              {lastRun && (
                <div style={{
                  fontSize: '13px',
                  color: lastRun.status === 'success' ? 'var(--cortex-success)' : 'var(--cortex-danger)',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '6px',
                }}>
                  <span>{lastRun.status === 'success' ? '✓' : '✕'}</span>
                  {lastRun.message}
                </div>
              )}
            </div>

          </div>
        )}
      </div>
    </div>
  )
}
