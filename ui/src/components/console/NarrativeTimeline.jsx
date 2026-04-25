import React, { useEffect, useRef, useState } from 'react'

/**
 * NarrativeTimeline — the hero artifact.
 *
 * Renders a horizontal kill-chain timeline. Each frame is a step on a track;
 * each detection is a card under the node, color-coded by status. SVG arcs
 * physically connect steps that share an XSIAM-stitched detection — drawn in
 * with an animation when the component mounts or new stitches arrive.
 *
 * Props:
 *   frames    — output of useTimelineData().frames
 *   stitches  — output of useTimelineData().stitches  (pairs of step indices)
 *   nowLabel  — optional override for the eyebrow timestamp
 */
export default function NarrativeTimeline({ frames = [], stitches = [], nowLabel }) {
  const containerRef = useRef(null)
  const nodeRefs = useRef([])
  const [paths, setPaths] = useState([])

  // Recompute SVG arcs whenever frames/stitches change OR the container resizes.
  useEffect(() => {
    if (!containerRef.current || frames.length === 0) {
      setPaths([])
      return
    }

    const computePaths = () => {
      const containerRect = containerRef.current.getBoundingClientRect()
      const out = stitches
        .map((s) => {
          const fromEl = nodeRefs.current[s.from]
          const toEl = nodeRefs.current[s.to]
          if (!fromEl || !toEl) return null
          const a = fromEl.getBoundingClientRect()
          const b = toEl.getBoundingClientRect()
          const x1 = a.left + a.width / 2 - containerRect.left
          const y1 = a.top  + a.height / 2 - containerRect.top
          const x2 = b.left + b.width / 2 - containerRect.left
          const y2 = b.top  + b.height / 2 - containerRect.top
          const sag = Math.min(60, Math.abs(x2 - x1) * 0.4)
          const cy = Math.max(y1, y2) + sag
          return {
            id: `${s.from}-${s.to}`,
            d: `M ${x1} ${y1} C ${x1} ${cy}, ${x2} ${cy}, ${x2} ${y2}`,
            label: s.label || '',
            // Position the label at the curve's apex.
            labelX: (x1 + x2) / 2,
            labelY: cy - 12,
          }
        })
        .filter(Boolean)
      setPaths(out)
    }

    computePaths()
    const ro = new ResizeObserver(computePaths)
    ro.observe(containerRef.current)
    return () => ro.disconnect()
  }, [frames, stitches])

  if (frames.length === 0) return null

  // Progress fill width: portion of the track covered by completed steps.
  const doneCount = frames.filter((f) => f.status === 'done' || f.status === 'pending').length
  const progressWidth =
    frames.length > 1 ? `${Math.max(0, Math.min(100, ((doneCount - 0.5) / (frames.length - 1)) * 100))}%` : '0%'

  return (
    <div className="narrative__timeline" ref={containerRef}>
      {nowLabel && (
        <div className="narrative__now-label mono">{nowLabel}</div>
      )}

      <div className="tl-track" />
      <div className="tl-track-progress" style={{ width: progressWidth }} />

      {/* SVG overlay for stitch arcs */}
      <svg className="stitch-svg" aria-hidden="true">
        {paths.map((p) => (
          <g key={p.id}>
            <path className="stitch-path" d={p.d} />
          </g>
        ))}
      </svg>

      {paths.map((p) => (
        <div
          key={`label-${p.id}`}
          className="narrative__stitch-label"
          style={{ left: p.labelX, top: p.labelY, transform: 'translate(-50%, -100%)' }}
        >
          {p.label}
        </div>
      ))}

      <div
        className="tl-steps"
        style={{ gridTemplateColumns: `repeat(${frames.length}, 1fr)` }}
      >
        {frames.map((f, i) => (
          <div
            key={f.id}
            className={
              'tl-step' +
              (f.status === 'pending' ? ' tl-step--pending' : '') +
              (f.status === 'idle'    ? ' tl-step--idle'    : '')
            }
          >
            <div className="tl-step__time">
              {f.timestamp ? formatTime(f.timestamp) : '—'}
            </div>
            <div className="tl-step__tid">{f.tid}</div>
            <div className="tl-step__name">{f.name}</div>
            <div
              className="tl-step__node"
              ref={(el) => { nodeRefs.current[i] = el }}
              title={`Step ${i + 1} · ${f.status}`}
            />
            {f.elapsedMttd != null && (
              <div className="tl-step__mttd mono">MTTD {f.elapsedMttd}s</div>
            )}
            <div className="tl-step__detections">
              {f.detections.map((d) => (
                <div
                  key={d.key}
                  className={'tl-det ' + detClass(d.status)}
                  title={`${d.plane} · ${d.type}\n${d.description || ''}`}
                >
                  <span className="tl-det__plane">{d.plane}</span>
                  {d.type || (d.status === 'detected' ? 'OK' : '—')}
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

function detClass(status) {
  switch (status) {
    case 'detected': return 'tl-det--detected'
    case 'missed':   return 'tl-det--missed'
    case 'pending':  return 'tl-det--pending'
    default:         return ''
  }
}

function formatTime(ts) {
  try {
    const d = new Date(ts)
    return d.toISOString().substring(11, 19) + 'Z'
  } catch {
    return ts
  }
}
