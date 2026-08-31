# First-Run Activation Tour Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give a first-time CortexSim user a five-stop spotlight tour down the critical path that ends holding the agent-enrollment one-liner, plus vocabulary tooltips and one-time hints on consequential controls.

**Architecture:** A declarative stop registry drives a small state machine (`useTour`) that renders one overlay component (`TourSpotlight`) positioned over real console elements located by `data-tour-id` attributes. All persistence goes through a single module (`onboardingState`) that owns every `localStorage` key. The tour replaces `HelpOverlay`'s existing first-run auto-open; `HelpOverlay` gains the "Start guided tour" CTA by wiring its long-dead `onTour` prop.

**Tech Stack:** React 18 (function components + hooks, no state library), Vite, vitest + @testing-library/react with `// @vitest-environment jsdom`, plain CSS using Cortex design tokens. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-30-onboarding-activation-tour-design.md`

## Global Constraints

- **No new npm dependencies.** The tour is hand-rolled; do not add a tour/popover library.
- **Plain CSS only** — no Tailwind. Use existing tokens: `--cortex-navy: #003366`, `--cortex-teal: #00C0E8`, `--cortex-steel: #6B7E8E`. Styles go in `ui/src/styles/cortex-theme.css`.
- **Every `localStorage` access is wrapped in `try/catch`** and lives in `onboardingState.js`. No other onboarding file touches `window.localStorage` directly. Private windows and storage-blocked browsers must not throw.
- **Storage key convention:** `cortexsim.onboarding.<name>`. Exact keys: `cortexsim.onboarding.tourSeenV1`, `cortexsim.onboarding.hint.<controlId>`.
- **Anchors are `data-tour-id` attributes only.** Never CSS class selectors, never `data-testid`.
- **Exiting the tour by ANY path marks it seen.** There is no path that re-shows the tour to a user who has left it.
- **A missing anchor skips the stop.** It never blocks, never hangs, never renders a bubble pointing at nothing.
- Test files use `// @vitest-environment jsdom` as line 1 and the `makeStorageStub()` polyfill pattern from `ui/src/components/__tests__/HelpOverlay.test.jsx` (jsdom in this Node env ships no `localStorage`).
- Run tests with `cd ui && npx vitest run <path>`.
- Stop copy states what a surface is *for*. It must never imply a run proves detection efficacy — evidence lands in Runs & Proof; verification is a separate, tenant-bound act.

---

## File Structure

**Create:**

| File | Responsibility |
|---|---|
| `ui/src/components/onboarding/onboardingState.js` | Sole owner of onboarding `localStorage` keys |
| `ui/src/components/onboarding/glossary.js` | `term key → { term, definition }`, defined once |
| `ui/src/components/onboarding/Term.jsx` | Hover tooltip that looks a term up by key |
| `ui/src/components/onboarding/tourStops.js` | The five stops, declarative |
| `ui/src/components/onboarding/useTour.js` | State machine: idle → running(index) → done |
| `ui/src/components/onboarding/TourSpotlight.jsx` | Dim layer, cutout, positioned bubble |
| `ui/src/components/onboarding/useFirstUseHint.js` | One-time hint state, cleared on use |
| `ui/src/components/onboarding/FirstUseHint.jsx` | The hint bubble |

**Modify:**

| File | Change |
|---|---|
| `ui/src/components/console/DestinationNav.jsx:52` | Add `data-tour-id={'nav-' + item.id}` to the nav button |
| `ui/src/components/console/ScenarioGrid.jsx:35` | Add `data-tour-id` to the first card only |
| `ui/src/components/console/TargetsView.jsx:245` | Add `data-tour-id="agent-enroll"` to the Deploy agent button |
| `ui/src/components/console/AppShell.jsx:68-74` | First-run effect starts the tour instead of opening HelpOverlay |
| `ui/src/components/console/HelpOverlay.jsx` | Pass through `onTour` (already supported) — no change needed beyond AppShell supplying it |
| `ui/src/styles/cortex-theme.css` | Tour + tooltip + hint styles |

---

### Task 1: Onboarding state module

Single owner of every onboarding storage key. Everything else depends on this, so it goes first.

**Files:**
- Create: `ui/src/components/onboarding/onboardingState.js`
- Test: `ui/src/components/onboarding/__tests__/onboardingState.test.js`

**Interfaces:**
- Consumes: nothing
- Produces: `tourSeen(): boolean`, `markTourSeen(): void`, `hintUsed(controlId: string): boolean`, `markHintUsed(controlId: string): void`, `resetOnboarding(): void`

- [ ] **Step 1: Write the failing test**

```javascript
// @vitest-environment jsdom
import { describe, it, expect, beforeAll, beforeEach } from 'vitest'
import {
  tourSeen, markTourSeen, hintUsed, markHintUsed, resetOnboarding,
} from '../onboardingState.js'

function makeStorageStub() {
  const m = new Map()
  return {
    get length() { return m.size },
    key:        (i)    => Array.from(m.keys())[i] ?? null,
    getItem:    (k)    => (m.has(k) ? m.get(k) : null),
    setItem:    (k, v) => { m.set(k, String(v)) },
    removeItem: (k)    => { m.delete(k) },
    clear:      ()     => { m.clear() },
  }
}

beforeAll(() => {
  if (!window.localStorage) {
    Object.defineProperty(window, 'localStorage', {
      value: makeStorageStub(), writable: true, configurable: true,
    })
  }
})
beforeEach(() => { window.localStorage.clear() })

describe('onboardingState', () => {
  it('reports the tour unseen on a fresh profile', () => {
    expect(tourSeen()).toBe(false)
  })

  it('reports the tour seen after markTourSeen', () => {
    markTourSeen()
    expect(tourSeen()).toBe(true)
  })

  it('writes the exact documented key', () => {
    markTourSeen()
    expect(window.localStorage.getItem('cortexsim.onboarding.tourSeenV1')).toBe('true')
  })

  it('tracks hints per control id independently', () => {
    markHintUsed('launch')
    expect(hintUsed('launch')).toBe(true)
    expect(hintUsed('abort')).toBe(false)
  })

  it('resetOnboarding clears tour and hints', () => {
    markTourSeen()
    markHintUsed('launch')
    resetOnboarding()
    expect(tourSeen()).toBe(false)
    expect(hintUsed('launch')).toBe(false)
  })

  it('never throws when storage is unavailable', () => {
    const original = window.localStorage
    Object.defineProperty(window, 'localStorage', {
      get() { throw new Error('SecurityError: storage blocked') },
      configurable: true,
    })
    expect(() => tourSeen()).not.toThrow()
    expect(tourSeen()).toBe(true)   // fail CLOSED: never nag a user we cannot remember
    expect(() => markTourSeen()).not.toThrow()
    Object.defineProperty(window, 'localStorage', {
      value: original, writable: true, configurable: true,
    })
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ui && npx vitest run src/components/onboarding/__tests__/onboardingState.test.js`
Expected: FAIL — "Failed to resolve import ... onboardingState.js"

