/**
 * ComposerStitchPanel — the scenario-level Stitch Context authoring view.
 *
 * Isolated-testable like ComposerPalette. What is pinned here is the contract
 * ComposerInspector/ComposerView depend on when it is wired in:
 *   - the nine entity keys render, NICE-grouped (container_id is Cloud, host is
 *     Endpoint)
 *   - the resolve <select> offers ONLY the directives compatible with each key,
 *     so a picker can never author a value the backend 422s
 *   - cloud_resource is literal-only (its resolve select is disabled)
 *   - every edit is an onSetEntity callback — the panel holds no state
 *   - the footer surfaces validateStitchContext problems and the 5-tuple badge
 *   - THE HONESTY RULE: from_agent previews "resolves to <agentName>", never a
 *     fabricated concrete value
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import ComposerStitchPanel from '../console/ComposerStitchPanel.jsx'
import { ENTITY_KEYS, directivesForKey } from '../console/stitchContext.js'

function renderPanel(overrides = {}) {
  const onSetEntity = vi.fn()
  render(
    <ComposerStitchPanel
      model={overrides.model ?? null}
      onSetEntity={onSetEntity}
      agentName={overrides.agentName ?? 'web-prod-01'}
    />,
  )
  return { onSetEntity }
}

describe('ComposerStitchPanel — structure', () => {
  it('renders the panel and one header per NICE group', () => {
    renderPanel()
    expect(screen.getByTestId('composer-stitch-panel')).toBeInTheDocument()
    expect(screen.getByTestId('stitch-group-network')).toBeInTheDocument()
    expect(screen.getByTestId('stitch-group-identity')).toBeInTheDocument()
    expect(screen.getByTestId('stitch-group-cloud')).toBeInTheDocument()
    expect(screen.getByTestId('stitch-group-endpoint')).toBeInTheDocument()
  })

  it('renders a row for every one of the nine entity keys', () => {
    renderPanel()
    for (const key of ENTITY_KEYS) {
      expect(screen.getByTestId(`stitch-row-${key}`)).toBeInTheDocument()
    }
    // container_id sits under Cloud, host under Endpoint (not "tidied" together).
    expect(within(screen.getByTestId('stitch-group-cloud'))
      .getByTestId('stitch-row-container_id')).toBeInTheDocument()
    expect(within(screen.getByTestId('stitch-group-endpoint'))
      .getByTestId('stitch-row-host')).toBeInTheDocument()
  })
})

describe('ComposerStitchPanel — resolve options are the compatible set only', () => {
  it('src_ip offers exactly directivesForKey(src_ip), and NOT incompatible ones', () => {
    renderPanel()
    const select = screen.getByLabelText('Resolve directive for src_ip')
    const options = within(select).getAllByRole('option')
      .map((o) => o.value)
      .filter(Boolean) // drop the "— resolve —" placeholder
    expect(options).toEqual(directivesForKey('src_ip')) // auto_ip, auto_5tuple, from_agent
    expect(options).not.toContain('auto_container_id')
    expect(options).not.toContain('canary_principal')
  })

  it('cloud_resource is literal-only — its resolve select is disabled', () => {
    renderPanel()
    const select = screen.getByLabelText('Resolve directive for cloud_resource')
    expect(select).toBeDisabled()
    const row = screen.getByTestId('stitch-row-cloud_resource')
    expect(within(row).getByText(/literal only/i)).toBeInTheDocument()
  })
})

describe('ComposerStitchPanel — edits call back, hold no state', () => {
  it('typing a literal calls onSetEntity(key, {literal})', async () => {
    const user = userEvent.setup()
    const { onSetEntity } = renderPanel()
    await user.type(screen.getByLabelText('Literal for dst_port'), '4')
    expect(onSetEntity).toHaveBeenCalledWith('dst_port', { literal: '4' })
  })

  it('choosing a directive calls onSetEntity(key, {resolve})', async () => {
    const user = userEvent.setup()
    const { onSetEntity } = renderPanel()
    await user.selectOptions(screen.getByLabelText('Resolve directive for account'), 'canary_principal')
    expect(onSetEntity).toHaveBeenCalledWith('account', { resolve: 'canary_principal' })
  })

  it('clearing a declared key calls onSetEntity(key, null)', async () => {
    const user = userEvent.setup()
    const { onSetEntity } = renderPanel({ model: { host: { resolve: 'from_agent' } } })
    await user.click(screen.getByRole('button', { name: 'Clear host' }))
    expect(onSetEntity).toHaveBeenCalledWith('host', null)
  })
})

describe('ComposerStitchPanel — footer honesty', () => {
  it('surfaces a validateStitchContext problem for a both-keys entry', () => {
    // Invalid: both literal AND resolve — the panel must SHOW it so the DC fixes it.
    renderPanel({ model: { src_ip: { literal: '10.0.0.1', resolve: 'auto_ip' } } })
    const problems = screen.getByTestId('stitch-panel-problems')
    expect(problems.textContent).toMatch(/both a literal and a resolve/i)
  })

  it('surfaces an incompatible-directive problem', () => {
    renderPanel({ model: { host: { resolve: 'auto_5tuple' } } })
    expect(screen.getByTestId('stitch-panel-problems').textContent)
      .toMatch(/cannot resolve/i)
  })

  it('shows the 5-tuple badge when all five tuple keys are declared', () => {
    renderPanel({
      model: {
        src_ip: { resolve: 'auto_ip' }, dst_ip: { literal: '203.0.113.10' },
        src_port: { resolve: 'auto_port' }, dst_port: { literal: 443 },
        protocol: { literal: 'tcp' },
      },
    })
    expect(screen.getByTestId('stitch-five-tuple-badge')).toBeInTheDocument()
  })

  it('shows the 5-tuple badge when a single auto_5tuple is declared', () => {
    renderPanel({ model: { src_ip: { resolve: 'auto_5tuple' } } })
    expect(screen.getByTestId('stitch-five-tuple-badge')).toBeInTheDocument()
  })

  it('no 5-tuple badge when the tuple is incomplete', () => {
    renderPanel({ model: { src_ip: { resolve: 'auto_ip' } } })
    expect(screen.queryByTestId('stitch-five-tuple-badge')).not.toBeInTheDocument()
  })
})

describe('ComposerStitchPanel — the honesty rule', () => {
  it('from_agent previews "resolves to <agentName>", never a fabricated value', () => {
    renderPanel({ model: { host: { resolve: 'from_agent' } }, agentName: 'web-prod-01' })
    const preview = screen.getByTestId('stitch-preview-host')
    expect(preview.textContent).toMatch(/resolves to/i)
    expect(within(preview).getByText('web-prod-01')).toBeInTheDocument()
  })

  it('renders directive NAMES only — no fabricated 5-tuple appears anywhere', () => {
    renderPanel({
      model: {
        src_ip: { resolve: 'auto_ip' }, account: { resolve: 'canary_principal' },
      },
    })
    // The panel must not invent a concrete address or canary UPN.
    expect(screen.queryByText(/10\.\d+\.\d+\.\d+/)).not.toBeInTheDocument()
    expect(screen.queryByText(/cortexsim-canary\.invalid/)).not.toBeInTheDocument()
  })
})
