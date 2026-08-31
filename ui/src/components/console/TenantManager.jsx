import React, { useState, useCallback } from 'react'
import {
  registerXsiamTenant,
  deleteXsiamTenant,
  testXsiamTenant,
} from '../../api/client.js'
import { useEnvironment } from '../../context/EnvironmentContext.jsx'

// Resolve the stable key a tenant is addressed by (matches EnvironmentContext).
const tenantKey = (t) => (t && (t.name || t.id)) || null

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
    <div className="tenant-mgr__steps">
      {steps.map((s, i) => {
        const done    = current > s.n
        const active  = current === s.n
        const circleClass = [
          'tenant-mgr__step-circle',
          done   && 'tenant-mgr__step-circle--done',
          active && 'tenant-mgr__step-circle--active',
          !done && !active && 'tenant-mgr__step-circle--pending',
        ].filter(Boolean).join(' ')
        const labelClass = [
          'tenant-mgr__step-label',
          active && 'tenant-mgr__step-label--active',
          done   && 'tenant-mgr__step-label--done',
        ].filter(Boolean).join(' ')
        return (
          <React.Fragment key={s.n}>
            <div className="tenant-mgr__step">
              <div className={circleClass}>{done ? '✓' : s.n}</div>
              <span className={labelClass}>{s.label}</span>
            </div>
            {i < steps.length - 1 && (
              <div className={`tenant-mgr__step-connector${current > s.n ? ' tenant-mgr__step-connector--done' : ''}`} />
            )}
          </React.Fragment>
        )
      })}
    </div>
  )
}

function FieldRow({ label, hint, required, error, children }) {
  return (
    <div className="tenant-mgr__field">
      <label className="tenant-mgr__field-label">
        {label}{required && <span className="tenant-mgr__field-required">*</span>}
      </label>
      {children}
      {hint && !error && (
        <div className="tenant-mgr__field-hint">{hint}</div>
      )}
      {error && (
        <div className="tenant-mgr__field-error">{error}</div>
      )}
    </div>
  )
}

function Field({ value, onChange, placeholder, type = 'text', mono = false, error }) {
  const className = [
    'tenant-mgr__input',
    mono && 'tenant-mgr__input--mono',
    error && 'tenant-mgr__input--error',
  ].filter(Boolean).join(' ')
  return (
    <input
      type={type}
      value={value}
      onChange={e => onChange(e.target.value)}
      placeholder={placeholder}
      autoComplete={type === 'password' ? 'new-password' : 'off'}
      className={className}
    />
  )
}

function HealthPill({ ok, testing, error: errText }) {
  if (testing) {
    return (
      <span className="tenant-mgr__pill tenant-mgr__pill--testing">
        TESTING…
      </span>
    )
  }
  if (ok === true) {
    return (
      <span className="tenant-mgr__pill tenant-mgr__pill--ok">
        HEALTHY
      </span>
    )
  }
  if (ok === false) {
    return (
      <span
        title={errText || 'Test failed'}
        className={`tenant-mgr__pill tenant-mgr__pill--fail${errText ? ' tenant-mgr__pill--hint' : ''}`}
      >
        FAILED
      </span>
    )
  }
  return (
    <span className="tenant-mgr__pill tenant-mgr__pill--unknown">
      NOT TESTED
    </span>
  )
}

// ── Step 1: Tenant Identity ────────────────────────────────────────────────