- [ ] **Step 3: Write minimal implementation**

```javascript
/**
 * onboardingState — the ONLY module that touches onboarding localStorage.
 *
 * Every read fails CLOSED: if storage is unavailable (private window, blocked
 * site data, a browser that throws on access) we report the tour as already
 * seen. Repeatedly showing a walkthrough to someone we are structurally unable
 * to remember is worse than never showing it.
 */
const TOUR_KEY = 'cortexsim.onboarding.tourSeenV1'
const HINT_PREFIX = 'cortexsim.onboarding.hint.'

function read(key) {
  try { return window.localStorage.getItem(key) } catch { return undefined }
}

function write(key, value) {
  try { window.localStorage.setItem(key, value) } catch { /* storage blocked */ }
}

export function tourSeen() {
  const v = read(TOUR_KEY)
  if (v === undefined) return true      // unreadable storage → fail closed
  return v === 'true'
}

export function markTourSeen() { write(TOUR_KEY, 'true') }

export function hintUsed(controlId) {
  const v = read(HINT_PREFIX + controlId)
  if (v === undefined) return true
  return v === 'true'
}

export function markHintUsed(controlId) { write(HINT_PREFIX + controlId, 'true') }

export function resetOnboarding() {
  try {
    const doomed = []
    for (let i = 0; i < window.localStorage.length; i += 1) {
      const k = window.localStorage.key(i)
      if (k === TOUR_KEY || (k && k.startsWith(HINT_PREFIX))) doomed.push(k)
    }
    doomed.forEach((k) => window.localStorage.removeItem(k))
  } catch { /* storage blocked */ }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ui && npx vitest run src/components/onboarding/__tests__/onboardingState.test.js`
Expected: PASS — 6 tests

- [ ] **Step 5: Commit**

```bash
git add ui/src/components/onboarding/onboardingState.js ui/src/components/onboarding/__tests__/onboardingState.test.js
git commit -m "feat(onboarding): storage module that fails closed when storage is blocked"
```

---

### Task 2: Glossary + Term tooltip

**Files:**
- Create: `ui/src/components/onboarding/glossary.js`
- Create: `ui/src/components/onboarding/Term.jsx`
- Test: `ui/src/components/onboarding/__tests__/Term.test.jsx`

**Interfaces:**
- Consumes: nothing
- Produces: `GLOSSARY: Record<string, {term: string, definition: string}>`, `lookup(key): {term, definition} | null`, `<Term k="mttd">MTTD</Term>`

- [ ] **Step 1: Write the failing test**

```jsx
// @vitest-environment jsdom
import React from 'react'
import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import Term from '../Term.jsx'
import { GLOSSARY, lookup } from '../glossary.js'

void React

describe('glossary', () => {
  it('defines every term the tour and console reference', () => {
    for (const key of ['mttd', 'abioc', 'bioc', 'cgo-anchor', 'tenant-verified',
                       'moat-tier', 'xdm-substrate', 's-13', 'detection-type',
                       'push-bundle', 'identity-harness']) {
      expect(GLOSSARY[key], `missing glossary key: ${key}`).toBeTruthy()
      expect(GLOSSARY[key].definition.length).toBeGreaterThan(20)
    }
  })

  it('lookup returns null for an unknown key rather than throwing', () => {
    expect(lookup('not-a-real-term')).toBeNull()
  })
})

describe('Term', () => {
  it('renders its children as the visible text', () => {
    render(<Term k="mttd">MTTD</Term>)
    expect(screen.getByText('MTTD')).toBeTruthy()
  })

  it('exposes the definition as an accessible description on focus', () => {
    render(<Term k="mttd">MTTD</Term>)
    const el = screen.getByText('MTTD')
    fireEvent.focus(el)
    expect(screen.getByRole('tooltip').textContent).toContain(GLOSSARY.mttd.definition)
  })

  it('renders plain text with no tooltip when the key is unknown', () => {
    render(<Term k="nope">Nope</Term>)
    const el = screen.getByText('Nope')
    fireEvent.focus(el)
    expect(screen.queryByRole('tooltip')).toBeNull()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ui && npx vitest run src/components/onboarding/__tests__/Term.test.jsx`
Expected: FAIL — cannot resolve `../glossary.js`

- [ ] **Step 3: Write minimal implementation**

`glossary.js`:

```javascript
/**
 * glossary — every CortexSim term defined exactly once.
 *
 * Referenced by key via <Term k="...">. Never inline a definition as a title=
 * string: the same term appears on several surfaces and duplicated prose drifts.
 */
export const GLOSSARY = {
  'mttd': {
    term: 'MTTD',
    definition: 'Mean time to detect — observed_at minus executed_at for a seeded result. Real only when an observation was ingested; a manual checkbox is not an MTTD.',
  },
  'abioc': {
    term: 'ABIOC',
    definition: 'A Palo Alto-authored, auto-tuned behavioural-ML detection carrying a causality chain. Not a static match wearing a label.',
  },
  'bioc': {
    term: 'BIOC',
    definition: 'Behavioural indicator of compromise — a rule keyed on behaviour. A BIOC keyed on a filename tests the filename, not the behaviour.',
  },
  'cgo-anchor': {
    term: 'CGO anchor',
    definition: 'Causality Group Owner — the realistic initial-access process that owns a run’s process chain, so the sensor sees one connected spine instead of a star rooted at the agent.',
  },
  'tenant-verified': {
    term: 'tenant-verified',
    definition: 'A run or assertion executed against a live Cortex tenant. It is currently 0 across this repo. Authored is not proven.',
  },
  'moat-tier': {
    term: 'moat tier',
    definition: 'Sales-motion differentiation tier carried by a scenario and its bound index row. Disagreements (S-13) are deliberate positioning calls, not defects.',
  },
  'xdm-substrate': {
    term: 'XDM substrate',
    definition: 'The modeling-rule normalization layer. Surfaced and exported, but counted informationally — it is not one of the six detection types.',
  },
  's-13': {
    term: 'S-13',
    definition: 'Loader warning: a scenario declares a moat tier that differs from its bound index row. 105 of these exist on purpose; silencing them is a regression.',
  },
  'detection-type': {
    term: 'detection type',
    definition: 'Exactly six values: BIOC, XQL, Analytics, Correlation, IOC, ABIOC.',
  },
  'push-bundle': {
    term: 'push bundle',
    definition: 'A self-contained script generated for offline execution. It runs on a clean Ubuntu 22.04 host with no SimCore dependency at run time.',
  },
  'identity-harness': {
    term: 'identity harness',
    definition: 'Runs each step as a service account (www-data, postgres, nobody) so the process causality chain looks like a real intrusion rather than one agent spawning everything.',
  },
}

export function lookup(key) {
  return Object.prototype.hasOwnProperty.call(GLOSSARY, key) ? GLOSSARY[key] : null
}
```

