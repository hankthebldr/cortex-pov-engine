/**
 * ComposerStitchPanel — the scenario-level Stitch Context authoring surface.
 *
 * Rendered inside the Composer inspector's WORKFLOW-META view (no step
 * selected), after the CGO anchor. It lets a DC declare, for each of the nine
 * canonical entity keys, whether it is a LITERAL value or is RESOLVED at launch
 * by one of the six directives — the shared entities that make a cross-surface
 * chain stitch into one causality instance.
 *
 * It is a DUMB VIEW, isolated-testable like `ComposerPalette`. It fetches
 * nothing and holds no source-of-truth state: every edit is an `onSetEntity`
 * callback and the container (`ComposerView`, via `ComposerInspector`) decides
 * what it means, folding it into `draft.stitchContext`. The model it renders is
 * `parseStitchContext`'s `{[key]:{literal}|{resolve}}` shape (or `null`), and
 * every constant/validation it uses comes from `stitchContext.js` so the panel
 * can never offer a key or directive the backend would 422.
 *
 * THE HONESTY RULE
 * ----------------
 * This panel shows directive NAMES only. It NEVER renders a fabricated resolved
 * value — no made-up `10.x.y.z`, no `token@cortexsim-canary.invalid`. The real
 * values are derived server-side from the run id at launch and surface only
 * afterwards, from the run's persisted `stitch_binding` (the Run lens quotes
 * them). The one look-ahead it offers is honest: `from_agent` previews "resolves
 * to <agentName> at launch" — the launch target's hostname, which the DC already
 * chose — not an invented address.
 */
import React from 'react'
import {
  NICE_GROUPS,
  KEY_LABEL,
  directivesForKey,
  plantedKeys,
  fiveTupleComplete,
  validateStitchContext,
} from './stitchContext.js'

/** The current authoring mode of one row, derived from its entry. */
function entryMode(entry) {
  if (entry && typeof entry === 'object') {
    if (Object.prototype.hasOwnProperty.call(entry, 'resolve')) return 'resolve'
    if (Object.prototype.hasOwnProperty.call(entry, 'literal')) return 'literal'
  }
  return 'unset'
}

/**
 * One entity row: a mode indicator, a literal editor, a resolve directive
 * <select> restricted to the compatible directives, and a Clear control. Both
 * editors are always in the DOM so their aria-labelled controls are reachable;
 * the mode chip reflects which one is authoritative.
 */
function StitchRow({ entityKey, entry, onSetEntity, agentName }) {
  const mode = entryMode(entry)
  const directives = directivesForKey(entityKey)
  const literalOnly = directives.length === 0
  const literalValue = mode === 'literal' ? String(entry.literal ?? '') : ''
  const resolveValue = mode === 'resolve' ? entry.resolve : ''

  return (
    <div className="stitch-row" data-testid={`stitch-row-${entityKey}`}>
      <div className="stitch-row__head">
        <span className="stitch-row__label">{KEY_LABEL[entityKey]}</span>
        <span className="mono stitch-row__key">{entityKey}</span>
        <span className="composer__spacer" />
        <span className={`stitch-row__mode stitch-row__mode--${mode}`}>{mode}</span>
        <button
          type="button"
          className="btn btn--xs btn--ghost"
          aria-label={`Clear ${entityKey}`}
          disabled={mode === 'unset'}
          onClick={() => onSetEntity(entityKey, null)}
        >
          Clear
        </button>
      </div>

      <div className="stitch-row__editors">
        {/* Literal — accepts any scalar, used verbatim server-side. */}
        <input
          className="field-input stitch-row__literal"
          type="text"
          value={literalValue}
          placeholder="literal value"
          aria-label={`Literal for ${entityKey}`}
          onChange={(e) => onSetEntity(entityKey, { literal: e.target.value })}
        />

        {/* Resolve — directive NAMES only, restricted to the compatible set so
            an incompatible directive is literally unpickable. cloud_resource has
            no directive, so its select is disabled (literal-only). */}
        <select
          className="field-input stitch-row__resolve"
          aria-label={`Resolve directive for ${entityKey}`}
          value={resolveValue}
          disabled={literalOnly}
          onChange={(e) =>
            (e.target.value
              ? onSetEntity(entityKey, { resolve: e.target.value })
              : onSetEntity(entityKey, null))
          }
        >
          <option value="">{literalOnly ? '— no directive —' : '— resolve —'}</option>
          {directives.map((d) => (
            <option key={d} value={d}>{d}</option>
          ))}
        </select>
      </div>

      {literalOnly && (
        <p className="stitch-row__note field-note">
          literal only — no directive resolves a cloud resource
        </p>
      )}

      {/* The ONE honest look-ahead: from_agent resolves to the chosen target's
          hostname. Never a fabricated concrete value. */}
      {mode === 'resolve' && entry.resolve === 'from_agent' && (
        <p className="stitch-row__preview field-note" data-testid={`stitch-preview-${entityKey}`}>
          resolves to <span className="mono">{agentName || 'the launch target'}</span> at launch
        </p>
      )}
    </div>
  )
}

