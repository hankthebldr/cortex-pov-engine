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
    <div className="tsp-row">
      {/* Row header */}
      <div className="tsp-row__head">
        <StatusDot status={status} />

        {/* Name + expand toggle */}
        <button
          onClick={() => setExpanded(v => !v)}
          className="tsp-row__toggle"
          aria-expanded={expanded}
          title={desc || name}
        >
          <span className="tsp-row__name">
            {name}
          </span>
          {port && (
            <span className="tsp-row__port">
              :{port}
            </span>
          )}
          <span className={`tsp-row__caret${expanded ? ' tsp-row__caret--open' : ''}`}>
            &#9658;
          </span>
        </button>

        {/* Action buttons */}
        <div className="tsp-row__actions">
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
        <div className="tsp-row__desc">
          {desc}
          {tool.plane && (
            <div className="tsp-row__plane">
              <span className="tsp-row__plane-label">Plane: </span>
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
      <div className="tsp-header">
        <p className="section-label tsp-header__label">Tools</p>
        <div className="tsp-header__meta">
          {/* Live poll indicator */}
          <span className="tsp-live">
            <span className="tsp-live__dot" />
            Live
          </span>
        </div>
      </div>

      {/* Summary stats */}
      {!loading && tools.length > 0 && (
        <div className="tsp-stats">
          {[
            { count: runningCount, label: 'Running', variant: 'success' },
            { count: stoppedCount, label: 'Stopped', variant: 'warning' },
            { count: notInstCount, label: 'Not Inst.', variant: 'steel' },
          ].map(({ count, label, variant }) => (
            <div key={label} className="tsp-stat">
              <div className={`tsp-stat__count tsp-stat__count--${variant}`}>
                {count}
              </div>
              <div className="tsp-stat__label">
                {label}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Tool list */}
      {loading ? (
        <div className="tsp-loading">
          <div className="spinner" />
          <span className="text-muted tsp-loading__text">Loading tools…</span>
        </div>
      ) : error ? (
        <div className="tsp-error">
          <strong>Error loading tools:</strong><br />{error}
        </div>
      ) : tools.length === 0 ? (
        <div className="empty-state tsp-empty">
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
          className="btn btn-navy btn-full tsp-install-all"
          onClick={handleInstallAll}
          disabled={!!actionInProgress}
          title={`Install ${notInstCount} uninstalled tool(s)`}
        >
          {actionInProgress ? (
            <><span className="spinner" /> Installing…</>
          ) : (
            <> &#8659; Install All ({notInstCount})</>
          )}
        </button>
      )}
    </div>
  )
}