`Term.jsx`:

```jsx
import React, { useId, useState } from 'react'
import { lookup } from './glossary.js'

/**
 * Term — hover/focus tooltip for a CortexSim vocabulary word.
 *
 * An unknown key renders plain text with NO tooltip. An empty tooltip and an
 * absent tooltip must not look the same: a dangling key is caught by the
 * glossary guard test, not papered over at runtime.
 */
export default function Term({ k, children }) {
  const entry = lookup(k)
  const [open, setOpen] = useState(false)
  const id = useId()

  if (!entry) return <>{children}</>

  return (
    <span className="term-wrap">
      <span
        className="term"
        tabIndex={0}
        aria-describedby={open ? id : undefined}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
      >
        {children}
      </span>
      {open && (
        <span role="tooltip" id={id} className="term__tip">
          {entry.definition}
        </span>
      )}
    </span>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ui && npx vitest run src/components/onboarding/__tests__/Term.test.jsx`
Expected: PASS — 5 tests

- [ ] **Step 5: Commit**

```bash
git add ui/src/components/onboarding/glossary.js ui/src/components/onboarding/Term.jsx ui/src/components/onboarding/__tests__/Term.test.jsx
git commit -m "feat(onboarding): one-definition glossary + Term tooltip"
```

---

### Task 3: Stop registry + anchor attributes

**Files:**
- Create: `ui/src/components/onboarding/tourStops.js`
- Modify: `ui/src/components/console/DestinationNav.jsx:52`
- Modify: `ui/src/components/console/ScenarioGrid.jsx:35`, `:160`
- Modify: `ui/src/components/console/TargetsView.jsx:245`
- Test: `ui/src/components/onboarding/__tests__/tourStops.test.jsx`

**Interfaces:**
- Consumes: nothing
- Produces: `TOUR_STOPS: Array<{id, anchor, destination, title, body}>`

- [ ] **Step 1: Write the failing test**

```jsx
// @vitest-environment jsdom
import React from 'react'
import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { TOUR_STOPS } from '../tourStops.js'
import DestinationNav from '../../console/DestinationNav.jsx'

void React

describe('TOUR_STOPS', () => {
  it('is the five-stop activation path in order', () => {
    expect(TOUR_STOPS.map((s) => s.id)).toEqual([
      'library', 'scenario', 'agents-empty', 'enroll', 'runs',
    ])
  })

  it('ends on the agents destination holding the enroll anchor', () => {
    const enroll = TOUR_STOPS.find((s) => s.id === 'enroll')
    expect(enroll.destination).toBe('agents')
    expect(enroll.anchor).toBe('agent-enroll')
  })

  it('gives every stop a non-empty title and body', () => {
    for (const s of TOUR_STOPS) {
      expect(s.title.length, `${s.id} title`).toBeGreaterThan(0)
      expect(s.body.length, `${s.id} body`).toBeGreaterThan(20)
    }
  })

  it('never claims a run proves detection efficacy', () => {
    const all = TOUR_STOPS.map((s) => s.body).join(' ').toLowerCase()
    expect(all).not.toContain('proves')
    expect(all).not.toContain('verified')
  })
})

describe('nav anchors', () => {
  it('DestinationNav renders a data-tour-id per destination', () => {
    const groups = [{ label: 'Operate', items: [{ id: 'library', label: 'Library' }] }]
    const { container } = render(<DestinationNav groups={groups} active="library" />)
    expect(container.querySelector('[data-tour-id="nav-library"]')).toBeTruthy()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ui && npx vitest run src/components/onboarding/__tests__/tourStops.test.jsx`
Expected: FAIL — cannot resolve `../tourStops.js`

- [ ] **Step 3: Write minimal implementation**

`tourStops.js`:

```javascript
/**
 * tourStops — the five-stop activation path.
 *
 * Deliberately NOT a tour of all eleven destinations. The goal is productive
 * use, not a map of the product. Copy says what a surface is FOR; it must never
 * imply a run proves detection efficacy.
 */
export const TOUR_STOPS = [
  {
    id: 'library',
    anchor: 'nav-library',
    destination: 'library',
    title: 'Start here',
    body: 'The Library holds 170 scenarios across 15 detection planes. Every POV starts by choosing one.',
  },
  {
    id: 'scenario',
    anchor: 'scenario-card-first',
    destination: 'library',
    title: 'A scenario is a TTP chain',
    body: 'Each card is an ordered set of steps plus the detections each step should trigger. Arm one to stage it for launch.',
  },
  {
    id: 'agents-empty',
    anchor: 'nav-agents',
    destination: 'agents',
    title: 'Nothing runs without a beacon',
    body: 'Agents is empty on a fresh install. Until one beacon checks in, a launch queues a task that nothing collects.',
  },
  {
    id: 'enroll',
    anchor: 'agent-enroll',
    destination: 'agents',
    title: 'Deploy one now',
    body: 'Mint an enrollment token and run the single line it gives you on the target host. SimCore assigns the agent id.',
  },
  {
    id: 'runs',
    anchor: 'nav-runs',
    destination: 'runs',
    title: 'Where the evidence lands',
    body: 'Runs & Proof collects each step’s output, the per-detection results, and the POV report you export for the customer.',
  },
]
```

`DestinationNav.jsx` — add the attribute to the existing button (line 52, alongside `data-testid`):

```jsx
                data-testid={`dest-button-${item.id}`}
                data-tour-id={`nav-${item.id}`}
```

`ScenarioGrid.jsx` — the card takes an optional flag (line 35 area):

```jsx
    <article
      data-tour-id={isFirst ? 'scenario-card-first' : undefined}
      className={
```

and `ScenarioCard` accepts `isFirst`, passed from the map (line ~160):

```jsx
      {ordered.map((s, i) => {
```
```jsx
          <ScenarioCard
            key={id}
            isFirst={i === 0}
            scenario={s}
```

`TargetsView.jsx` — line 245 button:

