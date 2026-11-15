/**
 * liveOutput.js — what each running step has printed so far. Pure.
 *
 * A beacon with the `live-output` capability streams a step's output while
 * the step runs. SimCore never stores those chunks: it keeps a tail per
 * running step on the run record (`live_tail`, only while the run is in
 * flight) and publishes each chunk on the run's event stream (`live: true`,
 * with a rising `seq`). When the step ends, its stored frame arrives and the
 * tail is dropped.
 *
 * So the page reads the tail when it opens, then appends the chunks the
 * stream delivers, skipping any the tail already holds (by `seq`). The step's
 * stored frame ends its live text: the stored text replaces it, and the two
 * are never shown together.
 *
 * Three things come back:
 *
 *   text    step id → the live text of a step that has not ended
 *   starts  the step starts heard on the stream (`{ step_id, command }`),
 *           for a step the record has not caught up with
 *   stored  stored frames heard on the stream that the record's output does
 *           not hold yet, to append to it until the next read
 */

/** The line that ends a step's own frame (`engine/live_output.py::STEP_FOOTER`). */
const STEP_FOOTER = '--- exit_code='

const endsStep = (text) => text.startsWith(STEP_FOOTER) || text.includes(`\n${STEP_FOOTER}`)

/**
 * @param {Object} args
 * @param {Array}  [args.tail]    `run.live_tail`: `[{ step_id, text, seq }]`
 * @param {Array}  [args.events]  the run's stream events, oldest first
 * @param {string} [args.output]  the record's stored output
 * @returns {{ text: Map<string, string>, starts: Array<{step_id: string, command: string}>, stored: string }}
 */
export function liveOutput({ tail = [], events = [], output = '' } = {}) {
  const text = new Map()
  const shown = new Map()     // step id → the last chunk number already in `text`
  const fromTail = new Set()
  for (const entry of Array.isArray(tail) ? tail : []) {
    if (!entry || !entry.step_id || typeof entry.text !== 'string') continue
    text.set(entry.step_id, entry.text)
    shown.set(entry.step_id, entry.seq ?? null)
    fromTail.add(entry.step_id)
  }

  const list = Array.isArray(events) ? events : []
  const closed = new Set()
  const starts = []
  const started = new Set()
  for (const e of list) {
    if (!e || !e.stepId) continue
    if (e.phase === 'start') {
      if (!started.has(e.stepId) && typeof e.command === 'string') {
        started.add(e.stepId)
        starts.push({ step_id: e.stepId, command: e.command })
      }
      continue
    }
    if (e.phase !== 'output' || typeof e.message !== 'string') continue
    if (!e.live) {
      // A stored frame. Only the one carrying the step's footer ends the step.
      if (endsStep(e.message)) {
        closed.add(e.stepId)
        text.delete(e.stepId)
      }
      continue
    }
    if (closed.has(e.stepId)) continue
    const last = shown.get(e.stepId)
    if (e.seq == null) {
      // An unnumbered chunk cannot be told from one the tail already holds.
      if (fromTail.has(e.stepId)) continue
    } else {
      if (last != null && e.seq <= last) continue
      shown.set(e.stepId, e.seq)
    }
    text.set(e.stepId, (text.get(e.stepId) || '') + e.message)
  }

  // Stored frames the record has not caught up with. The record is read again
  // on every frame, so only the newest few can be ahead of it: walk back from
  // the end and stop at the first one the record already holds.
  const have = typeof output === 'string' ? output : ''
  const ahead = []
  for (let i = list.length - 1; i >= 0; i -= 1) {
    const e = list[i]
    if (!e || e.phase !== 'output' || e.live || e.synthetic || typeof e.message !== 'string' || !e.message) continue
    if (have.includes(e.message)) break
    ahead.unshift(e.message)
  }
  return { text, starts, stored: ahead.join('') }
}
