import React, { useState, useEffect, useCallback, useMemo } from 'react'
import {
  getInfraModules,
  generateInfra,
  getInfraBundles,
  downloadInfraBundle,
} from '../../api/client.js'

/**
 * LabView — the Lab tab.
 *
 * Console-themed replacement for the legacy InfraGenerator. Drives the IaC
 * topology generator: provider/region → modules → params → generate → download.
 *
 * Layout:
 *   ▸ view head with title + provider segmented control + region input
 *   ▸ module picker (grid of cards; base is locked on)
 *   ▸ parameter block (grouped: identity, sizing, lifetime)
 *   ▸ generate action row with inline last-result chip
 *   ▸ recent bundles history table
 */

const DEFAULT_PARAMS = {
  project_name:     '',
  dc_ssh_cidr:      '',
  jumpbox_size:     't3.medium',
  k8s_node_count:   2,
  edr_target_count: 2,
  ttl_hours:        72,
}

const DEFAULT_REGION = 'us-east-1'

const PROVIDERS = [
  { id: 'aws',   label: 'AWS',   enabled: true,  note: '' },
  { id: 'gcp',   label: 'GCP',   enabled: false, note: 'Phase C — pending' },
  { id: 'azure', label: 'Azure', enabled: false, note: 'Phase D — pending' },
]

// Required modules are managed by the generator; surface them as locked-on.
const LOCKED_MODULES = new Set(['base'])

