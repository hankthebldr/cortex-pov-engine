import React, { useState, useEffect, useCallback, useRef } from 'react'
import { getTools, installTool, startTool, stopTool } from '../api/client.js'

// ─── Status helpers ───────────────────────────────────────────────────────────

const STATUS_CONFIG = {
  running:       { dot: 'status-dot-running',       label: 'Running',       actionLabel: null },
  stopped:       { dot: 'status-dot-stopped',       label: 'Stopped',       actionLabel: null },
  installed:     { dot: 'status-dot-installed',     label: 'Installed',     actionLabel: null },
  not_installed: { dot: 'status-dot-not-installed', label: 'Not Installed', actionLabel: null },
}

function StatusDot({ status }) {
  const cfg = STATUS_CONFIG[status] || STATUS_CONFIG.not_installed
  return (
    <span
      className={`status-dot ${cfg.dot}`}
      title={cfg.label}
    />
  )
}

// ─── Single Tool Row ──────────────────────────────────────────────────────────

function ToolRow({ tool, onAction, actionInProgress }) {
  const [expanded, setExpanded] = useState(false)

  const status  = tool.status || 'not_installed'
  const name    = tool.tool_name || tool.name || ''
  const desc    = tool.description || ''
  const port    = tool.port
  const busy    = actionInProgress === name

  const canInstall = status === 'not_installed'
  const canStart   = status === 'installed' || status === 'stopped'
  const canStop    = status === 'running'

  return (
    <div style={{
      borderBottom: '1px solid var(--cortex-border)',
      padding: '10px 0',
    }}>
      {/* Row header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
        <StatusDot status={status} />

        {/* Name + expand toggle */}
        <button
          onClick={() => setExpanded(v => !v)}
          style={{
            background: 'transparent',
            border: 'none',
            cursor: 'pointer',
            textAlign: 'left',
            flex: 1,
            padding: 0,
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
          }}
          aria-expanded={expanded}
          title={desc || name}
        >
          <span style={{
            fontSize: '13px',
            fontWeight: 600,
            color: 'var(--cortex-navy)',
            fontFamily: 'var(--font-mono)',
          }}>
            {name}
          </span>
          {port && (
            <span style={{ fontSize: '10px', color: 'var(--cortex-steel)' }}>
              :{port}
            </span>
          )}
          <span style={{
            fontSize: '9px',
            color: 'var(--cortex-steel)',
            marginLeft: 'auto',
            transition: 'transform var(--transition-fast)',
            transform: expanded ? 'rotate(90deg)' : 'rotate(0deg)',
          }}>
            &#9658;
          </span>
        </button>

        {/* Action buttons */}
        <div style={{ display: 'flex', gap: '4px', flexShrink: 0 }}>
          {canInstall && (
            <button
              className="btn btn-sm btn-navy"
              onClick={() => onAction('install', name)}
              disabled={busy}
              title="Build tool from submodule source"
            >
              {busy ? <span className="spinner" /> : '&#8659; Install'}
            </button>
          )}
          {canStart && (
            <button
              className="btn btn-sm btn-success"
              onClick={() => onAction('start', name)}
              disabled={busy}
              title="Start tool as managed process"
            >
              {busy ? <span className="spinner" /> : '&#9654; Start'}
            </button>
          )}
          {canStop && (
            <button
              className="btn btn-sm btn-danger"
              onClick={() => onAction('stop', name)}
              disabled={busy}
              title="Stop running tool"
            >
              {busy ? <span className="spinner" /> : '&#9646; Stop'}
            </button>
          )}
        </div>
      </div>

      {/* Expanded description */}
      {expanded && desc && (
        <div style={{
          marginTop: '8px',
          marginLeft: '24px',
          fontSize: '12px',
          color: 'var(--cortex-steel)',
          lineHeight: 1.5,
        }}>
          {desc}
          {tool.plane && (
            <div style={{ marginTop: '4px' }}>
              <span style={{ fontWeight: 600 }}>Plane: </span>
              {(Array.isArray(tool.plane) ? tool.plane : [tool.plane]).join(', ')}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ─── Main Panel ───────────────────────────────────────────────────────────────

const POLL_INTERVAL_MS = 5000

export default function ToolStatusPanel({ onMessage }) {
  const [tools, setTools]                     = useState([])
  const [loading, setLoading]                 = useState(true)
  const [error, setError]                     = useState(null)
  const [actionInProgress, setActionInProgress] = useState(null) // tool name
  const intervalRef = useRef(null)

  // ── Fetch tools ────────────────────────────────────────────────────────────
  const fetchTools = useCallback(() => {
    getTools()
      .then(data => {
        setTools(Array.isArray(data) ? data : [])
        setError(null)
      })
      .catch(err => {
        setError(err.message)
      })
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    fetchTools()
    intervalRef.current = setInterval(fetchTools, POLL_INTERVAL_MS)
    return () => clearInterval(intervalRef.current)
  }, [fetchTools])

  // ── Tool actions ──────────────────────────────────────────────────────────
  const handleAction = useCallback(async (action, toolName) => {
    setActionInProgress(toolName)
    try {
      if (action === 'install') {
        await installTool(toolName)
        if (onMessage) onMessage(`${toolName} installed successfully`, 'success')
      } else if (action === 'start') {
        await startTool(toolName)
        if (onMessage) onMessage(`${toolName} started`, 'success')
      } else if (action === 'stop') {
        await stopTool(toolName)
        if (onMessage) onMessage(`${toolName} stopped`, 'info')
      }
      fetchTools()
    } catch (err) {
      if (onMessage) onMessage(`${toolName}: ${err.message}`, 'error')
    } finally {
      setActionInProgress(null)
    }
  }, [fetchTools, onMessage])

  // ── Install All ───────────────────────────────────────────────────────────
  const handleInstallAll = useCallback(async () => {
    const notInstalled = tools.filter(t => t.status === 'not_installed')
    if (notInstalled.length === 0) return
    if (onMessage) onMessage(`Installing ${notInstalled.length} tool(s)…`, 'info')
    for (const tool of notInstalled) {
      const name = tool.tool_name || tool.name
      setActionInProgress(name)
      try {
        await installTool(name)
      } catch {
        /* continue with others */
      }
    }
    setActionInProgress(null)
    fetchTools()
    if (onMessage) onMessage('All tools installed', 'success')
  }, [tools, fetchTools, onMessage])

  // ── Summary counts ────────────────────────────────────────────────────────
  const runningCount  = tools.filter(t => t.status === 'running').length
  const stoppedCount  = tools.filter(t => t.status === 'stopped' || t.status === 'installed').length
  const notInstCount  = tools.filter(t => t.status === 'not_installed').length

  return (
    <div>
      {/* Header */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        marginBottom: '16px',
      }}>
        <p className="section-label" style={{ margin: 0 }}>Tools</p>
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
          {/* Live poll indicator */}
          <span style={{
            fontSize: '10px',
            color: 'var(--cortex-steel)',
            display: 'flex',
            alignItems: 'center',
            gap: '4px',
          }}>
            <span style={{
              width: '6px',
              height: '6px',
              borderRadius: '50%',
              background: 'var(--cortex-success)',
              display: 'inline-block',
              animation: 'pulse 2s infinite',
            }} />
            Live
          </span>
        </div>
      </div>

      {/* Summary stats */}
      {!loading && tools.length > 0 && (
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(3, 1fr)',
          gap: '6px',
          marginBottom: '16px',
        }}>
          {[
            { count: runningCount, label: 'Running', color: 'var(--cortex-success)' },
            { count: stoppedCount, label: 'Stopped', color: 'var(--cortex-warning)' },
            { count: notInstCount, label: 'Not Inst.', color: 'var(--cortex-steel)' },
          ].map(({ count, label, color }) => (
            <div key={label} style={{
              textAlign: 'center',
              padding: '8px 4px',
              background: 'var(--cortex-light-bg)',
              borderRadius: 'var(--radius-sm)',
              border: '1px solid var(--cortex-border)',
            }}>
              <div style={{ fontSize: '18px', fontWeight: 700, color, lineHeight: 1 }}>
                {count}
              </div>
              <div style={{ fontSize: '10px', color: 'var(--cortex-steel)', marginTop: '2px' }}>
                {label}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Tool list */}
      {loading ? (
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', padding: '16px 0' }}>
          <div className="spinner" />
          <span className="text-muted" style={{ fontSize: '12px' }}>Loading tools…</span>
        </div>
      ) : error ? (
        <div style={{
          padding: '12px',
          background: 'rgba(231,76,60,0.06)',
          border: '1px solid rgba(231,76,60,0.2)',
          borderRadius: 'var(--radius-md)',
          fontSize: '12px',
          color: 'var(--cortex-danger)',
        }}>
          <strong>Error loading tools:</strong><br />{error}
        </div>
      ) : tools.length === 0 ? (
        <div className="empty-state" style={{ padding: '24px 0' }}>
          <p>No tools registered.</p>
        </div>
      ) : (
        <div>
          {tools.map(tool => (
            <ToolRow
              key={tool.tool_name || tool.name || tool.id}
              tool={tool}
              onAction={handleAction}
              actionInProgress={actionInProgress}
            />
          ))}
        </div>
      )}

      {/* Install All button */}
      {!loading && notInstCount > 0 && (
        <button
          className="btn btn-navy btn-full"
          onClick={handleInstallAll}
          disabled={!!actionInProgress}
          style={{ marginTop: '16px' }}
          title={`Install ${notInstCount} uninstalled tool(s)`}
        >
          {actionInProgress ? (
            <><span className="spinner" /> Installing…</>
          ) : (
            <> &#8659; Install All ({notInstCount})</>
          )}
        </button>
      )}

      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50%       { opacity: 0.35; }
        }
      `}</style>
    </div>
  )
}
