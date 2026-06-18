import React, { useState, useEffect, useCallback } from 'react'
import {
  listXsiamTenants,
  registerXsiamTenant,
  deleteXsiamTenant,
  testXsiamTenant,
} from '../../api/client.js'

// ── Constants ──────────────────────────────────────────────────────────────

const XSIAM_TENANT_URL_HINT = 'https://api-<tenant>.xdr.<region>.paloaltonetworks.com'

const EMPTY_FORM = {
  name:       '',
  base_url:   '',
  region:     '',
  api_key_id: '',
  api_key:    '',
  auth_mode:  'standard',
}

// ── Sub-components ─────────────────────────────────────────────────────────

function StepIndicator({ current }) {
  const steps = [
    { n: 1, label: 'Tenant' },
    { n: 2, label: 'Credentials' },
    { n: 3, label: 'Review' },
  ]
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 0, marginBottom: 28 }}>
      {steps.map((s, i) => {
        const done    = current > s.n
        const active  = current === s.n
        const circleStyle = {
          width: 26, height: 26, borderRadius: '50%',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: 11, fontWeight: 700, fontFamily: 'var(--font-mono)',
          flexShrink: 0,
          background: done    ? 'var(--cortex-navy)'
                    : active  ? 'var(--cortex-teal)'
                               : 'var(--cortex-border)',
          color: (done || active) ? '#fff' : 'var(--cortex-steel)',
        }
        const labelStyle = {
          fontSize: 11, fontWeight: active ? 700 : 400,
          color: active ? 'var(--cortex-navy)'
               : done   ? 'var(--cortex-teal)'
                        : 'var(--cortex-steel)',
          marginLeft: 6,
        }
        return (
          <React.Fragment key={s.n}>
            <div style={{ display: 'flex', alignItems: 'center' }}>
              <div style={circleStyle}>{done ? '✓' : s.n}</div>
              <span style={labelStyle}>{s.label}</span>
            </div>
            {i < steps.length - 1 && (
              <div style={{
                flex: 1, height: 1, margin: '0 10px',
                background: current > s.n ? 'var(--cortex-teal)' : 'var(--cortex-border)',
              }} />
            )}
          </React.Fragment>
        )
      })}
    </div>
  )
}

function FieldRow({ label, hint, required, error, children }) {
  return (
    <div style={{ marginBottom: 16 }}>
      <label style={{
        display: 'block', fontSize: 11, fontWeight: 600,
        color: 'var(--cortex-navy)', marginBottom: 5, letterSpacing: '0.04em',
        textTransform: 'uppercase',
      }}>
        {label}{required && <span style={{ color: 'var(--cortex-danger)', marginLeft: 2 }}>*</span>}
      </label>
      {children}
      {hint && !error && (
        <div style={{ fontSize: 10, color: 'var(--cortex-steel)', marginTop: 4 }}>{hint}</div>
      )}
      {error && (
        <div style={{ fontSize: 10, color: 'var(--cortex-danger)', marginTop: 4 }}>{error}</div>
      )}
    </div>
  )
}

function Field({ value, onChange, placeholder, type = 'text', mono = false, error }) {
  return (
    <input
      type={type}
      value={value}
      onChange={e => onChange(e.target.value)}
      placeholder={placeholder}
      autoComplete={type === 'password' ? 'new-password' : 'off'}
      style={{
        width: '100%', boxSizing: 'border-box',
        padding: '8px 10px',
        border: `1px solid ${error ? 'var(--cortex-danger)' : 'var(--cortex-border)'}`,
        borderRadius: 'var(--radius-sm)',
        fontFamily: mono ? 'var(--font-mono)' : 'var(--font-primary)',
        fontSize: 12,
        color: 'var(--cortex-navy)',
        outline: 'none',
        background: '#fff',
        transition: 'border-color 0.12s',
      }}
      onFocus={e => { e.target.style.borderColor = 'var(--cortex-teal)' }}
      onBlur={e => { e.target.style.borderColor = error ? 'var(--cortex-danger)' : 'var(--cortex-border)' }}
    />
  )
}

