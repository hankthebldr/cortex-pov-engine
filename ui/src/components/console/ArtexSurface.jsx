/**
 * ArtexSurface — Offensive > ARTEX.
 *
 * ARTEX is an opt-in app SimCore reverse-proxies under /artex/ on the
 * console's own origin. This surface asks GET /api/artex/status which of four
 * states it is in and either frames the app (reachable + initialized) or shows
 * a gate that says what is missing. The gates and the loading state are the
 * console's (kit components, design tokens); only the frame's content is
 * ARTEX's, and ARTEX keeps its own login and first-run /setup inside the frame.
 *
 * The frame src is always the same-origin /artex/ — never an external origin.
 */
import React, { useCallback, useEffect, useState } from 'react'
import { request } from '../../api/client.js'
import { Button, Icon, StatusIcon } from './kit/index.js'

void React

const FRAME_SRC = '/artex/'

const WRAP = {
  display: 'flex', flexDirection: 'column', width: '100%', height: '100%', minHeight: 0, background: 'var(--s0)',
}
const BAR = {
  display: 'flex', alignItems: 'center', gap: 12, padding: '8px 16px',
  borderBottom: '1px solid var(--bd2)', color: 'var(--tx)',
}
const GATE = {
  margin: '48px auto', maxWidth: 520, padding: 24, border: '1px solid var(--bd2)',
  borderRadius: 8, color: 'var(--tx)',
}

function Gate({ testId, status, title, detail, alert = false, children }) {
  const headingId = `${testId}-title`
  return (
    <section
      style={GATE}
      data-testid={testId}
      role={alert ? 'alert' : 'status'}
      aria-live={alert ? 'assertive' : 'polite'}
      aria-labelledby={headingId}
    >
      <StatusIcon status={status} />
      <h2 id={headingId} style={{ margin: '12px 0 8px' }}>{title}</h2>
      {detail ? <p style={{ margin: '0 0 16px', color: 'var(--tx2)' }}>{detail}</p> : null}
      <div style={{ display: 'flex', gap: 8 }}>{children}</div>
    </section>
  )
}

export default function ArtexSurface({ onExit = null }) {
  const [state, setState] = useState({ phase: 'loading', status: null, error: null })
  const [showSetup, setShowSetup] = useState(false)

  const load = useCallback(() => {
    let live = true
    setState({ phase: 'loading', status: null, error: null })
    request('/api/artex/status')
      .then((status) => { if (live) setState({ phase: 'ready', status, error: null }) })
      .catch((err) => { if (live) setState({ phase: 'error', status: null, error: err }) })
    return () => { live = false }
  }, [])

  useEffect(() => load(), [load])

  const { phase, status, error } = state
  const retry = (
    <Button verb="Refresh" data-testid="artex-retry" onClick={load}>Retry</Button>
  )

  let body
  if (phase === 'loading') {
    body = <div className="destination-loading" role="status" aria-live="polite">loading…</div>
  } else if (phase === 'error') {
    body = (
      <Gate testId="artex-gate-unreachable" status="down" title="ARTEX service unreachable" alert
        detail={error?.message || 'POVengine could not read the ARTEX status.'}>
        {retry}
      </Gate>
    )
  } else if (status?.reachability === 'not_enabled') {
    body = (
      <Gate testId="artex-gate-not-enabled" status="off" title="ARTEX is not enabled" detail={status.detail} />
    )
  } else if (status?.reachability === 'unreachable') {
    body = (
      <Gate testId="artex-gate-unreachable" status="down" title="ARTEX service unreachable" alert detail={status.detail}>
        {retry}
      </Gate>
    )
  } else if (status?.reachability === 'reachable' && (status.initialized === true || showSetup)) {
    body = (
      <iframe
        data-testid="artex-frame"
        title="ARTEX offensive security console"
        src={FRAME_SRC}
        sandbox="allow-scripts allow-forms allow-same-origin allow-popups allow-downloads"
        style={{ flex: 1, width: '100%', height: '100%', border: 0 }}
      />
    )
  } else if (status?.reachability === 'reachable') {
    body = (
      <Gate testId="artex-gate-needs-setup" status="degraded" title="Finish ARTEX first-run setup"
        detail="ARTEX is running but has not been set up. Its setup runs inside the frame; ARTEX keeps its own login.">
        <Button variant="primary" verb="Configure" data-testid="artex-open-setup" onClick={() => setShowSetup(true)}>
          Open ARTEX setup
        </Button>
      </Gate>
    )
  } else {
    body = (
      <Gate testId="artex-gate-unreachable" status="unknown" title="ARTEX status unknown"
        detail="POVengine returned a status it does not recognise.">
        {retry}
      </Gate>
    )
  }

  return (
    <div style={WRAP} data-testid="artex-surface">
      <header style={BAR}>
        <Icon name="terminal" size={14} />
        <h1 style={{ margin: 0, fontSize: 14 }}>ARTEX</h1>
        <span style={{ flex: 1 }} />
        {onExit ? <Button data-testid="artex-exit" onClick={onExit}>Exit</Button> : null}
      </header>
      {body}
    </div>
  )
}
