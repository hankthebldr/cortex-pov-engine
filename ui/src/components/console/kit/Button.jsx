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
 * only navigates is a link, not a Button. `href` renders the same control as
 * an <a>, for a file the browser downloads.
 *
 * `iconOnly` draws just the verb's icon, for a row of small actions: the verb
 * is the button's accessible name and its tooltip.
 */
import React from 'react'
import Icon from './Icon.jsx'

void React

export const VERBS = Object.freeze([
  'Configure', 'Test', 'Run', 'Customize', 'Add to chain', 'Review', 'Open results', 'Abort run',
  // Agents › Fleet (#339): managing agent rows.
  'Deploy', 'Set active', 'Download', 'Copy', 'Delete', 'Select', 'Refresh',
  // The header's offer to leave an offline active agent (#341).
  'Switch',
  // A run's Live tab: hold or empty the stream, capture the timeline.
  'Pause', 'Clear', 'Screenshot',
  // Results › Detections: mark every detection that can be attested as seen.
  'Validate all',
  // Results › Detections: one detection, by hand. Reset puts it back to unjudged.
  'Mark detected', 'Mark missed', 'Reset',
  // Keep what was typed (a note on a detection).
  'Save',
])

/** The kit Icon each verb carries. */
export const VERB_ICONS = Object.freeze({
  Configure: 'gear',
  Test: 'check',
  Run: 'play',
  Customize: 'pencil',
  'Add to chain': 'plus',
  Review: 'rotate',
  'Open results': 'external',
  'Abort run': 'stop',
  Deploy: 'upload',
  'Set active': 'target',
  Download: 'download',
  Copy: 'copy',
  Delete: 'trash',
  Select: 'select',
  Refresh: 'refresh',
  Switch: 'swap',
  Pause: 'pause',
  Clear: 'clear',
  Screenshot: 'camera',
  'Validate all': 'check-all',
  'Mark detected': 'check-circle',
  'Mark missed': 'x',
  Reset: 'repeat',
  Save: 'save',
})

export default function Button({
  variant = 'secondary', type = 'button', verb, iconOnly = false, href, className = '', children, ...rest
}) {
  const kind = ['primary', 'secondary', 'destructive'].includes(variant) ? variant : 'secondary'
  const bare = iconOnly && !!verb
  const cls = `kit-btn kit-btn--${kind}${bare ? ' kit-btn--icon' : ''} ${className}`.trim()
  if (bare) {
    return (
      <button type={type} className={cls} data-verb={verb} aria-label={verb} title={verb} {...rest}>
        <Icon name={VERB_ICONS[verb]} size={12} />
      </button>
    )
  }
  const body = (
    <>
      {verb && <Icon name={VERB_ICONS[verb]} size={12} />}
      {children ?? verb}
    </>
  )
  // A file the browser saves (Download) is a link by HTML semantics; it keeps
  // the button's look and its verb icon.
  if (href) return <a href={href} className={cls} data-verb={verb} {...rest}>{body}</a>
  return <button type={type} className={cls} data-verb={verb} {...rest}>{body}</button>
}