```jsx
            <button type="button" className="btn btn--xs target-col__action" data-tour-id="agent-enroll" onClick={() => setDeployOpen(true)}>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ui && npx vitest run src/components/onboarding/__tests__/tourStops.test.jsx`
Expected: PASS — 5 tests

- [ ] **Step 5: Run the full UI suite to confirm no anchor change broke a snapshot**

Run: `cd ui && npx vitest run`
Expected: PASS — all existing tests still green (524+ before this work)

- [ ] **Step 6: Commit**

```bash
git add ui/src/components/onboarding/tourStops.js ui/src/components/onboarding/__tests__/tourStops.test.jsx ui/src/components/console/DestinationNav.jsx ui/src/components/console/ScenarioGrid.jsx ui/src/components/console/TargetsView.jsx
git commit -m "feat(onboarding): five-stop registry + data-tour-id anchors"
```

---

### Task 4: The tour state machine

The task where the failure behavior lives. Test the skip and exit paths before the happy path.

**Files:**
- Create: `ui/src/components/onboarding/useTour.js`
- Test: `ui/src/components/onboarding/__tests__/useTour.test.jsx`

**Interfaces:**
- Consumes: `TOUR_STOPS` (Task 3), `tourSeen`/`markTourSeen` (Task 1)
- Produces: `useTour({ stops, onNavigate, autoStart })` returning `{ active, stop, index, total, next, prev, exit, start }`

- [ ] **Step 1: Write the failing test**

```jsx
// @vitest-environment jsdom
import React from 'react'
import { describe, it, expect, vi, beforeAll, beforeEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useTour } from '../useTour.js'
import { markTourSeen } from '../onboardingState.js'

void React

function makeStorageStub() {
  const m = new Map()
  return {
    get length() { return m.size },
    key: (i) => Array.from(m.keys())[i] ?? null,
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => { m.set(k, String(v)) },
    removeItem: (k) => { m.delete(k) },
    clear: () => { m.clear() },
  }
}
beforeAll(() => {
  if (!window.localStorage) {
    Object.defineProperty(window, 'localStorage', {
      value: makeStorageStub(), writable: true, configurable: true,
    })
  }
})
beforeEach(() => {
  window.localStorage.clear()
  document.body.innerHTML = ''
})

function anchor(id) {
  const el = document.createElement('div')
  el.setAttribute('data-tour-id', id)
  document.body.appendChild(el)
  return el
}

const STOPS = [
  { id: 'a', anchor: 'anchor-a', destination: 'library', title: 'A', body: 'body a' },
  { id: 'b', anchor: 'anchor-b', destination: 'agents',  title: 'B', body: 'body b' },
  { id: 'c', anchor: 'anchor-c', destination: 'runs',    title: 'C', body: 'body c' },
]

describe('useTour', () => {
  it('does not auto-start when the tour is already seen', () => {
    markTourSeen()
    anchor('anchor-a')
    const { result } = renderHook(() => useTour({ stops: STOPS, onNavigate: vi.fn(), autoStart: true }))
    expect(result.current.active).toBe(false)
  })

  it('SKIPS a stop whose anchor is absent rather than hanging', () => {
    anchor('anchor-a')
    // anchor-b deliberately absent
    anchor('anchor-c')
    const { result } = renderHook(() => useTour({ stops: STOPS, onNavigate: vi.fn(), autoStart: true }))
    expect(result.current.stop.id).toBe('a')
    act(() => { result.current.next() })
    expect(result.current.stop.id).toBe('c')   // skipped b, did NOT stall on it
  })

  it('exits immediately and marks seen when NO anchor exists', () => {
    const { result } = renderHook(() => useTour({ stops: STOPS, onNavigate: vi.fn(), autoStart: true }))
    expect(result.current.active).toBe(false)
    expect(window.localStorage.getItem('cortexsim.onboarding.tourSeenV1')).toBe('true')
  })

  it('marks seen when exited part-way', () => {
    anchor('anchor-a'); anchor('anchor-b'); anchor('anchor-c')
    const { result } = renderHook(() => useTour({ stops: STOPS, onNavigate: vi.fn(), autoStart: true }))
    act(() => { result.current.exit() })
    expect(result.current.active).toBe(false)
    expect(window.localStorage.getItem('cortexsim.onboarding.tourSeenV1')).toBe('true')
  })

  it('does not re-show after an exit', () => {
    anchor('anchor-a'); anchor('anchor-b'); anchor('anchor-c')
    const first = renderHook(() => useTour({ stops: STOPS, onNavigate: vi.fn(), autoStart: true }))
    act(() => { first.result.current.exit() })
    const second = renderHook(() => useTour({ stops: STOPS, onNavigate: vi.fn(), autoStart: true }))
    expect(second.result.current.active).toBe(false)
  })

  it('navigates to each stop destination as it advances', () => {
    anchor('anchor-a'); anchor('anchor-b'); anchor('anchor-c')
    const onNavigate = vi.fn()
    const { result } = renderHook(() => useTour({ stops: STOPS, onNavigate, autoStart: true }))
    expect(onNavigate).toHaveBeenCalledWith('library')
    act(() => { result.current.next() })
    expect(onNavigate).toHaveBeenCalledWith('agents')
  })

  it('finishing the last stop ends the tour and marks seen', () => {
    anchor('anchor-a'); anchor('anchor-b'); anchor('anchor-c')
    const { result } = renderHook(() => useTour({ stops: STOPS, onNavigate: vi.fn(), autoStart: true }))
    act(() => { result.current.next() })
    act(() => { result.current.next() })
    act(() => { result.current.next() })
    expect(result.current.active).toBe(false)
    expect(window.localStorage.getItem('cortexsim.onboarding.tourSeenV1')).toBe('true')
  })

  it('start() runs the tour even when already seen (Help CTA path)', () => {
    markTourSeen()
    anchor('anchor-a'); anchor('anchor-b'); anchor('anchor-c')
    const { result } = renderHook(() => useTour({ stops: STOPS, onNavigate: vi.fn(), autoStart: false }))
    act(() => { result.current.start() })
    expect(result.current.active).toBe(true)
    expect(result.current.stop.id).toBe('a')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ui && npx vitest run src/components/onboarding/__tests__/useTour.test.jsx`
Expected: FAIL — cannot resolve `../useTour.js`

- [ ] **Step 3: Write minimal implementation**

