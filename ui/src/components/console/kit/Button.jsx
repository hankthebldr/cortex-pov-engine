/**
 * Button — the console's three button kinds (design v26 §4.1, §5).
 *
 *   primary     filled --ac (Cortex green), --ac-tx label. One per view.
 *   secondary   1px --bd2 outline, neutral label.
 *   destructive filled --crit, for a confirmed destructive step.
 *
 * Labels come from a closed set of verbs (VERBS). The set is the design's
 * vocabulary for what a DC can do; "wizard", "probe", "setup", "enroll" and
 * "bind" are implementation words and do not ship as button labels.
 *
 * Each verb carries its icon (design v33 C10). Pass `verb` and the button
 * draws that verb's icon before its label; the label is the verb unless
 * children say otherwise (a busy state such as "Launching…"). Anything that
 * only navigates is a link, not a Button.
 */
import React from 'react'
import Icon from './Icon.jsx'

void React

export const VERBS = Object.freeze([
  'Configure', 'Test', 'Run', 'Customize', 'Add to chain', 'Read back', 'Open results', 'Abort run',
])

/** The kit Icon each verb carries. */
export const VERB_ICONS = Object.freeze({
  Configure: 'gear',
  Test: 'check',
  Run: 'play',
  Customize: 'pencil',
  'Add to chain': 'plus',
  'Read back': 'rotate',
  'Open results': 'external',
  'Abort run': 'stop',
})

export default function Button({ variant = 'secondary', type = 'button', verb, className = '', children, ...rest }) {
  const kind = ['primary', 'secondary', 'destructive'].includes(variant) ? variant : 'secondary'
  return (
    <button type={type} className={`kit-btn kit-btn--${kind} ${className}`.trim()} data-verb={verb} {...rest}>
      {verb && <Icon name={VERB_ICONS[verb]} size={12} />}
      {children ?? verb}
    </button>
  )
}
