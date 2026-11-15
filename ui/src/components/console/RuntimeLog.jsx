/**
 * RuntimeLog — the one panel on a run's page: what was attempted, step by
 * step, and how it went on the host.
 *
 * One block per step, in order: its number, name, technique and the account
 * it ran as; the command that was attempted; a state as a StatusIcon and one
 * word (Queued · Running · Ran · Failed · Timed out · Did not run · Aborted ·
 * Staged) with `exit N · duration`; then what it printed. The blocks come from
 * `runtimeLog.buildRuntimeLog`, so a finished run (the stored output and the
 * host's step record) and a live one (the same record, plus what a running
 * step has streamed so far) draw the same way.
 *
 * A command shows one line, the first that does something, and opens in place
 * when there is more of it.
 *
 * The scenario cleanup the beacon ran when the task ended is its own block
 * after the steps: Cleanup, a StatusIcon and one word (Clean · "1 of 3
 * failed" · Not run · Pending), with its commands and output behind the same
 * one line.
 *
 * Props:
 *   log      `buildRuntimeLog(…)`: `{ blocks, preface, cleanup }`
 *   loaded   the run's record has been read (until then nothing is claimed)
 *   failed   the record could not be read
 *   stream   `{ connected, mode }` while the run is followed live, else null
 *   partial  only the last 2 KB of output could be read
 */
import React, { useLayoutEffect, useRef, useState } from 'react'
import { StatusIcon } from './kit/index.js'
import { blockMeta, cleanupMeta, commandLead } from './runtimeLog.js'
import '../../styles/destinations/runtime-log.css'

void React

const COMMAND_FROM = {
  agent: { word: 'from the agent', title: 'The command the agent reported running' },
  variant: { word: 'platform variant', title: "The step's variant for this agent's OS" },
  scenario: { word: 'as authored', title: "The scenario's command; values are filled in at launch" },
}

function streamBadge(stream, partial) {
  if (!stream) return { status: 'off', word: partial ? 'Stored · last 2 KB' : 'Stored' }
  if (stream.mode === 'error') return { status: 'down', word: 'Not updating' }
  if (!stream.connected) return { status: 'unknown', word: 'Connecting' }
  return stream.mode === 'stream' ? { status: 'up', word: 'Live' } : { status: 'degraded', word: 'Polling' }
}

