import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { composePayloads, getLaunchCapability } from '../../api/payloads.js'
import { getScenario, getScenarios, getTtp } from '../../api/client.js'
import { CopyButton } from './ProvenanceBlock.jsx'
import {
  partitionByFilename, previewSteps, ttpRefsOf,
  validateStagePath, neutralAliasFor,
} from './renameImpact.js'

const SEEN_WHY_KEY = 'cortexsim.renameWhySeen'

/**
 * PayloadComposer — compose a staged artifact into a run, under a chosen name.
 *
 * The rename control is the point. XDR should catch a privilege-escalation
 * sweep as malicious BEHAVIOUR, not as a tool it recognises by filename — and
 * two BIOCs in this corpus literally filter on the string "linpeas". Staging
 * the artifact as `/tmp/.cache/sysinfo.sh` makes that testable: the name-keyed
 * detections MUST go dark and the behavioural ones MUST NOT.
 *
 * DEFAULT IS VERBATIM. A DC who ignores this control gets exactly today's
 * behaviour. The impact panel renders in BOTH cases — in the verbatim case it
 * says explicitly that nothing goes dark, because hiding the panel would make
 * the trade-off invisible precisely when the operator declined to make it.
 */
export default function PayloadComposer({
  adapter,
  supply,
  candidateScenarioIds = [],
  scenarioIndex = {},
  initialScenarioId = null,
  onClose = () => {},
  onArm = () => {},
}) {
  const payloadName = supply?.payloadName || null
  const defaultPath = supply?.stagePath || (payloadName ? `/tmp/${payloadName}` : '')

  const [scenarioId, setScenarioId] = useState(initialScenarioId || candidateScenarioIds[0] || '')
  // Every scenario, so a composition can still be previewed when no scenario
  // references this adapter yet. `compose` takes `adapter_refs` additively, so
  // resolving against an unrelated scenario is a legitimate preflight — but the
  // UI says which case the operator is in rather than blurring them.
  const [allScenarioIds, setAllScenarioIds] = useState([])
  const [preset, setPreset] = useState('verbatim') // verbatim | alias | custom
  const [customPath, setCustomPath] = useState(neutralAliasFor(payloadName || 'tool.sh'))
  const [scenario, setScenario] = useState(null)
  const [scenarioError, setScenarioError] = useState(null)
  // `null` means "not fetched for the current scenario yet" — a STATE, not a
  // flag that can lag a render behind. A boolean here raced the scenario load
  // and flashed every detection into UNKNOWN with the reason "the card could
  // not be read", which is a different and wrong statement.
  const [cards, setCards] = useState(null)
  const [plan, setPlan] = useState(null)
  const [planError, setPlanError] = useState(null)
  const [composing, setComposing] = useState(false)
  const [capability, setCapability] = useState(null)
  const [whyOpen, setWhyOpen] = useState(() => {
    try { return window.localStorage.getItem(SEEN_WHY_KEY) !== '1' } catch { return true }
  })

  const destPath = preset === 'verbatim' ? defaultPath
    : preset === 'alias' ? neutralAliasFor(payloadName || 'tool.sh')
      : customPath
  const renamed = Boolean(destPath && defaultPath && destPath !== defaultPath)
  const pathCheck = useMemo(() => validateStagePath(destPath), [destPath])

  useEffect(() => {
    if (!whyOpen) { try { window.localStorage.setItem(SEEN_WHY_KEY, '1') } catch { /* private mode */ } }
  }, [whyOpen])

  useEffect(() => {
    if (candidateScenarioIds.length) return undefined
    let cancelled = false
    getScenarios()
      .then((d) => {
        const list = Array.isArray(d) ? d : (d?.scenarios || [])
        if (!cancelled) setAllScenarioIds(list.map((x) => x.scenario_id || x.id).filter(Boolean))
      })
      .catch(() => {})
    return () => { cancelled = true }
  }, [candidateScenarioIds.length])

  // Does this SimCore even accept a payload plan on POST /api/runs? Probed, not
  // assumed — Pydantic silently drops unknown fields, so posting a plan to a
  // SimCore that has none would report "renamed" for a run that was not.
  useEffect(() => { getLaunchCapability().then(setCapability) }, [])

  // Scenario detail (steps + expected_detections) drives both the impact
  // partition and the command preview.
  useEffect(() => {
    if (!scenarioId) { setScenario(null); return undefined }
    let cancelled = false
    setScenarioError(null)
    setCards(null)
    getScenario(scenarioId)
      .then((d) => { if (!cancelled) setScenario(d || null) })
      .catch((e) => { if (!cancelled) { setScenario(null); setScenarioError(e?.message || 'scenario load failed') } })
    return () => { cancelled = true }
  }, [scenarioId])

  // The TTP cards named by the scenario's detections — we scan their EXECUTABLE
  // text for the filename. A card that fails to load becomes UNKNOWN, never
  // "behavioural".
  useEffect(() => {
    const refs = ttpRefsOf(scenario)
    if (!refs.length) { setCards({}); return undefined }
    let cancelled = false
    Promise.all(refs.map((r) => getTtp(r).then((c) => [r, c]).catch(() => [r, null])))
      .then((pairs) => { if (!cancelled) setCards(Object.fromEntries(pairs)) })
    return () => { cancelled = true }
  }, [scenario])

  const cardsLoading = scenario != null && cards == null

  const impact = useMemo(
    () => partitionByFilename({ scenario, cards: cards || {}, filename: payloadName }),
    [scenario, cards, payloadName],
  )

  const steps = useMemo(
    () => previewSteps({
      scenario, adapterId: adapter?.adapter_id, payloadName, destPath, defaultPath,
    }),
    [scenario, adapter, payloadName, destPath, defaultPath],
  )

  // Ask the server to resolve the plan. This IS the preview — /compose is
  // stateless and persists nothing, so calling it here has no side effect.
  const compose = useCallback(async () => {
    if (!scenarioId || !pathCheck.ok) return
    setComposing(true)
    setPlanError(null)
    try {
      const body = await composePayloads({
        scenario_id: scenarioId,
        adapter_refs: adapter?.adapter_id ? [adapter.adapter_id] : [],
        stage_as: renamed && adapter?.adapter_id ? { [adapter.adapter_id]: destPath } : {},
        consumer: 'console',
      })
      setPlan(body)
    } catch (err) {
      setPlan(null)
      setPlanError(err)
    } finally {
      setComposing(false)
    }
  }, [scenarioId, adapter, destPath, renamed, pathCheck.ok])

  useEffect(() => {
    if (!scenarioId || !pathCheck.ok) return undefined
    const t = setTimeout(compose, 400)
    return () => clearTimeout(t)
  }, [compose, scenarioId, pathCheck.ok])

  const planJson = useMemo(() => JSON.stringify({
    scenario_id: scenarioId,
    payload_plan: (plan?.artifacts || []).map((a) => ({
      adapter_id: a.adapter_id, payload_name: a.name, sha256: a.sha256,
      dest_path: a.dest_path, mode: a.mode || '0755',
    })),
  }, null, 2), [plan, scenarioId])

  return (
    <div className="payload-compose" data-testid="payload-composer">
      <div className="competitive__detail-head">
        <div>
          <div className="competitive__detail-eyebrow mono">
            compose · {adapter?.adapter_id} · {payloadName}
          </div>
          <h3 className="competitive__detail-title">Compose into a run</h3>
        </div>
        <button type="button" className="btn" onClick={onClose}>Close</button>
      </div>

      {/* ── 1 · scenario ─────────────────────────────────────────────── */}
      <section className="payload-compose__section">
        <div className="competitive__detail-label">Scenario</div>
        {!candidateScenarioIds.length && (
          <p className="payload-compose__empty" data-testid="compose-unreferenced">
            <strong>No scenario references <span className="mono">{adapter?.adapter_id}</span> yet.</strong>{' '}
            Nothing will fetch this artifact during a run until a scenario declares{' '}
            <span className="mono">external_tools[].adapter_ref: {adapter?.adapter_id}</span>. You can
            still preview a composition against any scenario below — it resolves the digest and the
            destination — but treat it as a preflight, not as coverage.
          </p>
        )}
        {(candidateScenarioIds.length || allScenarioIds.length) ? (
          <label className="launch-field">
            <span className="launch-field__label">
              {candidateScenarioIds.length
                ? 'Scenario that references this adapter'
                : 'Preview against a scenario (none reference this adapter)'}
            </span>
            <select
              className="launch-select"
              value={scenarioId}
              onChange={(e) => setScenarioId(e.target.value)}
              data-testid="compose-scenario-select"
            >
              <option value="">— pick a scenario —</option>
              {(candidateScenarioIds.length ? candidateScenarioIds : allScenarioIds).map((sid) => (
                <option key={sid} value={sid}>
                  {sid}{scenarioIndex[sid]?.name ? ` — ${scenarioIndex[sid].name}` : ''}
                </option>
              ))}
            </select>
          </label>
        ) : null}
        {scenarioError && <p className="payload-compose__error mono">{scenarioError}</p>}
      </section>

      {/* ── 2 · the rename control ───────────────────────────────────── */}
      <section className="payload-compose__section">
        <div className="competitive__detail-label">On-disk name and path</div>
        <div className="payload-compose__presets" role="radiogroup" aria-label="On-disk name and path">
          <Preset
            id="verbatim" checked={preset === 'verbatim'} onSelect={setPreset}
            title="Verbatim" path={defaultPath}
            note="The tool's own filename. Filename-keyed detections will match."
          />
          <Preset
            id="alias" checked={preset === 'alias'} onSelect={setPreset}
            title="Neutral alias" path={neutralAliasFor(payloadName || 'tool.sh')}
            note="A name that says nothing about the tool. Only behavioural detections can fire."
          />
          <Preset
            id="custom" checked={preset === 'custom'} onSelect={setPreset}
            title="Custom" path={null}
            note="Anywhere under /tmp, /var/tmp, /dev/shm or /opt/cortexsim."
          >
            <input
              type="text"
              className="launch-select payload-compose__path"
              value={customPath}
              onChange={(e) => { setPreset('custom'); setCustomPath(e.target.value) }}
              aria-label="Custom destination path"
              data-testid="compose-custom-path"
            />
          </Preset>
        </div>

        {!pathCheck.ok && (
          <ul className="launch-blockers" data-testid="compose-path-error">
            <li className="launch-blockers__item"><span aria-hidden="true">▸</span> {pathCheck.error}</li>
          </ul>
        )}
        {pathCheck.ok && pathCheck.warning && (
          <p className="payload-compose__warn" data-testid="compose-path-warning">⚠ {pathCheck.warning}</p>
        )}

        <details
          className="payload-compose__why"
          open={whyOpen}
          onToggle={(e) => setWhyOpen(e.currentTarget.open)}
        >
          <summary>Why would I rename it?</summary>
          <p>
            XDR should catch this as malicious <strong>behaviour</strong> — a process reading
            /etc/shadow, sweeping SUID binaries, walking /proc — not as a tool it recognises by
            name. Detections that key on the literal filename go dark under a rename while the
            behavioural ones must still fire.
          </p>
          <p>
            That turns the run into a <strong>negative control</strong>, and it is the difference
            between &ldquo;your stack knows the name linpeas&rdquo; and &ldquo;your stack detects
            privilege-escalation enumeration&rdquo;. If <em>nothing</em> fires, the finding is that
            the customer&apos;s coverage is name-keyed — not that the TTP did not run.
          </p>
        </details>
      </section>

      {/* ── 3 · what this proves ─────────────────────────────────────── */}
      <section className="payload-compose__section">
        <div className="competitive__detail-label">What this run will prove</div>
        <ImpactPanel
          impact={impact}
          renamed={renamed}
          payloadName={payloadName}
          loading={cardsLoading}
          scenarioLoaded={Boolean(scenario)}
        />
      </section>

      {/* ── 4 · command preview ──────────────────────────────────────── */}
      <section className="payload-compose__section">
        <div className="competitive__detail-label">Command preview</div>
        <CommandPreview steps={steps} renamed={renamed} />
      </section>

      {/* ── 5 · the resolved plan ────────────────────────────────────── */}
      <section className="payload-compose__section">
        <div className="competitive__detail-label">Resolved plan</div>
        {composing && <p className="payload-compose__muted mono">resolving against the shelf…</p>}
        {planError && (
          <div className="payload-compose__error" data-testid="compose-plan-error">
            <p>
              {planError.message}
              {planError.code && <span className="launch-result__code mono"> [{planError.code}]</span>}
            </p>
            {planError.detail && <p className="mono payload-compose__detail">{String(planError.detail)}</p>}
          </div>
        )}
        {plan && !composing && (
          <>
            {(plan.artifacts || []).map((a) => (
              <div key={a.name} className="payload-compose__artifact mono" data-testid={`compose-artifact-${a.name}`}>
                {a.name} → {a.dest_path} · sha256 {String(a.sha256 || '').slice(0, 12)}…
                {a.renamed ? ' · renamed' : ''}
              </div>
            ))}
            {plan.air_gapped === false && (
              <p className="payload-compose__warn" data-testid="compose-not-airgapped">
                ⚠ This scenario is <strong>not</strong> air-gapped: {(plan.unstaged_adapters || []).length}{' '}
                referenced adapter(s) have no staged artifact, so the target still reaches the public
                internet for them.
              </p>
            )}
            {(plan.warnings || []).map((w) => (
              <p key={w.code} className="payload-compose__warn" data-testid={`compose-warning-${w.code}`}>
                <span className="mono">{w.code}</span> — {w.detail || w.message}
              </p>
            ))}
          </>
        )}
      </section>

      {/* ── 6 · arm ──────────────────────────────────────────────────── */}
      <div className="payload-compose__actions">
        {capability?.supported ? (
          <button
            type="button"
            className="btn btn--primary"
            disabled={!plan || !pathCheck.ok}
            onClick={() => onArm(scenarioId, plan)}
          >
            Compose &amp; arm ▸
          </button>
        ) : (
          <>
            <CopyButton value={planJson} label="Copy plan as JSON" variant="btn btn--primary" />
            <p className="payload-compose__note" data-testid="compose-no-plan-support">
              {capability?.unknown
                ? 'This SimCore does not advertise whether POST /api/runs accepts a payload_plan '
                  + `(${capability.reason}), so the console will not send one. `
                : 'This SimCore\'s POST /api/runs does not accept a payload_plan, so the console will not send one. '}
              Sending it anyway would be silently ignored and the run would execute the scenario&apos;s
              own commands unchanged — while this screen claimed a rename. Arm the scenario normally;
              the artifact is staged and served either way.
            </p>
          </>
        )}
        <button type="button" className="btn" onClick={onClose}>Cancel</button>
      </div>
    </div>
  )
}

