/**
 * ComposerInspector — the right column of the Composer.
 *
 * It is the configuration surface for the ONE selected step: name, command,
 * identity, technique, platforms, the causality edge (`parent_step` + pivot),
 * and the step's expected detections (add / remove, or bind a TTP card which
 * fills `type`/`ttp_ref`/`detection_id`/`description` in one move — the path to
 * satisfying the launch gate). When no step is selected it edits the workflow
 * META instead: name, plane, the `tc_ref` binding (deep-linkable into the
 * UC/TC Index), and the CGO anchor.
 *
 * This is a VIEW: it owns no source-of-truth state. Every edit is a call back
 * through a prop (`onEditStep` / `onAddDetection` / `onRemoveDetection` /
 * `onSetCausalityParent` / `onEditMeta`) — the container holds the draft and
 * decides what the edit means. That is deliberate: the same draft feeds the
 * canvas, the YAML, the launch gate and the save round-trip, so it must live in
 * exactly one place (ComposerView), never forked into this component.
 *
 * §6.6 makes the inspector editable, but four assertions in ComposerView.test
 * query values by TEXT (the command, the causality summary line, the
 * identity/technique `not declared` fallbacks, the scenario teardown). An
 * input's `.value` is not a text node, so every field the tests read keeps a
 * TEXT rendering alongside its editor: the command is a `contentEditable` <pre>
 * whose text content IS the command, and the causality summary is a caption
 * text node above the additive parent/pivot <select>s. Do not "clean this up"
 * into pure inputs — the text rendering is load-bearing.
 */
import React, { memo, useEffect, useRef } from 'react'
import { PIVOTS, DETECTION_TYPES, PLANES } from './composerDraft.js'

/**
 * CommandEditor — an UNCONTROLLED, memoised contentEditable for the step command.
 *
 * A controlled contentEditable (`<pre>{selected.command}</pre>` + onInput →
 * setState) rewrites its own text node on every parent re-render, which collapses
 * the caret to offset 0 — the field types backwards and is unusable. So this node
 * is uncontrolled: it renders NO JSX children, the ref seeds the text imperatively,
 * and the effect re-seeds ONLY when the value diverges (on a step switch), never on
 * the keystroke the user just made. `getByText(command)` still resolves because the
 * effect runs on mount. role="textbox"/aria-multiline announce it as an editable
 * multiline field; a real placeholder (data-placeholder) covers the null-command
 * case instead of rendering the help sentence AS editable content.
 */
const CommandEditor = memo(function CommandEditor({ stepId, initial, onEditStep }) {
  const ref = useRef(null)
  useEffect(() => {
    const el = ref.current
    const next = initial ?? ''
    if (el && el.textContent !== next) el.textContent = next
  }, [stepId, initial])
  return (
    <pre
      ref={ref}
      className="field-code mono field-code--editable"
      contentEditable
      role="textbox"
      aria-multiline="true"
      aria-label={`Command for ${stepId}`}
      suppressContentEditableWarning
      spellCheck={false}
      data-placeholder="# configure the command for this step"
      onInput={(e) => onEditStep(stepId, { command: e.currentTarget.textContent })}
    />
  )
})

/**
 * Map a detection type to a chip tone. Kept identical to the mapping the canvas
 * uses so a BIOC chip reads the same on both surfaces.
 */
export function detTone(type) {
  const t = String(type || '').toUpperCase()
  if (t === 'BIOC') return 'detected'
  if (t === 'XQL' || t === 'ANALYTICS') return 'signal'
  return 'pending'
}

