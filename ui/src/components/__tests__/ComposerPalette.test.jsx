/**
 * ComposerPalette — the NICE-organized left rail.
 *
 * The palette is a DUMB view: the container assembles API-sourced groups and
 * the palette only presents and narrows them. The behaviours pinned here are
 * exactly that boundary — it renders what it is given, it filters by the active
 * tab and the query as pure functions of props, it fires each item's own `add`
 * callback and nothing else, and it never fabricates a group of its own. The
 * preserved empty-state copy ('nothing matches "…"') is asserted so composer.css
 * and the ComposerView expectations stay satisfied when it is wired in.
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import ComposerPalette from '../console/ComposerPalette.jsx'

const TABS = [
  { id: 'build', label: 'Build' },
  { id: 'network', label: 'Network' },
  { id: 'endpoint', label: 'Endpoint' },
]

function groups({ onAdd = vi.fn() } = {}) {
  return [
    {
      label: 'Step kinds',
      tone: 'action',
      tab: 'build',
      items: [{ key: 'k-command', name: 'Command', meta: 'shell · identity-scoped', add: onAdd }],
    },
    {
      label: 'TTP cards',
      tone: 'detected',
      tab: 'endpoint',
      items: [
        { key: 't-0004', name: 'TTP-2026-0004', meta: 'credential dumping', add: vi.fn() },
      ],
    },
    {
      label: 'Targets',
      tone: 'signal',
      tab: 'network',
      items: [{ key: 'a-jump', name: 'jumpbox-01', meta: 'linux · online', add: vi.fn() }],
    },
  ]
}

describe('ComposerPalette', () => {
  it('renders the rail and its API-sourced groups verbatim', () => {
    render(<ComposerPalette groups={groups()} />)
    expect(screen.getByTestId('composer-palette')).toBeInTheDocument()
    // No tab filter (no tabs passed) ⇒ every group shows.
    expect(screen.getByText('Step kinds')).toBeInTheDocument()
    expect(screen.getByText('TTP cards')).toBeInTheDocument()
    expect(screen.getByText('Targets')).toBeInTheDocument()
    expect(screen.getByText('TTP-2026-0004')).toBeInTheDocument()
  })

  it('fires the item\'s own add callback, and nothing else', async () => {
    const onAdd = vi.fn()
    render(<ComposerPalette groups={groups({ onAdd })} />)
    await userEvent.click(screen.getByText('Command'))
    expect(onAdd).toHaveBeenCalledTimes(1)
  })

  it('narrows to the active tab via each group\'s tab field', () => {
    render(<ComposerPalette tabs={TABS} activeTab="endpoint" groups={groups()} />)
    // Only the endpoint-tabbed group is visible.
    expect(screen.getByText('TTP cards')).toBeInTheDocument()
    expect(screen.queryByText('Step kinds')).not.toBeInTheDocument()
    expect(screen.queryByText('Targets')).not.toBeInTheDocument()
    // Tab controls are present and marked.
    expect(screen.getByTestId('palette-tab-endpoint')).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByTestId('palette-tab-build')).toHaveAttribute('aria-selected', 'false')
  })

  it('flips the active tab through onTab', async () => {
    const onTab = vi.fn()
    render(<ComposerPalette tabs={TABS} activeTab="build" onTab={onTab} groups={groups()} />)
    await userEvent.click(screen.getByTestId('palette-tab-network'))
    expect(onTab).toHaveBeenCalledWith('network')
  })

  it('filters items by the query over name and meta, dropping empty groups', () => {
    render(<ComposerPalette query="credential" groups={groups()} />)
    expect(screen.getByText('TTP-2026-0004')).toBeInTheDocument() // meta matched
    expect(screen.queryByText('Step kinds')).not.toBeInTheDocument()
    expect(screen.queryByText('jumpbox-01')).not.toBeInTheDocument()
  })

  it('shows the preserved empty-state copy when nothing matches', () => {
    render(<ComposerPalette query="zzz-no-match" groups={groups()} />)
    expect(screen.getByText(/nothing matches “zzz-no-match”/)).toBeInTheDocument()
  })

  it('reports a still-loading surface honestly rather than a false empty', () => {
    render(<ComposerPalette groups={[]} loading />)
    expect(screen.getByText(/loading/i)).toBeInTheDocument()
    expect(screen.queryByText(/nothing matches/)).not.toBeInTheDocument()
  })

  it('honours a disabled item (unstageable payload) without firing add', async () => {
    const add = vi.fn()
    render(
      <ComposerPalette
        groups={[
          {
            label: 'Staged payloads',
            tone: 'pending',
            items: [{ key: 'p-x', name: 'linpeas.sh', meta: 'not staged', add, disabled: true, title: 'PAYLOAD_NOT_STAGED' }],
          },
        ]}
      />,
    )
    const btn = screen.getByText('linpeas.sh').closest('button')
    expect(btn).toBeDisabled()
    await userEvent.click(btn)
    expect(add).not.toHaveBeenCalled()
  })
})
