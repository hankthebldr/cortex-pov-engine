import React, { useEffect, useMemo, useState, useCallback } from 'react'
import {
  getTtp,
  getTtpSchema,
  createTtp,
  updateTtp,
  promoteTtp,
} from '../../api/client.js'

/**
 * TtpEditorView — minimal authoring surface for issue #59.
 *
 * Why a JSON editor instead of a structured form? The TTP schema is
 * deep (~25 nested objects, six required sections, polymorphic
 * detection arrays). A structured form is N weeks of UI work for the
 * lab-testing use case the issue called out. A JSON textarea + live
 * client-side schema validation gets the same lab-loop done in
 * minutes per card with no scope explosion.
 *
 * The wizard:
 *   1. Loads the schema once on mount (cacheable; backend serves
 *      ``/api/ttps/_schema``).
 *   2. Pre-fills the textarea — from the existing card if editing,
 *      else from a corpus template seeded into the placeholder.
 *   3. Highlights parse / schema errors inline as the operator types
 *      (debounced to ~250 ms so typing stays smooth).
 *   4. Save Draft → POST /api/ttps (new) or PUT /api/ttps/:id (edit).
 *   5. Promote → POST /api/ttps/:id/promote (drafts only).
 *
 * Auth: the underlying API endpoints are env-gated on
 * ``CORTEXSIM_AUTHORING_ENABLED``; this component checks the gate at
 * mount via the schema call and surfaces the 403 banner if disabled.
 */
