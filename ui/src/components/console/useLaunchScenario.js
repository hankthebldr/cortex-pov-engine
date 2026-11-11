import { useState, useEffect, useCallback, useMemo } from 'react'
import { postRun, getAgents, getRuns, postCanary, downloadScenario } from '../../api/client.js'
import { getLaunchCapability } from '../../api/payloads.js'
import { agentIdOf, runIdOf } from '../../api/ids.js'
import { triStateFromValue } from '../../outcomes.js'
import { AGENT_REFRESH_HINT_S } from './readiness/scenarioPreflight.js'

/**
 * Push-bundle formats GET /api/scenarios/{id}/download serves. One list, so the
 * Launch step, the drawer's quick-launch and the legacy panel cannot drift
 * apart again (all three offered only bash | k8s while SimCore served .ps1).
 */
export const PUSH_FORMATS = Object.freeze([
  { value: 'auto', label: 'auto — SimCore picks .sh or .ps1', short: 'auto' },
  { value: 'bash', label: 'bash (.sh)', short: 'bash' },
  { value: 'powershell', label: 'PowerShell (.ps1)', short: 'PowerShell' },
  { value: 'k8s', label: 'kubernetes (.yml)', short: 'k8s' },
])

/** "message [CODE]: detail" from a thrown API error, code and detail never dropped. */
export function describeError(err) {
  let text = err?.message || 'Request failed'
  if (err?.code && !text.includes(err.code)) text += ` [${err.code}]`
  if (typeof err?.detail === 'string' && err.detail && !text.includes(err.detail)) {
    text += `: ${err.detail}`
  }
  return text
}

/**
 * The step ids a bundle / pull refusal names, from either detail shape:
 *   push 409 BUNDLE_TARGET_UNSATISFIABLE — a STRING,
 *     "target=posix scenario=… unresolved=[step-01 (REASON), …] — hint"
 *   pull 409 PULL_TARGET_UNSATISFIABLE   — an OBJECT,
 *     {target, agent_os, unresolved: [{step_id, reason}], hint}
 *
 * @returns {{target: string|null, unresolved: Array<{step_id, reason}>, hint: string|null}}
 */
export function parseTargetRefusal(detail) {
  if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
    const unresolved = Array.isArray(detail.unresolved)
      ? detail.unresolved.map((u) => (Array.isArray(u)
        ? { step_id: String(u[0] ?? ''), reason: u[1] != null ? String(u[1]) : null }
        : { step_id: String(u?.step_id ?? ''), reason: u?.reason != null ? String(u.reason) : null }))
        .filter((u) => u.step_id)
      : []
    return {
      target: typeof detail.target === 'string' ? detail.target : null,
      unresolved,
      hint: typeof detail.hint === 'string' ? detail.hint : null,
    }
  }
  if (typeof detail !== 'string' || !detail) return { target: null, unresolved: [], hint: null }
  const target = /\btarget=(\S+)/.exec(detail)?.[1] || null
  const list = /\bunresolved=\[([^\]]*)\]/.exec(detail)?.[1] || ''
  const unresolved = list.split(',').map((s) => s.trim()).filter(Boolean).map((s) => {
    const m = /^(\S+)\s*(?:\(([^)]*)\))?$/.exec(s)
    return m ? { step_id: m[1], reason: m[2] || null } : { step_id: s, reason: null }
  })
  const hint = /\s—\s(.+)$/.exec(detail)?.[1]?.trim() || null
  return { target, unresolved, hint }
}

/**
 * Operator-readable lines for an error `detail` of ANY shape, so a structured
 * refusal is never reduced to its status line. Known keys are worded; any other
 * key is rendered `key: value` rather than dropped — an unrecognised shape must
 * be shown, not read as nothing.
 *
 * @returns {string[]}
 */
