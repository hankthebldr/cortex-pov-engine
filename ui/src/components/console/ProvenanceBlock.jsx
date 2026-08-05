import React from 'react'
import { SUPPLY, formatBytes, shortDigest, hostOf } from './supplyState.js'

/**
 * ProvenanceBlock — where the bytes came from, what they are, and who owns them.
 *
 * Rows are ordered availability → identity → legal, because that is the order a
 * customer's endpoint team asks the questions in: "is it here", "what is it",
 * "can you run it on my host". The `Copy provenance` block is the artefact a DC
 * is actually asked for when someone says *"what are you about to run on my
 * machine"* — which is why this is a first-class section and not a footnote.
 */
export default function ProvenanceBlock({ supply, adapter = null, compact = false }) {
  if (!supply) return null

  const rows = []
  rows.push(['state', <span key="s" className={supply.chipClass}>{supply.label}</span>])
  if (supply.payloadName) rows.push(['artifact', <span key="n" className="mono">{supply.payloadName}</span>])

  if (supply.sha256) {
    rows.push(['sha256', (
      <span key="d" className="provenance__digest mono">
        {supply.sha256}
        <CopyButton value={supply.sha256} label="copy digest" />
      </span>
    )])
  } else if (supply.state === SUPPLY.UNSTAGED) {
    rows.push(['sha256', (
      <span key="d" className="mono provenance__muted">
        not known until it is staged — this pack declares no pin
      </span>
    )])
  }

  if (supply.sizeBytes != null) rows.push(['size', <span key="z" className="mono">{formatBytes(supply.sizeBytes)}</span>])

  if (supply.sourceUrl) {
    rows.push(['source', (
      <a key="u" className="mono provenance__link" href={supply.sourceUrl} target="_blank" rel="noreferrer">
        {supply.sourceUrl}
      </a>
    )])
  }

  if (supply.state === SUPPLY.STAGED || supply.state === SUPPLY.UNSTAGED) {
    rows.push(['pinned', supply.pinned
      ? <span key="p" className="mono">yes{supply.pinType ? ` (${supply.pinType}${supply.pinRef ? ` ${supply.pinRef}` : ''})` : ''}</span>
      : (
        <span key="p" className="mono provenance__unpinned">
          no — upstream can change under you between engagements
        </span>
      )])
  }

  // LICENCE DISAGREEMENT. Silently preferring one is how a GPL artifact ends up
  // on a customer host under an MIT label.
  const artLicense = supply.license || null
  const packLicense = supply.packLicense || adapter?.upstream?.license || adapter?.license || null
  if (artLicense && packLicense && artLicense !== packLicense) {
    rows.push(['license', (
      <span key="l">
        <span className="chip chip--missed">LICENCE MISMATCH</span>{' '}
        <span className="mono">pack: {packLicense} · staged: {artLicense}</span>
        <div className="provenance__warn">
          The pack declares {packLicense}; the staged artifact records {artLicense}. The shelf
          may hold a different tool than this pack describes. Verify before handing it to a customer.
        </div>
      </span>
    )])
  } else if (artLicense || packLicense) {
    rows.push(['license', <span key="l" className="mono">{artLicense || packLicense}</span>])
  }

  if (adapter?.upstream?.attribution) {
    rows.push(['attribution', <span key="a" className="mono">{adapter.upstream.attribution}</span>])
  }
  if (supply.stagedAt) rows.push(['staged', <span key="t" className="mono">{supply.stagedAt}</span>])
  if (supply.stagePath) rows.push(['default dest', <span key="sp" className="mono">{supply.stagePath}</span>])
  if (adapter?.adapter_id) {
    rows.push(['adapter', <span key="id" className="mono">{adapter.adapter_id}{adapter.version ? ` v${adapter.version}` : ''}</span>])
  }

  return (
    <div className={'provenance' + (compact ? ' provenance--compact' : '')} data-testid="provenance-block">
      {rows.map(([k, v]) => (
        <div className="provenance__row" key={k}>
          <div className="provenance__key mono">{k}</div>
          <div className="provenance__val">{v}</div>
        </div>
      ))}
      {supply.state === SUPPLY.STAGED && (
        <div className="provenance__actions">
          <CopyButton
            value={provenanceText(supply, adapter)}
            label="Copy provenance"
            variant="btn btn--xs"
          />
          <span className="provenance__hint">
            paste into the customer&apos;s change ticket
          </span>
        </div>
      )}
    </div>
  )
}

/** The paste-ready block. Plain text on purpose — it goes into a ticket. */
export function provenanceText(supply, adapter) {
  const lines = [
    'CortexSim staged artifact',
    `  name       ${supply.payloadName || '—'}`,
    `  sha256     ${supply.sha256 || '—'}`,
    `  size       ${formatBytes(supply.sizeBytes)}`,
    `  source     ${supply.sourceUrl || '—'}`,
    `  host       ${hostOf(supply.sourceUrl) || '—'}`,
    `  pinned     ${supply.pinned ? `yes${supply.pinType ? ` (${supply.pinType} ${supply.pinRef || ''})` : ''}` : 'no'}`,
    `  license    ${supply.license || adapter?.upstream?.license || '—'}`,
    `  staged     ${supply.stagedAt || '—'}`,
    `  adapter    ${adapter?.adapter_id || supply.adapterId || '—'}${adapter?.version ? ` v${adapter.version}` : ''}`,
  ]
  return lines.join('\n')
}

export function CopyButton({ value, label = 'copy', variant = 'provenance__copy' }) {
  const [done, setDone] = React.useState(false)
  return (
    <button
      type="button"
      className={variant}
      onClick={(e) => {
        e.stopPropagation()
        try {
          navigator?.clipboard?.writeText?.(value)?.catch?.(() => {})
          setDone(true)
          setTimeout(() => setDone(false), 1200)
        } catch { /* clipboard unavailable — the text is on screen anyway */ }
      }}
    >
      {done ? 'copied' : label}
    </button>
  )
}

export function shortDigestOf(hex) { return shortDigest(hex) }
