import React, { useMemo, useState } from 'react'
import { topology } from './povdata/topology.js'
import { RUN_LOG } from './povdata/corpus.js'

/**
 * RunTopology — the lane topology, GENERATED rather than hand-placed.
 *
 * WHY GENERATION MATTERS HERE MORE THAN IT USUALLY DOES
 * A hand-drawn topology is a picture of one run. It has to be redrawn for the
 * next one, and in practice it is not — so it quietly becomes a picture of a
 * run that is not the run on screen, which on this surface is the worst
 * possible failure: a DC points at a diagram while describing different
 * telemetry.
 *
 * `topology()` takes a run's step list — `{ col, row, lane, kind, label, state,
 * from[] }` — and derives everything else: which of the ingestion lanes to draw
 * (a run that never touches the Data Connector gets no Data Connector band),
 * lane heights from row occupancy, node width from label length, column pitch,
 * edges from declared causality, and the viewBox from the result.
 *
 * LANE ORDER IS CAUSAL, WITH ONE PIN.
 * Lanes sort by the column where flow ENTERS them, so a run reads top-down:
 * Data Connector → Broker VM → Engine. Pure causal sort floated the Engine
 * band into the middle of an endpoint run, because its first alert fires
 * early — so the Engine is pinned terminal. It is derived signal, not an entry
 * door, and a band of alerts sitting above the steps that caused them reads
 * backwards.
 *
 * THE PULSE FOLLOWS THE RUN RECORD, NOT THE DIAGRAM.
 * A completed run does not pulse. That sounds obvious and was not: the pulse
 * was a property of the drawing, so every run animated as though it were live.
 */
export default function RunTopology({ runId = null, onSelectRun = null }) {
  const [openId, setOpenId] = useState(runId || RUN_LOG[0].id)
  const id = runId || openId
  const record = RUN_LOG.find((r) => r.id === id) || RUN_LOG[0]
  const topo = useMemo(() => topology({ runOpen: id }), [id])

  return (
    <div data-testid="run-topology">
      <div className="pov-section" style={{ marginTop: 0 }}>
        <span className="pov-section__label">
          Topology · {topo.lanes.length} lanes · {topo.nodes.length} nodes
        </span>
        <span className="pov-section__hr" />
        {/* Switching runs re-derives the whole diagram — lanes, geometry,
            edges and the pulse — from that run's own steps. */}
        <select
          className="pov-field__input"
          style={{ width: 'auto', padding: '5px 8px' }}
          value={id}
          onChange={(e) => {
            setOpenId(e.target.value)
            if (onSelectRun) onSelectRun(e.target.value)
          }}
          aria-label="Run"
        >
          {RUN_LOG.map((r) => (
            <option key={r.id} value={r.id}>{r.id} · {r.scen} · {r.state}</option>
          ))}
        </select>
      </div>

      <div className="pov-card__head" style={{ marginBottom: 10 }}>
        <span className="pov-card__code">{record.id}</span>
        <span className="pov-card__title">{record.name}</span>
        <span className="pov-card__spacer" />
        <span className={`pov-pill pov-pill--${record.state === 'COMPLETE' ? 'pos' : record.state === 'RUNNING' ? 'warn' : 'crit'}`}>
          {record.state}
        </span>
      </div>

      <div className="pov-facts" style={{ gridAutoFlow: 'column', justifyContent: 'start', gap: 22, marginBottom: 12 }}>
        {[['steps', record.steps], ['detections', record.dets], ['MTTD', record.mttd],
          ['workflow', record.wf], ['wall clock', record.wall], ['by', record.by]].map(([k, v]) => (
          <div key={k}>
            <span className="pov-fact__k" style={{ display: 'block', minWidth: 0 }}>{k}</span>
            <span className="pov-fact__v">{v}</span>
          </div>
        ))}
      </div>

      <div className="pov-table__scroll" style={{ padding: 4 }}>
        <svg
          viewBox={`0 0 ${topo.w} ${topo.h}`}
          width={topo.w}
          height={topo.h}
          role="img"
          aria-label={`Lane topology for run ${record.id}`}
          style={{ display: 'block', maxWidth: 'none' }}
        >
          {topo.lanes.map((l) => (
            <g key={l.key}>
              <rect x={0} y={l.y} width={topo.w} height={l.h} fill={l.fill} rx={6} />
              {/* Lane labels are the ingestion DOOR, which is the same
                  vocabulary the Composer's swimlanes use — where a node sits
                  means the same thing in both views. */}
              <text x={14} y={l.ty} fill="var(--tx2)" style={{ font: '700 9px/1 var(--font-ui)', letterSpacing: '.12em' }}>
                {l.label}
              </text>
              <text x={14} y={l.ty2} fill="var(--tx3)" style={{ font: '400 8.5px/1 var(--font-ui)' }}>
                {l.sub}
              </text>
            </g>
          ))}

          {topo.edges.map((e, i) => (
            <path
              key={i}
              d={e.d}
              fill="none"
              stroke={e.stroke}
              strokeWidth={e.sw}
              strokeDasharray={e.dash === 'none' ? undefined : e.dash}
            />
          ))}

          {topo.nodes.map((n) => (
            <g key={n.id}>
              <rect
                x={n.x} y={n.y} width={n.w} height={38} rx={5}
                fill="var(--s2)" stroke={n.stroke} strokeWidth={1.5}
              />
              <text x={n.tx} y={n.ty1} fill="var(--tx3)" style={{ font: '400 8px/1 var(--font-mono)', letterSpacing: '.06em' }}>
                {n.kind}
              </text>
              <text x={n.tx} y={n.ty2} fill="var(--tx)" style={{ font: '600 10.5px/1 var(--font-ui)' }}>
                {n.label}
              </text>
            </g>
          ))}

          {/* Only a RUNNING record animates. See the note at the top. */}
          {topo.live && (
            <circle cx={topo.pulseX} cy={topo.pulseY} r={6} fill="var(--pos)">
              <animate attributeName="opacity" values="0.25;1;0.25" dur="1.4s" repeatCount="indefinite" />
              <animate attributeName="r" values="5;9;5" dur="1.4s" repeatCount="indefinite" />
            </circle>
          )}
        </svg>
      </div>

      <div className="pov-legend">
        {[['confirmed', '#00CC66', 'the step ran and its signal arrived'],
          ['broken', '#FDAC96', 'the step ran and the signal did not arrive'],
          ['expected', '#4A4A4A', 'not reached yet — dashed, because nothing is claimed about it']].map(([label, color, def]) => (
          <span className="pov-legend__item" key={label}>
            <span className="pov-dot" style={{ background: color }} />
            <span className="pov-legend__def"><strong>{label}</strong> — {def}</span>
          </span>
        ))}
      </div>
    </div>
  )
}
