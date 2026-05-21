import React, { useEffect, useRef, useState, useMemo } from 'react'
import useRunEventStream from './useRunEventStream.js'

/**
 * EventStream — real-time agent stdout/stderr viewer for the In-Flight tab.
 *
 * Subscribes to /api/runs/:id/events (SSE) when the backend supports it,
 * falls back to polling /api/runs/:id with a synthetic event projector
 * for older builds. Either way the rendered shape is the same: a
 * scrollable terminal-like log with timestamp + level + message lines.
 *
 * Operator behavior:
 *   - Auto-scrolls to bottom by default (terminal-like)
 *   - User scroll-up pins the viewport; "↓ jump to live" pill appears
 *   - Level filter chips: info · step · detect · warn · error
 *   - Pause / resume / clear controls
 *   - Connection status badge: live (green) · poll (amber) · error (red)
 *
 * Props:
 *   runId   — string|null
 *   compact — boolean (smaller in inflight footer; bigger when full-tab)
 */
const LEVELS = ['info', 'step', 'detect', 'warn', 'error']

const LEVEL_LABEL = {
  info:   'info',
  step:   'step',
  detect: 'detect',
  warn:   'warn',
  error:  'error',
}

export default function EventStream({ runId, compact = false }) {
  const [paused, setPaused] = useState(false)
  const [enabled, setEnabled] = useState(new Set(LEVELS))
  const [stuckToBottom, setStuckToBottom] = useState(true)
  const scrollerRef = useRef(null)
  const endRef = useRef(null)

  const { events, connected, mode, reset } = useRunEventStream(runId, { paused })

  const filtered = useMemo(
    () => events.filter((e) => enabled.has(e.level)),
    [events, enabled],
  )

  // Auto-scroll to bottom unless user has scrolled up.
  // scrollIntoView isn't implemented in jsdom, so we guard for test
  // environments — production browsers all support it.
  useEffect(() => {
    if (!stuckToBottom) return
    const el = endRef.current
    if (el && typeof el.scrollIntoView === 'function') {
      el.scrollIntoView({ behavior: 'instant', block: 'end' })
    }
  }, [filtered, stuckToBottom])

  const handleScroll = () => {
    const el = scrollerRef.current
    if (!el) return
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight
    setStuckToBottom(distanceFromBottom < 16)
  }

  const toggleLevel = (level) => {
    setEnabled((prev) => {
      const next = new Set(prev)
      if (next.has(level)) next.delete(level)
      else next.add(level)
      return next
    })
  }

  const jumpToLive = () => {
    setStuckToBottom(true)
    const el = endRef.current
    if (el && typeof el.scrollIntoView === 'function') {
      el.scrollIntoView({ behavior: 'smooth', block: 'end' })
    }
  }

  if (!runId) {
    return (
      <div className={'event-stream' + (compact ? ' event-stream--compact' : '')}>
        <div className="event-stream__empty mono">
          no active run — launch a scenario to see live agent output
        </div>
      </div>
    )
  }

  const modeBadge = connected
    ? mode === 'stream' ? { text: 'LIVE',  cls: 'event-stream__mode--live' }
    : mode === 'poll'   ? { text: 'POLL',  cls: 'event-stream__mode--poll' }
    :                     { text: 'ERR',   cls: 'event-stream__mode--err'  }
    : { text: '...', cls: 'event-stream__mode--poll' }

  return (
    <section
      className={'event-stream' + (compact ? ' event-stream--compact' : '')}
      aria-label="Agent event stream"
    >
      <div className="event-stream__head">
        <div className="event-stream__title">
          <span className="event-stream__title-text mono">agent · stdout</span>
          <span className={`event-stream__mode mono ${modeBadge.cls}`}>
            {modeBadge.text}
          </span>
        </div>
        <div className="event-stream__filters" role="group" aria-label="Level filters">
          {LEVELS.map((level) => (
            <button
              key={level}
              type="button"
              className={
                'event-stream__filter event-stream__filter--' + level +
                (enabled.has(level) ? ' is-active' : '')
              }
              onClick={() => toggleLevel(level)}
              aria-pressed={enabled.has(level)}
            >
              {LEVEL_LABEL[level]}
            </button>
          ))}
        </div>
        <div className="event-stream__controls">
          <button
            type="button"
            className="event-stream__btn"
            onClick={() => setPaused((p) => !p)}
            title={paused ? 'Resume' : 'Pause'}
          >
            {paused ? '▶ resume' : '⏸ pause'}
          </button>
          <button
            type="button"
            className="event-stream__btn"
            onClick={reset}
            title="Clear buffer (does not affect server-side history)"
          >
            ⊘ clear
          </button>
        </div>
      </div>

      <div
        ref={scrollerRef}
        className="event-stream__scroller"
        onScroll={handleScroll}
        role="log"
        aria-live="polite"
        aria-relevant="additions"
      >
        {filtered.length === 0 ? (
          <div className="event-stream__empty mono">
            waiting for agent…
          </div>
        ) : (
          <ol className="event-stream__lines">
            {filtered.map((evt) => (
              <li
                key={evt.id}
                className={'event-stream__line event-stream__line--' + evt.level}
              >
                <span className="event-stream__ts mono">
                  {formatTs(evt.timestamp)}
                </span>
                <span className={'event-stream__level mono event-stream__level--' + evt.level}>
                  {evt.level}
                </span>
                {evt.stepIndex != null && (
                  <span className="event-stream__step mono">
                    s{evt.stepIndex + 1}
                  </span>
                )}
                <span className="event-stream__msg">
                  {evt.synthetic && (
                    <span className="event-stream__synthetic-flag" title="Synthetic event derived from run record — not real agent stdout">
                      ◊
                    </span>
                  )}
                  {evt.message}
                </span>
              </li>
            ))}
            <li ref={endRef} aria-hidden="true" />
          </ol>
        )}

        {!stuckToBottom && (
          <button
            type="button"
            className="event-stream__jump-live"
            onClick={jumpToLive}
            aria-label="Jump to live"
          >
            ↓ live
          </button>
        )}
      </div>
    </section>
  )
}

/* ─── helpers ────────────────────────────────────────────────────── */

function formatTs(ts) {
  if (!ts) return '--:--:--'
  try {
    const d = new Date(ts)
    const hh = String(d.getUTCHours()).padStart(2, '0')
    const mm = String(d.getUTCMinutes()).padStart(2, '0')
    const ss = String(d.getUTCSeconds()).padStart(2, '0')
    return `${hh}:${mm}:${ss}`
  } catch {
    return String(ts).slice(11, 19)
  }
}
