import React from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, within, fireEvent, waitFor } from '@testing-library/react'
import ComponentPage from '../ComponentPage.jsx'

void React

/**
 * The component page shape (design v26 §4.2), shared by all eight components:
 * a header with status, Configure and Test; an issue line only when something
 * is wrong; Services and Configuration rows that drill into the step that
 * fixes them; tabs Overview · Setup · one management tab.
 */
const base = {
  componentKey: 'demo',
  name: 'Demo',
  icon: 'people',
  status: { status: 'up', detail: 'All fine' },
  services: [],
  config: [],
  setup: () => <div data-testid="setup-frame">frame</div>,
  onTest: async () => ({ state: 'passed' }),
}

function mount(over = {}) {
  const setParams = vi.fn()
  const onNavigate = vi.fn()
  render(<ComponentPage {...base} params={{}} setParams={setParams} onNavigate={onNavigate} {...over} />)
  return { setParams, onNavigate }
}

describe('component page', () => {
  beforeEach(() => window.localStorage.clear())

  it('heads the page with its name, its status as icon + word, Configure and Test', () => {
    mount()
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Demo')
    expect(screen.getByRole('img', { name: 'Up' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Configure' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Test' })).toBeInTheDocument()
  })

  it('Configure opens the Setup tab at the first step not done', () => {
    const { setParams } = mount()
    fireEvent.click(screen.getByRole('button', { name: 'Configure' }))
    expect(setParams).toHaveBeenCalledWith({ tab: 'setup', step: null })
  })

  it('Test reads Testing…, then Passed', async () => {
    let finish
    mount({ onTest: () => new Promise((r) => { finish = r }) })
    fireEvent.click(screen.getByRole('button', { name: 'Test' }))
    expect(screen.getByRole('button', { name: 'Testing…' })).toBeDisabled()
    finish({ state: 'passed' })
    expect(await screen.findByRole('button', { name: 'Passed' })).toBeInTheDocument()
  })

  it('a failed Test is an error badge that opens the step that fixes it', async () => {
    const { setParams } = mount({
      onTest: async () => ({ state: 'failed', issue: 'HTTP 401', step: 'key' }),
    })
    fireEvent.click(screen.getByRole('button', { name: 'Test' }))
    const badge = await screen.findByRole('button', { name: /HTTP 401/ })
    fireEvent.click(badge)
    expect(setParams).toHaveBeenCalledWith({ tab: 'setup', step: 'key' })
  })

  it('shows an issue line only when something is wrong', () => {
    const { setParams } = mount({ status: { status: 'down', issue: 'None online', detail: 'No agent is polling.' }, issueStep: 'online' })
    const issue = screen.getByTestId('cmp-issue')
    expect(issue).toHaveTextContent('No agent is polling.')
    fireEvent.click(within(issue).getByRole('button', { name: /none online/i }))
    expect(setParams).toHaveBeenCalledWith({ tab: 'setup', step: 'online' })
  })

  it('has no issue line when all is well', () => {
    mount()
    expect(screen.queryByTestId('cmp-issue')).toBeNull()
  })

  it('a component never configured reads Off and makes Configure the one primary', () => {
    mount({ status: { status: 'off', detail: 'No agent has enrolled' } })
    expect(screen.getByRole('img', { name: 'Off' })).toBeInTheDocument()
    expect(screen.getByText('Configure it to unlock its scenarios.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Configure' })).toHaveClass('kit-btn--primary')
    expect(screen.getByRole('button', { name: 'Test' })).not.toHaveClass('kit-btn--primary')
  })

  it('services are buttons: View when up, Troubleshoot when not', () => {
    const { setParams } = mount({
      services: [
        { id: 'a', name: 'host-a', status: 'up', since: 'seen 2s ago', drill: { tab: 'fleet' } },
        { id: 'b', name: 'host-b', status: 'down', since: 'seen 9m ago', drill: { tab: 'setup', step: 'online' } },
      ],
      manageTab: { id: 'fleet', label: 'Fleet', render: () => null },
    })
    const services = screen.getByRole('region', { name: 'Services' })
    const a = within(services).getByRole('button', { name: /host-a/ })
    const b = within(services).getByRole('button', { name: /host-b/ })
    expect(a).toHaveTextContent('View ›')
    expect(b).toHaveTextContent('Troubleshoot ›')
    fireEvent.click(b)
    expect(setParams).toHaveBeenLastCalledWith({ tab: 'setup', step: 'online' })
  })

  it('configuration rows read OK · Missing · Changed · Can\'t check, failing first', () => {
    mount({
      config: [
        { id: 'ok', label: 'Thing that is fine', state: 'ok', drill: { tab: 'setup', step: 'x' } },
        { id: 'cc', label: 'Thing nobody can read', state: 'cant_check', drill: { tab: 'setup', step: 'y' } },
        { id: 'ch', label: 'Thing that drifted', state: 'changed', drill: { tab: 'setup', step: 'z' } },
        { id: 'mi', label: 'Thing that is missing', state: 'missing', drill: { tab: 'setup', step: 'w' } },
      ],
    })
    const cfg = screen.getByRole('region', { name: 'Configuration' })
    const rows = within(cfg).getAllByRole('button')
    expect(rows.map((r) => r.textContent)).toEqual([
      expect.stringContaining('missing'),
      expect.stringContaining('drifted'),
      expect.stringContaining('nobody can read'),
      expect.stringContaining('fine'),
    ])
    expect(within(rows[0]).getByRole('img', { name: 'Missing' })).toBeInTheDocument()
    expect(within(rows[1]).getByRole('img', { name: 'Changed' })).toBeInTheDocument()
    expect(within(rows[2]).getByRole('img', { name: "Can't check" })).toBeInTheDocument()
    expect(within(rows[3]).getByRole('img', { name: 'OK' })).toBeInTheDocument()
    expect(rows[0]).toHaveTextContent('Fix ›')
    expect(rows[3]).toHaveTextContent('View ›')
  })

  it('a configuration item the DC ticked reads You confirmed, never OK', () => {
    mount({ config: [{ id: 'c', label: 'Registered with a token', state: 'confirmed', drill: { tab: 'setup', step: 'register' } }] })
    const row = within(screen.getByRole('region', { name: 'Configuration' })).getByRole('button')
    expect(within(row).getByRole('img', { name: 'You confirmed' })).toBeInTheDocument()
    expect(within(row).queryByRole('img', { name: 'OK' })).toBeNull()
  })

  it('tabs are Overview · Setup · the management tab, the selected one pressed', () => {
    const { setParams } = mount({ manageTab: { id: 'fleet', label: 'Fleet', render: () => <p>fleet body</p> }, params: { tab: 'fleet' } })
    const tabs = screen.getByRole('group', { name: 'Component pages' })
    expect(within(tabs).getAllByRole('button').map((b) => b.textContent)).toEqual(['Overview', 'Setup', 'Fleet'])
    expect(within(tabs).getByRole('button', { name: 'Fleet' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByText('fleet body')).toBeInTheDocument()
    fireEvent.click(within(tabs).getByRole('button', { name: 'Setup' }))
    expect(setParams).toHaveBeenCalledWith({ tab: 'setup', step: null })
  })

  it('a component with no management tab has two tabs', () => {
    mount()
    const tabs = screen.getByRole('group', { name: 'Component pages' })
    expect(within(tabs).getAllByRole('button').map((b) => b.textContent)).toEqual(['Overview', 'Setup'])
  })

  it('on Setup the step owns the one primary, so the header buttons step back', () => {
    mount({ params: { tab: 'setup' }, status: { status: 'off' } })
    expect(screen.getByRole('button', { name: 'Configure' })).not.toHaveClass('kit-btn--primary')
    expect(screen.getByRole('button', { name: 'Test' })).not.toHaveClass('kit-btn--primary')
  })

  it('the Setup tab is the Configure frame', async () => {
    mount({ params: { tab: 'setup' } })
    await waitFor(() => expect(screen.getByTestId('setup-frame')).toBeInTheDocument())
  })
})
