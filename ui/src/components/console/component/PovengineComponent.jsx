import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { useEnvironment } from '../../../context/EnvironmentContext.jsx'
import { getHealth } from '../../../api/client.js'
import ComponentPage from './ComponentPage.jsx'
import ConfigureFrame from './ConfigureFrame.jsx'
import useComponentStatus from '../useComponentStatus.js'
import { componentByKey } from '../componentModel.js'
import { buildHealthModel } from '../readiness/healthModel.js'
import { serverHostVerdict } from '../deployCommands.js'

/**
 * POVengine — this console and its API, as a component (design v26 §4.2,
 * slice 4). Everything here is read from this instance's own /api/health,
 * which makes no outbound calls.
 *
 * API tab: the route groups are GENERATED from this POVengine's own OpenAPI
 * document (GET /api/openapi.json), grouped by their /api/<group> prefix —
 * not the design canvas's hand grouping (v26 §10.5). A document that cannot
 * be read says so; no list is invented in its place.
 */

const KEY = 'pov'
const HS = { ok: 'up', degraded: 'degraded', error: 'down', unreported: 'unknown' }
const CFG = { ok: 'ok', degraded: 'changed', error: 'missing', unreported: 'cant_check' }
const METHODS = ['get', 'post', 'put', 'patch', 'delete']

function component(health, key) {
  return (health?.components || []).find((c) => c.key === key) || null
}

function healthResult(model) {
  if (!model || !model.reachable) return { state: 'failed', issue: 'Not answering', detail: model?.unreachableReason || 'No answer from /api/health.' }
  const bad = (model.components || []).filter((c) => c.status === 'degraded' || c.status === 'error')
  const rows = bad.map((c) => ({
    label: c.label || c.key,
    state: c.status === 'error' ? 'failed' : 'degraded',
    detail: [c.detail, c.remediation].filter(Boolean).join(' — ') || undefined,
  }))
  if (!bad.length && !(model.degraded || []).length) {
    return { state: 'passed', detail: model.gaps?.length ? `Every reported component ok; ${model.gaps.length} not reported by this build.` : 'Every component ok.' }
  }
  return {
    state: 'failed',
    issue: `${Math.max(bad.length, (model.degraded || []).length)} not ok`,
    detail: `Not ok: ${bad.map((c) => c.label || c.key).join(', ') || (model.degraded || []).join(', ')}. A zero counts as degraded, not ok.`,
    rows,
  }
}

/** Route groups from an OpenAPI document: one row per /api/<group> prefix. */
export function routeGroups(doc) {
  const groups = new Map()
  for (const [path, item] of Object.entries(doc?.paths || {})) {
    const m = path.match(/^\/api\/([^/]+)/)
    if (!m) continue
    const prefix = `/api/${m[1]}`
    if (!groups.has(prefix)) groups.set(prefix, { prefix, ops: [] })
    for (const method of METHODS) {
      if (item && item[method]) groups.get(prefix).ops.push({ method: method.toUpperCase(), path, summary: item[method].summary || '' })
    }
  }
  return [...groups.values()]
    .filter((g) => g.ops.length)
    .sort((a, b) => b.ops.length - a.ops.length || a.prefix.localeCompare(b.prefix))
}

