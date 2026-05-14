import React, { useState, useEffect, useCallback, useMemo } from 'react'
import { getEalPlugins, getEalPlugin, postEalCampaign } from '../api/client.js'

/**
 * EalCampaignBuilder — declarative campaign authoring.
 *
 * Flow:
 *   1. Load /api/eal/plugins; pick a plugin.
 *   2. Fetch /api/eal/plugins/:name to get its Pydantic JSON schema.
 *   3. Render a form whose fields match the schema's properties.
 *   4. Operator fills in campaign metadata (id, name, allowlist, auth)
 *      and the per-step plugin params.
 *   5. POST /api/eal/campaigns persists it; parent flips back to the
 *      Campaigns tab.
 *
 * v1 supports a single-step campaign. Multi-step authoring goes in a
 * follow-up; today the "narrative" use case is best served by the
 * YAML-via-CLI path the DC already has.
 */
export default function EalCampaignBuilder({ onCreated, onError }) {
  // ── Catalog ────────────────────────────────────────────────────────────
  const [plugins, setPlugins] = useState([])
  const [loadingCatalog, setLoadingCatalog] = useState(true)

  // ── Selected plugin + its schema ──────────────────────────────────────
  const [selectedPlugin, setSelectedPlugin] = useState(null)
  const [pluginMeta, setPluginMeta] = useState(null)
  const [loadingMeta, setLoadingMeta] = useState(false)

  // ── Form state ─────────────────────────────────────────────────────────
  const [campaignId, setCampaignId] = useState('')
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [authorizedBy, setAuthorizedBy] = useState('')
  const [simulationAuthorized, setSimulationAuthorized] = useState(false)
  const [targetAllowlist, setTargetAllowlist] = useState('')   // CSV input
  const [dryRun, setDryRun] = useState(true)
  const [stepId, setStepId] = useState('step-01')
  const [params, setParams] = useState({})

  const [submitting, setSubmitting] = useState(false)
  const [warnings, setWarnings] = useState([])

  // ── Load plugin catalog on mount ──────────────────────────────────────
  useEffect(() => {
    setLoadingCatalog(true)
    getEalPlugins()
      .then(data => setPlugins(Array.isArray(data?.plugins) ? data.plugins : []))
      .catch(err => onError?.(`Failed to load plugins: ${err.message}`))
      .finally(() => setLoadingCatalog(false))
  }, [onError])

  // ── Load schema when plugin changes ───────────────────────────────────
  useEffect(() => {
    if (!selectedPlugin) {
      setPluginMeta(null)
      setParams({})
      return
    }
    setLoadingMeta(true)
    getEalPlugin(selectedPlugin)
      .then(meta => {
        setPluginMeta(meta)
        setParams(initialParamsFromSchema(meta?.params_schema))
      })
      .catch(err => onError?.(`Failed to load ${selectedPlugin}: ${err.message}`))
      .finally(() => setLoadingMeta(false))
  }, [selectedPlugin, onError])

  // ── Submit ────────────────────────────────────────────────────────────
  const handleSubmit = useCallback(async () => {
    const localWarnings = []
    if (!campaignId.match(/^CMP(-[A-Z0-9]+)+-\d{3,5}$/))
      localWarnings.push('campaign_id must match CMP-{LABEL}-NNN (e.g. CMP-NDR-001).')
    if (!name.trim()) localWarnings.push('Name is required.')
    if (!selectedPlugin) localWarnings.push('Pick a plugin first.')
    if (!dryRun) {
      if (!simulationAuthorized) localWarnings.push('Live execution requires simulation_authorized=true.')
      if (!authorizedBy.trim()) localWarnings.push('Live execution requires authorized_by.')
      if (!targetAllowlist.trim()) localWarnings.push('Live execution requires a non-empty target_allowlist.')
    }
    setWarnings(localWarnings)
    if (localWarnings.length > 0) return

    const body = {
      campaign_id: campaignId.trim(),
      name: name.trim(),
      description: description.trim() || null,
      authorized_by: authorizedBy.trim() || null,
      simulation_authorized: !!simulationAuthorized,
      target_allowlist: targetAllowlist
        .split(',').map(s => s.trim()).filter(Boolean),
      dry_run: !!dryRun,
      tags: [],
      mitre_techniques: [],
      steps: [{
        step_id: stepId.trim() || 'step-01',
        plugin: selectedPlugin,
        params,
      }],
    }

    setSubmitting(true)
    try {
      const persisted = await postEalCampaign(body)
      onCreated?.(persisted)
    } catch (err) {
      onError?.(`Save failed: ${err.message}`)
    } finally {
      setSubmitting(false)
    }
  }, [campaignId, name, description, authorizedBy, simulationAuthorized,
      targetAllowlist, dryRun, stepId, selectedPlugin, params,
      onCreated, onError])

  const properties = pluginMeta?.params_schema?.properties || {}
  const required = new Set(pluginMeta?.params_schema?.required || [])

  return (
    <div className="eal-builder">
      {/* Campaign metadata */}
      <fieldset className="eal-builder__fieldset">
        <legend>Campaign metadata</legend>
        <div className="form-grid">
          <Field label="campaign_id" required help="CMP-{LABEL}-NNN">
            <input
              className="input mono"
              value={campaignId}
              placeholder="CMP-NDR-001"
              onChange={e => setCampaignId(e.target.value.toUpperCase())}
            />
          </Field>
          <Field label="name" required>
            <input
              className="input"
              value={name}
              placeholder="Short human title"
              onChange={e => setName(e.target.value)}
            />
          </Field>
          <Field label="description" full>
            <textarea
              className="input"
              rows={2}
              value={description}
              placeholder="Optional. Markdown allowed."
              onChange={e => setDescription(e.target.value)}
            />
          </Field>
        </div>
      </fieldset>

      {/* Safety block */}
      <fieldset className="eal-builder__fieldset">
        <legend>Safety policy</legend>
        <div className="form-grid">
          <Field label="dry_run">
            <label className="toggle">
              <input
                type="checkbox"
                checked={dryRun}
                onChange={e => setDryRun(e.target.checked)}
              />
              <span>{dryRun ? 'dry-run (no traffic emitted)' : 'LIVE (real traffic)'}</span>
            </label>
          </Field>
          <Field label="simulation_authorized" help="Must be true for live execution">
            <label className="toggle">
              <input
                type="checkbox"
                checked={simulationAuthorized}
                onChange={e => setSimulationAuthorized(e.target.checked)}
                disabled={dryRun}
              />
              <span>authorised</span>
            </label>
          </Field>
          <Field label="authorized_by" help="Operator who signed off">
            <input
              className="input"
              value={authorizedBy}
              placeholder="dc@paloaltonetworks.com"
              onChange={e => setAuthorizedBy(e.target.value)}
              disabled={dryRun}
            />
          </Field>
          <Field label="target_allowlist" full help="Comma-separated hostnames or CIDRs">
            <input
              className="input mono small"
              value={targetAllowlist}
              placeholder="api.openai.com, 10.0.0.0/24"
              onChange={e => setTargetAllowlist(e.target.value)}
              disabled={dryRun}
            />
          </Field>
        </div>
      </fieldset>

      {/* Plugin picker + dynamic form */}
      <fieldset className="eal-builder__fieldset">
        <legend>Step — plugin + params</legend>
        <div className="form-grid">
          <Field label="step_id">
            <input
              className="input mono"
              value={stepId}
              onChange={e => setStepId(e.target.value)}
            />
          </Field>
          <Field label="plugin" required>
            {loadingCatalog ? (
              <span className="muted">Loading…</span>
            ) : (
              <select
                className="input"
                value={selectedPlugin || ''}
                onChange={e => setSelectedPlugin(e.target.value || null)}
              >
                <option value="">— pick a plugin —</option>
                {plugins.map(p => (
                  <option key={p.name} value={p.name}>
                    {p.name} (v{p.version}) — {p.description?.slice(0, 60)}
                  </option>
                ))}
              </select>
            )}
          </Field>
          {pluginMeta && (
            <Field label="EAL targets" full>
              <ul className="bullet">
                {(pluginMeta.eal_targets || []).map(t => (
                  <li key={t}><code className="mono small">{t}</code></li>
                ))}
              </ul>
            </Field>
          )}
        </div>

        {loadingMeta && <p className="muted">Loading schema…</p>}

        {pluginMeta && Object.keys(properties).length > 0 && (
          <div className="form-grid" style={{ marginTop: '8px' }}>
            {Object.entries(properties).map(([key, prop]) => (
              <SchemaField
                key={key}
                name={key}
                schema={prop}
                required={required.has(key)}
                value={params[key]}
                onChange={(v) => setParams(p => ({ ...p, [key]: v }))}
              />
            ))}
          </div>
        )}
      </fieldset>

      {warnings.length > 0 && (
        <div className="warning-banner">
          <strong>Validation:</strong>
          <ul>{warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
        </div>
      )}

      <div className="flex-row" style={{ gap: '8px', justifyContent: 'flex-end' }}>
        <button
          className="btn btn-navy"
          disabled={submitting || !selectedPlugin}
          onClick={handleSubmit}
        >
          {submitting ? 'Saving…' : 'Save campaign'}
        </button>
      </div>
    </div>
  )
}

// ─── Helpers ────────────────────────────────────────────────────────────────

function Field({ label, required, help, full, children }) {
  return (
    <div className={`form-field ${full ? 'form-field--full' : ''}`}>
      <label>
        <span className="form-field__label">
          {label}
          {required && <span className="form-field__required"> *</span>}
        </span>
        {help && <span className="form-field__help">{help}</span>}
      </label>
      {children}
    </div>
  )
}

/**
 * SchemaField — render one input from a JSON-schema property.
 *
 * Supports the subset of JSON-schema our plugins emit: string, integer,
 * number, boolean, array (string items via comma-separated input), enum.
 * Anything richer (oneOf, $ref) falls back to a JSON textarea.
 */
function SchemaField({ name, schema, required, value, onChange }) {
  const type = schema.type || (schema.enum ? 'string' : 'string')
  const help = useMemo(() => {
    const bits = []
    if (schema.description) bits.push(schema.description)
    if (schema.minimum != null) bits.push(`min=${schema.minimum}`)
    if (schema.maximum != null) bits.push(`max=${schema.maximum}`)
    if (schema.default != null) bits.push(`default=${JSON.stringify(schema.default)}`)
    return bits.join(' · ')
  }, [schema])

  if (schema.enum) {
    return (
      <Field label={name} required={required} help={help}>
        <select
          className="input"
          value={value ?? schema.default ?? ''}
          onChange={(e) => onChange(e.target.value || null)}
        >
          {!required && <option value="">— unset —</option>}
          {schema.enum.map(opt => (
            <option key={opt} value={opt}>{opt}</option>
          ))}
        </select>
      </Field>
    )
  }

  if (type === 'boolean') {
    return (
      <Field label={name} required={required} help={help}>
        <label className="toggle">
          <input
            type="checkbox"
            checked={!!(value ?? schema.default ?? false)}
            onChange={(e) => onChange(e.target.checked)}
          />
          <span>{value ? 'on' : 'off'}</span>
        </label>
      </Field>
    )
  }

  if (type === 'integer' || type === 'number') {
    return (
      <Field label={name} required={required} help={help}>
        <input
          type="number"
          className="input mono"
          value={value ?? schema.default ?? ''}
          step={type === 'integer' ? 1 : 'any'}
          min={schema.minimum}
          max={schema.maximum}
          onChange={(e) => {
            const raw = e.target.value
            if (raw === '') { onChange(null); return }
            const n = type === 'integer' ? parseInt(raw, 10) : parseFloat(raw)
            onChange(Number.isFinite(n) ? n : null)
          }}
        />
      </Field>
    )
  }

  if (type === 'array') {
    const items = schema.items || {}
    if (items.type === 'string') {
      const arrVal = Array.isArray(value) ? value : (schema.default || [])
      return (
        <Field label={name} required={required} full
               help={`${help}${help ? ' · ' : ''}comma-separated`}>
          <input
            className="input mono small"
            value={arrVal.join(', ')}
            onChange={(e) => onChange(
              e.target.value.split(',').map(s => s.trim()).filter(Boolean),
            )}
          />
        </Field>
      )
    }
  }

  if (type === 'object' || schema.additionalProperties) {
    const text = value != null ? JSON.stringify(value, null, 2) : ''
    return (
      <Field label={name} required={required} full
             help={`${help}${help ? ' · ' : ''}JSON object`}>
        <textarea
          className="input mono small"
          rows={4}
          value={text}
          onChange={(e) => {
            try { onChange(e.target.value.trim() ? JSON.parse(e.target.value) : {}) }
            catch { /* leave invalid intermediate state; submit will fail-fast */ }
          }}
        />
      </Field>
    )
  }

  // Default to text input
  return (
    <Field label={name} required={required} help={help}>
      <input
        className="input"
        value={value ?? schema.default ?? ''}
        onChange={(e) => onChange(e.target.value)}
      />
    </Field>
  )
}

function initialParamsFromSchema(schema) {
  const props = schema?.properties || {}
  const out = {}
  for (const [k, p] of Object.entries(props)) {
    if (p.default !== undefined) out[k] = p.default
  }
  return out
}
