/**
 * RunLifecycleStrip — the five stages of a run, the same for every run
 * (design v26 §4.6):
 *
 *   Launched → Running → Finished / Failed / Aborted → Read back → Results
 *
 * An ordered list; the stage the run is on carries aria-current="step". Each
 * stage shows a glyph AND its word — ✓ done, ● now, ✕ failed, – not yet —
 * never colour alone.
 *
 *   variant="compact"  a row of five pips for the Runs list. The words are
 *                      for assistive tech and the hover title; the row's own
 *                      status and result cells say the same thing in full.
 *   variant="full"     the run's page: label, glyph and one line under each
 *                      stage (`subs`, supplied by the caller who knows the
 *                      step counts and the tenant).
 */
import React from 'react'
import '../../styles/destinations/run-lifecycle.css'

void React

export default function RunLifecycleStrip({ lifecycle, variant = 'compact', subs = [], label }) {
  if (!lifecycle || !Array.isArray(lifecycle.stages)) return null
  const full = variant === 'full'
  return (
    <ol
      className={`lc lc--${full ? 'full' : 'compact'}`}
      aria-label={label || (full ? 'Run lifecycle' : 'Lifecycle')}
      data-phase={lifecycle.phase}
    >
      {lifecycle.stages.map((s, i) => (
        <li
          key={i}
          className={`lc-stage lc-stage--${s.state}`}
          aria-current={s.state === 'current' ? 'step' : undefined}
          title={full ? undefined : `${s.label}: ${s.word}`}
        >
          {full ? (
            <>
              <span className="lc-stage__head">
                <span className="lc-stage__glyph" role="img" aria-label={s.word} />
                <span className="lc-stage__label">{s.label}</span>
              </span>
              {subs[i] && <span className="lc-stage__sub">{subs[i]}</span>}
            </>
          ) : (
            <span className="sr-only">{`${s.label}: ${s.word}`}</span>
          )}
        </li>
      ))}
    </ol>
  )
}
