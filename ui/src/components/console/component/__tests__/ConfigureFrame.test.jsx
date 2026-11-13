import React, { useState } from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, within, fireEvent, waitFor, act } from '@testing-library/react'
import ConfigureFrame from '../ConfigureFrame.jsx'

void React

/**
 * The Configure frame (design v26 §4.3): one checklist for every component.
 * Each step has a title, a why, where it happens, its fields, an optional
 * copyable command and who verifies it — POVengine, Cortex, or the DC, whose
 * tick reads "You confirmed" and never "Passed".
 */
function Harness({ steps, initial = null, paths = null, ctx = {} }) {
  const [current, setCurrent] = useState(initial)
  const [path, setPath] = useState(paths ? paths.options[0][0] : null)
  return (
    <ConfigureFrame
      confirmPrefix="test"
      steps={typeof steps === 'function' ? steps(path) : steps}
      current={current}
      onSelect={setCurrent}
      pathPicker={paths ? { ...paths, value: path, onChange: setPath } : null}
      ctx={ctx}
    />
  )
}

const step = (over) => ({ id: 's', title: 'A step', why: 'Why it matters.', verifier: 'pov', ...over })
const list = () => screen.getByRole('list', { name: /steps/i })
const panel = () => screen.getByRole('region', { name: /step/i })