export function errorDetailLines(detail) {
  if (detail == null || detail === '') return []
  if (typeof detail === 'string') {
    const parsed = parseTargetRefusal(detail)
    if (parsed.unresolved.length) {
      return [
        ...(parsed.target ? [`target: ${parsed.target}`] : []),
        ...parsed.unresolved.map((u) => `${u.step_id}${u.reason ? ` — ${u.reason}` : ''}`),
        ...(parsed.hint ? [`→ ${parsed.hint}`] : []),
      ]
    }
    return [detail]
  }
  if (Array.isArray(detail)) {
    return detail.map((d) => (d && typeof d === 'object'
      ? [d.loc && [].concat(d.loc).join('.'), d.msg || d.message || JSON.stringify(d)]
        .filter(Boolean).join(': ')
      : String(d)))
  }
  if (typeof detail !== 'object') return [String(detail)]

  const lines = []
  const seen = new Set()
  const take = (k) => { seen.add(k); return detail[k] }
  const refusal = parseTargetRefusal(detail)
  if (refusal.unresolved.length) {
    take('unresolved')
    for (const u of refusal.unresolved) lines.push(`${u.step_id}${u.reason ? ` — ${u.reason}` : ''}`)
  }
  if (Array.isArray(detail.missing_agents)) {
    lines.push(`not enrolled: ${take('missing_agents').join(', ')}`)
  }
  if (detail.referencing_steps && typeof detail.referencing_steps === 'object') {
    for (const [agentId, steps] of Object.entries(take('referencing_steps'))) {
      lines.push(`${agentId} ← ${[].concat(steps || []).join(', ')}`)
    }
  }
  if (Array.isArray(detail.fields)) {
    for (const f of take('fields')) {
      lines.push(f && typeof f === 'object'
        ? `${f.field || [].concat(f.loc || []).join('.') || 'field'}: ${f.msg || f.message || f.type || 'invalid'}`
        : String(f))
    }
  }
  const hint = typeof detail.hint === 'string' ? take('hint') : null
  for (const [k, v] of Object.entries(detail)) {
    if (seen.has(k) || v == null || v === '') continue
    lines.push(`${k}: ${typeof v === 'object' ? JSON.stringify(v) : String(v)}`)
  }
  if (hint) lines.push(`→ ${hint}`)
  return lines
}

/** Hand a downloaded blob to the browser under `filename`. */
export function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob)
  const a   = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

/**
 * Which other format a bundle refusal can offer as a one-click retry, or null.
 *
 * Only an EXPLICIT posix format (bash / k8s) refused for want of POSIX text is
 * offered PowerShell, and vice versa. `auto` already tried both targets, so a
 * refusal under it has no alternative to offer; and when the scenario reports
 * `bundle_targets`, an alternative it does not list is not offered either — a
 * button that is certain to 409 again is worse than no button.
 */
export function bundleRetryFormat(result, scenario) {
  if (!result || result.status !== 'error' || result.code !== 'BUNDLE_TARGET_UNSATISFIABLE') return null
  if (!result.format || result.format === 'auto') return null
  const { target } = parseTargetRefusal(result.detail)
  const known = Array.isArray(scenario?.bundle_targets) ? scenario.bundle_targets : null
  if (target === 'posix' && result.format !== 'powershell') {
    return !known || known.includes('windows') ? 'powershell' : null
  }
  if (target === 'windows' && result.format === 'powershell') {
    return !known || known.includes('posix') ? 'bash' : null
  }
  return null
}

/** The structured error record the launch and download surfaces render. */
export function errorRecord(err, fallback) {
  return {
    status: 'error',
    message: err?.message || fallback,
    code: err?.code || null,
    // Kept in its original shape: a pull refusal's detail is an OBJECT
    // (unresolved steps, missing agents) and flattening it to null — what the
    // launch path used to do — dropped exactly the step ids the DC must fix.
    detail: err?.detail ?? null,
  }
}

/**
 * useLaunchScenario — encapsulates pull/push launch state for a scenario.
 *
 * Used by the ScenarioInspector drawer and (eventually) the command palette
 * quick-launch. Keeps mode, identity, agent, and push-format state local;
 * exposes handlers for launch + push-bundle download.
 *
 * @param {object|null} scenario        — scenario detail (must include
 *                                        execution_identity + push/pull flags)
 * @param {object} callbacks
 * @param {(run) => void} callbacks.onRunComplete
 * @param {(message: string) => void} callbacks.onError
 */