/** Has the column cut this one line short? Measured, so a line that fits never offers to open. */
function useCut(text, opens) {
  const lineRef = useRef(null)
  const [cut, setCut] = useState(false)
  useLayoutEffect(() => {
    const el = lineRef.current
    if (!el) return undefined
    // A line hidden behind its opened disclosure measures nothing: leave it be.
    const measure = () => { if (el.clientWidth > 0) setCut(el.scrollWidth > el.clientWidth + 1) }
    measure()
    if (typeof ResizeObserver === 'undefined') return undefined
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [text, opens])
  return [lineRef, cut]
}

const Toggle = () => (
  <>
    <span className="rtlog__cmd-toggle rtlog__cmd-toggle--show">Show all</span>
    <span className="rtlog__cmd-toggle rtlog__cmd-toggle--hide">Hide</span>
  </>
)

/**
 * The command, on one line: the first line that does something. It opens in
 * place when there is more of it than the line shows: more lines, a set-up
 * the line skipped, or a line the column cut short.
 */
function Command({ block }) {
  const command = block.command
  const lead = command ? commandLead(command) : ''
  const more = !!command && lead !== command.trim()
  const [lineRef, cut] = useCut(command, more)
  const opens = more || cut

  if (!command) {
    return <div className="rtlog__cmd rtlog__cmd--none" data-testid="runtime-step-command">Command not recorded</div>
  }
  const from = COMMAND_FROM[block.commandFrom]
  const line = <code ref={lineRef} className="rtlog__cmd-line mono">{lead}</code>
  return (
    <div className="rtlog__cmd" data-testid="runtime-step-command">
      {opens ? (
        <details className="rtlog__cmd-more">
          <summary>
            {line}
            <Toggle />
          </summary>
          <pre className="rtlog__cmd-full mono">{command}</pre>
        </details>
      ) : line}
      {from && <span className="rtlog__cmd-from" title={from.title}>{from.word}</span>}
    </div>
  )
}

function Note({ lines }) {
  return (
    <li className="rtlog__note" data-testid="runtime-note">
      <pre className="rtlog__out mono">{lines.join('\n')}</pre>
    </li>
  )
}

function Step({ block }) {
  const meta = blockMeta(block)
  const ended = block.state.key === 'ran' || block.state.key === 'failed' || block.state.key === 'timed_out'
  return (
    <li
      className={`rtlog__step rtlog__step--${block.state.key}`}
      data-testid="runtime-step"
      data-step={block.id}
      data-state={block.state.key}
    >
      <div className="rtlog__head">
        <StatusIcon status={block.state.status} word={block.state.word} />
        <span className="rtlog__title">
          <span className="rtlog__num mono">{String(block.index).padStart(2, '0')}</span>
          <span className="rtlog__name">{block.name || block.id}</span>
        </span>
        {block.technique && <span className="rtlog__tech mono">{block.technique}</span>}
        {block.account && (
          <span className="rtlog__as mono" title={block.accountDetail || undefined}>
            as {block.account}
            {block.accountNote && <span className="rtlog__as-note"> · {block.accountNote}</span>}
          </span>
        )}
        {meta && <span className="rtlog__meta mono">{meta}</span>}
      </div>
      <Command block={block} />
      {block.lines.length > 0 ? (
        <pre className="rtlog__out mono" data-testid="runtime-step-output">{block.lines.join('\n')}</pre>
      ) : ended ? (
        <p className="rtlog__quiet">No output</p>
      ) : null}
    </li>
  )
}

/** "exit 1 · 12ms" for one cleanup command; "not run" when the agent never started it. */
function commandMeta(c) {
  if (c.notRun) return 'not run'
  return [c.exit != null ? `exit ${c.exit}` : null, c.duration || null].filter(Boolean).join(' · ')
}

/**
 * The scenario cleanup, after the steps. One line shows the first command;
 * opening it lists every command with what it printed and how it exited.
 */
function Cleanup({ cleanup }) {
  const meta = cleanupMeta(cleanup)
  const first = cleanup.commands[0] ? commandLead(cleanup.commands[0].command) : ''
  return (
    <div
      className={`rtlog__cleanup rtlog__cleanup--${cleanup.state.key}`}
      data-testid="runtime-cleanup"
      data-state={cleanup.state.key}
    >
      <div className="rtlog__head">
        <StatusIcon status={cleanup.state.status} word={cleanup.state.word} />
        <span className="rtlog__title"><span className="rtlog__name">Cleanup</span></span>
        {meta && <span className="rtlog__meta mono">{meta}</span>}
      </div>
      {cleanup.commands.length > 0 && (
        <div className="rtlog__cmd">
          <details className="rtlog__cmd-more">
            <summary>
              <code className="rtlog__cmd-line mono">{first}</code>
              <Toggle />
            </summary>
            <ol className="rtlog__cleanup-cmds">
              {cleanup.commands.map((c, i) => (
                <li key={i} className="rtlog__cleanup-cmd">
                  <div className="rtlog__cleanup-line">
                    <pre className="rtlog__cmd-full mono">{c.command}</pre>
                    {commandMeta(c) && <span className="rtlog__meta mono">{commandMeta(c)}</span>}
                  </div>
                  {c.lines.length > 0 && <pre className="rtlog__out mono">{c.lines.join('\n')}</pre>}
                </li>
              ))}
            </ol>
          </details>
          {cleanup.commandsFrom === 'scenario' && (
            <span className="rtlog__cmd-from" title="The scenario's cleanup commands; the agent has not reported running them">
              as authored
            </span>
          )}
        </div>
      )}
    </div>
  )
}

export default function RuntimeLog({ log, loaded = true, failed = false, stream = null, partial = false }) {
  const badge = streamBadge(stream, partial)
  const blocks = log?.blocks || []
  const preface = log?.preface || []
  const cleanup = log?.cleanup || null
  let body
  if (!loaded) {
    body = <p className="rtlog__quiet" data-testid="runtime-log-empty">{failed ? 'Run record not loaded' : 'Loading…'}</p>
  } else if (blocks.length === 0 && preface.length === 0 && !cleanup) {
    body = <p className="rtlog__quiet" data-testid="runtime-log-empty">No steps recorded</p>
  } else {
    body = (
      <>
        <ol className="rtlog__steps">
          {preface.map((lines, i) => <Note key={`pre-${i}`} lines={lines} />)}
          {blocks.map((b) => (
            <React.Fragment key={b.id}>
              <Step block={b} />
              {b.notes.map((lines, i) => <Note key={`${b.id}-note-${i}`} lines={lines} />)}
            </React.Fragment>
          ))}
        </ol>
        {cleanup && <Cleanup cleanup={cleanup} />}
      </>
    )
  }
  return (
    <section className="rtlog" aria-labelledby="runtime-log-title" data-testid="runtime-log">
      <div className="rtlog__bar">
        <h2 className="rtlog__h" id="runtime-log-title">Runtime log</h2>
        {loaded && <StatusIcon status={badge.status} word={badge.word} />}
      </div>
      {body}
    </section>
  )
}