export default function LabView({ onError = () => {} }) {
  const [provider, setProvider]   = useState('aws')
  const [region, setRegion]       = useState(DEFAULT_REGION)
  const [modules, setModules]     = useState([])
  const [selected, setSelected]   = useState(new Set(['base']))
  const [params, setParams]       = useState(DEFAULT_PARAMS)
  const [bundles, setBundles]     = useState([])
  const [loading, setLoading]     = useState(false)
  const [generating, setGenerating] = useState(false)
  const [lastBundle, setLastBundle] = useState(null)

  // ── Data fetches ─────────────────────────────────────────────────────
  const refreshModules = useCallback(() => {
    setLoading(true)
    getInfraModules(provider)
      .then((d) => setModules(d.modules || []))
      .catch((e) => onError(e.message || 'Failed to load modules'))
      .finally(() => setLoading(false))
  }, [provider, onError])

  const refreshBundles = useCallback(() => {
    getInfraBundles()
      .then((d) => setBundles(d.bundles || []))
      .catch(() => { /* non-fatal */ })
  }, [])

  useEffect(() => { refreshModules() }, [refreshModules])
  useEffect(() => { refreshBundles() }, [refreshBundles])

  // ── Module toggle (with dependency awareness) ────────────────────────
  // See resolveModuleDependencies below — the policy decision lives there.
  const toggleModule = useCallback((name) => {
    if (LOCKED_MODULES.has(name)) return
    setSelected((prev) => resolveModuleDependencies(prev, name, modules))
  }, [modules])

  const updateParam = (key, value) => setParams((p) => ({ ...p, [key]: value }))

  // ── Submission validation ────────────────────────────────────────────
  const validationErrors = useMemo(() => {
    const errs = []
    if (!params.project_name.trim())   errs.push('project name required')
    if (!params.dc_ssh_cidr.trim())    errs.push('SSH CIDR required')
    if (params.project_name && !/^[a-z][a-z0-9-]{1,30}$/.test(params.project_name)) {
      errs.push('project name: lowercase, hyphens, 2-31 chars')
    }
    if (params.dc_ssh_cidr && !/^[\d.]+\/\d{1,2}$/.test(params.dc_ssh_cidr)) {
      errs.push('SSH CIDR: dotted-quad/mask (e.g. 203.0.113.0/32)')
    }
    return errs
  }, [params])

  const canGenerate = !generating && validationErrors.length === 0

  const handleGenerate = useCallback(async () => {
    if (!canGenerate) return
    setGenerating(true)
    try {
      const body = {
        provider,
        region,
        modules: Array.from(selected),
        params: {
          ...params,
          k8s_node_count:   Number(params.k8s_node_count),
          edr_target_count: Number(params.edr_target_count),
          ttl_hours:        Number(params.ttl_hours),
        },
      }
      const resp = await generateInfra(body)
      setLastBundle(resp)
      refreshBundles()
    } catch (e) {
      onError(e.message || 'Generation failed')
    } finally {
      setGenerating(false)
    }
  }, [canGenerate, provider, region, selected, params, refreshBundles, onError])

  const handleDownload = useCallback(async (bundleId) => {
    try {
      const blob = await downloadInfraBundle(bundleId)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `cortexsim-infra-${bundleId}.tar.gz`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
    } catch (e) {
      onError(e.message || 'Download failed')
    }
  }, [onError])

  return (
    <div className="lab">
      {/* ── Head ───────────────────────────────────────────────────── */}
      <div className="view-head">
        <div>
          <h1>Lab</h1>
          <div className="view-head__meta">
            IaC topology generator
            {' · '}<span className="mono">{modules.length} modules</span>
            {' · '}<span className="mono">{selected.size} selected</span>
            {' · '}<span className="mono">{bundles.length} bundle{bundles.length === 1 ? '' : 's'}</span>
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn" onClick={refreshModules} disabled={loading}>
            {loading ? 'Refreshing…' : 'Refresh modules'}
          </button>
        </div>
      </div>

      {/* ── Provider + Region ─────────────────────────────────────── */}
      <div className="lab__row">
        <div className="lab__field">
          <label className="lab__label">Cloud provider</label>
          <div className="lab__segmented">
            {PROVIDERS.map((p) => (
              <button
                key={p.id}
                type="button"
                className={provider === p.id ? 'is-active' : ''}
                disabled={!p.enabled}
                onClick={() => p.enabled && setProvider(p.id)}
                title={p.enabled ? '' : p.note}
              >
                {p.label}
              </button>
            ))}
          </div>
        </div>
        <div className="lab__field">
          <label className="lab__label" htmlFor="lab-region">Region</label>
          <input
            id="lab-region"
            type="text"
            value={region}
            onChange={(e) => setRegion(e.target.value)}
            className="lab__input mono"
            placeholder="us-east-1"
          />
        </div>
      </div>

      {/* ── Module picker ──────────────────────────────────────────── */}
      <div className="lab__section">
        <div className="lab__section-title">Modules</div>
        {loading && modules.length === 0 ? (
          <div className="lab__empty mono">loading modules…</div>
        ) : modules.length === 0 ? (
          <div className="lab__empty mono">
            no modules available for provider <strong>{provider}</strong>
          </div>
        ) : (
          <div className="lab__module-grid">
            {modules.map((m) => (
              <ModuleCard
                key={m.name}
                module={m}
                checked={selected.has(m.name)}
                locked={LOCKED_MODULES.has(m.name)}
                onToggle={() => toggleModule(m.name)}
              />
            ))}
          </div>
        )}
      </div>

      {/* ── Parameters ─────────────────────────────────────────────── */}
      <div className="lab__section">
        <div className="lab__section-title">Parameters</div>
        <div className="lab__param-grid">
          <ParamField
            label="Project name"
            hint="lowercase, hyphens, 2–31 chars"
            value={params.project_name}
            onChange={(v) => updateParam('project_name', v)}
            placeholder="acme-pov-2026"
            required
          />
          <ParamField
            label="Your SSH source"
            hint="CIDR (e.g. 203.0.113.0/32)"
            value={params.dc_ssh_cidr}
            onChange={(v) => updateParam('dc_ssh_cidr', v)}
            placeholder="203.0.113.0/32"
            required
          />
          <ParamField
            label="Jumpbox size"
            hint="EC2 / GCE instance type"
            value={params.jumpbox_size}
            onChange={(v) => updateParam('jumpbox_size', v)}
          />
          <ParamField
            label="K8s node count"
            hint="CDR plane node count"
            value={params.k8s_node_count}
            type="number"
            onChange={(v) => updateParam('k8s_node_count', v)}
          />
          <ParamField
            label="EDR target count"
            hint="Linux endpoints to provision"
            value={params.edr_target_count}
            type="number"
            onChange={(v) => updateParam('edr_target_count', v)}
          />
          <ParamField
            label="TTL hours"
            hint="lab lifetime (Torque hint)"
            value={params.ttl_hours}
            type="number"
            onChange={(v) => updateParam('ttl_hours', v)}
          />
        </div>
      </div>

      {/* ── Generate ───────────────────────────────────────────────── */}
      <div className="lab__generate-row">
        <button
          type="button"
          className="btn btn--primary"
          onClick={handleGenerate}
          disabled={!canGenerate}
          title={validationErrors.length ? validationErrors.join(' · ') : 'Generate IaC bundle'}
        >
          {generating ? 'Generating…' : 'Generate bundle'}
        </button>
        {validationErrors.length > 0 && !generating && (
          <span className="lab__validation mono">
            ! {validationErrors[0]}
          </span>
        )}
        {lastBundle && !generating && validationErrors.length === 0 && (
          <span className="lab__last-bundle mono">
            ✓ generated {String(lastBundle.bundle_id).slice(0, 12)}…
            <button
              type="button"
              className="btn"
              style={{ marginLeft: 10, height: 24 }}
              onClick={() => handleDownload(lastBundle.bundle_id)}
            >
              Download tar.gz
            </button>
          </span>
        )}
      </div>

      {/* ── Bundle history ─────────────────────────────────────────── */}
      {bundles.length > 0 && (
        <div className="lab__section">
          <div className="lab__section-title">Recent bundles</div>
          <div className="lab__bundles">
            <div className="lab__bundle-head">
              <div>Bundle ID</div>
              <div>Provider · modules</div>
              <div>Created</div>
              <div style={{ textAlign: 'right' }}>Size</div>
              <div></div>
            </div>
            {bundles.slice(0, 12).map((b) => (
              <BundleRow key={b.bundle_id} bundle={b} onDownload={handleDownload} />
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

/* ─── Subcomponents ────────────────────────────────────────────────── */

function ModuleCard({ module, checked, locked, onToggle }) {
  const deps = module.dependencies || []
  const tools = module.content_tools || []
  const toolsPreview = tools.slice(0, 4).join(' · ')
  const toolsMore = tools.length > 4 ? ` +${tools.length - 4}` : ''

  return (
    <label
      className={
        'lab-module' +
        (checked ? ' lab-module--checked' : '') +
        (locked  ? ' lab-module--locked'  : '')
      }
    >
      <input
        type="checkbox"
        checked={checked}
        disabled={locked}
        onChange={onToggle}
      />
      <div className="lab-module__body">
        <div className="lab-module__head">
          <span className="lab-module__name mono">{module.name}</span>
          {locked && <span className="chip chip--signal">required</span>}
          {deps.length > 0 && (
            <span className="lab-module__deps mono">
              ← {deps.join(' · ')}
            </span>
          )}
        </div>
        {module.description && (
          <div className="lab-module__desc">{module.description}</div>
        )}
        {tools.length > 0 && (
          <div className="lab-module__tools mono">
            {toolsPreview}{toolsMore}
          </div>
        )}
      </div>
    </label>
  )
}

function ParamField({ label, hint, value, onChange, placeholder, type = 'text', required = false }) {
  return (
    <div className="lab__field">
      <label className="lab__label">
        {label}
        {required && <span className="lab__required" aria-hidden="true"> *</span>}
      </label>
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className={'lab__input' + (type === 'number' ? ' mono' : '')}
      />
      {hint && <div className="lab__hint mono">{hint}</div>}
    </div>
  )
}

function BundleRow({ bundle, onDownload }) {
  const created = bundle.created_at ? bundle.created_at.slice(0, 19).replace('T', ' ') : '—'
  const sizeKb = bundle.size_bytes ? Math.round(bundle.size_bytes / 1024) : 0
  const mods = Array.isArray(bundle.modules) ? bundle.modules.join(' · ') : '—'

  return (
    <div className="lab__bundle-row">
      <div className="lab__bundle-id mono">{String(bundle.bundle_id).slice(0, 12)}…</div>
      <div className="lab__bundle-meta mono">{bundle.provider} · {mods}</div>
      <div className="lab__bundle-created mono">{created}</div>
      <div className="lab__bundle-size mono">{sizeKb} KB</div>
      <button
        type="button"
        className="btn"
        style={{ height: 26 }}
        onClick={() => onDownload(bundle.bundle_id)}
      >
        Download
      </button>
    </div>
  )
}

/* ─── Dependency resolution ────────────────────────────────────────── */

/**
 * resolveModuleDependencies — when a module is toggled, return the new selected set.
 *
 * Policy (chosen by operator):
 *   CHECK   → Option A · AUTO-SELECT: also check any modules listed in
 *             `module.dependencies`, transitively. Bundles always build.
 *   UNCHECK → Option Y · LEAVE ALONE: just remove the module. The DC may end
 *             up with an invalid set (e.g. removed something a still-checked
 *             module depends on) — the backend will reject on generate.
 *             We surface that rejection through the existing onError path.
 *
 * The walk is BFS so transitive deps of any depth are picked up, with a
 * `next.has(name)` short-circuit that doubles as cycle protection.
 */
export function resolveModuleDependencies(prev, toggledName, allModules) {
  const next = new Set(prev)

  // Uncheck path — don't cascade dependents.
  if (next.has(toggledName)) {
    next.delete(toggledName)
    return next
  }

  // Check path — BFS over the dependency graph, adding everything we touch.
  const moduleByName = new Map((allModules || []).map((m) => [m.name, m]))
  const queue = [toggledName]
  while (queue.length > 0) {
    const name = queue.shift()
    if (next.has(name)) continue           // visited / cycle guard
    next.add(name)
    const mod = moduleByName.get(name)
    const deps = mod && Array.isArray(mod.dependencies) ? mod.dependencies : []
    for (const dep of deps) {
      if (!next.has(dep)) queue.push(dep)
    }
  }
  return next
}