export default function useLaunchScenario(scenario, {
  onRunComplete, onError, payloadPlan = null,
  // True only while a `?plan=` deep link's payload plan is still being
  // decoded (destinations.jsx::useDecodedPlan). MUST NOT be treated as
  // "no plan" — see the I-1 guard in `launch()` below.
  payloadPlanResolving = false,
} = {}) {
  const [mode, setMode]                   = useState('pull')      // 'pull' | 'push'
  const [identity, setIdentity]           = useState('')
  const [agents, setAgents]               = useState([])
  const [selectedAgent, setSelectedAgent] = useState('')
  // 'auto' | 'bash' | 'powershell' | 'k8s'. Defaults to `auto` — SimCore picks
  // the target the scenario's command text can satisfy (POSIX first, PowerShell
  // for windows-only content). Defaulting to bash made every windows-only
  // scenario's first download a 409.
  const [pushFormat, setPushFormat]       = useState('auto')
  // null | {status:'success', filename, target, format}
  //      | {status:'error', message, code, detail, format}
  // Rendered BESIDE the download button. A refusal used to reach only
  // `onError`, which the Library and guided-flow mounts wired to a no-op.
  const [downloadResult, setDownloadResult] = useState(null)
  // Launch-time consent for gated tool adapters (dual-use / c2-framework).
  const [consent, setConsent]             = useState({})          // { simulation_authorized?, c2_authorized? }
  const [launching, setLaunching]         = useState(false)
  const [downloading, setDownloading]     = useState(false)
  const [lastRun, setLastRun]             = useState(null)        // { status, message }
  // null = not probed yet · true/false = this SimCore's LaunchRequest does /
  // does not carry `payload_plan`. Probed, never assumed: Pydantic silently
  // IGNORES unknown body fields, so posting a plan to a SimCore that has none
  // would return 200 and run every step with the tool under its ORIGINAL name
  // while the console reported a rename — a false claim in a customer-facing
  // report, produced by the back-compat mechanism itself.
  const [payloadPlanAccepted, setPayloadPlanAccepted] = useState(null)

  // Prevention-efficacy launch context. Operator DECLARATIONS: sent only when
  // set, so the default body stays byte-identical. 'unknown' / '' (= not
  // attested) are the unset values and are never sent.
  const [profileMode, setProfileMode]     = useState('unknown')   // report | block | unknown
  const [basMode, setBasMode]             = useState('')          // '' | enabled | disabled
  const [soaklessMode, setSoaklessMode]   = useState('')          // '' | enabled | disabled
  const [attestedBy, setAttestedBy]       = useState('')
  const [pairedWith, setPairedWith]       = useState('')
  const [canaryRunId, setCanaryRunId]     = useState('')
  const [canaryBusy, setCanaryBusy]       = useState(false)
  const [canaryError, setCanaryError]     = useState(null)        // { message, code }
  const [runHistory, setRunHistory]       = useState([])

  const identityOptions  = scenario?.execution_identity?.options || []
  const defaultIdentity  = scenario?.execution_identity?.default || ''
  const supportsPull     = scenario?.pull_supported ?? true
  const supportsPush     = scenario?.push_supported ?? true

  // Reset identity / lastRun when the scenario changes.
  useEffect(() => {
    setIdentity(defaultIdentity || identityOptions[0] || '')
    setLastRun(null)
    setDownloadResult(null)
    setPairedWith('')
    setCanaryRunId('')
    setCanaryError(null)
  }, [scenario?.scenario_id, defaultIdentity])

  // Terminal runs of this scenario on the chosen target: the only valid pair
  // partners (the server enforces it; the picker just does not offer the rest).
  useEffect(() => {
    if (mode !== 'pull') return undefined
    let cancelled = false
    getRuns()
      .then((list) => { if (!cancelled) setRunHistory(Array.isArray(list) ? list : []) })
      .catch(() => { if (!cancelled) setRunHistory([]) })
    return () => { cancelled = true }
  }, [mode, scenario?.scenario_id, lastRun?.status])
  // Pair options are filtered by agent: a choice made for another beacon (or a
  // canary run on it) no longer applies.
  useEffect(() => {
    setPairedWith('')
    setCanaryRunId('')
    setCanaryError(null)
  }, [selectedAgent])
  const pairOptions = useMemo(() => {
    const sid = scenario?.scenario_id || scenario?.id
    return runHistory.filter((r) =>
      r.scenario_id === sid
      && selectedAgent && String(r.target) === String(selectedAgent)
      && (r.status === 'complete' || r.status === 'failed'))
  }, [runHistory, scenario, selectedAgent])

  const runCanary = useCallback(async () => {
    if (!selectedAgent) {
      setCanaryError({ message: 'Pick the beacon to run the canary on', code: null })
      return null
    }
    setCanaryBusy(true)
    setCanaryError(null)
    try {
      const res = await postCanary(selectedAgent)
      setCanaryRunId(res?.run_id || '')
      return res
    } catch (err) {
      setCanaryRunId('')
      setCanaryError({ message: describeError(err), code: err.code || null })
      return null
    } finally {
      setCanaryBusy(false)
    }
  }, [selectedAgent])

  // Fetch agents whenever pull mode is active, and keep re-reading them.
  //
  // The list used to be read once. The preflight's Target row answers "is this
  // beacon polling right now?" from it, so a beacon that went offline after the
  // view opened still read online, and a BLOCK for a just-enrolled row (no
  // beacon poll yet) could never clear without leaving the view. A refresh that
  // fails keeps the last list: one dropped request must not empty the picker.
  useEffect(() => {
    if (mode !== 'pull') return undefined
    let cancelled = false
    const load = (first) => getAgents()
      .then((data) => {
        if (cancelled) return
        const list = Array.isArray(data) ? data : []
        setAgents(list)
        if (first && list.length > 0 && !selectedAgent) {
          setSelectedAgent(agentIdOf(list[0]) || '')
        }
      })
      .catch(() => { if (!cancelled && first) setAgents([]) })
    load(true)
    const t = setInterval(() => load(false), AGENT_REFRESH_HINT_S * 1000)
    return () => { cancelled = true; clearInterval(t) }
  }, [mode]) // eslint-disable-line react-hooks/exhaustive-deps

  // Probe once, only when a plan is actually in play.
  useEffect(() => {
    if (!payloadPlan?.artifacts?.length) return
    let cancelled = false
    getLaunchCapability().then((cap) => {
      if (!cancelled) setPayloadPlanAccepted(cap.supported === true)
    })
    return () => { cancelled = true }
  }, [payloadPlan])

  const launch = useCallback(async () => {
    if (!scenario) return null
    // I-1 guard: a `?plan=` deep link's chunk can still be in flight when
    // Launch fires. Refusing here (not just disabling the button) means no
    // caller of `launch()` — including a race that slips past the disabled
    // state — can ever POST a run silently missing the payload plan it was
    // supposed to carry.
    if (payloadPlanResolving) {
      const msg = 'Payload plan is still resolving — wait a moment before launching'
      setLastRun({ status: 'error', message: msg })
      if (onError) onError(msg)
      return null
    }
    setLaunching(true)
    setLastRun(null)
    try {
      const body = {
        scenario_id: scenario.scenario_id || scenario.id,
        mode,
        identity: identity || undefined,
      }
      if (mode === 'pull' && selectedAgent) body.target_agent_id = selectedAgent
      if (consent && Object.keys(consent).length) body.consent = consent
      if (mode === 'pull') {
        if (profileMode && profileMode !== 'unknown') body.agent_profile_mode = profileMode
        const bas = triStateFromValue(basMode)
        if (bas !== null) body.bas_mode_attested = bas
        const soak = triStateFromValue(soaklessMode)
        if (soak !== null) body.soakless_attested = soak
        if (attestedBy.trim()) body.attested_by = attestedBy.trim()
        if (pairedWith) body.paired_with_run_id = pairedWith
        if (canaryRunId) body.canary_run_id = canaryRunId
      }
      // Only sent when the server's own schema says it will be read.
      if (payloadPlanAccepted === true && payloadPlan?.artifacts?.length) {
        body.payload_plan = payloadPlan.artifacts
      }
      const run = await postRun(body)
      setLastRun({ status: 'success', message: `Run ${runIdOf(run) || ''} started` })
      if (onRunComplete) onRunComplete(run)
      return run
    } catch (err) {
      const msg = err.message || 'Launch failed'
      // Keep the structured contract intact — the panel renders the code as a
      // secondary line so a 422 reads as a named field, not a status number,
      // and an OBJECT detail (PULL_TARGET_UNSATISFIABLE's unresolved steps,
      // TARGET_AGENT_NOT_ENROLLED's missing agents) as a list.
      setLastRun(errorRecord(err, 'Launch failed'))
      if (onError) onError(msg)
      return null
    } finally {
      setLaunching(false)
    }
  }, [scenario, mode, identity, selectedAgent, consent, payloadPlan, payloadPlanAccepted,
      payloadPlanResolving, onRunComplete, onError,
      profileMode, basMode, soaklessMode, attestedBy, pairedWith, canaryRunId])

  // `formatOverride` lets a refusal offer a one-click retry in another format
  // ("Download as PowerShell"). It is ignored unless it is a string, because
  // this handler is also wired straight to onClick and receives the event.
  const downloadPushBundle = useCallback(async (formatOverride) => {
    if (!scenario) return null
    const fmt = typeof formatOverride === 'string' && formatOverride ? formatOverride : pushFormat
    if (fmt !== pushFormat) setPushFormat(fmt)
    setDownloading(true)
    setDownloadResult(null)
    try {
      const id  = scenario.scenario_id || scenario.id
      const res = await downloadScenario(id, fmt)
      saveBlob(res.blob, res.filename)
      const ok = { status: 'success', filename: res.filename, target: res.target, format: res.format }
      setDownloadResult(ok)
      return ok
    } catch (err) {
      setDownloadResult({ ...errorRecord(err, 'Download failed'), format: fmt })
      if (onError) onError(describeError(err))
      return null
    } finally {
      setDownloading(false)
    }
  }, [scenario, pushFormat, onError])

  // Named reasons, not one opaque boolean. A disabled Launch button with no
  // stated cause is a dead stop mid-demo — the operator clicks, nothing
  // happens, and nothing says why. Consumers render these verbatim.
  const blockers = useMemo(() => {
    const out = []
    if (!scenario) out.push('Arm a scenario from the Scenarios page')
    if (mode === 'pull' && supportsPull && agents.length === 0) {
      out.push('No agent enrolled — deploy a beacon from Agents')
    }
    if (mode === 'pull' && agents.length > 0 && !selectedAgent) {
      out.push('Pick the beacon to run on')
    }
    if (payloadPlanResolving) {
      out.push('Payload plan is still resolving from this link — wait before launching')
    }
    return out
  }, [scenario, mode, supportsPull, agents.length, selectedAgent, payloadPlanResolving])

  const launchDisabled = launching || blockers.length > 0

  return {
    // state
    mode, setMode,
    identity, setIdentity,
    agents,
    selectedAgent, setSelectedAgent,
    pushFormat, setPushFormat,
    consent, setConsent,
    launching, downloading,
    lastRun,
    downloadResult,
    payloadPlanAccepted,
    profileMode, setProfileMode,
    basMode, setBasMode,
    soaklessMode, setSoaklessMode,
    attestedBy, setAttestedBy,
    pairedWith, setPairedWith,
    canaryRunId, canaryBusy, canaryError, runCanary,
    pairOptions,
    // derived
    identityOptions,
    supportsPull, supportsPush,
    launchDisabled,
    blockers,
    // actions
    launch,
    downloadPushBundle,
  }
}
