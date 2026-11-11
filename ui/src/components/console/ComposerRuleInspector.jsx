/**
 * ComposerRuleInspector — the Inspector's view of a correlation RULE node.
 *
 * READ-ONLY. A rule is card content the tenant must hold; the Composer never
 * edits it and never persists it, so this panel has no input at all. Its job is
 * to say, in words, the two things the node's colour must not be trusted for:
 * what the card actually holds, and what this scenario asks of it.
 *
 * Everything shown comes from `composerContent.deriveTenantContent`. Nothing
 * here contacts a tenant, and nothing claims a rule exists in one.
 */
import React from 'react'
import { ruleLines } from './composerContent.js'

const CONTENT_EXPLAIN = {
  'intent-only':
    'The card holds a name, an id and a logic line. That is a statement of intent. There is no '
    + 'query, schedule, severity or alert mapping, so there is nothing to import into a tenant.',
  deployable:
    'The card holds a complete rule body. It is authored content only: it has not been imported '
    + 'into, or accepted by, any tenant.',
  unreadable:
    'The rule on the card has a shape this console does not recognise, so its status is not '
    + 'guessed. Fix the card; until then treat the rule as absent.',
}

const BINDING_EXPLAIN = {
  declared:
    'A step expects this rule, so a run seeds a result for it and scores it as detected or missed.',
  'card-only':
    'The rule is on a card this chain cites, and no step expects it. A run does not look for it '
    + 'and does not score it.',
}

/** Where one `KIND(name)` input resolved, in words. */
function where(input) {
  if (input.state === 'unknown') return 'not on the card under this name'
  const held = input.resolvedKind && input.resolvedKind !== input.kind
    ? ` · the card holds it as ${input.resolvedKind}`
    : ''
  if (input.state === 'card-only') return `on the card · no step expects it${held}`
  return `expected on ${input.steps.join(', ')}${held}`
}

export default function ComposerRuleInspector({
  rule,
  summary = null,
  onNavigate = () => {},
}) {
  if (!rule) return null
  const lines = ruleLines(rule)
  const stub = rule.contentStatus !== 'deployable'
  return (
    <aside
      className="composer-inspector composer-rule-inspector"
      aria-label="Correlation rule"
      data-testid="composer-rule-inspector"
    >
      <div className="composer-inspector__head">
        <span className="composer-inspector__title">Correlation rule · tenant content</span>
      </div>
      <div className="mono rule-insp__id">{rule.ruleId}</div>

      <div className="field-value rule-insp__name">{rule.name}</div>

      <div className="field-label">What the card holds</div>
      <div className={`rule-insp__status rule-insp__status--${rule.contentStatus}`}>{lines.content}</div>
      <div className="field-note">{CONTENT_EXPLAIN[rule.contentStatus]}</div>
      {rule.contentError && (
        <div className="field-note rule-insp__reason mono">reason: {rule.contentError}</div>
      )}

      <div className="field-label">What this scenario asks for</div>
      <div className="rule-insp__status">{lines.binding}</div>
      <div className="field-note">{BINDING_EXPLAIN[rule.binding]}</div>
      {rule.binding === 'declared' && stub && (
        <div className="field-note rule-insp__warn">
          Nothing CortexSim ships can raise this alert. The result confirms only if the tenant
          already raises a matching one; otherwise it reads as missed, and that is a gap in the
          content, not in Cortex.
        </div>
      )}
      {rule.declaredBy.length > 0 && (
        <div className="field-note mono">expected on {rule.declaredBy.join(', ')}</div>
      )}

      <div className="field-label">Authoring note, not XQL</div>
      <pre className="field-code mono rule-insp__logic">
        {rule.logic || '(no logic line on the card)'}
      </pre>
      <div className="field-note">
        Shown exactly as the card has it. A correlation rule is an XQL query the tenant runs on a
        schedule or in real time; this line names the detections the rule would join and is
        nothing a tenant can run.
      </div>
      {rule.logicError && (
        <div className="field-note rule-insp__reason mono">not parsed: {rule.logicError}</div>
      )}

      <div className="field-label">
        Inputs
        <span className="field-label__count mono"> {lines.inputs}</span>
      </div>
      {rule.inputs.length ? (
        <ul className="rule-insp__inputs">
          {rule.inputs.map((input, i) => (
            <li key={`${input.kind}:${input.name}:${i}`} data-testid={`rule-input-${i}`}>
              <span className={`rule-insp__state rule-insp__state--${input.state}`}>
                {input.kind.toUpperCase()}
              </span>
              <span className="rule-insp__input-name">{input.name}</span>
              <span className="rule-insp__input-where">{where(input)}</span>
            </li>
          ))}
        </ul>
      ) : (
        <div className="field-note">
          {rule.contentStatus === 'unreadable'
            ? 'Not read: the rule itself is unreadable.'
            : 'The logic line names no detection this console can resolve, so no step is claimed '
              + 'to feed this rule.'}
        </div>
      )}

      <div className="field-label">Card</div>
      <div className="field-value">
        <button
          type="button"
          className="linklike mono"
          onClick={() => onNavigate('ttps', { ttp: rule.ttpRef })}
        >
          {rule.ttpRef}
        </button>
      </div>

      <div className="field-note rule-insp__foot">
        {summary && summary.deployable === 0
          ? 'No correlation rule on this chain\'s cards is deployable. '
          : ''}
        Creating a rule is your own action in the XSIAM console. CortexSim never writes to a
        tenant, and nothing here says the rule exists in one.
      </div>
    </aside>
  )
}
