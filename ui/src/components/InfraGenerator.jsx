import React, { useState, useEffect, useCallback } from 'react'
import {
  getInfraModules,
  generateInfra,
  getInfraBundles,
  downloadInfraBundle,
} from '../api/client.js'

const DEFAULT_PARAMS = {
  project_name: '',
  dc_ssh_cidr: '',
  jumpbox_size: 't3.medium',
  k8s_node_count: 2,
  edr_target_count: 2,
  ttl_hours: 72,
}

const DEFAULT_REGION = 'us-east-1'

function ModuleCard({ module, checked, onToggle }) {
  const isBase = module.name === 'base'
  return (
    <label style={{
      display: 'flex',
      gap: '10px',
      padding: '12px',
      border: checked ? '2px solid var(--cortex-teal)' : '1px solid var(--cortex-border)',
      borderRadius: '6px',
      cursor: isBase ? 'not-allowed' : 'pointer',
      background: checked ? 'rgba(0,192,232,0.08)' : 'white',
      opacity: isBase ? 0.85 : 1,
      transition: 'border-color 0.12s',
    }}>
      <input
        type="checkbox"
        checked={checked}
        disabled={isBase}
        onChange={onToggle}
        style={{ marginTop: '4px' }}
      />
      <div style={{ flex: 1 }}>
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center', marginBottom: '3px' }}>
          <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 700, color: 'var(--cortex-navy)' }}>
            {module.name}
          </span>
          {isBase && <span className="badge badge-steel" style={{ fontSize: '10px' }}>required</span>}
          {module.dependencies?.length > 0 && (
            <span style={{ fontSize: '11px', color: 'var(--cortex-steel)' }}>
              requires: {module.dependencies.join(', ')}
            </span>
          )}
        </div>
        <div style={{ fontSize: '12px', color: 'var(--cortex-navy)', marginBottom: '4px' }}>
          {module.description}
        </div>
        {module.content_tools?.length > 0 && (
          <div style={{ fontSize: '11px', color: 'var(--cortex-steel)' }}>
            Content: {module.content_tools.slice(0, 5).join(', ')}
            {module.content_tools.length > 5 && ` +${module.content_tools.length - 5} more`}
          </div>
        )}
      </div>
    </label>
  )
}

function BundleRow({ bundle, onDownload }) {
  return (
    <div style={{
      display: 'flex', gap: '12px', alignItems: 'center',
      padding: '8px 0', borderBottom: '1px solid var(--cortex-border)',
    }}>
      <div style={{ flex: 1 }}>
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', color: 'var(--cortex-teal)' }}>
          {bundle.bundle_id}
        </div>
        <div style={{ fontSize: '11px', color: 'var(--cortex-steel)' }}>
          {bundle.provider} · {bundle.modules.join(', ')} · {bundle.created_at?.slice(0, 16) || 'n/a'}
          {' · '}{Math.round((bundle.size_bytes || 0) / 1024)} KB
        </div>
      </div>
      <button className="btn btn-sm btn-secondary" onClick={() => onDownload(bundle.bundle_id)}>
        &#8681; Download
      </button>
    </div>
  )
}