```javascript
import { useCallback, useEffect, useMemo, useState } from 'react'
import { tourSeen, markTourSeen } from './onboardingState.js'

export function anchorExists(anchorId) {
  try {
    return Boolean(document.querySelector(`[data-tour-id="${anchorId}"]`))
  } catch { return false }
}

/**
 * useTour — idle → running(index) → done.
 *
 * The invariants that matter are the failure ones: a stop whose anchor is not
 * mounted is SKIPPED, a tour with no mountable stop exits immediately, and
 * every exit path marks the tour seen. A spotlight that points at nothing with
 * no way out is the characteristic failure of this kind of feature.
 */
export function useTour({ stops, onNavigate, autoStart = false }) {
  const [index, setIndex] = useState(-1)

  const firstMountable = useCallback(
    (from) => {
      for (let i = from; i < stops.length; i += 1) {
        if (anchorExists(stops[i].anchor)) return i
      }
      return -1
    },
    [stops],
  )

  const finish = useCallback(() => {
    setIndex(-1)
    markTourSeen()
  }, [])

  const start = useCallback(() => {
    const i = firstMountable(0)
    if (i === -1) { markTourSeen(); return }
    setIndex(i)
  }, [firstMountable])

  useEffect(() => {
    if (!autoStart) return
    if (tourSeen()) return
    const i = firstMountable(0)
    if (i === -1) { markTourSeen(); return }
    setIndex(i)
  }, [autoStart, firstMountable])

  // Navigate whenever the active stop changes.
  useEffect(() => {
    if (index < 0 || !stops[index]) return
    if (onNavigate) onNavigate(stops[index].destination)
  }, [index, stops, onNavigate])

  const next = useCallback(() => {
    const i = firstMountable(index + 1)
    if (i === -1) { finish(); return }
    setIndex(i)
  }, [index, firstMountable, finish])

  const prev = useCallback(() => {
    for (let i = index - 1; i >= 0; i -= 1) {
      if (anchorExists(stops[i].anchor)) { setIndex(i); return }
    }
  }, [index, stops])

  const stop = index >= 0 ? stops[index] : null
  const total = useMemo(() => stops.length, [stops])

  return { active: index >= 0, stop, index, total, next, prev, exit: finish, start }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ui && npx vitest run src/components/onboarding/__tests__/useTour.test.jsx`
Expected: PASS — 8 tests

- [ ] **Step 5: Commit**

```bash
git add ui/src/components/onboarding/useTour.js ui/src/components/onboarding/__tests__/useTour.test.jsx
git commit -m "feat(onboarding): tour state machine that skips missing anchors and cannot trap"
```

---

### Task 5: The spotlight overlay

**Files:**
- Create: `ui/src/components/onboarding/TourSpotlight.jsx`
- Modify: `ui/src/styles/cortex-theme.css` (append)
- Test: `ui/src/components/onboarding/__tests__/TourSpotlight.test.jsx`

**Interfaces:**
- Consumes: a stop object `{id, anchor, title, body}` from Task 3
- Produces: `<TourSpotlight stop index total onNext onPrev onExit />`

- [ ] **Step 1: Write the failing test**

```jsx
// @vitest-environment jsdom
import React from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import TourSpotlight from '../TourSpotlight.jsx'

void React

const STOP = { id: 'a', anchor: 'anchor-a', destination: 'library', title: 'Start here', body: 'The Library holds 170 scenarios.' }

beforeEach(() => {
  document.body.innerHTML = ''
  const el = document.createElement('div')
  el.setAttribute('data-tour-id', 'anchor-a')
  document.body.appendChild(el)
})

describe('TourSpotlight', () => {
  it('renders the stop title and body', () => {
    render(<TourSpotlight stop={STOP} index={0} total={5} onNext={vi.fn()} onPrev={vi.fn()} onExit={vi.fn()} />)
    expect(screen.getByText('Start here')).toBeTruthy()
    expect(screen.getByText(/170 scenarios/)).toBeTruthy()
  })

  it('is a modal dialog named by the stop title', () => {
    render(<TourSpotlight stop={STOP} index={0} total={5} onNext={vi.fn()} onPrev={vi.fn()} onExit={vi.fn()} />)
    const dlg = screen.getByRole('dialog')
    expect(dlg.getAttribute('aria-modal')).toBe('true')
    expect(dlg.getAttribute('aria-label')).toBe('Start here')
  })

  it('shows progress as step n of total', () => {
    render(<TourSpotlight stop={STOP} index={2} total={5} onNext={vi.fn()} onPrev={vi.fn()} onExit={vi.fn()} />)
    expect(screen.getByText(/3 of 5/)).toBeTruthy()
  })

  it('Escape exits', () => {
    const onExit = vi.fn()
    render(<TourSpotlight stop={STOP} index={0} total={5} onNext={vi.fn()} onPrev={vi.fn()} onExit={onExit} />)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onExit).toHaveBeenCalled()
  })

  it('the skip control exits', () => {
    const onExit = vi.fn()
    render(<TourSpotlight stop={STOP} index={0} total={5} onNext={vi.fn()} onPrev={vi.fn()} onExit={onExit} />)
    fireEvent.click(screen.getByRole('button', { name: /skip/i }))
    expect(onExit).toHaveBeenCalled()
  })

  it('the last stop offers Done rather than Next', () => {
    render(<TourSpotlight stop={STOP} index={4} total={5} onNext={vi.fn()} onPrev={vi.fn()} onExit={vi.fn()} />)
    expect(screen.getByRole('button', { name: /done/i })).toBeTruthy()
    expect(screen.queryByRole('button', { name: /^next$/i })).toBeNull()
  })

  it('renders nothing when stop is null', () => {
    const { container } = render(<TourSpotlight stop={null} index={-1} total={5} onNext={vi.fn()} onPrev={vi.fn()} onExit={vi.fn()} />)
    expect(container.firstChild).toBeNull()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ui && npx vitest run src/components/onboarding/__tests__/TourSpotlight.test.jsx`
Expected: FAIL — cannot resolve `../TourSpotlight.jsx`

- [ ] **Step 3: Write minimal implementation**