export default function TtpEditorView({ editingTtpId = null, onClose, onSaved }) {
  const [schema, setSchema]     = useState(null)
  const [schemaErr, setSchemaErr] = useState(null)
  const [text, setText]         = useState('')
  const [parseErr, setParseErr] = useState(null)
  const [validateErr, setValidateErr] = useState(null)
  const [saveErr, setSaveErr]   = useState(null)
  const [saveOk, setSaveOk]     = useState(null)
  const [saving, setSaving]     = useState(false)
  const [authoringDisabled, setAuthoringDisabled] = useState(false)
  const isEdit = Boolean(editingTtpId)

  // 1. Load schema + (if editing) the existing card body.
  useEffect(() => {
    let cancelled = false
    async function init() {
      try {
        const s = await getTtpSchema()
        if (cancelled) return
        setSchema(s)
      } catch (e) {
        if (cancelled) return
        if (/AUTHORING_DISABLED/i.test(e?.message || '')) {
          setAuthoringDisabled(true)
        } else {
          setSchemaErr(e?.message || 'Failed to load TTP schema')
        }
        return
      }

      if (isEdit) {
        try {
          const card = await getTtp(editingTtpId)
          if (cancelled) return
          // The /api/ttps/:id response embeds `referenced_by_adapters`
          // — strip it so save echoes back the corpus shape.
          const { referenced_by_adapters: _ignore, ...rest } = card
          setText(JSON.stringify(rest, null, 2))
        } catch (e) {
          if (cancelled) return
          setSaveErr(e?.message || 'Failed to load TTP for editing')
        }
      } else {
        setText(_TEMPLATE)
      }
    }
    init()
    return () => { cancelled = true }
  }, [editingTtpId, isEdit])

  // 2. Debounced parse + schema-validate on text change.
  useEffect(() => {
    if (!text) { setParseErr(null); setValidateErr(null); return }
    const handle = setTimeout(() => {
      let parsed
      try {
        parsed = JSON.parse(text)
        setParseErr(null)
      } catch (e) {
        setParseErr(e?.message || 'Invalid JSON')
        setValidateErr(null)
        return
      }
      if (schema) {
        const errs = _validateAgainstSchema(parsed, schema)
        setValidateErr(errs)
      }
    }, 250)
    return () => clearTimeout(handle)
  }, [text, schema])

  const parsed = useMemo(() => {
    try { return JSON.parse(text) } catch { return null }
  }, [text])

  const canSave = !parseErr && !validateErr && parsed && schema && !saving
  const ttpIdInPayload = parsed?.id || null
  const cardStatus = parsed?.status || 'draft'

  const handleSave = useCallback(async () => {
    if (!canSave) return
    setSaving(true)
    setSaveErr(null)
    setSaveOk(null)
    try {
      let res
      if (isEdit) {
        res = await updateTtp(editingTtpId, parsed)
      } else {
        res = await createTtp(parsed)
      }
      setSaveOk(`Saved — ${res.ttp_id} (${res.status})`)
      onSaved?.(res)
    } catch (e) {
      setSaveErr(e?.message || 'Save failed')
    } finally {
      setSaving(false)
    }
  }, [canSave, isEdit, editingTtpId, parsed, onSaved])

  const handlePromote = useCallback(async () => {
    if (!ttpIdInPayload || !isEdit) return
    setSaving(true)
    setSaveErr(null)
    try {
      const res = await promoteTtp(ttpIdInPayload)
      setSaveOk(`Promoted — ${res.ttp_id} (${res.status}${res.moved ? '' : ', already active'})`)
      onSaved?.(res)
    } catch (e) {
      setSaveErr(e?.message || 'Promote failed')
    } finally {
      setSaving(false)
    }
  }, [ttpIdInPayload, isEdit, onSaved])

  if (authoringDisabled) {
    return (
      <div className="competitive__detail" data-testid="ttp-editor">
        <div className="competitive__detail-head">
          <h3 className="competitive__detail-title">TTP Editor — disabled</h3>
          <button type="button" className="btn" onClick={onClose}>Close</button>
        </div>
        <p className="adapter-registry__error mono">
          Authoring is disabled on this SimCore instance. Start the server with{' '}
          <span className="mono">CORTEXSIM_AUTHORING_ENABLED=true</span> to enable.
        </p>
      </div>
    )
  }

  return (
    <div className="competitive__detail" data-testid="ttp-editor">
      <div className="competitive__detail-head">
        <div>
          <div className="competitive__detail-eyebrow mono">
            {isEdit ? `editing · ${editingTtpId}` : 'new draft'}
          </div>
          <h3 className="competitive__detail-title">
            {isEdit ? 'Edit TTP card' : 'Author new TTP'}
          </h3>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          {isEdit && cardStatus === 'draft' && (
            <button
              type="button"
              className="btn"
              data-testid="ttp-editor-promote"
              disabled={saving}
              onClick={handlePromote}
              title="Move from _drafts/ to the active corpus"
            >
              Promote to active
            </button>
          )}
          <button
            type="button"
            className="btn"
            data-testid="ttp-editor-save"
            disabled={!canSave}
            onClick={handleSave}
          >
            {saving ? 'Saving…' : (isEdit ? 'Save changes' : 'Save draft')}
          </button>
          <button type="button" className="btn" onClick={onClose}>Close</button>
        </div>
      </div>

      {schemaErr && (
        <div className="adapter-registry__error mono" role="alert">{schemaErr}</div>
      )}
      {saveErr && (
        <div className="adapter-registry__error mono" role="alert" data-testid="ttp-editor-error">
          {saveErr}
        </div>
      )}
      {saveOk && (
        <div className="mono" role="status" data-testid="ttp-editor-saved"
             style={{ color: 'var(--c-success, #4FD1A1)', fontSize: 11, marginBottom: 8 }}>
          ✓ {saveOk}
        </div>
      )}

      <p
        className="mono"
        style={{ fontSize: 10, color: 'var(--c-text-muted)', margin: '6px 0' }}
      >
        Editing the full TTP JSON. Live-validated against{' '}
        <span className="mono">detection_scanner/schema/ttp-entry.schema.json</span>.
        Save creates a draft under{' '}
        <span className="mono">detection_scanner/ttps/_drafts/</span>; Promote moves
        it to the active corpus.
      </p>

      <textarea
        data-testid="ttp-editor-textarea"
        value={text}
        spellCheck={false}
        onChange={(e) => setText(e.target.value)}
        className="mono"
        style={{
          width: '100%',
          minHeight: 420,
          fontSize: 11,
          padding: '8px 10px',
          background: 'var(--c-bg-subtle, rgba(0,0,0,0.25))',
          border: `1px solid ${(parseErr || validateErr)
            ? 'var(--c-error, salmon)'
            : 'var(--c-hairline, rgba(255,255,255,0.08))'}`,
          borderRadius: 4,
          color: 'var(--c-text-primary)',
          resize: 'vertical',
        }}
      />

      {parseErr && (
        <div
          className="mono"
          data-testid="ttp-editor-parse-error"
          style={{ fontSize: 11, color: 'var(--c-error, salmon)', marginTop: 6 }}
        >
          JSON parse: {parseErr}
        </div>
      )}

      {!parseErr && validateErr && (
        <div
          className="mono"
          data-testid="ttp-editor-validate-error"
          style={{ fontSize: 11, color: 'var(--c-error, salmon)', marginTop: 6 }}
        >
          Schema: {validateErr.message}
          {validateErr.path?.length > 0 && (
            <> &mdash; at <span style={{ fontWeight: 600 }}>{validateErr.path.join('.')}</span></>
          )}
        </div>
      )}

      {!parseErr && !validateErr && parsed && (
        <div
          className="mono"
          data-testid="ttp-editor-valid"
          style={{ fontSize: 11, color: 'var(--c-success, #4FD1A1)', marginTop: 6 }}
        >
          ✓ JSON parses and validates against the TTP schema
        </div>
      )}
    </div>
  )
}

