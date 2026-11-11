import React, { useCallback, useEffect, useState } from 'react'
import '../../styles/destinations/readiness.css'
import '../../styles/destinations/firewall.css'
import { useEnvironment } from '../../context/EnvironmentContext.jsx'
import { listFirewalls } from '../../api/client.js'
import ConnectStep from './firewall/ConnectStep.jsx'
import CheckStep from './firewall/CheckStep.jsx'
import SourcesStep from './firewall/SourcesStep.jsx'

/**
 * FirewallView — register a PAN-OS NGFW, check it read-only, and see per-agent
 * stitch readiness (spec 2026-11-10-ngfw-connection-wizard §6).
 *
 * Three steps on one page, each usable on its own: Connect, Check, Sources.
 * Nothing here writes to the firewall or to Cortex. What the check proves is
 * the firewall's configuration; it does not show that the tenant ingested or
 * stitched anything, and the page says so wherever a result is shown.
 */
export default function FirewallView() {
  const env = useEnvironment()
  const [firewalls, setFirewalls] = useState(null)
  const [loadError, setLoadError] = useState(null)
  const [selected, setSelected] = useState(null)
  const [report, setReport] = useState(null)

  const refresh = useCallback(async () => {
    try {
      const body = await listFirewalls()
      const list = Array.isArray(body?.firewalls) ? body.firewalls : null
      if (!list) {
        // An unrecognised envelope is an error, not "none registered".
        setFirewalls(null)
        setLoadError('GET /api/ngfw returned no firewalls[] list')
        return
      }
      setLoadError(null)
      setFirewalls(list)
      setSelected((cur) => (cur && list.some((f) => f.name === cur) ? cur : (list[0]?.name ?? null)))
    } catch (err) {
      setFirewalls(null)
      setLoadError(err?.message || 'firewall list failed')
    }
  }, [])

  useEffect(() => { refresh() }, [refresh])

  // A report belongs to the firewall it was run against; switching firewalls
  // must not leave another firewall's verdict on screen.
  const select = (name) => {
    if (name !== selected) setReport(null)
    setSelected(name)
  }
  const reportFor = report && (!report.firewall || report.firewall === selected) ? report : null

  return (
    <div className="pov-page fw-page" data-testid="firewall-view">
      <h1 className="pov-page__title">Firewall</h1>
      <p className="pov-page__lede">
        Register a PAN-OS firewall, check read-only that it writes and forwards the logs a POV
        depends on, and see whether each enrolled agent&apos;s traffic can stitch. Nothing here
        writes to the firewall.
      </p>

      <section className="readiness__section fw-step" aria-labelledby="fw-step-connect">
        <h2 id="fw-step-connect" className="readiness__h">1 · Connect</h2>
        <ConnectStep firewalls={firewalls} loadError={loadError} selected={selected}
          onSelect={select} onChanged={refresh} />
      </section>

      <section className="readiness__section fw-step" aria-labelledby="fw-step-check">
        <h2 id="fw-step-check" className="readiness__h">2 · Check</h2>
        <CheckStep firewall={selected} agents={env?.agents || []} report={reportFor}
          onReport={setReport} onRan={refresh} />
      </section>

      <section className="readiness__section fw-step" aria-labelledby="fw-step-sources">
        <h2 id="fw-step-sources" className="readiness__h">3 · Sources</h2>
        <SourcesStep firewall={selected} report={reportFor} />
      </section>
    </div>
  )
}
