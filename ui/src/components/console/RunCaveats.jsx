/**
 * RunCaveats — what changes how this run's results should be read, said once
 * on Results before any detection view.
 *
 * These lived at the top of the run's page while the detection views were its
 * tabs. The views are Results' now, so the caveats moved with them: one
 * collapsed line ("Read with care · N caveats") that opens the same banners.
 * The run's own page states the same facts as execution, step by step, in its
 * runtime log.
 *
 *   launch     SimCore accepted the launch with a warning about the target
 *   identity   `!! IDENTITY NOT HONOURED`: steps ran as the agent's own account
 *   variant    `!! PLATFORM VARIANT`: steps ran their per-OS variant
 *   not run    `!! STEP NOT RUN: PLATFORM_UNSATISFIABLE`: the host refused them
 *   severed    the chain was cut at a step
 *   attested   what the operator declared at launch; SimCore verified none
 */
import React, { useMemo } from 'react'
import { parseStepTranscript } from './useRunStepTail.js'
import { launchWarningsFor } from './launchWarnings.js'
import { hasAttestations, attestationLines } from '../../outcomes.js'
import { StatusIcon } from './kit/index.js'
import '../../styles/destinations/run-detail.css'

void React

// Launch/host markers that change what a missing detection MEANS. Both carry
// `step=<id>` and may sit outside a step's === STEP === block (the variant
// marker is written at launch, before any step runs), so they are matched by
// line rather than through the step parser — which stays the one reader of
// the per-step grammar (identity, exit code, duration).
const VARIANT_MARKER = /^!! PLATFORM VARIANT:\s*step=(\S+)\s*(.*)$/
const NOT_RUN_MARKER = /^!! STEP NOT RUN: PLATFORM_UNSATISFIABLE\s+step=(\S+)\s*(.*)$/
// Launch-time reservations about the TARGET, not about one step. SimCore writes
// TARGET_AGENT_OS_UNKNOWN into the transcript with the `!!` prefix; it returns
// TARGET_AGENT_OFFLINE on the launch response without one. Both spellings are
// read, so the day the offline marker is written to Run.output it shows here.
const LAUNCH_MARKER = /^(?:!!\s*)?(TARGET_AGENT_[A-Z0-9_]+):\s*(.*)$/

/**
 * What in this run's transcript says its steps did not run as authored.
 * Pure. Identity comes from `parseStepTranscript` (useRunStepTail.js) — reused,
 * not re-implemented, so a beacon format change fails one parser test.
 */
export function runDegradations(output) {
  const parsed = parseStepTranscript(output)
  const identity = parsed.order
    .map((id) => ({ id, ...parsed.steps[id] }))
    .filter((s) => s.identityNote)
  const total = parsed.order.reduce((m, id) => Math.max(m, parsed.steps[id].total || 0), 0)
    || parsed.order.length

  const variant = []
  const notRun = []
  const launch = []
  const seen = { variant: new Set(), notRun: new Set(), launch: new Set() }
  if (typeof output === 'string') {
    for (const raw of output.split('\n')) {
      const line = raw.replace(/\r$/, '').trim()
      let m = VARIANT_MARKER.exec(line)
      if (m && !seen.variant.has(m[1])) {
        seen.variant.add(m[1])
        variant.push({ id: m[1], note: m[2] || '' })
        continue
      }
      m = NOT_RUN_MARKER.exec(line)
      if (m && !seen.notRun.has(m[1])) {
        seen.notRun.add(m[1])
        notRun.push({ id: m[1], note: m[2] || '' })
        continue
      }
      m = LAUNCH_MARKER.exec(line)
      if (m && !seen.launch.has(m[1])) {
        seen.launch.add(m[1])
        launch.push({ code: m[1], text: m[2] || '', source: 'record' })
      }
    }
  }
  return { identity, total, variant, notRun, launch }
}

/**
 * The launch warnings to show for a run: what the run RECORD carries, then
 * anything this browser session heard on the launch response that the record
 * does not (`source: 'session'`). One row per code. Step-level markers
 * (PLATFORM VARIANT) are left to their own banner.
 */
export function mergeLaunchWarnings(fromRecord, fromSession) {
  const out = [...(fromRecord || [])]
  const have = new Set(out.map((w) => w.code))
  for (const w of fromSession || []) {
    if (!w || !w.code || !/^TARGET_AGENT_/.test(w.code) || have.has(w.code)) continue
    have.add(w.code)
    out.push({ code: w.code, text: w.text || '', source: 'session' })
  }
  return out
}

/**
 * The three ways a step can finish without having run as the scenario authored
 * it, and the launch warnings about the target.
 *
 * `partial`: only the summary row's output tail was readable, so a step named
 * early in the transcript may be missing from these counts — said, not hidden.
 */