function HealthPill({ ok, testing, error: errText }) {
  if (testing) {
    return (
      <span style={{
        padding: '2px 9px', borderRadius: 12, fontSize: 10, fontWeight: 700,
        fontFamily: 'var(--font-mono)', letterSpacing: '0.06em',
        background: 'rgba(0,192,232,0.15)', color: 'var(--cortex-teal)',
        border: '1px solid var(--cortex-teal)',
        animation: 'pulse 1s ease-in-out infinite',
      }}>
        TESTING…
      </span>
    )
  }
  if (ok === true) {
    return (
      <span style={{
        padding: '2px 9px', borderRadius: 12, fontSize: 10, fontWeight: 700,
        fontFamily: 'var(--font-mono)', letterSpacing: '0.06em',
        background: 'rgba(0,184,148,0.15)', color: 'var(--cortex-success)',
        border: '1px solid var(--cortex-success)',
      }}>
        HEALTHY
      </span>
    )
  }
  if (ok === false) {
    return (
      <span
        title={errText || 'Test failed'}
        style={{
          padding: '2px 9px', borderRadius: 12, fontSize: 10, fontWeight: 700,
          fontFamily: 'var(--font-mono)', letterSpacing: '0.06em',
          background: 'rgba(231,76,60,0.10)', color: 'var(--cortex-danger)',
          border: '1px solid var(--cortex-danger)',
          cursor: errText ? 'help' : 'default',
        }}>
        FAILED
      </span>
    )
  }
  return (
    <span style={{
      padding: '2px 9px', borderRadius: 12, fontSize: 10, fontWeight: 700,
      fontFamily: 'var(--font-mono)', letterSpacing: '0.06em',
      background: 'rgba(107,126,142,0.12)', color: 'var(--cortex-steel)',
      border: '1px solid var(--cortex-border)',
    }}>
      NOT TESTED
    </span>
  )
}

// ── Step 1: Tenant Identity ────────────────────────────────────────────────

function Step1({ form, onChange, errors }) {
  return (
    <div>
      <h3 style={{ fontSize: 13, fontWeight: 700, color: 'var(--cortex-navy)', margin: '0 0 20px' }}>
        Identify the tenant
      </h3>
      <FieldRow
        label="Tenant Name"
        hint="A short identifier used to reference this tenant in the engine (e.g. acme-prod)"
        required
        error={errors.name}
      >
        <Field
          value={form.name}
          onChange={v => onChange('name', v)}
          placeholder="acme-prod"
          mono
          error={errors.name}
        />
      </FieldRow>

      <FieldRow
        label="Base URL"
        hint={`API base URL format: ${XSIAM_TENANT_URL_HINT}`}
        required
        error={errors.base_url}
      >
        <Field
          value={form.base_url}
          onChange={v => onChange('base_url', v)}
          placeholder={XSIAM_TENANT_URL_HINT}
          mono
          error={errors.base_url}
        />
      </FieldRow>

      <FieldRow
        label="Region"
        hint="Free-text region identifier (e.g. us, eu, ap, us-gov)"
        required
        error={errors.region}
      >
        <Field
          value={form.region}
          onChange={v => onChange('region', v)}
          placeholder="us"
          mono
          error={errors.region}
        />
      </FieldRow>
    </div>
  )
}

// ── Step 2: Credentials ────────────────────────────────────────────────────