```jsx
import React, { useEffect, useLayoutEffect, useRef, useState } from 'react'

/**
 * TourSpotlight — dim layer with a cutout over the anchored element, plus a
 * bubble beside it.
 *
 * The cutout is drawn with a very large box-shadow spread rather than an SVG
 * mask: it needs no extra element, scales to any viewport, and degrades to a
 * plain dim layer if the rect is unavailable.
 */
export default function TourSpotlight({ stop, index, total, onNext, onPrev, onExit }) {
  const [rect, setRect] = useState(null)
  const bubbleRef = useRef(null)

  useLayoutEffect(() => {
    if (!stop) { setRect(null); return undefined }
    const measure = () => {
      const el = document.querySelector(`[data-tour-id="${stop.anchor}"]`)
      setRect(el ? el.getBoundingClientRect() : null)
    }
    measure()
    window.addEventListener('resize', measure)
    window.addEventListener('scroll', measure, true)
    return () => {
      window.removeEventListener('resize', measure)
      window.removeEventListener('scroll', measure, true)
    }
  }, [stop])

  useEffect(() => {
    if (!stop) return undefined
    const onKey = (e) => {
      if (e.key === 'Escape') { e.preventDefault(); onExit() }
      else if (e.key === 'Enter') { e.preventDefault(); onNext() }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [stop, onExit, onNext])

  useEffect(() => {
    if (stop && bubbleRef.current) bubbleRef.current.focus()
  }, [stop])

  if (!stop) return null

  const isLast = index >= total - 1
  const cut = rect
    ? { top: rect.top - 6, left: rect.left - 6, width: rect.width + 12, height: rect.height + 12 }
    : null

  return (
    <div className="tour" data-testid="tour-spotlight">
      <div className="tour__dim" aria-hidden="true" />
      {cut && (
        <div
          className="tour__cutout"
          aria-hidden="true"
          style={{ top: cut.top, left: cut.left, width: cut.width, height: cut.height }}
        />
      )}
      <div
        className="tour__bubble"
        role="dialog"
        aria-modal="true"
        aria-label={stop.title}
        tabIndex={-1}
        ref={bubbleRef}
        style={cut ? { top: cut.top + cut.height + 12, left: cut.left } : undefined}
      >
        <h2 className="tour__title">{stop.title}</h2>
        <p className="tour__body">{stop.body}</p>
        <div className="tour__foot">
          <button type="button" className="btn btn--xs" onClick={onExit}>Skip</button>
          <span className="tour__progress" aria-live="polite">{index + 1} of {total}</span>
          {index > 0 && <button type="button" className="btn btn--xs" onClick={onPrev}>Back</button>}
          <button type="button" className="btn btn--primary btn--xs" onClick={isLast ? onExit : onNext}>
            {isLast ? 'Done' : 'Next'}
          </button>
        </div>
      </div>
    </div>
  )
}
```

Append to `ui/src/styles/cortex-theme.css`:

```css
/* ── First-run activation tour ─────────────────────────────────────────── */
.tour__dim {
  position: fixed; inset: 0; z-index: 900;
  background: rgba(0, 20, 40, 0.62);
}
.tour__cutout {
  position: fixed; z-index: 901; border-radius: 6px;
  border: 2px solid var(--cortex-teal, #00C0E8);
  box-shadow: 0 0 0 9999px rgba(0, 20, 40, 0.62);
  pointer-events: none;
}
.tour__bubble {
  position: fixed; z-index: 902; max-width: 340px;
  background: var(--cortex-navy, #003366);
  border: 1px solid var(--cortex-teal, #00C0E8);
  border-radius: 8px; padding: 14px 16px;
  color: #fff; font-family: Inter, system-ui, sans-serif;
  box-shadow: 0 8px 28px rgba(0, 0, 0, 0.45);
}
.tour__title { margin: 0 0 6px; font-size: 15px; font-weight: 600; }
.tour__body  { margin: 0 0 12px; font-size: 13px; line-height: 1.5; color: #d7e3ec; }
.tour__foot  { display: flex; align-items: center; gap: 8px; }
.tour__progress { margin-left: auto; font-size: 11px; color: var(--cortex-steel, #6B7E8E); }

/* ── Vocabulary tooltip ────────────────────────────────────────────────── */
.term-wrap { position: relative; display: inline-block; }
.term {
  border-bottom: 1px dotted var(--cortex-teal, #00C0E8);
  cursor: help; outline-offset: 2px;
}
.term__tip {
  position: absolute; z-index: 950; bottom: calc(100% + 6px); left: 0;
  width: 280px; padding: 8px 10px;
  background: var(--cortex-navy, #003366);
  border: 1px solid var(--cortex-teal, #00C0E8);
  border-radius: 6px; font-size: 12px; line-height: 1.45; color: #d7e3ec;
}

@media (prefers-reduced-motion: reduce) {
  .tour__cutout, .tour__bubble { transition: none !important; }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ui && npx vitest run src/components/onboarding/__tests__/TourSpotlight.test.jsx`
Expected: PASS — 7 tests

- [ ] **Step 5: Commit**

```bash
git add ui/src/components/onboarding/TourSpotlight.jsx ui/src/components/onboarding/__tests__/TourSpotlight.test.jsx ui/src/styles/cortex-theme.css
git commit -m "feat(onboarding): spotlight overlay with cutout, keyboard exit, and progress"
```

---

### Task 6: Wire into AppShell and the Help CTA

The task that closes the dead `onTour` hook.

**Files:**
- Modify: `ui/src/components/console/AppShell.jsx:68-74` (first-run effect), render block
- Test: `ui/src/components/onboarding/__tests__/appShellTour.test.jsx`

**Interfaces:**
- Consumes: `useTour` (Task 4), `TourSpotlight` (Task 5), `TOUR_STOPS` (Task 3), `shouldShowOnFirstRun` (existing `HelpOverlay` export)
- Produces: no new exports — behavioral wiring only

- [ ] **Step 1: Write the failing test**

```jsx
// @vitest-environment jsdom
import React from 'react'
import { describe, it, expect, vi, beforeAll, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import AppShell from '../../console/AppShell.jsx'
import { markTourSeen } from '../onboardingState.js'
import { markFirstRunSeen } from '../../console/HelpOverlay.jsx'

void React

function makeStorageStub() {
  const m = new Map()
  return {
    get length() { return m.size },
    key: (i) => Array.from(m.keys())[i] ?? null,
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => { m.set(k, String(v)) },
    removeItem: (k) => { m.delete(k) },
    clear: () => { m.clear() },
  }
}
beforeAll(() => {
  if (!window.localStorage) {
    Object.defineProperty(window, 'localStorage', {
      value: makeStorageStub(), writable: true, configurable: true,
    })
  }
})
beforeEach(() => { window.localStorage.clear() })

const GROUPS = [{ label: 'Operate', items: [{ id: 'library', label: 'Library' }] }]

describe('AppShell first-run', () => {
  it('starts the tour on a fresh profile, not the help overlay', async () => {
    render(<AppShell destination="library" navGroups={GROUPS} paletteItems={[]}><div /></AppShell>)
    expect(await screen.findByTestId('tour-spotlight')).toBeTruthy()
    expect(screen.queryByText(/Keyboard shortcuts/i)).toBeNull()
  })

  it('does not start the tour when it has already been seen', async () => {
    markTourSeen()
    render(<AppShell destination="library" navGroups={GROUPS} paletteItems={[]}><div /></AppShell>)
    await new Promise((r) => setTimeout(r, 500))
    expect(screen.queryByTestId('tour-spotlight')).toBeNull()
  })

  it('does not start the tour for a user who already dismissed the help overlay', async () => {
    markFirstRunSeen()
    render(<AppShell destination="library" navGroups={GROUPS} paletteItems={[]}><div /></AppShell>)
    await new Promise((r) => setTimeout(r, 500))
    expect(screen.queryByTestId('tour-spotlight')).toBeNull()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ui && npx vitest run src/components/onboarding/__tests__/appShellTour.test.jsx`
