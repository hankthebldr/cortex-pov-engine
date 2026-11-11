import React from 'react'
import SwitcherPill from './SwitcherPill.jsx'
import { useEnvironment } from '../../../context/EnvironmentContext.jsx'

/**
 * TenantSwitcher — an active-XSIAM-tenant switcher pill for a global bar.
 *
 * NOT MOUNTED. Nothing in the console renders this component (checked
 * 2026-11-10: only tests import it). The header's tenant chip is in
 * ConsoleHeader.jsx. A change here changes nothing on screen until something
 * mounts it.
 *
 * Reads the tenant list + active tenant + composed health from the provider;
 * selecting writes through to the provider (which persists + re-scopes health).
 * The "Manage tenants…" footer deep-links to the Tenants destination.
 */
function rawStatus(tenant, health) {
  const raw =
    (health && health.tenantHealth && health.tenantHealth.status) ||
    tenant?.status ||
    null
  return raw ? String(raw) : null
}

function tenantStatus(tenant, health) {
  const raw = rawStatus(tenant, health)
  if (!raw) return null
  const s = raw.toLowerCase()
  if (s.includes('ok') || s.includes('connect') || s.includes('healthy') || s.includes('verified')) return 'healthy'
  if (s.includes('degrad') || s.includes('warn') || s.includes('stale')) return 'warn'
  return 'bad'
}

/**
 * The status in words, for the pill's accessible name and the word beside the
 * dot (UI-22). SimCore's own word is shown as it came. A tenant with no
 * reported status says so: it is not healthy, it is unreported.
 */
function tenantStatusLabel(tenant, health) {
  const raw = rawStatus(tenant, health)
  return raw ? raw.toLowerCase() : 'status not reported'
}

export default function TenantSwitcher({ onManage = () => {} }) {
  const { tenant, tenants, setTenant, health } = useEnvironment()

  const options = tenants.map((t) => {
    const scoped = tenant && (t.name || t.id) === (tenant.name || tenant.id) ? health : null
    return {
      id: t.name || t.id,
      label: t.name || t.id,
      meta: t.config?.region || t.region || t.base_url || t.config?.base_url || '',
      status: tenantStatus(t, scoped),
      statusLabel: tenantStatusLabel(t, scoped),
    }
  })

  return (
    <SwitcherPill
      kind="tenant"
      label={tenant ? (tenant.name || tenant.id) : 'no tenant'}
      status={tenantStatus(tenant, health)}
      statusLabel={tenant ? tenantStatusLabel(tenant, health) : null}
      empty={!tenant}
      options={options}
      activeId={tenant ? (tenant.name || tenant.id) : null}
      onSelect={setTenant}
      manageLabel="Manage tenants…"
      onManage={onManage}
    />
  )
}
