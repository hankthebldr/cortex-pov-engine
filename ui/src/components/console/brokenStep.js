/**
 * brokenStep.js — why a Broken step may have broken, in the order to check
 * (design v26 §4.6). Pure, no React.
 *
 * A card reads Broken once the run is read back, when the host recorded the
 * step failing (a non-zero exit, or the agent stopped reporting) and the
 * tenant showed nothing for it. Every cause here is read from that host
 * record (`buildStepRows`: status, rc, signal, termination, and the two notes
 * the step frame carries). None is guessed: a step whose record says nothing
 * more than its exit code gets that, and the line that opens its output.
 *
 * The order is the order a DC should check them in: what ended the step
 * first (the agent went quiet, the run was aborted, something killed it, it
 * ran out of time), then what the host lacked (an interpreter, the declared
 * account), then what the command itself reported (126, 127), and last the
 * step's own output, which every Broken step has.
 *
 * Each cause names the place that fixes it, as a route ({destination,
 * params}) the shell already knows. No line is a dead end.
 */

/** Terminations a process ended by something POVengine did not send. */
const KILLED = new Set(['signaled', 'anchor_terminated'])

function place(label, destination, params) {
  return { label, to: params ? { destination, params } : { destination } }
}

/**
 * @param {Object} row        one `buildStepRows` row (status, rc, signal,
 *                            termination, identityNote, runtimeNote)
 * @param {Object} ctx
 * @param {string} ctx.runId
 * @param {string} [ctx.scenarioId]
 * @returns {Array<{id: string, title: string, detail: string,
 *                  place: {label: string, to: {destination: string, params?: Object}}}>}
 */
export function brokenStepCauses(row, { runId, scenarioId } = {}) {
  if (!row) return []
  const rc = row.rc == null ? null : Number(row.rc)
  const term = row.termination || null
  const signal = row.signal ? Number(row.signal) : null
  const agents = place('Agents › Fleet', 'component-agents', { tab: 'fleet' })
  const output = place("This run's output", 'runs', { run: runId, tab: 'live' })
  const causes = []

  if (row.status === 'severed' || term === 'beacon_lost') {
    causes.push({
      id: 'severed',
      title: 'The agent stopped reporting during this step',
      detail: 'POVengine never received how this step ended. Check that the agent is still polling, then run the scenario again.',
      place: agents,
    })
  }
  if (term === 'aborted' || rc === 130) {
    causes.push({
      id: 'aborted',
      title: 'The run was aborted while this step ran',
      detail: 'The agent stopped the step on an abort. Nothing after it ran.',
      place: output,
    })
  }
  if (KILLED.has(term) || (signal && term !== 'aborted')) {
    causes.push({
      id: 'killed',
      title: signal
        ? `Something other than POVengine killed the step (signal ${signal})`
        : 'Something other than POVengine ended the step',
      detail: 'That is the host half of a prevention. The read back found no alert for this step, so check whether the endpoint blocked it and its alert has not arrived yet (read back again), or whether the host itself ended it (out of memory, a watchdog).',
      place: place('Results', 'proof', { run: runId }),
    })
  }
  if (term === 'timeout' || rc === 124) {
    causes.push({
      id: 'timeout',
      title: 'The step ran past its time limit',
      detail: 'The agent stopped it when its timeout ran out. A slow target or a step that waits on the network can need a longer limit.',
      place: scenarioId ? place('Build a chain', 'composer', { from: scenarioId }) : output,
    })
  }
  if (row.runtimeNote) {
    causes.push({
      id: 'runtime',
      title: 'An interpreter the step needs is missing on the agent',
      detail: row.runtimeNote,
      place: agents,
    })
  }
  if (row.identityNote) {
    causes.push({
      id: 'identity',
      title: 'The step did not run as the account it declares',
      detail: row.identityNote,
      place: agents,
    })
  }
  if (rc === 127) {
    causes.push({
      id: 'not_found',
      title: 'The agent could not find a command the step calls (exit 127)',
      detail: 'A tool the step runs is not installed on the agent. Packages lists the tools the payload shelf has not staged.',
      place: place('Catalog › Packages', 'adapters', { supply: 'unstaged' }),
    })
  }
  if (rc === 126) {
    causes.push({
      id: 'not_executable',
      title: 'A command was found but could not run (exit 126)',
      detail: 'Usually a permission or a file-type problem on the agent: the account the step runs as cannot execute it.',
      place: agents,
    })
  }
  causes.push({
    id: 'output',
    title: 'Read what the step printed',
    detail: rc != null && rc !== 0
      ? `The step exited ${rc}. Its own output says why.`
      : 'The step\'s own output says what it did before it ended.',
    place: output,
  })
  return causes
}
