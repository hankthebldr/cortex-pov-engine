import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from 'react'
import usePinnedScenarios from '../components/console/usePinnedScenarios.js'
import { isRunTerminal } from '../components/console/runStatus.js'
import {
  getHealth,
  getRuns,
  getScenarios,
  getAgents,
  listXsiamTenants,
  getXsiamTenantHealth,
} from '../api/client.js'

/**
 * EnvironmentContext — the single home for ALL ambient console scope.
 *
 * Replaces the 678-line AppConsole monolith's local state (activeTab aside):
 * the active XSIAM tenant, the active beacon/agent, per-tenant health, the
 * canonical scenario corpus (ends the AppConsole + OperationsView double
 * fetch), derived planes, the globally-polled run list, the derived active
 * run, and the pinned-scenario set.
 *
 * Consumers read via `useEnvironment()` — never prop-drilled. The global
 * context bar's tenant/agent switchers write to this ONE provider, so a
 * single switch re-scopes every surface.
 *
 * Persistence: the active tenant + agent selection survives reloads via
 * localStorage (`cortexsim.activeTenant` / `cortexsim.activeAgent`). A
 * stale-pointer guard clears a persisted id that no longer resolves against
 * the live list and falls back to the first available entry.
 */

const LS_TENANT = 'cortexsim.activeTenant'
const LS_AGENT = 'cortexsim.activeAgent'

// Human-friendly plane labels. A plane with no entry falls back to a
// humanized code so newly-added planes surface automatically. PLANE_ORDER is
// display preference only — unlisted planes sort alphabetically after.
export const PLANE_LABELS = {
  EDR:       'Endpoint',
  CDR:       'Cloud',
  NDR:       'Network',
  ITDR:      'Identity',
  CLOUD_APP: 'Cloud App',
  AI_ACCESS: 'AI Access',
  AIRS:      'AI Runtime',
  AI_SPM:    'AI Posture',
  BROWSER:   'Browser',
  KOI:       'Agentic',
  CSPM:      'Cloud Posture',
  ASM:       'Attack Surface',
  TIM:       'Threat Intel',
  EMAIL:     'Email',
  ANALYTICS: 'Multi-plane',
}
export const PLANE_ORDER = [
  'EDR', 'CDR', 'NDR', 'ITDR', 'CLOUD_APP', 'AI_ACCESS', 'AIRS', 'AI_SPM',
  'BROWSER', 'KOI', 'CSPM', 'ASM', 'TIM', 'EMAIL', 'ANALYTICS',
]

function humanizePlane(code) {
  return (
    PLANE_LABELS[code] ||
    code
      .split('_')
      .map((w) => (w ? w.charAt(0) + w.slice(1).toLowerCase() : w))
      .join(' ')
  )
}

// Safe default so consumers rendered OUTSIDE a provider (e.g. isolated
// component tests) never crash — they get inert scope instead of a throw.
const DEFAULT_ENV = {
  tenant: null,
  tenants: [],
  agent: null,
  agents: [],
  health: { hostname: '', version: 'v1.0', sensors: {}, tenantHealth: null },
  scenarios: [],
  planes: [],
  runs: [],
  activeRun: null,
  lastRun: null,
  pinnedIds: [],
  loading: { scenarios: false, runs: false, agents: false, tenants: false },
  isPinned: () => false,
  togglePin: () => {},
  unpin: () => {},
  setTenant: () => {},
  setAgent: () => {},
  refreshHealth: () => {},
  refreshRuns: () => {},
  refreshScenarios: () => {},
  refreshAgents: () => {},
  refreshTenants: () => {},
}

const EnvironmentContext = createContext(DEFAULT_ENV)

function readLS(key) {
  try { return window.localStorage.getItem(key) } catch { return null }
}
function writeLS(key, value) {
  try {
    if (value == null) window.localStorage.removeItem(key)
    else window.localStorage.setItem(key, value)
  } catch { /* storage unavailable */ }
}