Expected: FAIL — no element with testid `tour-spotlight` (AppShell still opens HelpOverlay)

- [ ] **Step 3: Write minimal implementation**

In `AppShell.jsx`, add imports:

```jsx
import { useTour } from '../onboarding/useTour.js'
import TourSpotlight from '../onboarding/TourSpotlight.jsx'
import { TOUR_STOPS } from '../onboarding/tourStops.js'
```

Replace the first-run effect (lines 68-74) with the tour hook. `autoStart` also
honours `shouldShowOnFirstRun()` so a user who already dismissed the overlay is
not re-onboarded:

```jsx
  const tour = useTour({
    stops: TOUR_STOPS,
    onNavigate,
    autoStart: shouldShowOnFirstRun(),
  })
```

Delete the `useEffect` that called `setHelpOpen(true)` on first run. Keep
`handleCloseHelp` and its `markFirstRunSeen()` call unchanged.

Pass the CTA through to the overlay (this is the dead hook being wired):

```jsx
        <HelpOverlay open={helpOpen} onClose={handleCloseHelp} onTour={() => { setHelpOpen(false); tour.start() }} />
```

Render the spotlight at the end of the shell, as a sibling of the overlay:

```jsx
      {tour.active && (
        <TourSpotlight
          stop={tour.stop}
          index={tour.index}
          total={tour.total}
          onNext={tour.next}
          onPrev={tour.prev}
          onExit={tour.exit}
        />
      )}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ui && npx vitest run src/components/onboarding/__tests__/appShellTour.test.jsx`
Expected: PASS — 3 tests

- [ ] **Step 5: Run the full UI suite**

Run: `cd ui && npx vitest run`
Expected: PASS. If a pre-existing AppShell test asserted the help overlay auto-opens on first run, update it to assert the tour opens instead — that behavior change is intended and is documented in spec §3.

- [ ] **Step 6: Commit**

```bash
git add ui/src/components/console/AppShell.jsx ui/src/components/onboarding/__tests__/appShellTour.test.jsx
git commit -m "feat(onboarding): tour replaces first-run overlay; wire HelpOverlay onTour"
```

---

### Task 7: First-use hints on consequential controls

**Files:**
- Create: `ui/src/components/onboarding/useFirstUseHint.js`
- Create: `ui/src/components/onboarding/FirstUseHint.jsx`
- Test: `ui/src/components/onboarding/__tests__/useFirstUseHint.test.jsx`

**Interfaces:**
- Consumes: `hintUsed`/`markHintUsed` (Task 1)
- Produces: `useFirstUseHint(controlId)` returning `{ show, onUse }`; `<FirstUseHint show text onDismiss />`

- [ ] **Step 1: Write the failing test**

```jsx
// @vitest-environment jsdom
import React from 'react'
import { describe, it, expect, beforeAll, beforeEach } from 'vitest'
import { renderHook, act, render, screen } from '@testing-library/react'
import { useFirstUseHint } from '../useFirstUseHint.js'
import FirstUseHint from '../FirstUseHint.jsx'

void React

function makeStorageStub() {
  const m = new Map()
  return {
    get length() { return m.size },
    key: (i) => Array.from(m.keys())[i] ?? null,
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => { m.set(k, String(v)) },
    removeItem: (k) => { m.delete(k) },
    clear: () => { m.clear() },
  }
}
beforeAll(() => {
  if (!window.localStorage) {
    Object.defineProperty(window, 'localStorage', {
      value: makeStorageStub(), writable: true, configurable: true,
    })
  }
})
beforeEach(() => { window.localStorage.clear() })

describe('useFirstUseHint', () => {
  it('shows on a fresh profile', () => {
    const { result } = renderHook(() => useFirstUseHint('launch'))
    expect(result.current.show).toBe(true)
  })

  it('clears permanently once the control is USED', () => {
    const { result } = renderHook(() => useFirstUseHint('launch'))
    act(() => { result.current.onUse() })
    expect(result.current.show).toBe(false)
    const again = renderHook(() => useFirstUseHint('launch'))
    expect(again.result.current.show).toBe(false)
  })

  it('is independent per control id', () => {
    const launch = renderHook(() => useFirstUseHint('launch'))
    act(() => { launch.result.current.onUse() })
    const abort = renderHook(() => useFirstUseHint('abort'))
    expect(abort.result.current.show).toBe(true)
  })
})

describe('FirstUseHint', () => {
  it('renders its text when show is true', () => {
    render(<FirstUseHint show text="Launch runs the armed scenario." onDismiss={() => {}} />)
    expect(screen.getByText(/Launch runs the armed scenario/)).toBeTruthy()
  })

  it('renders nothing when show is false', () => {
    const { container } = render(<FirstUseHint show={false} text="hidden" onDismiss={() => {}} />)
    expect(container.firstChild).toBeNull()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ui && npx vitest run src/components/onboarding/__tests__/useFirstUseHint.test.jsx`
Expected: FAIL — cannot resolve `../useFirstUseHint.js`

- [ ] **Step 3: Write minimal implementation**

`useFirstUseHint.js`:

```javascript
import { useCallback, useState } from 'react'
import { hintUsed, markHintUsed } from './onboardingState.js'

/**
 * useFirstUseHint — a one-time pointer on a consequential control.
 *
 * Cleared when the control is USED, not when the bubble is dismissed:
 * dismissing a hint you did not act on is not evidence you learned it.
 */
export function useFirstUseHint(controlId) {
  const [show, setShow] = useState(() => !hintUsed(controlId))

  const onUse = useCallback(() => {
    markHintUsed(controlId)
    setShow(false)
  }, [controlId])

  return { show, onUse }
}
```

`FirstUseHint.jsx`:

```jsx
import React from 'react'

export default function FirstUseHint({ show, text, onDismiss }) {
  if (!show) return null
  return (
    <span className="first-use-hint" role="note">
      {text}
      {onDismiss && (
        <button type="button" className="first-use-hint__x" aria-label="Dismiss hint" onClick={onDismiss}>×</button>
      )}
    </span>
  )
}
```

