/**
 * Tests for TtpEditorView — the authoring wizard shipped for issue #59.
 *
 * The editor renders a JSON textarea + live schema validation against
 * the GET /api/ttps/_schema response. These tests mock the schema +
 * the save endpoints and assert the UI affordances (validate-on-type,
 * save → POST/PUT, promote button visibility) behave per contract.
 */
import React from 'react'
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import TtpEditorView from '../console/TtpEditorView.jsx'
import { installRoutes } from '../../test/mockFetch.js'

void React

// Minimal schema slice — enough to exercise required-field validation
// and the summary maxLength rule.
const minimalSchema = {
  type: 'object',
  required: ['id', 'identity'],
  properties: {
    id: { type: 'string', pattern: '^TTP-\\d{4}-\\d{4}$' },
    status: { type: 'string', enum: ['active', 'draft', 'deprecated'] },
    identity: {
      type: 'object',
      required: ['name', 'summary'],
      properties: {
        name:    { type: 'string', minLength: 4, maxLength: 140 },
        summary: { type: 'string', minLength: 20, maxLength: 400 },
      },
    },
  },
}

const validPayload = {
  id: 'TTP-2026-9999',
  status: 'draft',
  identity: {
    name: 'Test card',
    summary: 'A summary long enough to satisfy the 20-character minimum.',
  },
}