function Step2({ form, onChange, errors }) {
  return (
    <div>
      <h3 style={{ fontSize: 13, fontWeight: 700, color: 'var(--cortex-navy)', margin: '0 0 20px' }}>
        API credentials
      </h3>

      <FieldRow label="Auth Mode" required>
        <div style={{ display: 'flex', gap: 10, marginTop: 4 }}>
          {[
            { value: 'standard', label: 'Standard',  available: true  },
            { value: 'advanced', label: 'Advanced',  available: false },
          ].map(opt => (
            <label key={opt.value} style={{
              display: 'flex', alignItems: 'center', gap: 6,
              padding: '7px 12px',
              border: form.auth_mode === opt.value
                ? '1.5px solid var(--cortex-teal)'
                : '1px solid var(--cortex-border)',
              borderRadius: 'var(--radius-sm)',
              background: form.auth_mode === opt.value ? 'rgba(0,192,232,0.07)' : '#fff',
              cursor: opt.available ? 'pointer' : 'not-allowed',
              opacity: opt.available ? 1 : 0.45,
              fontSize: 12,
              fontWeight: form.auth_mode === opt.value ? 700 : 400,
              color: 'var(--cortex-navy)',
              userSelect: 'none',
            }}>
              <input
                type="radio"
                name="auth_mode"
                value={opt.value}
                checked={form.auth_mode === opt.value}
                disabled={!opt.available}
                onChange={() => opt.available && onChange('auth_mode', opt.value)}
                style={{ accentColor: 'var(--cortex-teal)' }}
              />
              {opt.label}
              {!opt.available && (
                <span style={{ fontSize: 9, color: 'var(--cortex-steel)', marginLeft: 2 }}>
                  (Slice 2)
                </span>
              )}
            </label>
          ))}
        </div>
        <div style={{ fontSize: 10, color: 'var(--cortex-steel)', marginTop: 5 }}>
          Standard auth uses static headers: x-xdr-auth-id + Authorization.
        </div>
      </FieldRow>

      <FieldRow
        label="API Key ID"
        hint="The numeric ID shown in Settings → API Keys (e.g. 42)"
        required
        error={errors.api_key_id}
      >
        <Field
          value={form.api_key_id}
          onChange={v => onChange('api_key_id', v)}
          placeholder="42"
          mono
          error={errors.api_key_id}
        />
      </FieldRow>

      <FieldRow
        label="API Key"
        hint="The key value — stored Fernet-encrypted, never returned in plaintext"
        required
        error={errors.api_key}
      >
        <Field
          value={form.api_key}
          onChange={v => onChange('api_key', v)}
          placeholder="Paste API key…"
          type="password"
          mono
          error={errors.api_key}
        />
      </FieldRow>
    </div>
  )
}

// ── Step 3: Review ─────────────────────────────────────────────────────────

