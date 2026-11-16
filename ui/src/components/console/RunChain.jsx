/**
 * RunChain — Run detail draws the run's chain (design v26 §4.6, §9.3).
 *
 * The same ChainStage the Composer edits and a scenario's sheet previews, in
 * live mode, fed by this run's real steps (the scenario's authored chain) and
 * the runtime log's own blocks, so a card and its log block always agree. A
 * card says how its step went on the host (Queued · Running · Ran · Failed ·
 * …) and nothing about a detection: that reading is Results'. A lane header
 * opens the component behind that signal path. A scenario with no authored
 * chain shows the stage's empty state, never a synthetic one.
 *
 * Props:
 *   runId          the run
 *   scenario       the run's scenario document, or null while it loads
 *   scenarioError  why it could not be loaded, or null
 *   blocks         `runtimeLog.buildRuntimeLog(…).blocks`
 *   live           false until the run's record is read: the chain is drawn
 *                  with no state rather than with one nobody has read
 *   onNavigate     (destination, params?) => void
 */
import React, { useEffect, useMemo, useState } from 'react'
import ChainStage from './ChainStage.jsx'
import BrokenStepSheet from './BrokenStepSheet.jsx'
import { draftFromScenario } from './composerDraft.js'
import { laneDestination } from './runChain.js'
import { EXEC_VOCABULARY } from './runState.js'

void React

/** The states whose card opens the troubleshooting sheet. */
const TROUBLED = new Set(['failed', 'timed_out'])

export default function RunChain({
  runId, scenario = null, scenarioError = null, blocks = [], live = true, onNavigate = () => {},
}) {
  const draft = useMemo(() => (scenario ? draftFromScenario(scenario) : null), [scenario])
  const states = useMemo(
    () => Object.fromEntries(blocks.map((b) => [b.id, b.state.key])),
    [blocks],
  )

  // A Failed card opens its troubleshooting sheet (§4.6); no other card is a
  // button, because no other card has anywhere to go.
  const [openId, setOpenId] = useState(null)
  useEffect(() => { setOpenId(null) }, [runId])
  const troubled = openId ? blocks.find((b) => b.id === openId && TROUBLED.has(b.state.key)) || null : null

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
        mode={live ? 'live' : 'preview'}
        vocabulary={EXEC_VOCABULARY}
        steps={draft.steps}
        cgo={draft.cgo}
        plane={draft.plane}
        teardown={draft.teardown}
        states={states}
        onSelect={setOpenId}
        canSelect={(_, state) => TROUBLED.has(state)}
        onOpenLane={(lane) => {
          const to = laneDestination(lane)
          if (to) onNavigate(to.destination, to.params)
        }}
        label={`Chain of run ${runId}`}
      />
      <BrokenStepSheet
        open={!!troubled}
        step={troubled ? draft.steps.find((s) => s.id === troubled.id) || null : null}
        row={troubled ? troubled.row : null}
        word={troubled ? troubled.state.word : undefined}
        runId={runId}
        scenarioId={scenario?.scenario_id || scenario?.id || null}
        onClose={() => setOpenId(null)}
        onNavigate={onNavigate}
      />
    </section>
  )
}
