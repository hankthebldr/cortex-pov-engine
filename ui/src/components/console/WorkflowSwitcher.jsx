import React, { useState } from 'react'
import { WORKFLOWS, WF_TONE } from './povdata/corpus.js'

/**
 * WorkflowSwitcher — which chain am I editing, and is it saved?
 *
 * WHAT THIS ANSWERS THAT NOTHING DID
 * The Composer's own head named the scenario it was started from and nothing
 * else, so three questions had no answer on screen: which of this POV's chains
 * is open, has it been saved, and how do I start another one. A DC asked all
 * three out loud — "I don't see any navigation arrows, or save as, and I don't
 * know how to build a workflow from the various objects".
 *
 * NODE COUNTS ARE DERIVED FROM THE CHAIN, NEVER STORED.
 * The list cannot advertise a number the canvas does not render. That is not
 * hypothetical: each workflow used to share one module-level CHAIN constant, so
 * selecting WF-0015 kept rendering WF-0012's six steps while the switcher
 * confidently said three.
 *
 * Props:
 *   current    — workflow id
 *   onSelect   — (id) => void
 *   dirty      — boolean; the live unsaved state of the OPEN draft, which
 *                overrides the catalog's own `state` for the current row
 *   onSave / onSaveAs / onNew / onDuplicate / onValidate
 */
export default function WorkflowSwitcher({
  current,
  onSelect = () => {},
  dirty = false,
  onSave = null,
  onSaveAs = null,
  onNew = null,
  onDuplicate = null,
  onValidate = null,
}) {
  const [open, setOpen] = useState(false)
  const wf = WORKFLOWS.find((w) => w.id === current) || WORKFLOWS[0]
  // The OPEN workflow's save state comes from the live draft, not the catalog —
  // the catalog's value is the state it was last persisted in.
  const state = dirty ? 'unsaved' : wf.state
  const [bg, fg] = WF_TONE[state] || WF_TONE.draft

  return (
    <div className="pov-collection" data-testid="workflow-switcher">
      <button
        type="button"
        className="pov-collection__trigger"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="pov-collection__kicker">{wf.id}</span>
        <span className="pov-collection__name">{wf.version}</span>
        <span className="pov-collection__rule" />
        <span className="pov-collection__state" style={{ background: bg, color: fg }}>
          {state.toUpperCase()}
        </span>
      </button>

      {open && (
        <div className="pov-collection__panel" style={{ width: 420 }}>
          <div className="pov-collection__head">
            <div className="pov-collection__head-row">
              <span className="pov-rail__label">Workflows in this POV</span>
              <span className="pov-collection__id mono">{WORKFLOWS.length}</span>
            </div>
            <div className="pov-collection__note">
              A workflow owns its own chain, lanes and edges. Switching re-renders the
              canvas, the execution order and the launch gate together.
            </div>
          </div>

          <div className="pov-collection__list">
            {WORKFLOWS.map((w) => {
              const on = w.id === current
              const st = on && dirty ? 'unsaved' : w.state
              const [b, f] = WF_TONE[st] || WF_TONE.draft
              return (
                <button
                  key={w.id}
                  type="button"
                  className="pov-collection__group"
                  style={{ gridTemplateColumns: '14px minmax(0,1fr) 64px 62px' }}
                  onClick={() => { onSelect(w.id); setOpen(false) }}
                  aria-pressed={on}
                >
                  <span className={'pov-collection__box' + (on ? ' pov-collection__box--on' : '')}><i /></span>
                  <span>
                    <span className="pov-collection__glabel">{w.name}</span>
                    <span className="pov-collection__gsub">{w.id} {w.version} · {w.touched}</span>
                  </span>
                  {/* Derived. See the note at the top of this file. */}
                  <span className="pov-collection__n">{w.chain.length} nodes</span>
                  <span className="pov-pill" style={{ background: b, color: f, justifySelf: 'end' }}>
                    {st.toUpperCase()}
                  </span>
                </button>
              )
            })}
          </div>

          <div className="pov-collection__foot">
            <div className="pov-collection__actions">
              {onNew && (
                <button type="button" className="pov-btn pov-btn--flex" onClick={() => { onNew(); setOpen(false) }}>
                  New workflow
                </button>
              )}
              {onDuplicate && (
                <button type="button" className="pov-btn pov-btn--flex" onClick={() => { onDuplicate(); setOpen(false) }}>
                  Duplicate current
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Save / Save as… / Validate sit OUTSIDE the panel, in the toolbar, so
          the three things a DC does most often are one click rather than two. */}
      {onSave && (
        <span style={{ display: 'none' }} data-testid="workflow-actions-present" />
      )}
    </div>
  )
}

/**
 * The toolbar half of the switcher: the actions that must not be behind a
 * dropdown. Kept as a separate export so ComposerView can place them beside
 * its existing Load / Launch buttons rather than duplicating a toolbar.
 */
export function WorkflowActions({ dirty = false, onSave = null, onSaveAs = null, onValidate = null, onRunAll = null }) {
  return (
    <>
      {onSave && (
        <button
          type="button"
          className="btn btn--xs"
          data-testid="workflow-save"
          onClick={onSave}
          // Disabled on a clean draft rather than hidden: a Save button that
          // disappears when there is nothing to save reads as a missing
          // feature, and reappearing chrome is how a DC loses a button
          // mid-sentence in front of a customer.
          disabled={!dirty}
          title={dirty ? 'Save this workflow' : 'No unsaved changes'}
        >
          Save
        </button>
      )}
      {onSaveAs && (
        <button type="button" className="btn btn--xs" data-testid="workflow-save-as" onClick={onSaveAs}>
          Save as…
        </button>
      )}
      {onValidate && (
        <button type="button" className="btn btn--xs" data-testid="workflow-validate" onClick={onValidate}>
          Validate
        </button>
      )}
      {onRunAll && (
        <button type="button" className="btn btn--xs" data-testid="workflow-run-all" onClick={onRunAll}>
          Run all
        </button>
      )}
    </>
  )
}