export default function ComposerInspector({
  selected = null,
  draft = {},
  steps = [],
  onEditStep = () => {},
  onAddDetection = () => {},
  onRemoveDetection = () => {},
  onSetCausalityParent = () => {},
  onBindTtp = () => {},
  onEditMeta = () => {},
  pivots = PIVOTS,
  detectionTypes = DETECTION_TYPES,
  planes = PLANES,
  onNavigate = () => {},
}) {
  const teardown = Array.isArray(draft.teardown) ? draft.teardown : []

  return (
    <aside className="composer-inspector" aria-label="Step configuration" data-testid="composer-inspector">
      <div className="composer-inspector__head">
        <span className="composer-inspector__title">
          {selected ? 'Step config' : 'Workflow meta'}
        </span>
        <span className="composer__spacer" />
        <span className="mono composer-inspector__id">{selected?.id || '—'}</span>
      </div>

      {!selected ? (
        <WorkflowMeta
          draft={draft}
          planes={planes}
          onEditMeta={onEditMeta}
          onNavigate={onNavigate}
        />
      ) : (
        <>
          {/* ── name ── */}
          <label className="field-label" htmlFor="insp-name">Step name</label>
          <input
            id="insp-name"
            className="field-input"
            type="text"
            value={selected.name || ''}
            placeholder="name this step"
            onChange={(e) => onEditStep(selected.id, { name: e.target.value })}
          />

          {/* ── command (uncontrolled contentEditable — see CommandEditor) ── */}
          <div className="field-label">Command</div>
          <CommandEditor
            stepId={selected.id}
            initial={selected.command}
            onEditStep={onEditStep}
          />

          {/* ── identity / technique (text value + editor) ── */}
          <div className="composer-inspector__pair">
            <div>
              <div className="field-label">Identity</div>
              <div className="field-value mono">{selected.identity || 'not declared'}</div>
              <input
                className="field-input"
                type="text"
                value={selected.identity || ''}
                placeholder="identity"
                aria-label={`Identity for ${selected.id}`}
                onChange={(e) => onEditStep(selected.id, { identity: e.target.value })}
              />
            </div>
            <div>
              <div className="field-label">Technique</div>
              <div className="field-value mono">{selected.technique || 'not declared'}</div>
              <input
                className="field-input"
                type="text"
                value={selected.technique || ''}
                placeholder="T1003.008"
                aria-label={`Technique for ${selected.id}`}
                onChange={(e) => onEditStep(selected.id, { technique: e.target.value })}
              />
            </div>
          </div>

          {/* ── causality (summary text node + parent/pivot selects) ── */}
          <div className="field-label">Causality</div>
          <div className="field-value mono">
            {selected.causalityParent
              ? `parent ${selected.causalityParent} · pivot ${selected.causalityPivot || 'process_lineage'}`
              : 'root of chain'}
          </div>
          <div className="composer-inspector__pair">
            <div>
              <label className="field-sub" htmlFor="insp-parent">parent step</label>
              <select
                id="insp-parent"
                className="field-input"
                aria-label={`Causality parent for ${selected.id}`}
                value={selected.causalityParent || ''}
                onChange={(e) =>
                  onSetCausalityParent(
                    selected.id,
                    e.target.value || null,
                    selected.causalityPivot || 'process_lineage',
                  )
                }
              >
                <option value="">— (chain root)</option>
                {steps
                  .filter((s) => s.id !== selected.id)
                  .map((s) => (
                    <option key={s.id} value={s.id}>{s.id}</option>
                  ))}
              </select>
            </div>
            <div>
              <label className="field-sub" htmlFor="insp-pivot">pivot</label>
              <select
                id="insp-pivot"
                className="field-input"
                aria-label={`Causality pivot for ${selected.id}`}
                value={selected.causalityPivot || 'process_lineage'}
                onChange={(e) =>
                  onSetCausalityParent(selected.id, selected.causalityParent || null, e.target.value)
                }
              >
                {pivots.map((p) => (
                  <option key={p} value={p}>{p}</option>
                ))}
              </select>
            </div>
          </div>

          {/* ── platforms (text value + comma editor) ── */}
          <div className="field-label">Platforms</div>
          <div className="field-value mono">
            {selected.platforms?.length ? selected.platforms.join(' · ') : 'not declared'}
          </div>
          <input
            className="field-input"
            type="text"
            value={(selected.platforms || []).join(', ')}
            placeholder="linux, container"
            aria-label={`Platforms for ${selected.id}`}
            onChange={(e) =>
              onEditStep(selected.id, {
                platforms: e.target.value
                  .split(',')
                  .map((p) => p.trim())
                  .filter(Boolean),
              })
            }
          />

          {/* ── expected detections ── */}
          <div className="field-label">
            Expected detections
            <span className="field-label__count mono"> {selected.detections?.length || 0}</span>
          </div>

          {selected.detections?.length ? (
            selected.detections.map((d, k) => (
              <div className="detection-card" key={k}>
                <div className="detection-card__head">
                  <span className={`chip chip--${detTone(d.type)}`}>{d.type || '?'}</span>
                  {d.ttpRef && (
                    <button
                      type="button"
                      className="linklike mono"
                      onClick={() => onNavigate('ttps', { ttp: d.ttpRef })}
                    >
                      {d.ttpRef}
                    </button>
                  )}
                  <span className="composer__spacer" />
                  <button
                    type="button"
                    className="btn btn--xs btn--ghost"
                    aria-label={`Remove detection ${k + 1} from ${selected.id}`}
                    onClick={() => onRemoveDetection(selected.id, k)}
                  >
                    Remove
                  </button>
                </div>
                <div className="detection-card__desc">{d.description || 'no description'}</div>
                {d.detectionId && <div className="detection-card__id mono">{d.detectionId}</div>}
              </div>
            ))
          ) : (
            <div className="detection-card detection-card--empty">
              This step declares no expected detection. It will execute and then be
              reported as a gap — bind a TTP card from the TTP Cards surface.
              <button
                type="button"
                className="btn btn--xs"
                onClick={() => onBindTtp(selected.id)}
              >
                Browse TTP cards
              </button>
            </div>
          )}

          <AddDetection
            planes={planes}
            detectionTypes={detectionTypes}
            onAdd={(det) => onAddDetection(selected.id, det)}
          />

          {/* ── teardown (scenario-level, per the schema) ── */}
          <div className="field-label">Teardown</div>
          <div className="field-value">
            {teardown.length ? (
              <>
                <span className="field-note">
                  Scenario-level (the schema has no per-step cleanup):
                </span>
                <pre className="field-code mono">{teardown.join('\n')}</pre>
              </>
            ) : (
              'no cleanup declared for this scenario'
            )}
          </div>
        </>
      )}
    </aside>
  )
}

