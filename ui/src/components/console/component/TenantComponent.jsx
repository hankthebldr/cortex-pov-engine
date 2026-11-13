import React, { useCallback, useMemo, useState } from 'react'
import { useEnvironment } from '../../../context/EnvironmentContext.jsx'
import { listXsiamTenants, preflightXsiamTenant } from '../../../api/client.js'
import { serverDate } from '../../../api/time.js'
import ComponentPage from './ComponentPage.jsx'
import ConfigureFrame from './ConfigureFrame.jsx'
import TenantManager from '../TenantManager.jsx'
import CollectorTargetsPanel from '../CollectorTargetsPanel.jsx'
import useComponentStatus from '../useComponentStatus.js'
import { componentByKey } from '../componentModel.js'
import { configState, headerResult, reportResult } from './checks.js'

/**
 * Tenant — the Cortex tenant POVengine reads detections back from (design
 * v26 §4.2, slice 3). Read-only, always: POVengine never writes to Cortex.
 *
 * Overview: one service row per stored key, from the tenant's last
 * connection test. Setup: the four v26 steps; "Test the connection" and
 * "Check datasets and clock" run the staged, read-only connector preflight
 * (config → dns/tls → auth → scope → datasets → clock), and a report nothing
 * answered reads Can't check. Read-back & integrations: today's tenant form
 * and the collector targets, unchanged.
 *
 * Deviation from the design: v26 asks for an "Advanced" key. The tenant form
 * on this build offers Standard keys only (Advanced is marked unavailable),
 * so step 1 says Standard, read-only.
 */

const KEY = 'tenant'

function when(iso) {
  const d = serverDate(iso)
  return d ? d.toLocaleString() : null
}

export default function TenantComponent({ params = {}, setParams = () => {}, onNavigate = () => {} }) {
  const env = useEnvironment()
  const { statuses } = useComponentStatus()
  const meta = componentByKey(KEY)
  const tenants = useMemo(() => env.tenants || [], [env.tenants])
  const active = env.tenant || tenants[0] || null
  const name = active ? (active.name || active.id) : null
  // The last staged check this session ran. Current state only; never kept.
  const [report, setReport] = useState(null)

  const runPreflight = useCallback(async () => {
    if (!name) return null
    const r = await preflightXsiamTenant(name)
    setReport(r)
    return r
  }, [name])

  const noKey = { state: 'pending', detail: 'Store a key first (step 2).' }

  const steps = useMemo(() => [
    {
      id: 'key',
      title: 'Create a read-only API key in Cortex',
      why: 'Create a Standard API key with a read-only role: alerts, incidents and cases, and XQL. POVengine never writes to the tenant.',
      where: 'Cortex › Settings › Configurations › Integrations › API Keys',
      verifier: 'you',
      fields: [['Security level', 'Standard'], ['Role', 'read-only (Viewer)'], ['Copy', 'the key, its key ID and the API URL']],
    },
    {
      id: 'add',
      title: 'Add it to POVengine',
      why: 'The key goes to the encrypted vault and is never shown again. A URL that is not a Cortex tenant is refused before the key is stored.',
      verifier: 'pov',
      body: () => <TenantManager embedded />,
      derive: ({ env: e }) => ((e.tenants || []).length
        ? { state: 'passed', detail: `Stored: ${(e.tenants || []).map((t) => t.name || t.id).join(', ')}` }
        : null),
      test: async ({ env: e }) => {
        const list = await listXsiamTenants()
        e.refreshTenants && e.refreshTenants()
        return list.length
          ? { state: 'passed', detail: `Stored: ${list.map((t) => t.name || t.id).join(', ')}` }
          : { state: 'failed', issue: 'No key stored', detail: 'POVengine holds no tenant key yet.' }
      },
    },
    {
      id: 'test',
      title: 'Test the connection',
      why: 'One staged check: reach the tenant, TLS, sign in, what the key may read, and the clock. Every stage reports, even if an earlier one fails.',
      verifier: 'pov',
      fields: name ? [['Tenant', name]] : [],
      test: async () => {
        if (!name) return noKey
        return reportResult(await runPreflight(), { step: 'test', okDetail: 'The tenant answered every stage.' })
      },
    },
    {
      id: 'datasets',
      title: 'Check datasets and clock',
      why: 'The datasets the scenarios read must exist, and the tenant clock must be within 5 seconds — detection time is measured across both clocks.',
      verifier: 'pov',
      test: async () => {
        if (!name) return noKey
        return reportResult(await runPreflight(), { ids: ['datasets', 'clock'], step: 'datasets' })
      },
    },
  ], [name, runPreflight]) // eslint-disable-line react-hooks/exhaustive-deps

  const services = tenants.map((t) => {
    const n = t.name || t.id
    const at = when(t.last_verified_at)
    if (t.last_verified_ok === true) {
      return { id: n, name: `API key · ${n}`, status: 'up', since: `last test passed${at ? ` · ${at}` : ''}`, drill: { tab: 'readback' } }
    }
    if (t.last_verified_ok === false) {
      return { id: n, name: `API key · ${n}`, status: 'down', since: `last test failed${t.last_verified_error ? ` — ${t.last_verified_error}` : ''}`, drill: { tab: 'setup', step: 'test' } }
    }
    return { id: n, name: `API key · ${n}`, status: 'unknown', since: 'stored, never tested', drill: { tab: 'setup', step: 'test' } }
  })

  const lastTest = active?.last_verified_ok
  const scope = configState(report, ['scope'])
  const dataClock = configState(report, ['datasets', 'clock'])
  const config = [
    {
      id: 'stored',
      label: 'A read-only API key is stored',
      state: tenants.length ? 'ok' : 'missing',
      detail: tenants.length ? tenants.map((t) => t.name || t.id).join(', ') : 'No tenant connected',
      drill: { tab: 'setup', step: 'add' },
    },
    {
      id: 'last-test',
      label: 'The last connection test passed',
      state: !tenants.length ? 'missing' : lastTest === true ? 'ok' : lastTest === false ? 'missing' : 'cant_check',
      detail: lastTest === false ? active?.last_verified_error || 'failed' : lastTest == null && tenants.length ? 'Never tested' : undefined,
      drill: { tab: 'setup', step: 'test' },
    },
    { id: 'scope', label: 'What the key may read', ...scope, drill: { tab: 'setup', step: 'test' } },
    { id: 'datasets', label: 'Datasets present, clock within 5 s', ...dataClock, drill: { tab: 'setup', step: 'datasets' } },
  ]

  const onTest = useCallback(async () => {
    if (!name) return { state: 'failed', issue: 'No tenant connected', step: 'add' }
    return headerResult(await runPreflight(), { step: 'test' })
  }, [name, runPreflight])

  return (
    <ComponentPage
      componentKey={KEY}
      name={meta.name}
      icon={meta.icon}
      status={statuses[KEY]}
      issueStep="test"
      services={services}
      servicesEmpty="No tenant key is stored."
      config={config}
      manageTab={{
        id: 'readback',
        label: 'Read-back & integrations',
        render: () => (
          <div className="cmp-manage__stack">
            <TenantManager embedded />
            <CollectorTargetsPanel />
          </div>
        ),
      }}
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
      testId="component-tenant"
    />
  )
}