/**
 * @param {Object}   props
 * @param {Object|null} [props.model]        parsed `{[key]:{literal}|{resolve}}` model, or null
 * @param {Function} [props.onSetEntity]     `(key, entry|null) => void`; entry = {literal:v}|{resolve:d}
 * @param {string|null} [props.agentName]    for the `from_agent` "resolves to <agentName>" preview
 * @param {Function} [props.onNavigate]      optional deep-links (unused rows may add them later)
 */
export default function ComposerStitchPanel({
  model = null,
  onSetEntity = () => {},
  agentName = null,
  onNavigate = () => {},
}) {
  const planted = plantedKeys(model)
  const tupleReady = fiveTupleComplete(model)
  const { problems } = validateStitchContext(model)

  return (
    <section className="composer-stitch-panel" data-testid="composer-stitch-panel" aria-label="Stitch context">
      <div className="composer-stitch-panel__intro">
        <span className="field-label">Stitch context</span>
        <p className="field-note">
          Declare the shared entities that make this chain stitch into one causality
          instance. A <span className="mono">literal</span> is used verbatim; a{' '}
          <span className="mono">resolve</span> directive is derived from the run at
          launch (its real value shows on the Run lens afterwards, never here).
        </p>
      </div>

      {NICE_GROUPS.map((group) => (
        <div
          className="stitch-group"
          key={group.nice}
          data-testid={`stitch-group-${group.nice.toLowerCase()}`}
          role="group"
          aria-label={`${group.nice} entities`}
        >
          <div className="stitch-group__head">{group.nice}</div>
          {group.keys.map((key) => (
            <StitchRow
              key={key}
              entityKey={key}
              entry={model ? model[key] : undefined}
              onSetEntity={onSetEntity}
              agentName={agentName}
            />
          ))}
        </div>
      ))}

      <div className="composer-stitch-panel__foot" data-testid="stitch-panel-foot">
        <span className="stitch-foot__count">
          {planted.length} planted{planted.length === 1 ? ' entity' : ' entities'}
        </span>
        {tupleReady && (
          <span className="chip chip--detected" data-testid="stitch-five-tuple-badge">
            5-tuple complete
          </span>
        )}
        {/* Always-present polite live region so a newly-introduced authoring
            error (e.g. both literal+resolve on one key) is announced — a
            conditionally-mounted region is inserted with its text already set
            and most screen readers never speak it. */}
        <div className="stitch-foot__live" role="status" aria-live="polite">
          {problems.length > 0 && (
            <ul className="stitch-foot__problems" data-testid="stitch-panel-problems">
              {problems.map((p, i) => (
                <li key={i} className="stitch-foot__problem">{p}</li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </section>
  )
}