function Step1({ form, onChange, errors }) {
  return (
    <div>
      <h3 className="tenant-mgr__section-title">
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
      <h3 className="tenant-mgr__section-title">
        API credentials
      </h3>

      <FieldRow label="Auth Mode" required>
        <div className="tenant-mgr__radio-row">
          {[
            { value: 'standard', label: 'Standard',  available: true  },
            { value: 'advanced', label: 'Advanced',  available: false },
          ].map(opt => {
            const selected = form.auth_mode === opt.value
            const optionClass = [
              'tenant-mgr__radio-option',
              selected && 'tenant-mgr__radio-option--selected',
              !opt.available && 'tenant-mgr__radio-option--disabled',
            ].filter(Boolean).join(' ')
            return (
              <label key={opt.value} className={optionClass}>
                <input
                  type="radio"
                  name="auth_mode"
                  value={opt.value}
                  checked={selected}
                  disabled={!opt.available}
                  onChange={() => opt.available && onChange('auth_mode', opt.value)}
                  className="tenant-mgr__radio-input"
                />
                {opt.label}
                {!opt.available && (
                  <span className="tenant-mgr__radio-note">
                    (Slice 2)
                  </span>
                )}
              </label>
            )
          })}
        </div>
        <div className="tenant-mgr__radio-hint">
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
      <h3 className="tenant-mgr__section-title">
        Review & activate
      </h3>

      <table className="tenant-mgr__table">
        <tbody>
          {rows.map(r => (
            <tr key={r.label} className="tenant-mgr__table-row">
              <td className="tenant-mgr__table-label">{r.label}</td>
              <td className={`tenant-mgr__table-value${r.mono ? ' tenant-mgr__table-value--mono' : ''}`}>
                {r.value || <span className="tenant-mgr__table-empty">—</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {testResult && !testResult.ok && (
        <div className="tenant-mgr__alert tenant-mgr__alert--error">
          <strong>Test failed:</strong>{' '}
          {testResult.error || 'The tenant did not respond as expected.'}
        </div>
      )}
      {testResult && testResult.ok && (
        <div className="tenant-mgr__alert tenant-mgr__alert--success">
          Tenant is reachable.{' '}
          {testResult.status?.status && <span>Status: <strong>{testResult.status.status}</strong></span>}
        </div>
      )}

      <div className="tenant-mgr__actions">
        <button
          className="btn btn-primary tenant-mgr__btn-wide"
          onClick={onSaveAndTest}
          disabled={saving}
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
      <div className="tenant-mgr__footnote">
        The API key is encrypted with CORTEXSIM_SECRET before storage. It is never logged or returned in API responses.
      </div>
    </div>
  )
}

// ── Tenant List ────────────────────────────────────────────────────────────

function TenantRow({ tenant, active, onSelect, onTest, onDelete, testing }) {
  const cfg   = tenant.config || {}
  const name  = tenantKey(tenant)
  const verAt = tenant.last_verified_at
    ? new Date(tenant.last_verified_at).toLocaleString(undefined, { hour12: false })
    : null

  const rows = [
    { k: 'Base URL', v: cfg.base_url || '—' },
    { k: 'Region',   v: cfg.region || '—' },
    { k: 'Auth',     v: cfg.auth_mode || 'standard' },
    { k: 'Key ID',   v: cfg.api_key_id || '—' },
    ...(verAt ? [{ k: 'Tested', v: verAt }] : []),
  ]

  return (
    <div
      aria-current={active ? 'true' : undefined}
      className={`tenant-mgr__row${active ? ' tenant-mgr__row--active' : ''}`}
    >
      <div className="tenant-mgr__row-top">
        <span className="tenant-mgr__row-name">
          {tenant.name}
        </span>
        <HealthPill
          ok={testing ? undefined : tenant.last_verified_ok}
          testing={testing}
          error={tenant.last_verified_error}
        />
      </div>

      <div className="tenant-mgr__row-grid">
        {rows.map(r => (
          <div key={r.k} className="tenant-mgr__row-cell">
            <span className="tenant-mgr__row-k">{r.k}</span>
            <span className="tenant-mgr__row-v">{r.v}</span>
          </div>
        ))}
      </div>

      {tenant.last_verified_error && (
        <div className="tenant-mgr__row-error">
          ↳ {tenant.last_verified_error}
        </div>
      )}

      <div className="tenant-mgr__row-actions">
        {active ? (
          <span
            aria-label="Active tenant"
            className="tenant-mgr__badge-active"
          >
            ● ACTIVE
          </span>
        ) : (
          <button
            className="btn btn-sm btn-secondary tenant-mgr__btn-align"
            onClick={() => onSelect(name)}
            aria-pressed={false}
            aria-label={`Set ${name} as active tenant`}
          >
            Set active
          </button>
        )}

        <button
          className="btn btn-sm btn-secondary tenant-mgr__btn-nowrap"
          onClick={() => onTest(name)}
          disabled={testing}
        >
          {testing ? 'Testing…' : '▸ Test'}
        </button>

        <button
          className="btn btn-sm tenant-mgr__btn-delete"
          onClick={() => onDelete(name)}
          aria-label={`Remove tenant ${name}`}
        >
          ✕
        </button>
      </div>
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
  // ── Ambient scope from the EnvironmentProvider ──────────────────────────
  // The tenant LIST + the ACTIVE tenant pointer live in the provider so this
  // surface and the global header switcher share one source of truth. This
  // surface owns MANAGEMENT (register/test/delete); SELECTION writes through
  // to setTenant so a switch here re-scopes every surface, and vice-versa.
  const {
    tenants,
    tenant: activeTenant,
    setTenant,
    refreshTenants,
    loading,
  } = useEnvironment()

  const activeKey = tenantKey(activeTenant)

  const [step, setStep]           = useState(1)
  const [form, setForm]           = useState(EMPTY_FORM)
  const [errors, setErrors]       = useState({})
  const [saving, setSaving]       = useState(false)
  const [testResult, setTestResult] = useState(null)

  const [actionError, setActionError] = useState(null)
  const [testing, setTesting]     = useState({})   // { [name]: bool }

  // ── Confirmation dialog for delete ──────────────────────────────────────
  const [pendingDelete, setPendingDelete] = useState(null)

  // Reload the shared list (drives both this surface and the header switcher).
  const loadTenants = useCallback(() => {
    const r = refreshTenants()
    return r && typeof r.then === 'function' ? r : Promise.resolve()
  }, [refreshTenants])

  // ── Select / activate a tenant (shared with the header switcher) ─────────
  const handleSelect = useCallback((name) => {
    if (name) setTenant(name)
  }, [setTenant])

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
  // On save, if nothing is active yet, make the just-registered tenant active
  // so a first-run install lands with a live scope without a second click.
  const doSave = async () => {
    const savedName = form.name
    await registerXsiamTenant(form)
    if (!activeKey && savedName) setTenant(savedName)
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
      // The provider's stale-pointer guard re-scopes the active tenant to the
      // first-available entry when the active one is deleted — no action here.
      setActionError(null)
      loadTenants()
    } catch (e) {
      setActionError(e.message)
    } finally {
      setPendingDelete(null)
    }
  }

  // ── Render ───────────────────────────────────────────────────────────────
  return (
    <div className="tenant-mgr">

      {/* ── Delete confirm overlay ────────────────────────────────────────── */}
      {pendingDelete && (
        <div className="tenant-mgr__overlay">
          <div className="tenant-mgr__dialog">
            <h3 className="tenant-mgr__dialog-title">
              Remove tenant?
            </h3>
            <p className="tenant-mgr__dialog-text">
              The encrypted API key for <strong className="tenant-mgr__mono">{pendingDelete}</strong> will be permanently deleted. This action cannot be undone.
            </p>
            <div className="tenant-mgr__dialog-actions">
              <button className="btn btn-secondary btn-sm" onClick={() => setPendingDelete(null)}>
                Cancel
              </button>
              <button
                className="btn btn-sm tenant-mgr__btn-delete-confirm"
                onClick={handleDeleteConfirmed}
              >
                Delete
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── View header ───────────────────────────────────────────────────── */}
      <div className="tenant-mgr__header">
        <div className="tenant-mgr__accent-bar" />
        {/* Masthead eyebrow: the nav group alone, no "· Phase N" — this
            product has no app-level phase stepper to echo (M-4). */}
        <div className="tenant-mgr__eyebrow">Manage</div>
        <div className="tenant-mgr__header-row">
          <h2 className="tenant-mgr__title">
            XSIAM Tenants
          </h2>
          <span className="tenant-mgr__subtitle">
            Register and manage XSIAM tenant connections for health & metrics
          </span>
        </div>
        <div className="tenant-mgr__scope-line">
          Active scope:{' '}
          {activeTenant ? (
            <strong className="tenant-mgr__scope-value">
              {activeKey}
            </strong>
          ) : (
            <span className="tenant-mgr__scope-empty">none selected</span>
          )}
          <span className="tenant-mgr__scope-suffix">— shared with the global header switcher.</span>
        </div>
      </div>

      <div className="tenant-mgr__layout">

        {/* ── LEFT: Wizard ──────────────────────────────────────────────── */}
        <div className="tenant-mgr__card">
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
            <div className="tenant-mgr__nav">
              {step > 1 ? (
                <button className="btn btn-secondary btn-sm" onClick={handleBack}>← Back</button>
              ) : <div />}
              <button className="btn btn-primary" onClick={handleNext}>
                {step === 2 ? 'Review →' : 'Next →'}
              </button>
            </div>
          )}

          {step === 3 && (
            <div className="tenant-mgr__nav-single">
              <button className="btn btn-secondary btn-sm" onClick={handleBack}>← Back</button>
            </div>
          )}
        </div>

        {/* ── RIGHT: Registered tenants ─────────────────────────────────── */}
        <div>
          <div className="tenant-mgr__list-header">
            <h3 className="tenant-mgr__list-title">
              Registered Tenants
            </h3>
            <span className="tenant-mgr__count-badge">
              {tenants.length}
            </span>
          </div>

          {actionError && (
            <div className="tenant-mgr__error-banner">
              {actionError}
            </div>
          )}

          {tenants.length === 0 && loading?.tenants && (
            <div className="tenant-mgr__empty">
              Loading tenants…
            </div>
          )}

          {tenants.length === 0 && !loading?.tenants && (
            <div className="tenant-mgr__empty tenant-mgr__empty--bordered">
              No tenants registered yet.
              <br />Use the wizard to add your first XSIAM tenant.
            </div>
          )}

          <div className="tenant-mgr__list">
            {tenants.map(t => {
              const key = tenantKey(t)
              return (
                <TenantRow
                  key={key}
                  tenant={t}
                  active={key === activeKey}
                  onSelect={handleSelect}
                  testing={!!testing[key]}
                  onTest={handleTest}
                  onDelete={name => setPendingDelete(name)}
                />
              )
            })}
          </div>
        </div>
      </div>
    </div>
  )
}