function Preset({ id, checked, onSelect, title, path, note, children }) {
  return (
    <label className={'payload-compose__preset' + (checked ? ' is-active' : '')}>
      <input
        type="radio"
        name="payload-dest-preset"
        value={id}
        checked={checked}
        onChange={() => onSelect(id)}
        data-testid={`compose-preset-${id}`}
      />
      <span className="payload-compose__preset-body">
        <span className="payload-compose__preset-title">{title}</span>
        {path && <span className="payload-compose__preset-path mono">{path}</span>}
        <span className="payload-compose__preset-note">{note}</span>
        {children}
      </span>
    </label>
  )
}

/**
 * The two-column partition, plus the third column this repo's honesty rules
 * require: UNKNOWN. A detection whose query text we could not read is never
 * counted as behavioural.
 */
export function ImpactPanel({ impact, renamed, payloadName, loading, scenarioLoaded }) {
  if (!scenarioLoaded) {
    return <p className="payload-compose__muted">Pick a scenario to see what a rename would suppress.</p>
  }
  // A card still in flight is NOT a card that failed. Rendering the partition
  // mid-fetch would flash every detection into UNKNOWN with the reason "could
  // not be read", which is a different — and wrong — statement.
  if (loading) {
    return (
      <p className="payload-compose__muted" data-testid="impact-loading">
        scanning the TTP card query text for the filename…
      </p>
    )
  }
  if (!renamed) {
    return (
      <p className="payload-compose__ok" data-testid="impact-verbatim">
        No detections go dark — the artifact keeps its own name{payloadName ? ` (${payloadName})` : ''}.
        Filename-keyed detections will match, and this run proves recognition of the tool as much as
        of the behaviour.
      </p>
    )
  }
  return (
    <div className="payload-compose__impact" data-testid="impact-panel">
      <div className="payload-compose__impact-col">
        <div className="payload-compose__impact-head">
          ✗ Will not fire ({impact.nameKeyed.length})
        </div>
        <div className="payload-compose__impact-sub mono">
          keyed on the literal &quot;{impact.tokens[impact.tokens.length - 1]}&quot;
        </div>
        {impact.nameKeyed.length === 0 && (
          <p className="payload-compose__muted">
            none found — no expected detection names the file in its identifier, and no scanned
            query text mentions it.
          </p>
        )}
        <ul className="payload-compose__impact-list">
          {impact.nameKeyed.map((d) => (
            <li key={d.detection_id} data-testid={`impact-namekeyed-${d.detection_id}`}>
              <span className="mono">{d.detection_id}</span>
              <span className="chip chip--missed">{d.evidence === 'query' ? 'query' : 'identifier'}</span>
              {d.snippet && <div className="payload-compose__snippet mono">{d.snippet}</div>}
            </li>
          ))}
        </ul>
      </div>

      <div className="payload-compose__impact-col">
        <div className="payload-compose__impact-head">
          ✓ Not keyed on the filename ({impact.notNameKeyed.length})
        </div>
        <div className="payload-compose__impact-sub mono">query text scanned, no filename match</div>
        <ul className="payload-compose__impact-list">
          {impact.notNameKeyed.map((d) => (
            <li key={d.detection_id}><span className="mono">{d.detection_id}</span></li>
          ))}
        </ul>
        {impact.notNameKeyed.length === 0 && (
          <p className="payload-compose__muted">none confirmed.</p>
        )}
      </div>

      {impact.unknown.length > 0 && (
        <div className="payload-compose__impact-col payload-compose__impact-col--unknown">
          <div className="payload-compose__impact-head">
            ? Unknown ({impact.unknown.length})
          </div>
          <div className="payload-compose__impact-sub mono">query text NOT scanned</div>
          <ul className="payload-compose__impact-list">
            {impact.unknown.map((d) => (
              <li key={d.detection_id} data-testid={`impact-unknown-${d.detection_id}`}>
                <span className="mono">{d.detection_id}</span>
                <div className="payload-compose__snippet">{d.reason}</div>
              </li>
            ))}
          </ul>
          <p className="payload-compose__muted">
            These are <strong>not</strong> counted as behavioural. Check the TTP card before you
            present the result.
          </p>
        </div>
      )}

      <p className="payload-compose__impact-foot">
        Renaming makes this a negative control for the {impact.nameKeyed.length} filename-keyed
        detection{impact.nameKeyed.length === 1 ? '' : 's'}. If the others still fire, the
        customer&apos;s stack detected the <strong>behaviour</strong>. If nothing fires, that
        coverage was only ever a filename match — <strong>which is the finding</strong>.
      </p>
    </div>
  )
}