Append to `ui/src/styles/cortex-theme.css`:

```css
.first-use-hint {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 3px 8px; border-radius: 4px; font-size: 11px;
  background: rgba(0, 192, 232, 0.12);
  border: 1px solid var(--cortex-teal, #00C0E8);
  color: var(--cortex-teal, #00C0E8);
}
.first-use-hint__x { background: none; border: 0; color: inherit; cursor: pointer; font-size: 13px; line-height: 1; }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ui && npx vitest run src/components/onboarding/__tests__/useFirstUseHint.test.jsx`
Expected: PASS — 5 tests

- [ ] **Step 5: Commit**

```bash
git add ui/src/components/onboarding/useFirstUseHint.js ui/src/components/onboarding/FirstUseHint.jsx ui/src/components/onboarding/__tests__/useFirstUseHint.test.jsx ui/src/styles/cortex-theme.css
git commit -m "feat(onboarding): first-use hints that clear on use, not on dismiss"
```

---

### Task 8: The glossary guard

The repo-idiomatic gate: a dangling `<Term k>` renders an empty tooltip that looks fine and teaches nothing. This test makes that state impossible.

**Files:**
- Test: `ui/src/components/onboarding/__tests__/glossaryGuard.test.js`

**Interfaces:**
- Consumes: `GLOSSARY` (Task 2)
- Produces: nothing — a guard only

- [ ] **Step 1: Write the failing test**

Prove it can fail first by adding a deliberately bogus `<Term k="not-real">x</Term>`
into any console component, running the test, and watching it go red. Remove the
bogus term afterward.

```javascript
// @vitest-environment node
import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { GLOSSARY } from '../glossary.js'

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) { if (name !== '__tests__') walk(p, out) }
    else if (p.endsWith('.jsx') || p.endsWith('.js')) out.push(p)
  }
  return out
}

describe('glossary guard', () => {
  it('every <Term k="..."> in the UI resolves to a glossary entry', () => {
    const files = walk(new URL('../../..', import.meta.url).pathname)
    const dangling = []
    for (const f of files) {
      const src = readFileSync(f, 'utf8')
      for (const m of src.matchAll(/<Term\s+k=["']([^"']+)["']/g)) {
        if (!Object.prototype.hasOwnProperty.call(GLOSSARY, m[1])) {
          dangling.push(`${f}: k="${m[1]}"`)
        }
      }
    }
    expect(dangling, `dangling glossary keys:\n${dangling.join('\n')}`).toEqual([])
  })

  it('no glossary definition is a placeholder', () => {
    for (const [key, entry] of Object.entries(GLOSSARY)) {
      expect(entry.definition, key).not.toMatch(/TODO|TBD|FIXME|lorem/i)
      expect(entry.definition.length, key).toBeGreaterThan(20)
    }
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Add `<Term k="definitely-not-real">x</Term>` to `ui/src/components/console/HelpOverlay.jsx` temporarily.
Run: `cd ui && npx vitest run src/components/onboarding/__tests__/glossaryGuard.test.js`
Expected: FAIL — `dangling glossary keys: .../HelpOverlay.jsx: k="definitely-not-real"`

- [ ] **Step 3: Remove the deliberate break**

Delete the temporary `<Term>` from `HelpOverlay.jsx`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ui && npx vitest run src/components/onboarding/__tests__/glossaryGuard.test.js`
Expected: PASS — 2 tests

- [ ] **Step 5: Commit**

```bash
git add ui/src/components/onboarding/__tests__/glossaryGuard.test.js
git commit -m "test(onboarding): guard against a dangling Term key rendering an empty tooltip"
```

---

### Task 9: Full-suite verification and Gate A evidence

**Files:**
- Modify: none (verification only)

- [ ] **Step 1: Run the full UI suite**

Run: `cd ui && npx vitest run`
Expected: PASS — the 524 pre-existing tests plus ~36 added here.

- [ ] **Step 2: Run the production build**

Run: `cd ui && npm run build`
Expected: build succeeds, no unresolved imports.

- [ ] **Step 3: Verify in the running console**

```bash
cd ui && npm run build && cp -r dist/* ../core/static/
DOCKER_CONTEXT=default docker compose -f ../docker-compose.yml restart simcore
```

Open http://localhost:8888 in a private window (fresh `localStorage`). Confirm:
the tour auto-starts on Library; Next advances through all five stops; it lands on
Agents with the Deploy agent control spotlit; Escape exits; reloading does not
re-show it; ⌘/ then "Start guided tour" replays it.

- [ ] **Step 4: Record Gate A evidence in the PR body**

Per `CONTRIBUTING.md` §3 A2, state what was observably wrong, the verification
command and its output, and confirm each new guard was observed failing before
its implementation existed (Task 8 Step 2 is the worked example).

- [ ] **Step 5: Commit any test updates and push**

```bash
git push -u origin feature/ui-onboarding-activation-tour
```

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| §3 tour replaces auto-shown overlay; `shouldShowOnFirstRun()` reused | Task 6 |
| §4 five stops, ends on Agents | Task 3 |
| §5 components (8 files) | Tasks 1, 2, 3, 4, 5, 7 |
| §5 storage keys | Task 1 |
| §5 `data-tour-id` anchoring, five attributes | Task 3 |
| §5 vocabulary registry | Task 2 |
| §5 first-use hints clear on use | Task 7 |
| §6 missing anchor skips; all-missing exits; every exit marks seen | Task 4 |
| §6 `prefers-reduced-motion` | Task 5 (CSS) |
| §7 accessibility — dialog, label, live region, Escape | Task 5 |
| §8 all seven listed tests | Tasks 1, 4, 6, 7, 8 |

No spec requirement is without a task.

**Placeholder scan:** No TBD/TODO/"handle edge cases"/"similar to Task N". Every code step carries real code.

**Type consistency:** `tourSeen`/`markTourSeen`/`hintUsed`/`markHintUsed`/`resetOnboarding` (Task 1) are used with those exact names in Tasks 4 and 7. `useTour` returns `{active, stop, index, total, next, prev, exit, start}` (Task 4) and Task 6 consumes exactly those. `TourSpotlight` props `{stop, index, total, onNext, onPrev, onExit}` match between Tasks 5 and 6. `TOUR_STOPS` shape `{id, anchor, destination, title, body}` is identical in Tasks 3, 4 and 5.

**One deliberate deviation from the spec, flagged:** spec §5 says storage reads are wrapped in try/catch; this plan additionally specifies they **fail closed** (unreadable storage reports the tour as already seen). Nagging a user we are structurally unable to remember is worse than never showing the tour. Task 1 tests this explicitly.
