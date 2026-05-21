import React, { useEffect, useState } from 'react'

/**
 * HelpOverlay — ⌘/ overlay listing every keyboard shortcut + a brief
 * "what is this tab for" cheatsheet.
 *
 * Discoverable from anywhere via ⌘/. For new DCs the same overlay is
 * surfaced on first visit (gated by localStorage so it appears once).
 *
 * Two sections:
 *   1. Keyboard shortcuts grouped by category
 *   2. Tab cheatsheet — short prose explaining what each tab does and
 *      when to use it
 *
 * Props:
 *   open      — boolean
 *   onClose   — () => void
 *   onTour    — () => void   (optional) launch first-run tour
 */
const SHORTCUTS = [
  {
    group: 'Navigation',
    items: [
      { keys: ['⌘', 'K'], label: 'Command palette · search scenarios, jump to tabs' },
      { keys: ['⌘', 'F'], label: 'Filter palette · slice scenarios by tactic, technique, actor…' },
      { keys: ['⌘', '/'], label: 'Help · this overlay' },
      { keys: ['esc'],    label: 'Close any open palette or drawer' },
    ],
  },
  {
    group: 'Run lifecycle',
    items: [
      { keys: ['⌘', 'L'], label: 'Launch · run the currently selected scenario' },
      { keys: ['⌘', 'E'], label: 'Export · POV report for the last run' },
      { keys: ['⌘', 'A'], label: 'Abort · stop the active run (requires confirmation)' },
    ],
  },
  {
    group: 'Inspection',
    items: [
      { keys: ['↑', '↓'], label: 'Navigate scenario cards / palette results' },
      { keys: ['↵'],      label: 'Select · open scenario drawer / pick palette item' },
      { keys: ['P'],      label: 'Pin / unpin selected scenario (when drawer is open)' },
    ],
  },
]

const TABS_CHEATSHEET = [
  {
    name: 'Operations',
    body: 'Browse and launch scenarios. Filter by plane (rail) or by tactic / technique / actor / difficulty (⌘F). Click a card → inspector drawer with launch CTA pinned at top. Pin scenarios you use repeatedly — they appear in the rail and at the top of the palette.',
  },
  {
    name: 'In-Flight',
    body: 'Live attack-narrative timeline for the running scenario. Each step pulses amber while pending, fills teal when detected. XSIAM stitch arcs draw between correlated events — that\'s the POV money shot. Hit Screenshot for a PNG you can drop straight into a slide deck.',
  },
  {
    name: 'Evidence',
    body: 'Validate detections + export the POV report. KPI row shows coverage %, median MTTD, stitch count, pending. Scorecard rows have inline ✓ ✗ ○ buttons for per-detection validation. "Export POV report" produces a Cortex-branded markdown you can hand to the customer.',
  },
  {
    name: 'Lab',
    body: 'Generate IaC bundles for the target environment. Pick provider + modules, fill required params, Generate Bundle → download tar.gz with Terraform. Modules auto-select their dependencies (e.g. picking cdr also picks base + tim).',
  },
  {
    name: 'ATT&CK Coverage',
    body: 'MITRE matrix showing what techniques the scenario library covers. Click a technique cell → detail panel → "Filter Operations →" jumps to the Operations tab pre-filtered to scenarios exercising that technique.',
  },
]

const FIRST_RUN_KEY = 'cortexsim.helpOverlay.seenV1'

export function shouldShowOnFirstRun() {
  try {
    return window.localStorage.getItem(FIRST_RUN_KEY) !== 'true'
  } catch {
    return false
  }
}

export function markFirstRunSeen() {
  try { window.localStorage.setItem(FIRST_RUN_KEY, 'true') } catch {}
}

