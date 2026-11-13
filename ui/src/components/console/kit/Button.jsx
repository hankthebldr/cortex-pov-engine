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
 */
import React from 'react'

void React

export const VERBS = Object.freeze([
  'Configure', 'Test', 'Run', 'Customize', 'Add to chain', 'Read back', 'Open results', 'Abort run',
])

export default function Button({ variant = 'secondary', type = 'button', className = '', children, ...rest }) {
  const kind = ['primary', 'secondary', 'destructive'].includes(variant) ? variant : 'secondary'
  return (
    <button type={type} className={`kit-btn kit-btn--${kind} ${className}`.trim()} {...rest}>
      {children}
    </button>
  )
}
