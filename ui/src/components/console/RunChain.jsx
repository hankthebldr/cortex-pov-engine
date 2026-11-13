/**
 * RunChain — Run detail draws the run's chain (design v26 §4.6, §9.3).
 *
 * The same ChainStage the Composer edits and a scenario's sheet previews, in
 * live mode, fed by this run's real steps (the scenario's authored chain),
 * the host's record of each step and — only once the run has been read back —
 * its Result rows (runChain.liveStepStates). A lane header opens the
 * component behind that signal path. A scenario with no authored chain shows
 * the stage's empty state, never a synthetic one.
 */
import React, { useEffect, useMemo, useState } from 'react'
import ChainStage from './ChainStage.jsx'
import { draftFromScenario } from './composerDraft.js'
import { buildStepRows, parseStepTranscript } from './useRunStepTail.js'
import { liveStepStates, laneDestination } from './runChain.js'
import { PHASE } from './runLifecycle.js'
import { getScenario, getResultsForRun } from '../../api/client.js'

void React

export default function RunChain({ runId, run, lifecycle, onNavigate = () => {} }) {
  const scenarioId = run?.scenario_id || null
  const [scenario, setScenario] = useState(null)
  const [scenarioError, setScenarioError] = useState(null)
  useEffect(() => {
    setScenario(null)
    setScenarioError(null)
    if (!scenarioId) return undefined
    let cancelled = false
    getScenario(scenarioId)
      .then((s) => { if (!cancelled) setScenario(s || null) })
      .catch((e) => { if (!cancelled) setScenarioError(e?.message || 'could not load the scenario') })
    return () => { cancelled = true }
  }, [scenarioId])

  // Result rows matter only once the run has been read back; before that no
  // card may say anything about a detection, so they are not even fetched.
  const ready = lifecycle?.phase === PHASE.READY
  const readAt = lifecycle?.readBack?.at || null
  const [results, setResults] = useState([])
  useEffect(() => {
    setResults([])
    if (!ready || !runId) return undefined
    let cancelled = false
    getResultsForRun(runId)
      .then((d) => {
        const list = Array.isArray(d) ? d : (d && Array.isArray(d.results) ? d.results : [])
        if (!cancelled) setResults(list)
      })
      .catch(() => {})
    return () => { cancelled = true }
  }, [ready, runId, readAt])

  const draft = useMemo(() => (scenario ? draftFromScenario(scenario) : null), [scenario])
  const states = useMemo(() => {
    if (!scenario || !lifecycle) return {}
    const rows = buildStepRows(scenario.steps || [], run, parseStepTranscript(run?.output))
    return liveStepStates(rows, lifecycle.phase, results)
  }, [scenario, run, lifecycle, results])

  if (!scenarioId) return null
  if (scenarioError) {
    return (
      <p className="run-chain__error mono" data-testid="run-chain-error">
        The chain could not be drawn: {scenarioError}
      </p>
    )
  }
  if (!draft) return null

  return (
    <section className="run-chain" aria-label="Chain">
      <ChainStage
        mode="live"
        steps={draft.steps}
        cgo={draft.cgo}
        plane={draft.plane}
        teardown={draft.teardown}
        states={states}
        onOpenLane={(lane) => {
          const to = laneDestination(lane)
          if (to) onNavigate(to)
        }}
        label={`Chain of run ${runId}`}
      />
    </section>
  )
}