function ApiTab() {
  const [state, setState] = useState({ loading: true, groups: null, error: null })
  useEffect(() => {
    let cancelled = false
    fetch('/api/openapi.json')
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((doc) => { if (!cancelled) setState({ loading: false, groups: routeGroups(doc), error: null }) })
      .catch((err) => { if (!cancelled) setState({ loading: false, groups: null, error: err?.message || 'request failed' }) })
    return () => { cancelled = true }
  }, [])

  if (state.loading) return <p className="cmp-card__empty">Reading this POVengine&apos;s API description…</p>
  if (state.error) {
    return (
      <p className="cmp-card__empty" role="status">
        Could not read this POVengine&apos;s API description (GET /api/openapi.json: {state.error}), so no route list is shown.
      </p>
    )
  }
  const total = state.groups.reduce((n, g) => n + g.ops.length, 0)
  return (
    <section className="cmp-card" aria-label="API">
      <h2 className="cmp-card__title">
        API
        <span className="cmp-card__count">{total} operations in {state.groups.length} groups · from GET /api/openapi.json</span>
      </h2>
      <table className="cmp-api" aria-label="Route groups">
        <thead>
          <tr><th scope="col">Group</th><th scope="col">Operations</th><th scope="col">Endpoints</th></tr>
        </thead>
        <tbody>
          {state.groups.map((g) => (
            <tr key={g.prefix}>
              <td className="mono">{g.prefix}</td>
              <td className="mono">{g.ops.length}</td>
              <td>
                <details>
                  <summary>{[...new Set(g.ops.map((o) => o.method))].join(' · ')}</summary>
                  <ul className="cmp-api__ops">
                    {g.ops.map((o) => (
                      <li key={`${o.method} ${o.path}`}>
                        <span className="mono cmp-api__method">{o.method}</span>
                        <span className="mono">{o.path}</span>
                        {o.summary && <span className="cmp-api__summary">{o.summary}</span>}
                      </li>
                    ))}
                  </ul>
                </details>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="cmp-card__empty">Interactive reference: <a href="/api/docs" target="_blank" rel="noreferrer">/api/docs</a></p>
    </section>
  )
}

export default function PovengineComponent({ params = {}, setParams = () => {}, onNavigate = () => {} }) {
  const env = useEnvironment()
  const { statuses } = useComponentStatus()
  const meta = componentByKey(KEY)
  const health = env.healthModel
  const origin = typeof window !== 'undefined' ? window.location.origin : ''
  const consoleHost = typeof window !== 'undefined' ? window.location.hostname : ''

  const steps = useMemo(() => [
    {
      id: 'host',
      title: 'Prepare the host',
      why: 'A Linux host in the lab with Docker Engine and Compose v2. If you are reading this page, the host is already running POVengine.',
      verifier: 'pov',
      fields: [['Needs', 'Docker Engine · Compose v2']],
      command: 'docker compose version   # must report v2',
      derive: ({ env: e }) => (e.healthModel?.reachable ? { state: 'passed', detail: 'This page is served by POVengine on this host.' } : null),
    },
    {
      id: 'key',
      title: 'Set the master key',
      why: 'Copy the example settings and set the key that encrypts stored credentials (tenant keys, firewall keys, collector keys).',
      where: 'repo root',
      verifier: 'pov',
      fields: [['File', '.env (from .env.example)'], ['CORTEXSIM_SECRET', 'required']],
      command: 'cp .env.example .env',
      derive: ({ env: e }) => {
        const c = component(e.healthModel, 'master_key')
        if (!c) return null
        if (c.status === 'ok') return { state: 'passed', detail: 'The credential master key is set and opens the vault.' }
        if (c.status === 'unreported') return { state: 'cant_check', detail: c.detail || 'This build does not report the master key.' }
        return { state: 'failed', issue: c.code || 'Master key', detail: [c.detail, c.remediation].filter(Boolean).join(' — ') }
      },
    },
    {
      id: 'name',
      title: 'Name the host',
      why: 'Every name agents and browsers use must be allowed, or requests are refused with HOST_NOT_ALLOWED before anything is served.',
      where: '.env',
      verifier: 'pov',
      fields: [['CORTEXSIM_ALLOWED_HOSTS', 'the names agents use, comma-separated'], ['This console is open at', origin]],
      command: './scripts/dev-up.sh --apply   # writes a missing CORTEXSIM_ALLOWED_HOSTS line',
      derive: ({ env: e }) => {
        const v = serverHostVerdict(e.healthModel, origin, consoleHost)
        if (v?.state === 'admitted') return { state: 'passed', detail: `POVengine answers to ${v.host}.` }
        if (v?.state === 'not_listed' && v.certain) return { state: 'failed', issue: 'HOST_NOT_ALLOWED', detail: `POVengine refuses ${v.host}; add it to CORTEXSIM_ALLOWED_HOSTS.` }
        return null
      },
    },
    {
      id: 'start',
      title: 'Start POVengine',
      why: 'Builds the image — the beacon binaries for every target included — and starts the console.',
      verifier: 'pov',
      fields: [['Console', origin]],
      command: './scripts/dev-up.sh',
      derive: ({ env: e }) => (e.healthModel?.reachable
        ? { state: 'passed', detail: `Answering at ${origin}${e.healthModel.version ? ` · version ${e.healthModel.version}` : ''}.` }
        : null),
    },
    {
      id: 'health',
      title: 'Check health',
      why: 'Every component must report ok — a zero counts as degraded, not ok. Health makes no outbound calls.',
      verifier: 'pov',
      fields: [['Reads', 'GET /api/health']],
      derive: ({ env: e }) => (e.healthModel ? healthResult(e.healthModel) : null),
      test: async ({ env: e }) => {
        e.refreshHealth && e.refreshHealth()
        try {
          return healthResult(buildHealthModel(await getHealth()))
        } catch (err) {
          return healthResult(buildHealthModel(null, { error: err?.message }))
        }
      },
    },
  ], [origin, consoleHost])

  const services = [
    {
      id: 'api',
      name: 'Web console & API',
      status: !health ? 'unknown' : health.reachable ? 'up' : 'down',
      since: health?.reachable ? `answering /api/health${health.version ? ` · v${health.version}` : ''}` : 'no answer yet',
      drill: { tab: 'setup', step: 'health' },
    },
    ...['db', 'task_queue', 'payload_shelf', 'agent_binaries'].map((k) => {
      const c = component(health, k)
      return {
        id: k,
        name: c?.label || k,
        status: c ? (HS[c.status] || 'unknown') : 'unknown',
        since: c ? (c.detail || c.status) : 'not reported',
        drill: { tab: 'setup', step: 'health' },
      }
    }),
  ]

  const cfg = (id, label, key, step) => {
    const c = component(health, key)
    return {
      id,
      label,
      state: c ? (CFG[c.status] || 'cant_check') : 'cant_check',
      detail: c ? (c.detail || undefined) : 'Not reported by /api/health',
      drill: { tab: 'setup', step },
    }
  }
  const hostVerdict = serverHostVerdict(health, origin, consoleHost)
  const config = [
    cfg('key', 'Master key set (CORTEXSIM_SECRET)', 'master_key', 'key'),
    {
      id: 'hosts',
      label: 'This console\'s address is an allowed host',
      state: hostVerdict?.state === 'admitted' ? 'ok' : hostVerdict?.state === 'not_listed' && hostVerdict.certain ? 'missing' : 'cant_check',
      detail: hostVerdict?.host || undefined,
      drill: { tab: 'setup', step: 'name' },
    },
    cfg('binaries', 'Beacon binaries in the image', 'agent_binaries', 'start'),
    cfg('tools', 'Tool catalog loaded (tools/ in the image)', 'adapter_catalog', 'start'),
    cfg('scenarios', 'Scenario library loaded', 'scenario_catalog', 'start'),
  ]

  const onTest = useCallback(async () => {
    env.refreshHealth && env.refreshHealth()
    let model
    try { model = buildHealthModel(await getHealth()) } catch (err) { model = buildHealthModel(null, { error: err?.message }) }
    const r = healthResult(model)
    return { state: r.state, issue: r.issue, step: 'health' }
  }, [env])

  return (
    <ComponentPage
      componentKey={KEY}
      name={meta.name}
      icon={meta.icon}
      status={statuses[KEY]}
      issueStep="health"
      services={services}
      config={config}
      manageTab={{ id: 'api', label: 'API', render: () => <ApiTab /> }}
      setup={() => (
        <ConfigureFrame
          confirmPrefix={KEY}
          steps={steps}
          current={params.step || null}
          onSelect={(step) => setParams({ step })}
          ctx={{ env }}
        />
      )}
      onTest={onTest}
      params={params}
      setParams={setParams}
      onNavigate={onNavigate}
      testId="component-pov"
    />
  )
}
