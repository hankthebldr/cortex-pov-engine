import React from 'react'
import SwitcherPill from './SwitcherPill.jsx'
import { useEnvironment } from '../../../context/EnvironmentContext.jsx'

/**
 * TenantSwitcher — always-visible active-XSIAM-tenant pill in the global bar.
 *
 * Reads the tenant list + active tenant + composed health from the provider;
 * selecting writes through to the provider (which persists + re-scopes health).
 * The "Manage tenants…" footer deep-links to the Tenants destination.
 */
function tenantStatus(tenant, health) {
  const raw =
    (health && health.tenantHealth && health.tenantHealth.status) ||
    tenant?.status ||
    null
  if (!raw) return null
  const s = String(raw).toLowerCase()
  if (s.includes('ok') || s.includes('connect') || s.includes('healthy') || s.includes('verified')) return 'healthy'
  if (s.includes('degrad') || s.includes('warn') || s.includes('stale')) return 'warn'
  return 'bad'
}

export default function TenantSwitcher({ onManage = () => {} }) {
  const { tenant, tenants, setTenant, health } = useEnvironment()

  const options = tenants.map((t) => ({
    id: t.name || t.id,
    label: t.name || t.id,
    meta: t.config?.region || t.region || t.base_url || t.config?.base_url || '',
    status: tenantStatus(t, tenant && (t.name || t.id) === (tenant.name || tenant.id) ? health : null),
  }))

  return (
    <SwitcherPill
      kind="tenant"
      label={tenant ? (tenant.name || tenant.id) : 'no tenant'}
      status={tenantStatus(tenant, health)}
      empty={!tenant}
      options={options}
      activeId={tenant ? (tenant.name || tenant.id) : null}
      onSelect={setTenant}
      manageLabel="Manage tenants…"
      onManage={onManage}
    />
  )
}
