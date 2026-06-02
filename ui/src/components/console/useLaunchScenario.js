import { useState, useEffect, useCallback } from 'react'
import { postRun, getAgents, downloadScenario } from '../../api/client.js'

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
export default function useLaunchScenario(scenario, { onRunComplete, onError } = {}) {
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

  const identityOptions  = scenario?.execution_identity?.options || []
  const defaultIdentity  = scenario?.execution_identity?.default || ''
  const supportsPull     = scenario?.pull_supported ?? true
  const supportsPush     = scenario?.push_supported ?? true

  // Reset identity / lastRun when the scenario changes.
  useEffect(() => {
    setIdentity(defaultIdentity || identityOptions[0] || '')
    setLastRun(null)
  }, [scenario?.scenario_id, defaultIdentity])

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
          setSelectedAgent(list[0].id || list[0].agent_id || '')
        }
      })
      .catch(() => { if (!cancelled) setAgents([]) })
    return () => { cancelled = true }
  }, [mode]) // eslint-disable-line react-hooks/exhaustive-deps

  const launch = useCallback(async () => {
    if (!scenario) return null
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
      const run = await postRun(body)
      setLastRun({ status: 'success', message: `Run ${run?.id || ''} started` })
      if (onRunComplete) onRunComplete(run)
      return run
    } catch (err) {
      const msg = err.message || 'Launch failed'
      setLastRun({ status: 'error', message: msg })
      if (onError) onError(msg)
      return null
    } finally {
      setLaunching(false)
    }
  }, [scenario, mode, identity, selectedAgent, consent, onRunComplete, onError])

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

  const launchDisabled = !scenario || launching ||
    (mode === 'pull' && supportsPull && agents.length === 0)

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
    // derived
    identityOptions,
    supportsPull, supportsPush,
    launchDisabled,
    // actions
    launch,
    downloadPushBundle,
  }
}
