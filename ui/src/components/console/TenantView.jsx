import React from 'react'
import { useEnvironment } from '../../context/EnvironmentContext.jsx'
import TenantManager from './TenantManager.jsx'

/**
 * TenantView — the Cortex tenant this instance reads detections back from.
 *
 * Real state only. The previous page rendered a fixed "Acme Financial" tenant
 * with nine canned health checks, four tabs of read-back surface and a binding
 * wizard — all from the design prototype's seed catalog — while the header,
 * reading the actual provider, said "no tenant bound". Two answers to "which
 * tenant is this" on one screen is the worst kind of wrong, so the page now
 * shows only what SimCore holds: the registered tenants, their test results,
 * and the form to add one (TenantManager, the same component Get started uses).
 */
export default function TenantView() {
  const { tenants } = useEnvironment()
  return (
    <div className="pov-page" data-testid="tenant-view">
      <h1 className="pov-page__title">Tenant</h1>
      <p className="pov-page__lede">
        {tenants.length
          ? 'The Cortex tenant POVengine queries to check which expected detections fired. Keys are read-only.'
          : 'No tenant is connected yet. Add a read-only API key below so POVengine can check which detections fired.'}
      </p>
      <TenantManager embedded />
    </div>
  )
}