describe('<TtpEditorView />', () => {
  it('renders the new-draft view with the placeholder template', async () => {
    installRoutes({ 'GET /api/ttps/_schema': minimalSchema })
    render(<TtpEditorView onClose={() => {}} />)
    await waitFor(() => expect(screen.getByTestId('ttp-editor')).toBeInTheDocument())
    const ta = screen.getByTestId('ttp-editor-textarea')
    expect(ta.value).toContain('TTP-2026-NNNN')
    expect(ta.value).toContain('"schema_version"')
  })

  it('shows JSON parse-error banner for invalid JSON', async () => {
    installRoutes({ 'GET /api/ttps/_schema': minimalSchema })
    render(<TtpEditorView onClose={() => {}} />)
    await waitFor(() => expect(screen.getByTestId('ttp-editor-textarea')).toBeInTheDocument())
    fireEvent.change(screen.getByTestId('ttp-editor-textarea'), {
      target: { value: '{not valid json' },
    })
    await waitFor(() => expect(screen.getByTestId('ttp-editor-parse-error')).toBeInTheDocument())
    // Save must be disabled while the JSON is broken.
    expect(screen.getByTestId('ttp-editor-save')).toBeDisabled()
  })

  it('shows schema-error banner for parse-ok but schema-invalid input', async () => {
    installRoutes({ 'GET /api/ttps/_schema': minimalSchema })
    render(<TtpEditorView onClose={() => {}} />)
    await waitFor(() => expect(screen.getByTestId('ttp-editor-textarea')).toBeInTheDocument())
    // Summary too short to pass minLength=20
    fireEvent.change(screen.getByTestId('ttp-editor-textarea'), {
      target: { value: JSON.stringify({
        id: 'TTP-2026-1111',
        identity: { name: 'Too short', summary: 'short' },
      }) },
    })
    await waitFor(() => expect(screen.getByTestId('ttp-editor-validate-error')).toBeInTheDocument())
    expect(screen.getByTestId('ttp-editor-save')).toBeDisabled()
  })

  it('shows the green valid-banner when JSON parses + validates', async () => {
    installRoutes({ 'GET /api/ttps/_schema': minimalSchema })
    render(<TtpEditorView onClose={() => {}} />)
    await waitFor(() => expect(screen.getByTestId('ttp-editor-textarea')).toBeInTheDocument())
    fireEvent.change(screen.getByTestId('ttp-editor-textarea'), {
      target: { value: JSON.stringify(validPayload) },
    })
    await waitFor(() => expect(screen.getByTestId('ttp-editor-valid')).toBeInTheDocument())
    expect(screen.getByTestId('ttp-editor-save')).not.toBeDisabled()
  })

  it('Save → POST /api/ttps for a new draft and surfaces the saved banner', async () => {
    const posted = []
    installRoutes({
      'GET /api/ttps/_schema': minimalSchema,
      'POST /api/ttps': (_url, init) => {
        posted.push(JSON.parse(init.body))
        return { ttp_id: 'TTP-2026-9999', status: 'draft', path: '_drafts/x.json' }
      },
    })
    const onSaved = vi.fn()
    render(<TtpEditorView onClose={() => {}} onSaved={onSaved} />)
    await waitFor(() => expect(screen.getByTestId('ttp-editor-textarea')).toBeInTheDocument())
    fireEvent.change(screen.getByTestId('ttp-editor-textarea'), {
      target: { value: JSON.stringify(validPayload) },
    })
    await waitFor(() => expect(screen.getByTestId('ttp-editor-valid')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('ttp-editor-save'))
    await waitFor(() => expect(screen.getByTestId('ttp-editor-saved')).toBeInTheDocument())
    expect(posted).toHaveLength(1)
    expect(posted[0].id).toBe('TTP-2026-9999')
    expect(onSaved).toHaveBeenCalled()
  })

  it('edit mode → PUT /api/ttps/:id with the loaded body', async () => {
    const existing = {
      ...validPayload,
      id: 'TTP-2026-0004',
      identity: { name: 'Existing', summary: 'A long enough existing summary.' },
      referenced_by_adapters: [{ adapter_id: 'TOOL-X' }],  // backend echo, must be stripped
    }
    const puts = []
    installRoutes({
      'GET /api/ttps/_schema': minimalSchema,
      'GET /api/ttps/TTP-2026-0004': existing,
      'PUT /api/ttps/TTP-2026-0004': (_url, init) => {
        puts.push(JSON.parse(init.body))
        return { ttp_id: 'TTP-2026-0004', status: 'active', path: 'x.json' }
      },
    })
    render(<TtpEditorView editingTtpId="TTP-2026-0004" onClose={() => {}} />)
    await waitFor(() => {
      const ta = screen.getByTestId('ttp-editor-textarea')
      expect(ta.value).toContain('TTP-2026-0004')
    })
    // Save without modifying — the loaded body should already validate.
    await waitFor(() => expect(screen.getByTestId('ttp-editor-valid')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('ttp-editor-save'))
    await waitFor(() => expect(screen.getByTestId('ttp-editor-saved')).toBeInTheDocument())
    expect(puts).toHaveLength(1)
    expect(puts[0].id).toBe('TTP-2026-0004')
    // referenced_by_adapters MUST be stripped — it's not in the corpus schema
    expect(puts[0].referenced_by_adapters).toBeUndefined()
  })

  it('shows Promote button only when editing a draft', async () => {
    installRoutes({
      'GET /api/ttps/_schema': minimalSchema,
      'GET /api/ttps/TTP-2026-DRAFT': { ...validPayload, id: 'TTP-2026-DRAFT', status: 'draft' },
      'GET /api/ttps/TTP-2026-ACTIVE': { ...validPayload, id: 'TTP-2026-ACTIVE', status: 'active' },
    })

    const { rerender } = render(<TtpEditorView editingTtpId="TTP-2026-DRAFT" onClose={() => {}} />)
    await waitFor(() => expect(screen.getByTestId('ttp-editor-textarea')).toBeInTheDocument())
    await waitFor(() => expect(screen.getByTestId('ttp-editor-promote')).toBeInTheDocument())

    rerender(<TtpEditorView editingTtpId="TTP-2026-ACTIVE" onClose={() => {}} />)
    await waitFor(() => {
      const ta = screen.getByTestId('ttp-editor-textarea')
      expect(ta.value).toContain('TTP-2026-ACTIVE')
    })
    expect(screen.queryByTestId('ttp-editor-promote')).not.toBeInTheDocument()
  })

  it('renders the disabled banner when the server rejects the schema fetch with AUTHORING_DISABLED', async () => {
    installRoutes({
      'GET /api/ttps/_schema': (_url) => new Response(
        JSON.stringify({ detail: { error: 'Authoring disabled', code: 'AUTHORING_DISABLED' } }),
        { status: 403, headers: { 'content-type': 'application/json' } },
      ),
    })
    render(<TtpEditorView onClose={() => {}} />)
    await waitFor(() => {
      expect(screen.getByText(/Authoring is disabled/i)).toBeInTheDocument()
    })
  })

  it('save error surfaces in the error banner without closing the editor', async () => {
    installRoutes({
      'GET /api/ttps/_schema': minimalSchema,
      'POST /api/ttps': (_url) => new Response(
        JSON.stringify({ detail: { error: 'Save failed', code: 'TTP_ID_CONFLICT' } }),
        { status: 409, headers: { 'content-type': 'application/json' } },
      ),
    })
    render(<TtpEditorView onClose={() => {}} />)
    await waitFor(() => expect(screen.getByTestId('ttp-editor-textarea')).toBeInTheDocument())
    fireEvent.change(screen.getByTestId('ttp-editor-textarea'), {
      target: { value: JSON.stringify(validPayload) },
    })
    await waitFor(() => expect(screen.getByTestId('ttp-editor-valid')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('ttp-editor-save'))
    await waitFor(() => expect(screen.getByTestId('ttp-editor-error')).toBeInTheDocument())
    // Editor still rendered — the operator can fix and retry.
    expect(screen.getByTestId('ttp-editor')).toBeInTheDocument()
  })
})
