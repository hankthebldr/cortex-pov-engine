import React, { useMemo } from 'react'

/**
 * MttdHistogram — distribution of detection mean-time-to-detect (MTTD)
 * across the rows of a scorecard.
 *
 * Surfaces beyond the median: lets DCs say things like "75th percentile
 * MTTD was 90 seconds" or "two outliers landed in the 2-5 minute bucket
 * — investigate the cause." Plain SVG, no external chart lib. With no MTTD
 * to draw it is one line.
 *
 * Props:
 *   rows — array of useResultsData rows ({ mttd, observed, ... })
 */

/** Seconds as one tidy number: a tenth under ten seconds, whole seconds above. */
export function tidySeconds(n) {
  if (typeof n !== 'number' || !Number.isFinite(n)) return n
  return n < 10 ? Math.round(n * 10) / 10 : Math.round(n)
}

// Bucket edges in seconds. Bins inclusive of the lower bound, exclusive
// of the upper. The last bin catches anything 120s+ in one tail.
const BUCKETS = [
  { label: '0-15s',     min: 0,    max: 15  },
  { label: '15-30s',    min: 15,   max: 30  },
  { label: '30-60s',    min: 30,   max: 60  },
  { label: '60-120s',   min: 60,   max: 120 },
  { label: '2-5m',      min: 120,  max: 300 },
  { label: '5-10m',     min: 300,  max: 600 },
  { label: '10m+',      min: 600,  max: Infinity },
]

export default function MttdHistogram({ rows = [] }) {
  const { detected, bins, stats } = useMemo(() => analyze(rows), [rows])

  if (detected.length === 0) {
    return (
      <p
        className="mttd-histogram mttd-histogram--empty"
        data-testid="mttd-panel"
        title="A detection has an MTTD once it is detected with a time"
      >
        No MTTD yet
      </p>
    )
  }

  const maxCount = Math.max(...bins.map((b) => b.count), 1)

  return (
    <div className="mttd-histogram" data-testid="mttd-panel">
      <div className="mttd-histogram__head">
        <h3 className="mttd-histogram__title">MTTD distribution</h3>
        <div className="mttd-histogram__stats">
          <Stat label="median" value={stats.p50} suffix="s" />
          <Stat label="p75" value={stats.p75} suffix="s" />
          <Stat label="p95" value={stats.p95} suffix="s" />
          <Stat label="max" value={stats.max} suffix="s" highlight={stats.max > 300} />
        </div>
      </div>

      <div className="mttd-histogram__chart" role="img" aria-label="Detection MTTD histogram">
        {bins.map((b, i) => {
          const heightPct = maxCount > 0 ? (b.count / maxCount) * 100 : 0
          return (
            <div key={i} className="mttd-histogram__col" title={`${b.label}: ${b.count} detection${b.count === 1 ? '' : 's'}`}>
              <div className="mttd-histogram__count mono">
                {b.count > 0 ? b.count : ''}
              </div>
              <div className="mttd-histogram__bar-wrap">
                <div
                  className={'mttd-histogram__bar' + (b.contains.p50 ? ' mttd-histogram__bar--median' : '')}
                  style={{ height: `${heightPct}%` }}
                />
              </div>
              <div className="mttd-histogram__label mono">{b.label}</div>
            </div>
          )
        })}
      </div>

      <div className="mttd-histogram__hint">
        {detected.length} detection{detected.length === 1 ? '' : 's'} · median bin highlighted
      </div>
    </div>
  )
}

function Stat({ label, value, suffix = '', highlight = false }) {
  return (
    <div className={'mttd-histogram__stat' + (highlight ? ' mttd-histogram__stat--alert' : '')}>
      <div className="mttd-histogram__stat-label mono">{label}</div>
      <div className="mttd-histogram__stat-value">
        {value != null ? tidySeconds(value) : '—'}
        {value != null && (
          <span className="mttd-histogram__stat-unit">{suffix}</span>
        )}
      </div>
    </div>
  )
}

/* ─── analysis ────────────────────────────────────────────────────── */

function analyze(rows) {
  const detected = rows
    .filter((r) => r.observed === true && typeof r.mttd === 'number')
    .map((r) => r.mttd)
    .sort((a, b) => a - b)

  const stats = {
    p50: percentile(detected, 50),
    p75: percentile(detected, 75),
    p95: percentile(detected, 95),
    max: detected.length > 0 ? detected[detected.length - 1] : null,
  }

  const bins = BUCKETS.map((b) => ({
    label: b.label,
    min: b.min, max: b.max,
    count: detected.filter((v) => v >= b.min && v < b.max).length,
    contains: {
      p50: stats.p50 != null && stats.p50 >= b.min && stats.p50 < b.max,
    },
  }))

  return { detected, bins, stats }
}

function percentile(sorted, p) {
  if (sorted.length === 0) return null
  const idx = Math.min(sorted.length - 1, Math.floor((p / 100) * sorted.length))
  return sorted[idx]
}