export function EnvironmentProvider({ children, runPollMs = 10_000 }) {
  // ── Corpus ──────────────────────────────────────────────────────────────
  const [scenarios, setScenarios] = useState([])
  const [runs, setRuns] = useState([])
  const [agents, setAgents] = useState([])
  const [tenants, setTenants] = useState([])
  const [baseHealth, setBaseHealth] = useState({
    hostname: typeof window !== 'undefined' ? window.location.hostname : 'lab',
    version: 'v1.0',
    sensors: {},
  })
  const [tenantHealth, setTenantHealth] = useState(null)
  const [loading, setLoading] = useState({
    scenarios: true, runs: true, agents: true, tenants: true,
  })

  // ── Active-scope pointers (persisted) ─────────────────────────────────────
  const [tenantId, setTenantId] = useState(() => readLS(LS_TENANT))
  const [agentId, setAgentId] = useState(() => readLS(LS_AGENT))

  const { pinnedIds, isPinned, unpin, toggle: togglePin } = usePinnedScenarios()

  // ── Fetchers ──────────────────────────────────────────────────────────────
  const refreshScenarios = useCallback(() => {
    return getScenarios({})
      .then((data) => {
        const list = Array.isArray(data) ? data : (data && data.scenarios) || []
        setScenarios(list)
      })
      .catch(() => setScenarios([]))
      .finally(() => setLoading((l) => ({ ...l, scenarios: false })))
  }, [])

  const refreshRuns = useCallback(() => {
    return getRuns()
      .then((data) => setRuns(Array.isArray(data) ? data : []))
      .catch(() => {})
      .finally(() => setLoading((l) => ({ ...l, runs: false })))
  }, [])

  const refreshAgents = useCallback(() => {
    return getAgents()
      .then((data) => setAgents(Array.isArray(data) ? data : []))
      .catch(() => setAgents([]))
      .finally(() => setLoading((l) => ({ ...l, agents: false })))
  }, [])

  const refreshTenants = useCallback(() => {
    return listXsiamTenants()
      .then((data) => setTenants(Array.isArray(data) ? data : []))
      .catch(() => setTenants([]))
      .finally(() => setLoading((l) => ({ ...l, tenants: false })))
  }, [])

  // ── Derived active tenant / agent (resolve pointer against live list) ─────
  const tenant = useMemo(() => {
    if (!tenantId) return null
    return (
      tenants.find((t) => (t.name || t.id) === tenantId) || null
    )
  }, [tenants, tenantId])

  const agent = useMemo(() => {
    if (!agentId) return null
    return agents.find((a) => (a.id || a.agent_id) === agentId) || null
  }, [agents, agentId])

  // ── Health (base /api/health + per-tenant health) ────────────────────────
  const refreshHealth = useCallback(() => {
    getHealth()
      .then((d) => {
        if (!d) return
        setBaseHealth({
          hostname: d.hostname || (typeof window !== 'undefined' ? window.location.hostname : 'lab'),
          version: d.version ? `v${d.version}` : 'v1.0',
          // /api/health does not expose sensor status; the real per-tenant
          // sensor summary is layered in from getXsiamTenantHealth below.
          sensors: {},
        })
      })
      .catch(() => {})
  }, [])

  // Per-tenant health — refetched whenever the active tenant changes.
  const refreshTenantHealth = useCallback((name) => {
    if (!name) { setTenantHealth(null); return Promise.resolve() }
    return getXsiamTenantHealth(name)
      .then((h) => setTenantHealth(h || null))
      .catch(() => setTenantHealth(null))
  }, [])

  // ── Initial load ──────────────────────────────────────────────────────────
  useEffect(() => { refreshScenarios() }, [refreshScenarios])
  useEffect(() => { refreshRuns() }, [refreshRuns])
  useEffect(() => { refreshAgents() }, [refreshAgents])
  useEffect(() => { refreshTenants() }, [refreshTenants])
  useEffect(() => { refreshHealth() }, [refreshHealth])

  // Global run poll — not tab-gated (drives the global LIVE pill everywhere).
  useEffect(() => {
    const t = setInterval(refreshRuns, runPollMs)
    return () => clearInterval(t)
  }, [refreshRuns, runPollMs])

  // Re-scope health when the active tenant changes.
  useEffect(() => {
    refreshTenantHealth(tenant?.name || null)
  }, [tenant, refreshTenantHealth])

  // ── Stale-pointer guard ───────────────────────────────────────────────────
  // Once tenants load, if the persisted active id no longer resolves, clear it
  // and fall back to first-available. Ditto for agents.
  useEffect(() => {
    if (loading.tenants) return
    if (tenantId && !tenants.some((t) => (t.name || t.id) === tenantId)) {
      const first = tenants[0]
      const next = first ? (first.name || first.id) : null
      setTenantId(next)
      writeLS(LS_TENANT, next)
    }
  }, [loading.tenants, tenants, tenantId])

  useEffect(() => {
    if (loading.agents) return
    if (agentId && !agents.some((a) => (a.id || a.agent_id) === agentId)) {
      const first = agents[0]
      const next = first ? (first.id || first.agent_id) : null
      setAgentId(next)
      writeLS(LS_AGENT, next)
    }
  }, [loading.agents, agents, agentId])

  // ── Setters (write-through to localStorage) ───────────────────────────────
  const setTenant = useCallback((id) => {
    setTenantId(id)
    writeLS(LS_TENANT, id)
  }, [])

  const setAgent = useCallback((id) => {
    setAgentId(id)
    writeLS(LS_AGENT, id)
  }, [])

  // ── Derived planes (rail source of truth) ────────────────────────────────
  const planes = useMemo(() => {
    const counts = scenarios.reduce((acc, s) => {
      const p = (s.plane || '').toUpperCase()
      if (p) acc[p] = (acc[p] || 0) + 1
      return acc
    }, {})
    return Object.keys(counts)
      .sort((a, b) => {
        const ia = PLANE_ORDER.indexOf(a)
        const ib = PLANE_ORDER.indexOf(b)
        if (ia !== -1 && ib !== -1) return ia - ib
        if (ia !== -1) return -1
        if (ib !== -1) return 1
        return a.localeCompare(b)
      })
      .map((code) => ({ code, name: humanizePlane(code), count: counts[code] }))
  }, [scenarios])

  // ── Derived active run (most recent truly in-flight run) ─────────────────
  const activeRun = useMemo(() => {
    const running = runs.find((r) => r && r.status === 'running')
    if (!running) return null
    const totalSteps = running.total_steps ?? running.steps?.length ?? 0
    const currentStep = running.current_step ?? running.step ?? 0
    const elapsedSec = running.started_at
      ? Math.floor((Date.now() - new Date(running.started_at).getTime()) / 1000)
      : running.elapsed_seconds ?? 0
    return {
      runId: running.id || running.run_id,
      scenarioId: running.scenario_id,
      step: currentStep,
      totalSteps,
      elapsed: elapsedSec,
      detected: running.detected_count ?? 0,
      total: running.expected_detections ?? 0,
      nextStep: running.next_technique ?? running.next_step ?? null,
    }
  }, [runs])

  const lastRun = useMemo(() => {
    const finished = runs.find((r) => r && isRunTerminal(r.status))
    if (!finished) return null
    return {
      runId: finished.id || finished.run_id,
      scenarioId: finished.scenario_id,
      status: finished.status,
    }
  }, [runs])

  // ── Composed health object ────────────────────────────────────────────────
  const health = useMemo(() => {
    // Derive a sensor summary from the per-tenant health payload when present.
    let sensors = {}
    if (tenantHealth && tenantHealth.sensors && typeof tenantHealth.sensors === 'object') {
      sensors = tenantHealth.sensors
    } else if (tenantHealth && tenantHealth.status) {
      // Map the tenant's overall verified status into a single sensor entry so
      // the header chip shows something real instead of "sensors pending".
      const s = String(tenantHealth.status).toLowerCase()
      const norm = s.includes('ok') || s.includes('connect') || s.includes('healthy')
        ? 'healthy'
        : s.includes('degrad') || s.includes('warn') ? 'warn' : 'bad'
      sensors = { xsiam: norm }
    }
    return {
      hostname: baseHealth.hostname,
      version: baseHealth.version,
      sensors,
      tenantHealth,
    }
  }, [baseHealth, tenantHealth])

  const value = useMemo(() => ({
    tenant, tenants,
    agent, agents,
    health,
    scenarios, planes,
    runs, activeRun, lastRun,
    pinnedIds,
    loading,
    isPinned, togglePin, unpin,
    setTenant, setAgent,
    refreshHealth, refreshRuns, refreshScenarios, refreshAgents, refreshTenants,
  }), [
    tenant, tenants, agent, agents, health, scenarios, planes, runs, activeRun,
    lastRun, pinnedIds, loading, isPinned, togglePin, unpin, setTenant, setAgent,
    refreshHealth, refreshRuns, refreshScenarios, refreshAgents, refreshTenants,
  ])

  return (
    <EnvironmentContext.Provider value={value}>
      {children}
    </EnvironmentContext.Provider>
  )
}

export function useEnvironment() {
  return useContext(EnvironmentContext)
}

export { EnvironmentContext, DEFAULT_ENV }
export default EnvironmentContext