function Step3({ form, saving, testResult, onSaveAndTest, onSaveOnly }) {
  const keyTail = form.api_key.length >= 4
    ? `…${form.api_key.slice(-4)}`
    : '(too short)'

  const rows = [
    { label: 'Name',       value: form.name,       mono: true  },
    { label: 'Base URL',   value: form.base_url,   mono: true  },
    { label: 'Region',     value: form.region,     mono: true  },
    { label: 'Auth Mode',  value: form.auth_mode,  mono: false },
    { label: 'Key ID',     value: form.api_key_id, mono: true  },
    { label: 'API Key',    value: keyTail,          mono: true  },
  ]

  return (
    <div>
      <h3 style={{ fontSize: 13, fontWeight: 700, color: 'var(--cortex-navy)', margin: '0 0 20px' }}>
        Review & activate
      </h3>

      <table style={{ width: '100%', borderCollapse: 'collapse', marginBottom: 24 }}>
        <tbody>
          {rows.map(r => (
            <tr key={r.label} style={{ borderBottom: '1px solid var(--cortex-border)' }}>
              <td style={{
                padding: '7px 0', fontSize: 11, fontWeight: 600, width: 110,
                color: 'var(--cortex-steel)', textTransform: 'uppercase', letterSpacing: '0.04em',
              }}>{r.label}</td>
              <td style={{
                padding: '7px 0', fontSize: 12,
                fontFamily: r.mono ? 'var(--font-mono)' : 'var(--font-primary)',
                color: 'var(--cortex-navy)',
              }}>{r.value || <span style={{ color: 'var(--cortex-steel)' }}>—</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {testResult && !testResult.ok && (
        <div style={{
          padding: '10px 14px', marginBottom: 16,
          background: 'rgba(231,76,60,0.08)', border: '1px solid var(--cortex-danger)',
          borderRadius: 'var(--radius-sm)', fontSize: 12, color: 'var(--cortex-danger)',
        }}>
          <strong>Test failed:</strong>{' '}
          {testResult.error || 'The tenant did not respond as expected.'}
        </div>
      )}
      {testResult && testResult.ok && (
        <div style={{
          padding: '10px 14px', marginBottom: 16,
          background: 'rgba(0,184,148,0.08)', border: '1px solid var(--cortex-success)',
          borderRadius: 'var(--radius-sm)', fontSize: 12, color: 'var(--cortex-success)',
        }}>
          Tenant is reachable.{' '}
          {testResult.status?.status && <span>Status: <strong>{testResult.status.status}</strong></span>}
        </div>
      )}

      <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
        <button
          className="btn btn-primary"
          onClick={onSaveAndTest}
          disabled={saving}
          style={{ minWidth: 130 }}
        >
          {saving ? 'Saving…' : '✦ Save & Test'}
        </button>
        <button
          className="btn btn-secondary btn-sm"
          onClick={onSaveOnly}
          disabled={saving}
        >
          Save without testing
        </button>
      </div>
      <div style={{ fontSize: 10, color: 'var(--cortex-steel)', marginTop: 8 }}>
        The API key is encrypted with CORTEXSIM_SECRET before storage. It is never logged or returned in API responses.
      </div>
    </div>
  )
}

// ── Tenant List ────────────────────────────────────────────────────────────

function TenantRow({ tenant, onTest, onDelete, testing }) {
  const cfg   = tenant.config || {}
  const verAt = tenant.last_verified_at
    ? new Date(tenant.last_verified_at).toLocaleString(undefined, { hour12: false })
    : null

  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: '1fr auto auto',
      gap: 16, alignItems: 'start',
      padding: '14px 0',
      borderBottom: '1px solid var(--cortex-border)',
    }}>
      <div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 4 }}>
          <span style={{
            fontFamily: 'var(--font-mono)', fontWeight: 700,
            fontSize: 12, color: 'var(--cortex-navy)',
          }}>
            {tenant.name}
          </span>
          <HealthPill
            ok={testing ? undefined : tenant.last_verified_ok}
            testing={testing}
            error={tenant.last_verified_error}
          />
        </div>
        <div style={{ fontSize: 11, color: 'var(--cortex-steel)', fontFamily: 'var(--font-mono)' }}>
          {cfg.base_url || '—'}
        </div>
        <div style={{ fontSize: 10, color: 'var(--cortex-steel)', marginTop: 3 }}>
          Region: {cfg.region || '—'} · Auth: {cfg.auth_mode || 'standard'} · Key ID: {cfg.api_key_id || '—'}
          {verAt && <span> · Tested: {verAt}</span>}
          {tenant.last_verified_error && (
            <span style={{ color: 'var(--cortex-danger)', marginLeft: 6 }}>
              ↳ {tenant.last_verified_error}
            </span>
          )}
        </div>
      </div>

      <button
        className="btn btn-sm btn-secondary"
        onClick={() => onTest(tenant.name)}
        disabled={testing}
        style={{ whiteSpace: 'nowrap' }}
      >
        {testing ? 'Testing…' : '▸ Test'}
      </button>

      <button
        className="btn btn-sm"
        onClick={() => onDelete(tenant.name)}
        style={{
          background: 'none', border: '1px solid var(--cortex-border)',
          color: 'var(--cortex-danger)', cursor: 'pointer',
          padding: '4px 10px', borderRadius: 'var(--radius-sm)', fontSize: 11,
        }}
      >
        ✕
      </button>
    </div>
  )
}

// ── Validation ─────────────────────────────────────────────────────────────

