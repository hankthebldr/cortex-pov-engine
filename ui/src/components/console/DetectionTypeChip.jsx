import React from 'react'

/**
 * DetectionTypeChip — a single detection-type pill.
 *
 * GAP-2: the detection-type vocabulary now includes XQL and Correlation
 * alongside the legacy BIOC | Analytics | IOC. Correlation is the strongest
 * XSIAM differentiator, so it gets a distinct violet treatment; XQL inherits
 * the teal signal color (XSIAM hunting query language), BIOC the Cortex-green
 * action color (behavioral / prevention-grade), IOC amber, Analytics neutral.
 *
 * Accepts any casing ('bioc', 'XQL', 'Correlation') and normalizes for both
 * the CSS variant class and the displayed label.
 */

// Canonical display labels keyed by the lower-cased token.
const LABELS = {
  bioc:        'BIOC',
  xql:         'XQL',
  analytics:   'Analytics',
  correlation: 'Correlation',
  ioc:         'IOC',
}

/** Normalize a free-form detection-type string to a known token, or null. */
export function detectionTypeToken(type) {
  const t = (type || '').toLowerCase().trim()
  if (!t) return null
  if (t in LABELS) return t
  // tolerate a few common aliases the corpus uses
  if (t === 'corr' || t === 'correlation_rule') return 'correlation'
  if (t === 'xql_query') return 'xql'
  return null
}

export default function DetectionTypeChip({ type, title }) {
  const token = detectionTypeToken(type)
  // Unknown / empty types fall back to a neutral chip showing the raw value so
  // nothing silently disappears.
  const label = token ? LABELS[token] : (type || '').toString().toUpperCase()
  const variant = token ? ` det-chip--${token}` : ''
  if (!label) return null
  return (
    <span
      className={'det-chip' + variant}
      title={title || `${label} detection`}
    >
      {label}
    </span>
  )
}