function RunDegradationBanners({ degraded, launchWarnings = [], partial = false }) {
  const { identity, total, variant, notRun } = degraded
  if (!identity.length && !variant.length && !notRun.length && !launchWarnings.length) return null
  const partialNote = partial && (
    <p className="run-attest__lede">
      Read from the last 2 KB of this run&apos;s output only — the full transcript could not be
      loaded, so these counts may be low.
    </p>
  )
  const sessionOnly = launchWarnings.some((w) => w.source === 'session')
  return (
    <>
      {launchWarnings.length > 0 && (
        <div className="run-severed run-degraded" role="status" data-testid="run-launch-warnings-banner">
          <div>
            {`SimCore accepted this launch with ${launchWarnings.length === 1 ? 'a warning' : `${launchWarnings.length} warnings`} `
              + 'about the target — read before treating a silent run as missed detections'}
          </div>
          <ul className="run-attest__list">
            {launchWarnings.map((w) => (
              <li key={w.code}>
                <span className="mono">{w.code}</span>{w.text ? ` — ${w.text}` : ''}
                {w.source === 'session' ? ' (from the launch response)' : ''}
              </li>
            ))}
          </ul>
          {sessionOnly && (
            <p className="run-attest__lede">
              A warning marked “from the launch response” is not in the run record: this browser
              session kept it from the launch. Another browser, or an export of this run, will not show it.
            </p>
          )}
          {partialNote}
        </div>
      )}
      {identity.length > 0 && (
        <div className="run-severed run-degraded" role="status" data-testid="run-identity-banner">
          <div>
            {`${identity.length} of ${Math.max(total, identity.length)} steps ran as the beacon account, `
              + 'not the authored identity — lineage-keyed misses on these steps are not a coverage '
              + 'measurement'}
          </div>
          <ul className="run-attest__list">
            {identity.map((s) => (
              <li key={s.id}>
                <span className="mono">{s.id}</span>
                {s.identity ? <> · authored <span className="mono">{s.identity}</span></> : null}
                {` — ${s.identityNote}`}
              </li>
            ))}
          </ul>
          {partialNote}
        </div>
      )}
      {variant.length > 0 && (
        <div className="run-severed run-degraded" role="status" data-testid="run-platform-variant-banner">
          <div>
            {`${variant.length} step${variant.length === 1 ? '' : 's'} ran a platform variant, not the `
              + 'authored command — expected detections unverified for this OS'}
          </div>
          <ul className="run-attest__list">
            {variant.map((s) => (
              <li key={s.id}><span className="mono">{s.id}</span>{s.note ? ` — ${s.note}` : ''}</li>
            ))}
          </ul>
          {partialNote}
        </div>
      )}
      {notRun.length > 0 && (
        <div className="run-severed run-degraded" role="status" data-testid="run-platform-notrun-banner">
          <div>
            {`${notRun.length} step${notRun.length === 1 ? '' : 's'} never ran on this host `
              + '(PLATFORM_UNSATISFIABLE) — their detections could not fire, so a miss on them is not '
              + 'a coverage measurement'}
          </div>
          <ul className="run-attest__list">
            {notRun.map((s) => (
              <li key={s.id}><span className="mono">{s.id}</span>{s.note ? ` — ${s.note}` : ''}</li>
            ))}
          </ul>
          {partialNote}
        </div>
      )}
    </>
  )
}

/**
 * @param {Object} props
 * @param {string} props.runId
 * @param {Object} props.run      the run (row merged with its record)
 * @param {string|null} props.output   the run's transcript, or null when unread
 * @param {boolean} [props.partial]    `output` is the record's last 2 KB only
 */
export default function RunCaveats({ runId, run = null, output = null, partial = false }) {
  const degraded = useMemo(() => runDegradations(output), [output])
  const launchWarnings = useMemo(
    () => mergeLaunchWarnings(degraded.launch, launchWarningsFor(runId)),
    [degraded.launch, runId],
  )
  const attest = hasAttestations(run) ? attestationLines(run) : null
  const count = (launchWarnings.length ? 1 : 0)
    + (degraded.identity.length ? 1 : 0)
    + (degraded.variant.length ? 1 : 0)
    + (degraded.notRun.length ? 1 : 0)
    + (run?.severed_at_step ? 1 : 0)
    + (attest ? 1 : 0)
  if (count === 0) return null

  return (
    <details className="run-detail run-caveats" data-testid="run-caveats">
      <summary className="run-caveats__summary">
        <StatusIcon status="degraded" word="Read with care" />
        <span className="run-caveats__count">{count} {count === 1 ? 'caveat' : 'caveats'}</span>
      </summary>
      <div className="run-caveats__body">
        {run?.severed_at_step && (
          <div className="run-severed" role="status" data-testid="run-severed-banner">
            {`Chain severed at ${run.severed_at_step}`}
          </div>
        )}

        <RunDegradationBanners degraded={degraded} launchWarnings={launchWarnings} partial={partial} />

        {attest && (
          <section className="run-attest" aria-label="Attestations" data-testid="run-attestations">
            <h2 className="run-attest__h">Attestations</h2>
            <p className="run-attest__lede">
              Operator declarations at launch. SimCore did not observe or verify any of these.
            </p>
            <ul className="run-attest__list mono">
              <li>{attest.profile}</li>
              <li>{attest.bas}</li>
              <li>{attest.soakless}</li>
              <li>{attest.canary}</li>
            </ul>
          </section>
        )}
      </div>
    </details>
  )
}