function validateStep(step, form) {
  const errs = {}
  if (step === 1) {
    if (!form.name.trim())     errs.name     = 'Required'
    if (!/^[a-z0-9][a-z0-9_-]*$/.test(form.name))
      errs.name = 'Lowercase letters, numbers, hyphens, underscores only'
    if (!form.base_url.trim()) errs.base_url = 'Required'
    if (!form.base_url.startsWith('https://'))
      errs.base_url = 'Must start with https://'
    if (!form.region.trim())   errs.region   = 'Required'
  }
  if (step === 2) {
    if (!form.api_key_id.trim()) errs.api_key_id = 'Required'
    if (!form.api_key.trim())    errs.api_key    = 'Required'
    if (form.api_key.length < 10)
      errs.api_key = 'API key looks too short — paste the full value'
  }
  return errs
}

// ── Main component ─────────────────────────────────────────────────────────

export default function TenantManager() {
  const [step, setStep]           = useState(1)
  const [form, setForm]           = useState(EMPTY_FORM)
  const [errors, setErrors]       = useState({})
  const [saving, setSaving]       = useState(false)
  const [testResult, setTestResult] = useState(null)

  const [tenants, setTenants]     = useState([])
  const [loadError, setLoadError] = useState(null)
  const [testing, setTesting]     = useState({})   // { [name]: bool }

  // ── Confirmation dialog for delete ──────────────────────────────────────
  const [pendingDelete, setPendingDelete] = useState(null)

  const loadTenants = useCallback(() => {
    listXsiamTenants()
      .then(setTenants)
      .catch(e => setLoadError(e.message))
  }, [])

  useEffect(() => { loadTenants() }, [loadTenants])

  // ── Wizard field change ──────────────────────────────────────────────────
  const handleChange = useCallback((field, value) => {
    setForm(f => ({ ...f, [field]: value }))
    setErrors(e => ({ ...e, [field]: undefined }))
  }, [])

  // ── Next / Back ──────────────────────────────────────────────────────────
  const handleNext = () => {
    const errs = validateStep(step, form)
    if (Object.keys(errs).length) { setErrors(errs); return }
    setErrors({})
    setStep(s => s + 1)
  }

  const handleBack = () => {
    setErrors({})
    setTestResult(null)
    setStep(s => s - 1)
  }

  // ── Save helpers ─────────────────────────────────────────────────────────
  const doSave = async () => {
    await registerXsiamTenant(form)
  }

  const handleSaveAndTest = async () => {
    setSaving(true)
    setTestResult(null)
    try {
      await doSave()
      const result = await testXsiamTenant(form.name)
      setTestResult(result)
      if (result.ok) {
        setTimeout(() => {
          setStep(1)
          setForm(EMPTY_FORM)
          setTestResult(null)
          loadTenants()
        }, 1400)
      }
    } catch (e) {
      setTestResult({ ok: false, error: e.message })
    } finally {
      setSaving(false)
      loadTenants()
    }
  }

  const handleSaveOnly = async () => {
    setSaving(true)
    try {
      await doSave()
      setStep(1)
      setForm(EMPTY_FORM)
      loadTenants()
    } catch (e) {
      setTestResult({ ok: false, error: e.message })
    } finally {
      setSaving(false)
    }
  }

  // ── Test existing tenant ─────────────────────────────────────────────────
  const handleTest = async (name) => {
    setTesting(t => ({ ...t, [name]: true }))
    try {
      await testXsiamTenant(name)
    } finally {
      setTesting(t => ({ ...t, [name]: false }))
      loadTenants()
    }
  }

  // ── Delete tenant ────────────────────────────────────────────────────────
  const handleDeleteConfirmed = async () => {
    if (!pendingDelete) return
    try {
      await deleteXsiamTenant(pendingDelete)
      loadTenants()
    } catch (e) {
      setLoadError(e.message)
    } finally {
      setPendingDelete(null)
    }
  }

  // ── Render ───────────────────────────────────────────────────────────────
  return (
    <div style={{ padding: '20px 24px', maxWidth: 900 }}>

      {/* ── Delete confirm overlay ────────────────────────────────────────── */}
      {pendingDelete && (
        <div style={{
          position: 'fixed', inset: 0, zIndex: 300,
          background: 'rgba(0,0,0,0.45)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <div style={{
            background: '#fff', borderRadius: 'var(--radius-md)',
            boxShadow: 'var(--shadow-lg)', padding: 28, minWidth: 360,
          }}>
            <h3 style={{ fontSize: 14, fontWeight: 700, color: 'var(--cortex-navy)', marginTop: 0 }}>
              Remove tenant?
            </h3>
            <p style={{ fontSize: 12, color: 'var(--cortex-steel)', marginBottom: 20 }}>
              The encrypted API key for <strong style={{ fontFamily: 'var(--font-mono)' }}>{pendingDelete}</strong> will be permanently deleted. This action cannot be undone.
            </p>
            <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
              <button className="btn btn-secondary btn-sm" onClick={() => setPendingDelete(null)}>
                Cancel
              </button>
              <button
                className="btn btn-sm"
                onClick={handleDeleteConfirmed}
                style={{ background: 'var(--cortex-danger)', color: '#fff', border: 'none' }}
              >
                Delete
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── View header ───────────────────────────────────────────────────── */}
      <div style={{ marginBottom: 28 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 12 }}>
          <h2 style={{ margin: 0, fontSize: 16, fontWeight: 800, color: 'var(--cortex-navy)' }}>
            XSIAM Tenants
          </h2>
          <span style={{ fontSize: 11, color: 'var(--cortex-steel)' }}>
            Register and manage XSIAM tenant connections for health & metrics
          </span>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 32, alignItems: 'start' }}>

        {/* ── LEFT: Wizard ──────────────────────────────────────────────── */}
        <div style={{
          background: '#fff',
          border: '1px solid var(--cortex-border)',
          borderRadius: 'var(--radius-md)',
          padding: '24px 24px 20px',
          boxShadow: 'var(--shadow-sm)',
        }}>
          <StepIndicator current={step} />

          {step === 1 && <Step1 form={form} onChange={handleChange} errors={errors} />}
          {step === 2 && <Step2 form={form} onChange={handleChange} errors={errors} />}
          {step === 3 && (
            <Step3
              form={form}
              saving={saving}
              testResult={testResult}
              onSaveAndTest={handleSaveAndTest}
              onSaveOnly={handleSaveOnly}
            />
          )}

          {step < 3 && (
            <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 24 }}>
              {step > 1 ? (
                <button className="btn btn-secondary btn-sm" onClick={handleBack}>← Back</button>
              ) : <div />}
              <button className="btn btn-primary" onClick={handleNext}>
                {step === 2 ? 'Review →' : 'Next →'}
              </button>
            </div>
          )}

          {step === 3 && (
            <div style={{ marginTop: 16 }}>
              <button className="btn btn-secondary btn-sm" onClick={handleBack}>← Back</button>
            </div>
          )}
        </div>

        {/* ── RIGHT: Registered tenants ─────────────────────────────────── */}
        <div>
          <div style={{
            display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 12,
          }}>
            <h3 style={{ margin: 0, fontSize: 12, fontWeight: 700,
              color: 'var(--cortex-navy)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
              Registered Tenants
            </h3>
            <span style={{
              background: 'var(--cortex-teal)', color: '#fff',
              fontSize: 9, fontWeight: 800, padding: '1px 6px',
              borderRadius: 10, fontFamily: 'var(--font-mono)',
            }}>
              {tenants.length}
            </span>
          </div>

          {loadError && (
            <div style={{ fontSize: 11, color: 'var(--cortex-danger)', marginBottom: 10 }}>
              {loadError}
            </div>
          )}

          {tenants.length === 0 && !loadError && (
            <div style={{
              padding: '24px 0', textAlign: 'center',
              fontSize: 11, color: 'var(--cortex-steel)',
              border: '1px dashed var(--cortex-border)', borderRadius: 'var(--radius-sm)',
            }}>
              No tenants registered yet.
              <br />Use the wizard to add your first XSIAM tenant.
            </div>
          )}

          {tenants.map(t => (
            <TenantRow
              key={t.name}
              tenant={t}
              testing={!!testing[t.name]}
              onTest={handleTest}
              onDelete={name => setPendingDelete(name)}
            />
          ))}
        </div>
      </div>
    </div>
  )
}