/**
 * The add-a-detection form. Local ONLY to hold the in-progress row (plane /
 * type / description) before it is committed; on Add it hands a plain detection
 * object up through `onAdd` and clears itself. It never holds a draft detection
 * that the rest of the app can see — the source of truth is the step.
 */
function AddDetection({ planes, detectionTypes, onAdd }) {
  const [plane, setPlane] = React.useState(planes[0] || 'EDR')
  const [type, setType] = React.useState(detectionTypes[0] || 'BIOC')
  const [description, setDescription] = React.useState('')

  const commit = () => {
    onAdd({ plane, type, description: description.trim() || null })
    setDescription('')
  }

  return (
    <div className="detection-add" data-testid="detection-add">
      <div className="composer-inspector__pair">
        <div>
          <label className="field-sub" htmlFor="det-plane">plane</label>
          <select
            id="det-plane"
            className="field-input"
            aria-label="New detection plane"
            value={plane}
            onChange={(e) => setPlane(e.target.value)}
          >
            {planes.map((p) => (
              <option key={p} value={p}>{p}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="field-sub" htmlFor="det-type">type</label>
          <select
            id="det-type"
            className="field-input"
            aria-label="New detection type"
            value={type}
            onChange={(e) => setType(e.target.value)}
          >
            {detectionTypes.map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
        </div>
      </div>
      <input
        className="field-input"
        type="text"
        value={description}
        placeholder="what should fire"
        aria-label="New detection description"
        onChange={(e) => setDescription(e.target.value)}
      />
      <button type="button" className="btn btn--xs" onClick={commit}>
        Add detection
      </button>
    </div>
  )
}

/**
 * Editable workflow meta, shown when no step is selected: the chain's own
 * identity (name, plane), its `tc_ref` binding (which is what the launch gate
 * checks against the FY27 index — so we offer a deep link INTO that index), and
 * the CGO anchor line. No preserved assertion reads this branch, so it is free
 * to be inputs.
 */
function WorkflowMeta({ draft, planes, onEditMeta, onNavigate }) {
  return (
    <div className="composer-inspector__meta">
      <label className="field-label" htmlFor="meta-name">Workflow name</label>
      <input
        id="meta-name"
        className="field-input"
        type="text"
        value={draft.name || ''}
        placeholder="name this workflow"
        onChange={(e) => onEditMeta({ name: e.target.value })}
      />

      <label className="field-label" htmlFor="meta-plane">Plane</label>
      <select
        id="meta-plane"
        className="field-input"
        value={draft.plane || ''}
        onChange={(e) => onEditMeta({ plane: e.target.value })}
      >
        <option value="">— select plane</option>
        {planes.map((p) => (
          <option key={p} value={p}>{p}</option>
        ))}
      </select>

      <div className="field-label">Test-case binding</div>
      <div className="field-value mono">{draft.tcRef || 'UNBOUND'}</div>
      <div className="composer-inspector__pair">
        <input
          className="field-input"
          type="text"
          value={draft.tcRef || ''}
          placeholder="TC-EDR-03"
          aria-label="Bind tc_ref"
          onChange={(e) => onEditMeta({ tcRef: e.target.value })}
        />
        <button
          type="button"
          className="btn btn--xs"
          onClick={() => onNavigate('uctc')}
        >
          Browse UC/TC Index
        </button>
      </div>
      <p className="field-note">
        An UNBOUND draft is a legal saved state, but it cannot be launched — the
        launch gate refuses it until `tc_ref` names a real FY27 index test case.
      </p>

      <label className="field-label" htmlFor="meta-cgo">CGO anchor</label>
      <input
        id="meta-cgo"
        className="field-input"
        type="text"
        value={draft.cgo || ''}
        placeholder="apache2 / www-data"
        onChange={(e) => onEditMeta({ cgo: e.target.value })}
      />
    </div>
  )
}