export default function HelpOverlay({ open, onClose = () => {}, onTour = null }) {
  const [activeTab, setActiveTab] = useState('shortcuts')

  useEffect(() => {
    if (!open) return undefined
    const handler = (e) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [open, onClose])

  if (!open) return null

  return (
    <div
      className="help-overlay__backdrop"
      onClick={(e) => { if (e.target === e.currentTarget) onClose() }}
      role="dialog"
      aria-modal="true"
      aria-label="Help & keyboard shortcuts"
    >
      <div className="help-overlay">
        <div className="help-overlay__head">
          <div>
            <div className="help-overlay__eyebrow mono">CortexSim · operator console</div>
            <h2 className="help-overlay__title">Quick reference</h2>
          </div>
          <button
            type="button"
            className="btn"
            onClick={onClose}
            aria-label="Close help overlay"
          >
            <span>Close</span>
            <span className="kbd">esc</span>
          </button>
        </div>

        <div className="help-overlay__tabs">
          <button
            type="button"
            className={'help-overlay__tab' + (activeTab === 'shortcuts' ? ' is-active' : '')}
            onClick={() => setActiveTab('shortcuts')}
          >Shortcuts</button>
          <button
            type="button"
            className={'help-overlay__tab' + (activeTab === 'tabs' ? ' is-active' : '')}
            onClick={() => setActiveTab('tabs')}
          >Tab cheatsheet</button>
          <button
            type="button"
            className={'help-overlay__tab' + (activeTab === 'about' ? ' is-active' : '')}
            onClick={() => setActiveTab('about')}
          >About</button>
        </div>

        <div className="help-overlay__body">
          {activeTab === 'shortcuts' && <ShortcutsList />}
          {activeTab === 'tabs' && <TabsCheatsheet />}
          {activeTab === 'about' && <AboutPane />}
        </div>

        <div className="help-overlay__footer">
          <span className="mono">
            Tip: hit <span className="kbd">⌘</span><span className="kbd">/</span> from
            anywhere to reopen this overlay.
          </span>
          {onTour && (
            <button type="button" className="btn btn--primary" onClick={onTour}>
              Start guided tour
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

/* ─── Subcomponents ──────────────────────────────────────────────── */

function ShortcutsList() {
  return (
    <div className="help-overlay__shortcuts">
      {SHORTCUTS.map((group) => (
        <section key={group.group}>
          <h3 className="help-overlay__group-title">{group.group}</h3>
          <ul className="help-overlay__shortcut-list">
            {group.items.map((s) => (
              <li key={s.label}>
                <span className="help-overlay__keys">
                  {s.keys.map((k) => (
                    <span key={k} className="kbd">{k}</span>
                  ))}
                </span>
                <span className="help-overlay__shortcut-label">{s.label}</span>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  )
}

function TabsCheatsheet() {
  return (
    <div className="help-overlay__cheatsheet">
      {TABS_CHEATSHEET.map((t) => (
        <section key={t.name}>
          <h3 className="help-overlay__group-title">{t.name}</h3>
          <p className="help-overlay__cheatsheet-body">{t.body}</p>
        </section>
      ))}
    </div>
  )
}

function AboutPane() {
  return (
    <div className="help-overlay__about">
      <h3 className="help-overlay__group-title">CortexSim</h3>
      <p>
        Enterprise detection-simulation engine for Palo Alto Networks
        Domain Consultants. Generates controlled, high-fidelity signals
        into customer Cortex environments (XSIAM / XDR / Cloud / ITDR)
        to validate detection logic — BIOC, Analytics, IOC, and incident
        stitching.
      </p>
      <p>
        <strong>Not a red-team C2.</strong> This is a quality-assurance
        engine for Cortex detection content — opinionated, repeatable,
        and built around the operator workflow of running a POV in a
        customer lab.
      </p>

      <h3 className="help-overlay__group-title">PANW stack coverage</h3>
      <ul className="help-overlay__stack-list">
        <li><strong>Cortex XDR / XSIAM</strong> — endpoint BIOCs, behavioral profiles, incident stitching</li>
        <li><strong>Cortex Cloud</strong> — cloud audit-log detections, CSPM findings</li>
        <li><strong>Cortex ITDR</strong> — Kerberos / LDAP / AD hygiene</li>
        <li><strong>Cortex XSOAR</strong> — auto-containment playbooks</li>
        <li><strong>Cortex Xpanse</strong> — external exposure correlation</li>
        <li><strong>Strata Network Security</strong> — NGFW + Network Security Analytics</li>
        <li><strong>Prisma Cloud</strong> — workload protection</li>
      </ul>

      <h3 className="help-overlay__group-title">Need help?</h3>
      <p>
        Operator runbook: <a href="/docs/operator-runbook.md">docs/operator-runbook.md</a>
        {' · '}
        Quick start: <a href="/docs/quick-start.md">docs/quick-start.md</a>
        {' · '}
        Architecture: <a href="/CORTEXSIM_AGENT_CONTEXT.md">repo root</a>
      </p>
    </div>
  )
}
