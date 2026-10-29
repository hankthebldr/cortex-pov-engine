import { useState, useEffect, useCallback, useMemo } from 'react'
import { postRun, getAgents, getRuns, postCanary, downloadScenario } from '../../api/client.js'
import { getLaunchCapability } from '../../api/payloads.js'
import { agentIdOf, runIdOf } from '../../api/ids.js'
import { triStateFromValue } from '../../outcomes.js'

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
  const [pushFormat, setPushFormat]       = useState('bash')      // 'bash' | 'k8s'
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

  // Fetch agents whenever pull mode is active.
  useEffect(() => {
    if (mode !== 'pull') return
    let cancelled = false
    getAgents()
      .then((data) => {
        if (cancelled) return
        const list = Array.isArray(data) ? data : []
        setAgents(list)
        if (list.length > 0 && !selectedAgent) {
          setSelectedAgent(agentIdOf(list[0]) || '')
        }
      })
      .catch(() => { if (!cancelled) setAgents([]) })
    return () => { cancelled = true }
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
      // secondary line so a 422 reads as a named field, not a status number.
      setLastRun({
        status: 'error', message: msg, code: err.code || null,
        detail: typeof err.detail === 'string' ? err.detail : null,
      })
      if (onError) onError(msg)
      return null
    } finally {
      setLaunching(false)
    }
  }, [scenario, mode, identity, selectedAgent, consent, payloadPlan, payloadPlanAccepted,
      payloadPlanResolving, onRunComplete, onError,
      profileMode, basMode, soaklessMode, attestedBy, pairedWith, canaryRunId])

  const downloadPushBundle = useCallback(async () => {
    if (!scenario) return
    setDownloading(true)
    try {
      const id  = scenario.scenario_id || scenario.id
      const blob = await downloadScenario(id, pushFormat)
      const ext  = pushFormat === 'k8s' ? 'yml' : 'sh'
      const filename = `cortexsim-${id}-${pushFormat}.${ext}`
      const url = URL.createObjectURL(blob)
      const a   = document.createElement('a')
      a.href = url
      a.download = filename
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
    } catch (err) {
      if (onError) onError(err.message || 'Download failed')
    } finally {
      setDownloading(false)
    }
  }, [scenario, pushFormat, onError])

  // Named reasons, not one opaque boolean. A disabled Launch button with no
  // stated cause is a dead stop mid-demo — the operator clicks, nothing
  // happens, and nothing says why. Consumers render these verbatim.
  const blockers = useMemo(() => {
    const out = []
    if (!scenario) out.push('Arm a scenario from the Library')
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