export default function InfraGenerator() {
  const [provider, setProvider] = useState('aws')
  const [region, setRegion] = useState(DEFAULT_REGION)
  const [modules, setModules] = useState([])
  const [selected, setSelected] = useState(new Set(['base']))
  const [params, setParams] = useState(DEFAULT_PARAMS)
  const [bundles, setBundles] = useState([])
  const [loading, setLoading] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [lastBundle, setLastBundle] = useState(null)
  const [error, setError] = useState(null)

  const refreshModules = useCallback(() => {
    setLoading(true)
    getInfraModules(provider)
      .then(d => setModules(d.modules || []))
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [provider])

  const refreshBundles = useCallback(() => {
    getInfraBundles().then(d => setBundles(d.bundles || [])).catch(() => {})
  }, [])

  useEffect(() => { refreshModules() }, [refreshModules])
  useEffect(() => { refreshBundles() }, [refreshBundles])

  const toggleModule = (name) => {
    if (name === 'base') return
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(name)) next.delete(name); else next.add(name)
      return next
    })
  }

  const updateParam = (key, value) => setParams(p => ({ ...p, [key]: value }))

  const handleGenerate = async () => {
    setError(null)
    setGenerating(true)
    try {
      const body = {
        provider,
        region,
        modules: Array.from(selected),
        params: {
          ...params,
          k8s_node_count: Number(params.k8s_node_count),
          edr_target_count: Number(params.edr_target_count),
          ttl_hours: Number(params.ttl_hours),
        },
      }
      const resp = await generateInfra(body)
      setLastBundle(resp)
      refreshBundles()
    } catch (e) {
      setError(e.message)
    } finally {
      setGenerating(false)
    }
  }

  const handleDownload = async (bundleId) => {
    try {
      const blob = await downloadInfraBundle(bundleId)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `cortexsim-infra-${bundleId}.tar.gz`
      a.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      setError(e.message)
    }
  }

  const canGenerate = !generating && params.project_name.trim() && params.dc_ssh_cidr.trim()

  return (
    <div className="panel-card">
      <div className="panel-card-header">
        <h3>Deploy POV Infrastructure (IaC Generator)</h3>
        <button className="btn btn-secondary btn-sm" onClick={refreshModules} disabled={loading}>
          {loading ? <span className="spinner" /> : '⟳ Refresh'}
        </button>
      </div>

      <div className="panel-card-body">
        {error && (
          <div style={{ padding: '10px', background: '#FEF0F0', border: '1px solid var(--cortex-danger)',
                       borderRadius: '4px', color: 'var(--cortex-danger)', fontSize: '12px', marginBottom: '12px' }}>
            {error}
          </div>
        )}

        {/* Provider + Region */}
        <div style={{ display: 'flex', gap: '16px', marginBottom: '16px' }}>
          <div style={{ flex: 1 }}>
            <label style={{ display: 'block', fontSize: '11px', fontWeight: 600, marginBottom: '4px',
                           textTransform: 'uppercase', letterSpacing: '0.4px', color: 'var(--cortex-steel)' }}>
              Cloud Provider
            </label>
            <div style={{ display: 'flex', gap: '6px' }}>
              {['aws', 'gcp', 'azure'].map(p => (
                <button
                  key={p}
                  className={`btn btn-sm ${provider === p ? '' : 'btn-secondary'}`}
                  onClick={() => setProvider(p)}
                  disabled={p !== 'aws'}
                  title={p === 'aws' ? '' : 'Coming in a future phase'}
                  style={{ textTransform: 'uppercase' }}
                >
                  {p}
                </button>
              ))}
            </div>
          </div>
          <div style={{ flex: 1 }}>
            <label style={{ display: 'block', fontSize: '11px', fontWeight: 600, marginBottom: '4px',
                           textTransform: 'uppercase', letterSpacing: '0.4px', color: 'var(--cortex-steel)' }}>
              Region
            </label>
            <input type="text" value={region} onChange={e => setRegion(e.target.value)}
              style={{ width: '100%', padding: '6px 8px', border: '1px solid var(--cortex-border)',
                      borderRadius: '4px', fontFamily: 'var(--font-mono)', fontSize: '12px' }} />
          </div>
        </div>

        {/* Modules */}
        <div style={{ marginBottom: '16px' }}>
          <p className="section-label">Modules</p>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))',
                       gap: '8px' }}>
            {modules.map(m => (
              <ModuleCard
                key={m.name}
                module={m}
                checked={selected.has(m.name)}
                onToggle={() => toggleModule(m.name)}
              />
            ))}
          </div>
        </div>

        {/* Params */}
        <div style={{ marginBottom: '16px' }}>
          <p className="section-label">Parameters</p>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '10px' }}>
            <LabeledInput label="Project name (lowercase, hyphens)" value={params.project_name}
              onChange={v => updateParam('project_name', v)} placeholder="acme-pov-2026" required />
            <LabeledInput label="Your IP for SSH (CIDR)" value={params.dc_ssh_cidr}
              onChange={v => updateParam('dc_ssh_cidr', v)} placeholder="203.0.113.0/32" required />
            <LabeledInput label="Jumpbox instance type" value={params.jumpbox_size}
              onChange={v => updateParam('jumpbox_size', v)} />
            <LabeledInput label="K8s node count (CDR)" value={params.k8s_node_count}
              type="number" onChange={v => updateParam('k8s_node_count', v)} />
            <LabeledInput label="EDR target count" value={params.edr_target_count}
              type="number" onChange={v => updateParam('edr_target_count', v)} />
            <LabeledInput label="TTL hours (Torque hint)" value={params.ttl_hours}
              type="number" onChange={v => updateParam('ttl_hours', v)} />
          </div>
        </div>

        {/* Generate */}
        <div style={{ display: 'flex', gap: '10px', alignItems: 'center', marginBottom: '16px' }}>
          <button className="btn" onClick={handleGenerate} disabled={!canGenerate}>
            {generating ? <span className="spinner" /> : '⚙ Generate Bundle'}
          </button>
          {lastBundle && (
            <span style={{ fontSize: '12px', color: 'var(--cortex-success)', fontFamily: 'var(--font-mono)' }}>
              ✓ Generated {lastBundle.bundle_id.slice(0, 8)}…
              <button className="btn btn-sm btn-secondary" style={{ marginLeft: '8px' }}
                onClick={() => handleDownload(lastBundle.bundle_id)}>
                Download now
              </button>
            </span>
          )}
        </div>

        {/* Bundle history */}
        {bundles.length > 0 && (
          <div>
            <p className="section-label">Recent Bundles</p>
            {bundles.map(b => <BundleRow key={b.bundle_id} bundle={b} onDownload={handleDownload} />)}
          </div>
        )}
      </div>
    </div>
  )
}

function LabeledInput({ label, value, onChange, placeholder, type = 'text', required = false }) {
  return (
    <div>
      <label style={{
        display: 'block', fontSize: '11px', fontWeight: 600, marginBottom: '3px',
        textTransform: 'uppercase', letterSpacing: '0.4px', color: 'var(--cortex-steel)',
      }}>
        {label}{required && <span style={{ color: 'var(--cortex-danger)' }}> *</span>}
      </label>
      <input
        type={type}
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder={placeholder}
        style={{
          width: '100%', padding: '6px 8px',
          border: '1px solid var(--cortex-border)', borderRadius: '4px',
          fontSize: '12px', fontFamily: type === 'number' ? 'var(--font-mono)' : 'inherit',
        }}
      />
    </div>
  )
}