function CommandPreview({ steps, renamed }) {
  if (!steps.length) {
    return (
      <p className="payload-compose__muted">
        No step in this scenario names this artifact, its default path, or its adapter token — so
        nothing here would change. That usually means the scenario does not actually invoke this
        tool through the adapter.
      </p>
    )
  }
  return (
    <div className="payload-compose__cmds">
      {steps.map((s) => (
        <div key={s.step_id} className="payload-compose__cmd" data-testid={`compose-step-${s.step_id}`}>
          <div className="payload-compose__cmd-head mono">
            {s.step_id} · {s.name}
            {s.shape === 'inline_fetch' && (
              <span className="chip chip--missed" style={{ marginLeft: 6 }}>inline fetch</span>
            )}
          </div>
          {s.shape === 'inline_fetch' && (
            <p className="payload-compose__warn" data-testid={`compose-inline-${s.step_id}`}>
              ⚠ This step downloads the tool itself with a literal URL. A composed rename does
              <strong> not</strong> change that command: the target still reaches the internet and the
              file still lands under its own name. To make the rename real, the step must invoke the
              adapter (<span className="mono">{'{adapter:…}'}</span>) or the scenario must declare{' '}
              <span className="mono">external_tools[].stage_as</span>.
            </p>
          )}
          <pre className="payload-compose__cmd-pre mono">
            <span className="payload-compose__cmd-label">was</span> {s.original}
          </pre>
          {renamed && s.substitutions.length > 0 && (
            <pre className="payload-compose__cmd-pre payload-compose__cmd-pre--new mono">
              <span className="payload-compose__cmd-label">now</span> {s.rewritten}
            </pre>
          )}
        </div>
      ))}
    </div>
  )
}