// ───────────────────────────────────────────────────────────────────────────
// Minimal schema validator — covers required-property + type checks +
// enum + minLength + maxLength. The backend re-validates against the full
// jsonschema spec on save; this client-side pass just gives fast feedback
// for the most common authoring mistakes (missing key, wrong type, summary
// too long for the 400-char cap).
// ───────────────────────────────────────────────────────────────────────────

function _validateAgainstSchema(doc, schema) {
  const issues = _validateNode(doc, schema, [])
  return issues.length > 0 ? issues[0] : null
}

function _validateNode(value, node, path) {
  const issues = []
  if (!node) return issues

  // Type check.
  if (node.type) {
    const expected = node.type
    const actual   = _jsonType(value)
    if (expected !== actual && !(expected === 'integer' && actual === 'number' && Number.isInteger(value))) {
      issues.push({ message: `expected ${expected}, got ${actual}`, path })
      return issues  // bail — further checks would be noisy
    }
  }

  if (node.enum && !node.enum.includes(value)) {
    issues.push({
      message: `value must be one of: ${node.enum.slice(0, 6).join(', ')}${node.enum.length > 6 ? '…' : ''}`,
      path,
    })
  }

  if (typeof value === 'string') {
    if (typeof node.minLength === 'number' && value.length < node.minLength) {
      issues.push({ message: `string too short (min ${node.minLength})`, path })
    }
    if (typeof node.maxLength === 'number' && value.length > node.maxLength) {
      issues.push({ message: `string too long (max ${node.maxLength})`, path })
    }
    if (node.pattern) {
      try {
        const re = new RegExp(node.pattern)
        if (!re.test(value)) {
          issues.push({ message: `does not match pattern ${node.pattern}`, path })
        }
      } catch { /* invalid regex from schema — ignore client-side */ }
    }
  }

  if (node.type === 'object' && value && typeof value === 'object') {
    for (const req of (node.required || [])) {
      if (!(req in value)) {
        issues.push({ message: `missing required property "${req}"`, path: [...path, req] })
      }
    }
    if (node.properties) {
      for (const [key, sub] of Object.entries(node.properties)) {
        if (key in value) {
          issues.push(..._validateNode(value[key], sub, [...path, key]))
        }
      }
    }
  }

  if (node.type === 'array' && Array.isArray(value)) {
    if (typeof node.minItems === 'number' && value.length < node.minItems) {
      issues.push({ message: `array too short (min ${node.minItems})`, path })
    }
    if (node.items) {
      value.forEach((item, i) => {
        issues.push(..._validateNode(item, node.items, [...path, String(i)]))
      })
    }
  }

  return issues
}

function _jsonType(v) {
  if (v === null) return 'null'
  if (Array.isArray(v)) return 'array'
  return typeof v
}

// Minimal placeholder so the operator isn't staring at an empty box.
// Not schema-valid on purpose — the live validator will guide the
// operator to fill the required fields.
const _TEMPLATE = `{
  "id": "TTP-2026-NNNN",
  "schema_version": "1.0.0",
  "entry_version": "0.1.0",
  "status": "draft",
  "metadata": {
    "created_at": "2026-05-30T00:00:00Z",
    "updated_at": "2026-05-30T00:00:00Z",
    "authors": [
      { "name": "Domain Consultant" }
    ],
    "tags": ["lab-test"],
    "pov_engine": {
      "auto_load": true,
      "simulation_class": "endpoint",
      "destructive": false,
      "platforms": ["linux"]
    }
  },
  "identity": {
    "name": "Working title — replace before save",
    "summary": "Describe what this TTP simulates and what Cortex detects. 20–400 characters."
  },
  "threat_context": { "actors": [] },
  "mitre_attack": {
    "matrix": "enterprise",
    "techniques": [
      {
        "technique_id": "T1059",
        "name": "Command and Scripting Interpreter",
        "tactic_ids": ["TA0002"],
        "tactic_names": ["Execution"]
      }
    ],
    "kill_chain_phase": "actions-on-objectives",
    "data_sources": ["Process"]
  },
  "execution": {
    "target_platform": "linux",
    "privilege_required": "user",
    "payload": {
      "interpreter": "bash",
      "code": "echo simulate; true"
    }
  },
  "detections": {
    "iocs": [],
    "biocs": [],
    "xql_queries": [],
    "correlation_rules": [],
    "analytics_modules": []
  },
  "panw_mapping": {
    "products": [
      { "module": "cortex-xdr", "coverage_tier": "detection", "rule_ids": [] }
    ]
  },
  "remediation_guidance": {
    "preventive_controls": [],
    "detection_engineering": []
  },
  "references": [
    {
      "title": "Replace with a real reference",
      "url": "https://example.invalid/",
      "publisher": "TODO"
    }
  ],
  "changelog": [
    {
      "entry_version": "0.1.0",
      "date": "2026-05-30",
      "author": "Domain Consultant",
      "change": "Initial draft."
    }
  ]
}
`