describe('Configure frame', () => {
  beforeEach(() => window.localStorage.clear())

  it('lists the steps in order and shows the current one beside them', () => {
    render(<Harness steps={[
      step({ id: 'a', title: 'First', where: 'Cortex › Settings', fields: [['Name', 'pov-http']], command: 'echo hi' }),
      step({ id: 'b', title: 'Second' }),
    ]} />)
    const items = within(list()).getAllByRole('listitem')
    expect(list().tagName).toBe('OL')
    expect(items.map((li) => li.textContent)).toEqual([expect.stringContaining('First'), expect.stringContaining('Second')])
    expect(within(items[0]).getByRole('button')).toHaveAttribute('aria-current', 'step')
    expect(within(panel()).getByRole('heading', { name: 'First' })).toBeInTheDocument()
    expect(panel()).toHaveTextContent('Why it matters.')
    expect(panel()).toHaveTextContent('Cortex › Settings')
    expect(panel()).toHaveTextContent('pov-http')
    expect(within(panel()).getByText('echo hi')).toBeInTheDocument()
    expect(within(panel()).getByRole('button', { name: /copy/i })).toBeInTheDocument()
  })

  it('selecting a step in the list shows it', () => {
    render(<Harness steps={[step({ id: 'a', title: 'First' }), step({ id: 'b', title: 'Second' })]} />)
    fireEvent.click(within(list()).getByRole('button', { name: /second/i }))
    expect(within(panel()).getByRole('heading', { name: 'Second' })).toBeInTheDocument()
  })

  it('says who verifies each step', () => {
    const { rerender } = render(<Harness steps={[step({ verifier: 'pov', test: async () => ({ state: 'passed' }) })]} />)
    expect(panel()).toHaveTextContent('POVengine tests this step.')
    expect(within(panel()).getByRole('button', { name: 'Test' })).toBeInTheDocument()
    rerender(<Harness steps={[step({ verifier: 'cortex', test: async () => ({ state: 'passed' }) })]} />)
    expect(panel()).toHaveTextContent('Cortex shows the result — Test reads it back.')
    rerender(<Harness steps={[step({ verifier: 'you' })]} />)
    expect(panel()).toHaveTextContent('POVengine cannot see this step — it takes your word.')
    expect(within(panel()).queryByRole('button', { name: 'Test' })).toBeNull()
  })

  it('a step only the DC can see is ticked by the DC and reads "You confirmed"', () => {
    render(<Harness steps={[step({ id: 'you', title: 'Do a thing', verifier: 'you' })]} />)
    fireEvent.click(within(panel()).getByRole('checkbox'))
    const item = within(list()).getByRole('listitem')
    expect(within(item).getByRole('img', { name: 'You confirmed' })).toBeInTheDocument()
    expect(screen.queryByRole('img', { name: 'Passed' })).toBeNull()
  })

  it('Test reads Testing… while it runs, then Passed', async () => {
    let finish
    const test = vi.fn(() => new Promise((r) => { finish = r }))
    render(<Harness steps={[step({ test })]} />)
    fireEvent.click(within(panel()).getByRole('button', { name: 'Test' }))
    const busy = within(panel()).getByRole('button', { name: 'Testing…' })
    expect(busy).toBeDisabled()
    await act(async () => finish({ state: 'passed', detail: 'All good' }))
    expect(within(panel()).getByRole('button', { name: 'Passed' })).toBeEnabled()
    expect(within(list()).getByRole('img', { name: 'Passed' })).toBeInTheDocument()
  })

  it('a failed Test shows the failure as an error badge, not a pass', async () => {
    render(<Harness steps={[step({ test: async () => ({ state: 'failed', issue: 'HTTP 401', detail: 'The key was refused.' }) })]} />)
    fireEvent.click(within(panel()).getByRole('button', { name: 'Test' }))
    await waitFor(() => expect(within(panel()).getByRole('button', { name: /HTTP 401/ })).toBeInTheDocument())
    expect(panel()).toHaveTextContent('The key was refused.')
    expect(within(panel()).getByRole('button', { name: 'Test' })).toBeInTheDocument()
  })

  // Lab session requirement 1: a pov step that stores values saves its fields
  // when Test runs, then checks them. There is no Save verb.
  it('Test receives the values the step body collected', async () => {
    const test = vi.fn(async ({ draft }) => ({ state: 'passed', detail: `saved ${draft.url}` }))
    render(<Harness steps={[step({
      test,
      body: ({ draft, setDraft }) => (
        <label>URL<input value={draft.url || ''} onChange={(e) => setDraft({ url: e.target.value })} /></label>
      ),
    })]} />)
    fireEvent.change(within(panel()).getByLabelText('URL'), { target: { value: 'https://api-x/logs/v1/event' } })
    fireEvent.click(within(panel()).getByRole('button', { name: 'Test' }))
    await waitFor(() => expect(panel()).toHaveTextContent('saved https://api-x/logs/v1/event'))
    expect(test.mock.calls[0][0].draft).toEqual({ url: 'https://api-x/logs/v1/event' })
  })

  // Lab session requirement 2: a cortex step that cannot be read can be
  // ticked; it then reads "You confirmed", never "Passed".
  it("a Cortex step that reads Can't check can be ticked, and reads You confirmed", async () => {
    render(<Harness steps={[step({ verifier: 'cortex', test: async () => ({ state: 'cant_check', detail: 'No tenant credential with XQL' }) })]} />)
    expect(within(panel()).queryByRole('checkbox')).toBeNull()
    fireEvent.click(within(panel()).getByRole('button', { name: 'Test' }))
    await waitFor(() => expect(within(list()).getByRole('img', { name: "Can't check" })).toBeInTheDocument())
    expect(panel()).toHaveTextContent('No tenant credential with XQL')
    fireEvent.click(within(panel()).getByRole('checkbox'))
    expect(within(list()).getByRole('img', { name: 'You confirmed' })).toBeInTheDocument()
    expect(screen.queryByRole('img', { name: 'Passed' })).toBeNull()
  })

  // Lab session requirement 3: one Test runs two things in order and reports
  // per-row results as they come in.
  it('a Test can report several rows, in order, as it goes', async () => {
    let release
    const test = async (_ctx, { progress }) => {
      progress({ rows: [{ label: 'Canary', state: 'passed', detail: '200' }] })
      await new Promise((r) => { release = r })
      return {
        state: 'failed',
        issue: '1 of 2 not delivered',
        rows: [
          { label: 'Canary', state: 'passed', detail: '200' },
          { label: 'okta example', state: 'failed', detail: '401 · key refused' },
        ],
      }
    }
    render(<Harness steps={[step({ test })]} />)
    fireEvent.click(within(panel()).getByRole('button', { name: 'Test' }))
    const rows = await within(panel()).findByRole('list', { name: /test result/i })
    expect(within(rows).getAllByRole('listitem')).toHaveLength(1)
    await act(async () => release())
    const items = within(within(panel()).getByRole('list', { name: /test result/i })).getAllByRole('listitem')
    expect(items.map((li) => li.textContent)).toEqual([
      expect.stringContaining('Canary'), expect.stringContaining('okta example'),
    ])
    expect(within(items[1]).getByRole('img', { name: 'Failed' })).toBeInTheDocument()
  })

  it('a value one step shares is available to the next', async () => {
    render(<Harness steps={[
      step({ id: 'mint', title: 'Mint', test: async () => ({ state: 'passed', share: { token: 'cxs_abc' } }) }),
      step({ id: 'use', title: 'Use', verifier: 'you', command: ({ shared }) => `run --token ${shared.token || 'cxs_…'}` }),
    ]} />)
    fireEvent.click(within(panel()).getByRole('button', { name: 'Test' }))
    await waitFor(() => expect(within(list()).getByRole('img', { name: 'Passed' })).toBeInTheDocument())
    fireEvent.click(within(list()).getByRole('button', { name: /use/i }))
    expect(within(panel()).getByText('run --token cxs_abc')).toBeInTheDocument()
  })

  it('a path picker switches the steps that follow the common ones', () => {
    render(<Harness
      paths={{ label: 'Target runs', options: [['linux', 'Linux'], ['windows', 'Windows']] }}
      steps={(path) => [step({ id: 'common', title: 'Common' }), step({ id: `${path}.x`, title: `On ${path}` })]}
    />)
    const picker = screen.getByRole('radiogroup', { name: 'Target runs' })
    expect(within(list()).getByText('On linux')).toBeInTheDocument()
    fireEvent.click(within(picker).getByRole('radio', { name: 'Windows' }))
    expect(within(list()).getByText('On windows')).toBeInTheDocument()
  })

  // Found in the browser: the console renders under StrictMode, which mounts,
  // unmounts and mounts again — and the frame then dropped every Test result.
  it('a Test finishes under StrictMode, as the console renders it', async () => {
    render(
      <React.StrictMode>
        <Harness steps={[step({ test: async () => ({ state: 'passed' }) })]} />
      </React.StrictMode>,
    )
    fireEvent.click(within(panel()).getByRole('button', { name: 'Test' }))
    expect(await within(panel()).findByRole('button', { name: 'Passed' })).toBeInTheDocument()
  })

  it('a step whose live state is done reads Passed without a Test', () => {
    render(<Harness steps={[step({ derive: () => ({ state: 'passed', detail: '1 online' }) })]} />)
    expect(within(list()).getByRole('img', { name: 'Passed' })).toBeInTheDocument()
  })
})
