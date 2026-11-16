import React, { useState, useEffect, useCallback, useRef } from 'react'
import {
  deleteAgent,
  abortRun,
  agentInstallUrl,
  mintEnrollmentToken,
} from '../../api/client.js'
import { agentIdOf } from '../../api/ids.js'
import { parseServerTime, serverDate } from '../../api/time.js'
import DeployAgentKubernetes from './DeployAgentKubernetes.jsx'
import { useEnvironment } from '../../context/EnvironmentContext.jsx'
import { agentLiveness, LIVENESS } from './setupProgress.js'
import {
  installerOS,
  pullOneLiner,
  windowsDownloadRunLine,
  uninstallCommand,
  posixRootUninstallCommand,
  POSIX_UNINSTALL_NOTE,
  WINDOWS_MANUAL_UNINSTALL,
  WINDOWS_ELEVATION_LABEL,
  WINDOWS_INSTALLER_FILE,
  POSIX_INSTALLER_FILE,
  WINDOWS_BUNDLE_UNPROVEN,
  TOKEN_HISTORY_NOTE,
  agentIdProblem,
  legacyRunHint,
  beaconShipState,
  attemptsSince,
} from './deployCommands.js'
import {
  useInstallAttempts,
  useCopyFeedback,
  CopyButton,
  CopyLive,
  WindowsNoCradle,
  LatestAttempt,
  InstallAttemptsList,
  PosixBeaconGap,
  WindowsBeaconMissing,
  WindowsCaveats,
} from './DeployParts.jsx'
import { trapTabKey } from './focusTrap.js'
import { Button, StatusIcon } from './kit/index.js'
import '../../styles/destinations/agents.css'
import '../../styles/destinations/deploy-console.css'

// Compact relative age ("12s" / "5m" / "3h" / "2d") from a number of seconds.
function fmtAge(seconds) {
  if (seconds == null || !Number.isFinite(seconds)) return 'never'
  const s = Math.max(0, Math.floor(seconds))
  if (s < 60) return `${s}s ago`
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}

// Liveness as a kit status: a glyph and one word. The card keeps a
// `target-card--<state>` modifier so its top edge carries the same tone.
const LIVENESS_STATUS = {
  [LIVENESS.ONLINE]:   { status: 'up',       word: 'Online',   tone: 'live' },
  [LIVENESS.STALE]:    { status: 'degraded', word: 'Stale',    tone: 'stale' },
  [LIVENESS.OFFLINE]:  { status: 'down',     word: 'Offline',  tone: 'offline' },
  [LIVENESS.ENROLLED]: { status: 'degraded', word: 'Enrolled', tone: 'enrolled' },
  [LIVENESS.UNKNOWN]:  { status: 'unknown',  word: 'Unknown',  tone: 'unknown' },
}

/**
 * What a pull agent can do, as it registered, and whether it runs steps under
 * the service accounts a scenario declares. `capabilities` is the beacon's own
 * roster from GET /api/agents. Windows never honours a step identity — the
 * executor collapses every non-direct identity to `direct` — so a Windows row
 * reads "not honoured" even if it lists identity-harness (enroll defaults an
 * empty roster to ["shell","identity-harness"]). A row with no roster makes no
 * identity claim either way. An honoured harness needs no line of its own:
 * the identity-harness chip says it, with the meaning in its tooltip. Only the
 * exception, a row that runs every step as its own user, gets a line.
 */
const HARNESS_TITLE = 'Runs steps as the service accounts each scenario declares.'

function AgentCapabilities({ agent }) {
  const caps = Array.isArray(agent.capabilities)
    ? [...new Set(agent.capabilities.map((c) => String(c).trim()).filter(Boolean))]
    : []
  const windows = installerOS(agent.os || agent.platform) === 'windows'
  const harness = caps.some((c) => c.toLowerCase() === 'identity-harness')
  const honoured = harness && !windows
  const identity = !honoured && (caps.length > 0 || windows) ? (
    <p className="target-card__identity target-card__identity--warn" data-testid="agent-identity"
      title="Identity not honoured: every step runs as the agent's own user, not the service account the scenario declares.">
      Runs as its own user
    </p>
  ) : null
  return (
    <div className="target-card__caps" data-testid="agent-capabilities">
      {caps.length > 0 ? (
        <ul className="target-card__chips" aria-label="Capabilities">
          {caps.map((c) => (
            <li
              key={c}
              className="target-card__chip mono"
              title={honoured && c.toLowerCase() === 'identity-harness' ? HARNESS_TITLE : undefined}
            >{c}</li>
          ))}
        </ul>
      ) : (
        <p className="target-card__caps-none">Capabilities not reported</p>
      )}
      {identity}
    </div>
  )
}


/**
 * TargetsView — the Agents destination AND the guided-flow target picker.
 *
 * Two responsibilities, cleanly split:
 *
 *   1. MANAGEMENT (always) — enroll/deploy a pull beacon, prune stale agents,
 *      surface the push-bundle path and provisioned IaC lab targets. This is
 *      the "Agents" first-class destination.
 *
 *   2. ACTIVE-AGENT SELECTION (global) — the agent a card names is lifted to
 *      the EnvironmentProvider via `setAgent`, so the always-visible header
 *      AgentSwitcher and this surface share ONE active agent. The active agent
 *      is no longer a launch-only local prop; it is ambient console scope read
 *      back from `useEnvironment().agent`.
 *
 * When embedded in the guided "New POV run" flow, `onSelectTarget` is also
 * provided: selecting an agent/push card lifts it as the launch target
 * (pull vs push mode is derived downstream). Selecting an agent does BOTH —
 * sets the global active agent AND lifts the guided launch target — so the two
 * concepts stay in sync instead of drifting.
 *
 * Props (all optional — the standalone Agents destination passes none):
 *   selectedTarget  — { kind, id } | null   guided-flow launch target
 *   onSelectTarget  — (target) => void       lift the guided launch target
 *   embedded        — boolean                mounted inside another page, which
 *                                            owns the h1; the title steps down to h2
 */

// Profile · node · failed prepare, or '' when every member is missing.
function containerFactsLine(f) {
  return [
    f.posture_profile,
    f.node_name && `node ${f.node_name}`,
    String(f.prepare_status ?? '').startsWith('PREPARE_FAILED') && f.prepare_status,
  ].filter(Boolean).join(' · ')
}

// What a failed DELETE /api/agents/{id} said, in the shape the card renders.
// SimCore refuses with 409 AGENT_HAS_OPEN_RUNS (+ run_ids) while a run is owed
// to the agent or in flight on it, and 404 AGENT_NOT_FOUND for a row that is
// already gone. A request that never got an answer has no code.
function refusalOf(err) {
  const p = err?.payload || {}
  const detail = typeof err?.detail === 'string' ? err.detail : null
  return {
    code: err?.code || null,
    message: detail || err?.message || 'SimCore refused the delete.',
    runIds: Array.isArray(p.run_ids) ? p.run_ids : [],
    aborted: {},
  }
}

const plural = (n, one, many = `${one}s`) => (n === 1 ? one : many)

// The id list in a bulk confirm scrolls past this many rows; only then is it a
// tab stop, so a keyboard can scroll it.
const BULK_LIST_SCROLLS_AT = 8

export default function TargetsView({ selectedTarget = null, onSelectTarget = () => {}, embedded = false }) {
  const Title = embedded ? 'h2' : 'h1'
  // ── Global scope from the provider (agents + active agent are ambient) ─────
  const { agents, agent: activeAgent, setAgent, tenant, refreshAgents, errors, healthModel, loading: envLoading } = useEnvironment()
  const agentsLoading = Boolean(envLoading?.agents)
  const agentsError = errors?.agents || null
  const activeAgentId = activeAgent ? agentIdOf(activeAgent) : null

  // Deploy-agent flow: mint an enrollment token → copy ONE line → SimCore
  // assigns the id when the jumpbox redeems it. The legacy self-asserted-id
  // installer is kept behind a disclosure for hosts that cannot reach back.
  const [deployOpen, setDeployOpen] = useState(false)
  const [deployOS, setDeployOS]     = useState('linux')   // 'linux' | 'windows'
  const [deployId, setDeployId]     = useState('jumpbox-01')
  const [tokenLabel, setTokenLabel] = useState('')
  const [tokenTtl, setTokenTtl]     = useState(3600)      // seconds
  const [tokenUses, setTokenUses]   = useState(1)
  const [token, setToken]           = useState(null)      // minted token (shown once)
  const [mintedAtMs, setMintedAtMs] = useState(null)      // browser clock at mint, fallback only
  const [minting, setMinting]       = useState(false)
  const [mintError, setMintError]   = useState(null)
  const [showLegacy, setShowLegacy] = useState(false)
  const deployCopy = useCopyFeedback()
  const cardCopy = useCopyFeedback()
  // The Kubernetes tab (DeployAgentKubernetes) takes a plain `copyText(text)`.
  // It goes through the deploy modal's copy feedback, so its Copy buttons get
  // the clipboard fallback and report into the same live region
  // (deploy-copy-live), including "could not copy" when the clipboard is blocked.
  const deployCopyFn = deployCopy.copy
  const copyText = useCallback((text) => {
    if (!text) return
    deployCopyFn('kubernetes', text, null, 'kubectl command')
  }, [deployCopyFn])
  const oneLinerRef = useRef(null)
  const tokenRef = useRef(null)
  const runLineRef = useRef(null)
  const legacyRef = useRef(null)
  const uninstallRef = useRef(null)
  const [pendingDelete, setPendingDelete] = useState(null) // agent_id awaiting confirm
  const [busyDelete, setBusyDelete] = useState(null)       // agent_id mid-delete

  // A refused delete, per agent: {message, code, runIds, aborted: {runId: text}}.
  // The list does not change on a refusal, so swallowing the error (as this
  // used to: "surfaced via list refresh") made Delete row a button that did
  // nothing. SimCore refuses with 409 AGENT_HAS_OPEN_RUNS while a run is owed
  // to the agent or in flight on it; ending such a run is the operator's call.
  const [deleteRefusal, setDeleteRefusal] = useState({})
  // The per-row confirm keeps the host-side uninstall steps behind a
  // disclosure, so nothing sits between the Delete click and the confirm.
  const [howOpen, setHowOpen] = useState(false)
  const [howMoreOpen, setHowMoreOpen] = useState(false) // the by-hand / root fallback
  const rowConfirmRef = useRef(null)
  const deleteTriggerRefs = useRef({})   // agent_id → that row's Delete button
  const rowReturnFocusRef = useRef(null) // agent_id whose Delete gets focus back

  // ── Bulk delete: "Delete all offline (N)" and select mode ─────────────────
  const [selectMode, setSelectMode] = useState(false)
  const [selected, setSelected] = useState([])         // agent ids, click order
  const [bulkConfirm, setBulkConfirm] = useState(null) // {kind: 'offline'|'selected', ids}
  const [bulkProgress, setBulkProgress] = useState(null) // {current, total} while running
  const [bulkSummary, setBulkSummary] = useState(null)   // {total, deleted[], refused[]}
  const bulkRef = useRef(null)
  const bulkToolbarRef = useRef(null)
  const bulkReturnFocusRef = useRef(null) // the toolbar button that opened the confirm
  const bulkRunningRef = useRef(false)    // pauses the 10 s poll while a batch runs
  const bulkBusy = bulkProgress !== null

  // One DELETE. A refusal is recorded against its row (the card renders it,
  // abort controls included) and returned; null means the row is gone.
  const deleteOne = useCallback(async (agentId) => {
    setDeleteRefusal((m) => { const n = { ...m }; delete n[agentId]; return n })
    try {
      await deleteAgent(agentId)
      return null
    } catch (err) {
      const refusal = refusalOf(err)
      setDeleteRefusal((m) => ({ ...m, [agentId]: refusal }))
      return refusal
    }
  }, [])

  const removeAgent = useCallback(async (agentId) => {
    setBusyDelete(agentId)
    try {
      // The provider owns the agent list; re-pull so the header switcher and
      // this surface both reflect the removal (its stale-pointer guard clears
      // the active pointer + falls back if the deleted agent was active).
      if (!(await deleteOne(agentId))) await refreshAgents()
    } finally {
      rowReturnFocusRef.current = agentId
      setBusyDelete(null); setPendingDelete(null)
    }
  }, [deleteOne, refreshAgents])

  const openRowDelete = useCallback((agentId) => {
    setBulkConfirm(null)
    setHowOpen(false)
    setHowMoreOpen(false)
    setPendingDelete(agentId)
  }, [])

  const cancelRowDelete = useCallback((agentId) => {
    rowReturnFocusRef.current = agentId
    setPendingDelete(null)
  }, [])

  // Focus lands on Cancel when a row confirm opens, and goes back to the row's
  // Delete when it closes (if the row is still there).
  useEffect(() => {
    if (pendingDelete) {
      rowConfirmRef.current?.querySelector('[data-confirm-cancel]')?.focus()
      return
    }
    const back = rowReturnFocusRef.current
    rowReturnFocusRef.current = null
    if (back) deleteTriggerRefs.current[back]?.focus()
  }, [pendingDelete])

  const askBulk = useCallback((kind, ids, opener) => {
    if (!ids.length) return
    setPendingDelete(null)
    setBulkSummary(null)
    bulkReturnFocusRef.current = opener
    setBulkConfirm({ kind, ids })
  }, [])

  const cancelBulk = useCallback(() => setBulkConfirm(null), [])

  useEffect(() => {
    if (bulkConfirm) {
      bulkRef.current?.querySelector('[data-confirm-cancel]')?.focus()
      return
    }
    const back = bulkReturnFocusRef.current
    bulkReturnFocusRef.current = null
    if (back) bulkToolbarRef.current?.querySelector(`[data-bulk-action="${back}"]`)?.focus()
  }, [bulkConfirm])

  // One DELETE at a time, never in parallel: SQLite takes one writer, and the
  // order in the summary is the order on screen. A refusal never stops the
  // batch. The list is re-pulled once, after the last call.
  const runBulk = useCallback(async (ids) => {
    bulkReturnFocusRef.current = null
    setBulkConfirm(null)
    setBulkSummary(null)
    bulkRunningRef.current = true
    const deleted = []
    const refused = []
    try {
      for (let i = 0; i < ids.length; i += 1) {
        setBulkProgress({ current: i + 1, total: ids.length })
        const refusal = await deleteOne(ids[i])
        if (refusal) refused.push({ id: ids[i], ...refusal })
        else deleted.push(ids[i])
      }
    } finally {
      bulkRunningRef.current = false
      setBulkProgress(null)
      setSelectMode(false)
      setSelected([])
      setBulkSummary({ total: ids.length, deleted, refused })
    }
    await refreshAgents()
  }, [deleteOne, refreshAgents])

  useEffect(() => {
    if (bulkSummary) bulkRef.current?.querySelector('[data-summary-dismiss]')?.focus()
  }, [bulkSummary])

  const toggleSelectMode = useCallback(() => {
    setSelectMode((on) => !on)
    setSelected([])
    setBulkConfirm(null)
  }, [])

  const toggleSelected = useCallback((agentId) => {
    setSelected((s) => (s.includes(agentId) ? s.filter((x) => x !== agentId) : [...s, agentId]))
  }, [])

  const abortOpenRun = useCallback(async (agentId, runId) => {
    let text
    try {
      await abortRun(runId)
      text = `${runId} aborted · delete again`
    } catch (err) {
      text = `Abort failed: ${err?.message || 'unknown error'}`
    }
    setDeleteRefusal((m) => (m[agentId]
      ? { ...m, [agentId]: { ...m[agentId], aborted: { ...m[agentId].aborted, [runId]: text } } }
      : m))
  }, [])

  const origin = typeof window !== 'undefined' ? window.location.origin : ''
  // The one-liner is only as good as the address it carries. A DC on the
  // control plane usually has the console open at localhost:8888 (dev-up.sh),
  // and `localhost` pasted on a jumpbox resolves to the JUMPBOX — the beacon
  // installs, calls itself, and the roster stays empty with nothing to read.
  // SimCore does NOT refuse a loopback address (installing on the same machine
  // is legitimate; there is no INSTALLER_SERVER_LOOPBACK code), so this warning
  // is the only thing between the copy and the paste.
  const originIsLoopback = /^https?:\/\/(localhost|127\.0\.0\.1|\[::1\]|0\.0\.0\.0)(:|$)/i.test(origin)

  // Token-bearing installer (preferred) vs legacy explicit-id installer.
  // The token rides in an env var rather than the URL, which keeps it out of
  // any proxy or web-server access log. It does NOT keep it out of the
  // jumpbox's shell history — the pasted line carries it (TOKEN_HISTORY_NOTE
  // says so beside the token). Command strings come from deployCommands.js —
  // the one spelling shared with Get started (TLS 1.2 opt-in included for
  // Windows PowerShell 5.1).
  const isWindows = installerOS(deployOS) === 'windows'
  const installUrl = `${origin}${agentInstallUrl({ os: deployOS })}`
  // An empty field falls back to the documented default; anything typed is
  // held to SimCore's own id rule before it is put into a command.
  const legacyIdProblem = deployId ? agentIdProblem(deployId) : null
  const legacyInstallUrl = `${origin}${agentInstallUrl({ os: deployOS, id: deployId || 'jumpbox-01' })}`
  const oneLiner = token ? pullOneLiner({ os: deployOS, url: installUrl, token: token.token }) : null
  const legacyOneLiner = pullOneLiner({ os: deployOS, url: legacyInstallUrl })
  const downloadRunLine = token && isWindows ? windowsDownloadRunLine({ token: token.token }) : null
  const ship = beaconShipState(healthModel, deployOS)

  // Install telemetry: poll while the modal is open; the verdict on THIS token
  // is the newest attempt SimCore recorded after the token's own created_at
  // (server clock against server clock — no browser skew).
  const attemptsState = useInstallAttempts(deployOpen, { refreshKey: token ? (token.id ?? token.token) : null })
  const sinceMs = token
    ? (Number.isFinite(parseServerTime(token.created_at)) ? parseServerTime(token.created_at) : mintedAtMs)
    : NaN
  const latestAttempt = token ? attemptsSince(attemptsState.attempts, sinceMs)[0] || null : null

  const mint = useCallback(async () => {
    setMinting(true); setMintError(null)
    try {
      const t = await mintEnrollmentToken({
        label: tokenLabel.trim() || null,
        ttl_seconds: Number(tokenTtl),
        max_uses: Number(tokenUses),
      })
      setMintedAtMs(Date.now())
      setToken(t)
    } catch (err) {
      setMintError(err.message || 'Could not mint an enrollment token')
    } finally {
      setMinting(false)
    }
  }, [tokenLabel, tokenTtl, tokenUses])

  const closeDeploy = useCallback(() => {
    setDeployOpen(false)
    // The full token value is unrecoverable once dismissed — drop it from state
    // rather than leaving a live credential sitting in a closed dialog.
    setToken(null); setMintedAtMs(null); setMintError(null); setShowLegacy(false)
  }, [])

  // ── Dialog focus, Tab trap + Escape ──────────────────────────────────────
  // A keydown handler on the dialog element only fires once focus is already
  // inside it, and opening the modal leaves focus on the opener behind the
  // backdrop — so Escape did nothing for the operator who just clicked Deploy.
  // Move focus in, listen on the document, keep Tab inside (aria-modal alone
  // does not: Tab used to walk out into the page behind the backdrop), and
  // hand focus back on close.
  const deployRef = useRef(null)
  const deployPrevFocusRef = useRef(null)
  useEffect(() => {
    if (!deployOpen) return undefined
    deployPrevFocusRef.current = document.activeElement
    const t = setTimeout(() => {
      const el = deployRef.current
      if (!el) return
      const first = el.querySelector('input, select, button:not(.deploy-modal__close)')
      ;(first || el.querySelector('.deploy-modal__close'))?.focus()
    }, 20)
    const onKey = (e) => {
      if (e.key === 'Escape') { e.preventDefault(); closeDeploy(); return }
      trapTabKey(e, deployRef.current)
    }
    document.addEventListener('keydown', onKey)
    return () => {
      clearTimeout(t)
      document.removeEventListener('keydown', onKey)
      const prev = deployPrevFocusRef.current
      if (prev && prev.focus) prev.focus()
    }
  }, [deployOpen, closeDeploy])

  // ── Agent liveness refresh (provider) ──────────────────────────────────────
  const refresh = useCallback(() => {
    refreshAgents()
  }, [refreshAgents])
  useEffect(() => {
    // Keep beacon liveness + bundle list fresh. Agents flow through the
    // provider so a refresh here re-scopes the header switcher too. A bulk
    // delete re-pulls once when it ends, so the poll sits that batch out.
    const t = setInterval(() => { if (!bulkRunningRef.current) refresh() }, 10_000)
    return () => clearInterval(t)
  }, [refresh])

  const isSel = (kind, id) => selectedTarget && selectedTarget.kind === kind && selectedTarget.id === id

  // Selecting an agent lifts it to global scope (setAgent) AND, in the guided
  // flow, arms it as the launch target. One click, both concepts in sync.
  const chooseAgent = useCallback((a) => {
    const id = agentIdOf(a)
    setAgent(id)
    onSelectTarget({ kind: 'agent', id, label: a.hostname || a.host || id })
  }, [setAgent, onSelectTarget])

  // "All offline" is offline by SimCore's own liveness word: never online,
  // stale, enrolled or unknown, and never the active agent.
  const agentIds = agents.map(agentIdOf)
  const offlineIds = agents
    .filter((a) => agentLiveness(a).state === LIVENESS.OFFLINE && agentIdOf(a) !== activeAgentId)
    .map(agentIdOf)
  // Fleet order, and only rows that are still on the list.
  const selectedIds = agentIds.filter((id) => selected.includes(id))

  const bulkSummaryText = bulkSummary
    ? `Deleted ${bulkSummary.deleted.length}`
      + (bulkSummary.refused.length ? ` · ${bulkSummary.refused.length} refused` : '')
    : ''
  const bulkLiveText = bulkProgress
    ? `Deleting ${bulkProgress.current} of ${bulkProgress.total}`
    : bulkSummaryText

  let bulkPanel = null
  if (bulkConfirm) {
    const { kind, ids } = bulkConfirm
    const n = ids.length
    const byId = new Map(agents.map((a) => [agentIdOf(a), a]))
    const notOffline = ids.filter((id) => agentLiveness(byId.get(id)).state !== LIVENESS.OFFLINE).length
    const question = `Delete ${n} ${kind === 'offline' ? 'offline' : 'selected'} ${plural(n, 'agent')}?`
    const scrolls = n > BULK_LIST_SCROLLS_AT
    const note = kind === 'offline'
      ? ['Skips the active, online and stale agents.']
      : [
        ids.includes(activeAgentId) && 'Includes the active agent.',
        notOffline > 0 && `${notOffline} not offline · may re-register.`,
      ].filter(Boolean)
    bulkPanel = (
      <div
        className="fleet-bulk fleet-bulk--confirm"
        role="group"
        aria-labelledby="fleet-bulk-question"
        ref={bulkRef}
        data-testid="fleet-bulk-confirm"
        onKeyDown={(e) => { if (e.key === 'Escape') { e.preventDefault(); cancelBulk() } }}
      >
        <p className="fleet-bulk__q" id="fleet-bulk-question">{question}</p>
        <ul
          className="fleet-bulk__ids"
          aria-label="Agents to delete"
          tabIndex={scrolls ? 0 : undefined}
        >
          {ids.map((id) => <li key={id} className="mono">{id}</li>)}
        </ul>
        {note.length > 0 && (
          <p className="fleet-bulk__note" title="Deleting removes the registry row only. Run history stays.">
            {note.join(' ')}
          </p>
        )}
        <div className="fleet-bulk__actions">
          <Button variant="secondary" data-confirm-cancel="" onClick={cancelBulk}>Cancel</Button>
          <Button variant="destructive" verb="Delete" onClick={() => runBulk(ids)}>Delete {n}</Button>
        </div>
      </div>
    )
  } else if (bulkProgress) {
    const { current, total } = bulkProgress
    bulkPanel = (
      // Visual only; the live region below is what assistive tech hears.
      <div className="fleet-bulk fleet-bulk--progress" aria-hidden="true">
        <p className="fleet-bulk__q">Deleting {current} of {total}</p>
        <div className="fleet-bulk__bar"><span style={{ width: `${((current - 1) / total) * 100}%` }} /></div>
      </div>
    )
  } else if (bulkSummary) {
    const { deleted, refused } = bulkSummary
    bulkPanel = (
      <div className="fleet-bulk fleet-bulk--summary" ref={bulkRef} data-testid="fleet-bulk-summary">
        <div className="fleet-bulk__counts">
          <StatusIcon status={deleted.length ? 'up' : 'off'} word={`Deleted ${deleted.length}`} />
          {refused.length > 0 && <StatusIcon status="down" word={`${refused.length} refused`} />}
          <Button variant="secondary" className="fleet-bulk__dismiss" data-summary-dismiss="" onClick={() => setBulkSummary(null)}>
            Dismiss
          </Button>
        </div>
        {refused.length > 0 && (
          <ul className="fleet-bulk__refused" aria-label="Refused deletes">
            {refused.map((r) => (
              <li key={r.id} title={r.message}>
                <span className="mono fleet-bulk__refused-id">{r.id}</span>
                {' · '}
                {r.code ? <code className="mono">{r.code}</code> : r.message}
                {r.runIds.map((rid) => (
                  <React.Fragment key={rid}>
                    {' · '}
                    <a className="mono" href={`#/runs?run=${encodeURIComponent(rid)}`}>{rid}</a>
                  </React.Fragment>
                ))}
              </li>
            ))}
          </ul>
        )}
      </div>
    )
  }

  return (
    <div className="targets">
      <header className="view-head">
        <div>
          <Title>Agents &amp; targets</Title>
          <p className="view-head__meta">
            Pick the active agent; the header switcher follows.
            {tenant && <> Tenant <strong className="mono">{tenant.name || tenant.id}</strong>.</>}
          </p>
        </div>
        <div className="targets__head-actions">
          <span className="targets__active" data-testid="active-agent-summary">
            active agent:{' '}
            {activeAgentId
              ? <strong className="mono">{activeAgentId}</strong>
              : <em className="targets__active--none">none</em>}
          </span>
          <Button verb="Refresh" onClick={refresh} />
        </div>
      </header>

      <div className="targets__grid">
        {/* ── Pull agents ─────────────────────────────────────────────── */}
        <section className="target-col">
          <div className="target-col__title">
            <span className="plane-dot plane-dot--detected" /> Pull agents
            <span className="target-col__count">{agents.length}</span>
            <Button verb="Deploy" className="target-col__action" data-tour-id="agent-enroll" onClick={() => setDeployOpen(true)}>
              Deploy agent
            </Button>
          </div>
          {agents.length > 0 && (
            <div className="fleet-toolbar" role="group" aria-label="Remove agent rows" ref={bulkToolbarRef}>
              <Button
                verb="Select"
                className="fleet-toolbar__select"
                aria-pressed={selectMode}
                disabled={bulkBusy}
                onClick={toggleSelectMode}
              />
              {selectMode && (
                <Button
                  verb="Delete"
                  className="btn--danger"
                  data-bulk-action="selected"
                  disabled={bulkBusy || selectedIds.length === 0}
                  onClick={() => askBulk('selected', selectedIds, 'selected')}
                >Delete selected ({selectedIds.length})</Button>
              )}
              {offlineIds.length > 0 && (
                <Button
                  verb="Delete"
                  className="btn--danger"
                  data-bulk-action="offline"
                  disabled={bulkBusy}
                  onClick={() => askBulk('offline', offlineIds, 'offline')}
                >Delete offline ({offlineIds.length})</Button>
              )}
            </div>
          )}
          {bulkPanel}
          {/* Always mounted, so the first progress line is announced. */}
          <p className="sr-only" role="status" aria-live="polite" data-testid="fleet-bulk-live">{bulkLiveText}</p>
          {agentsLoading && agents.length === 0 && <div className="target-card target-card--ghost">polling beacons…</div>}
          {agentsError && (
            <div className="target-card target-card--empty" role="alert" data-testid="agents-load-error">
              <div className="target-card__title">Agent list not loaded</div>
              <p className="target-card__sub" title="The list below is the last one SimCore returned, not a fresh answer.">
                <span className="mono">{agentsError}</span> · showing the last list
              </p>
              <Button verb="Refresh" onClick={refresh}>Retry</Button>
            </div>
          )}
          {!agentsLoading && !agentsError && agents.length === 0 && (
            <div className="target-card target-card--empty">
              <div className="target-card__title">No agents registered</div>
              <p className="target-card__sub">Deploy a beacon, or use the push bundle.</p>
              <Button verb="Deploy" onClick={() => setDeployOpen(true)}>Deploy agent</Button>
            </div>
          )}
          {agents.map((a) => {
            const id = agentIdOf(a)
            const live = agentLiveness(a)
            const liveness = LIVENESS_STATUS[live.state] || LIVENESS_STATUS[LIVENESS.UNKNOWN]
            const os = a.os || a.platform || 'linux'
            const targetOs = installerOS(os)
            const confirming = pendingDelete === id
            const isActive = id === activeAgentId
            const removeCmd = uninstallCommand({ os: targetOs, origin })
            const refusal = deleteRefusal[id]
            const ageTitle = live.ageSource === 'server'
              ? 'age measured on SimCore\'s clock'
              : live.ageSource === 'browser' ? 'age from this browser\'s clock — SimCore sent none' : undefined
            const copied = cardCopy.isCopied(`uninstall-${id}`)
            return (
              <div
                key={id}
                className={
                  `target-card target-card--agent target-card--${liveness.tone}`
                  + (isSel('agent', id) ? ' is-selected' : '')
                  + (isActive ? ' is-active' : '')
                  + (selectMode && selected.includes(id) ? ' is-picked' : '')
                }
              >
                {selectMode && (
                  <label className="target-card__pick">
                    <input
                      type="checkbox"
                      checked={selected.includes(id)}
                      disabled={bulkBusy}
                      onChange={() => toggleSelected(id)}
                      aria-label={`Select ${id}`}
                    />
                    <span aria-hidden="true">Select</span>
                  </label>
                )}
                <button
                  type="button"
                  className="target-card__select"
                  aria-pressed={isActive}
                  onClick={() => chooseAgent(a)}
                >
                  <div className="target-card__head">
                    <span className="target-card__title mono">{id}</span>
                    {isActive && <span className="target-card__tag">Active</span>}
                    <span className="target-card__status" title={live.label} data-testid="agent-liveness">
                      <StatusIcon status={liveness.status} word={liveness.word} />
                    </span>
                  </div>
                  <p className="target-card__sub">
                    {a.hostname || a.host || 'unknown host'} · {os} ·{' '}
                    <span title={ageTitle}>seen {fmtAge(live.ageSeconds)}</span>
                  </p>
                  {a.runtime_facts?.container === true && containerFactsLine(a.runtime_facts) && (
                    <p className="target-card__sub mono" data-testid="agent-container-facts">
                      {containerFactsLine(a.runtime_facts)}
                    </p>
                  )}
                  {live.state === LIVENESS.ENROLLED && (
                    <span
                      className="target-card__waiting"
                      data-testid="agent-enrolled"
                      title={'The installer reached SimCore; no beacon has polled yet. '
                        + (targetOs === 'windows'
                          ? 'If this does not change within a minute, check the CortexSimAgent service and the install attempts in Deploy agent.'
                          : 'If this does not change within a minute, the beacon did not start: check the install attempts in Deploy agent.')}
                    >Waiting for first poll · do not launch</span>
                  )}
                  {live.state === LIVENESS.UNKNOWN && live.label !== 'unknown' && (
                    <span className="target-card__waiting">SimCore {live.label}</span>
                  )}
                  {isSel('agent', id) && <span className="target-card__selected">Selected · pull mode</span>}
                </button>

                <AgentCapabilities agent={a} />

                {confirming ? (
                  // Escape cancels from anywhere in the confirm, the disclosure
                  // and its Copy button included.
                  <div
                    className="target-card__confirming"
                    onKeyDown={(e) => { if (e.key === 'Escape') { e.preventDefault(); cancelRowDelete(id) } }}
                  >
                    <div
                      className="target-card__confirm"
                      role="group"
                      aria-label={`Delete ${id}?`}
                      ref={rowConfirmRef}
                    >
                      <span className="target-card__confirm-q">Delete <strong className="mono">{id}</strong>?</span>
                      <Button data-confirm-cancel="" onClick={() => cancelRowDelete(id)}>Cancel</Button>
                      <Button
                        variant="destructive"
                        verb="Delete"
                        disabled={busyDelete === id || bulkBusy}
                        onClick={() => removeAgent(id)}
                      >{busyDelete === id ? 'Deleting…' : 'Delete'}</Button>
                    </div>
                    <div className="target-card__how">
                      <Button aria-expanded={howOpen} onClick={() => setHowOpen((v) => !v)}>Uninstall command</Button>
                      {howOpen && (
                        <div className="target-card__remove" data-testid="agent-remove-help">
                          <p title="A beacon still running re-registers on its next poll and the row comes back.">
                            Deleting removes the row only. Run this on the host
                            {targetOs === 'windows' ? ' as Administrator' : ''} to remove the beacon.
                          </p>
                          <div className="deploy-snippet">
                            <code data-testid="agent-uninstall-cmd" ref={uninstallRef}>{removeCmd}</code>
                            <Button
                              verb="Copy"
                              aria-label={`${copied ? 'Copied' : 'Copy'} removal command for ${id}`}
                              onClick={() => cardCopy.copy(`uninstall-${id}`, removeCmd, uninstallRef, 'removal command')}
                            >{copied ? 'Copied' : 'Copy'}</Button>
                          </div>
                          <Button
                            aria-expanded={howMoreOpen}
                            data-testid={targetOs === 'windows' ? undefined : 'agent-uninstall-note'}
                            title={targetOs === 'windows' ? undefined : POSIX_UNINSTALL_NOTE}
                            onClick={() => setHowMoreOpen((v) => !v)}
                          >{targetOs === 'windows' ? 'Host can’t reach POVengine?' : 'Installed as root?'}</Button>
                          {howMoreOpen && (targetOs === 'windows'
                            ? <code className="mono target-card__fallback" data-testid="agent-uninstall-manual">{WINDOWS_MANUAL_UNINSTALL}</code>
                            : <code className="mono target-card__fallback" data-testid="agent-uninstall-root-cmd">{posixRootUninstallCommand({ origin })}</code>)}
                          <CopyLive feedback={cardCopy.feedback} testId="agent-copy-live" />
                        </div>
                      )}
                    </div>
                  </div>
                ) : (
                  <>
                  {refusal && (
                    <div className="target-card__refused" role="alert" data-testid="agent-delete-refused" title={refusal.message}>
                      <p>
                        <strong>Not deleted</strong>
                        {' · '}
                        {refusal.code ? <code className="mono">{refusal.code}</code> : refusal.message}
                      </p>
                      {refusal.runIds.length > 0 && (
                        <ul>
                          {refusal.runIds.map((rid) => (
                            <li key={rid}>
                              <a className="mono" href={`#/runs?run=${encodeURIComponent(rid)}`}>{rid}</a>
                              {refusal.aborted[rid] ? (
                                <span>{refusal.aborted[rid]}</span>
                              ) : (
                                <Button
                                  verb="Abort run"
                                  className="btn--danger"
                                  onClick={() => abortOpenRun(id, rid)}
                                  aria-label={`Abort ${rid}`}
                                >Abort</Button>
                              )}
                            </li>
                          ))}
                        </ul>
                      )}
                    </div>
                  )}
                  <div className="target-card__actions">
                    {!isActive && (
                      <Button verb="Set active" onClick={() => chooseAgent(a)} title="Make this the active agent" />
                    )}
                    {agentIdProblem(id) ? (
                      // A beacon may have REGISTERED with an id the installer
                      // route refuses (register does not apply the id rule). A
                      // download link for it would save a 400 as a script.
                      <Button
                        verb="Download"
                        disabled
                        data-testid="agent-installer-unavailable"
                        title={`SimCore refuses to build an installer for this id (400 BAD_AGENT_ID): ${agentIdProblem(id)}. Deploy a fresh agent with an enrollment token instead.`}
                      >No installer</Button>
                    ) : (
                      <Button
                        verb="Download"
                        href={agentInstallUrl({ os: targetOs, id })}
                        download={targetOs === 'windows' ? WINDOWS_INSTALLER_FILE : POSIX_INSTALLER_FILE}
                        title="Re-download this agent's installer"
                      >Installer</Button>
                    )}
                    <span
                      className="target-card__actions-end"
                      ref={(el) => { deleteTriggerRefs.current[id] = el?.querySelector('button') || null }}
                    >
                      <Button
                        verb="Delete"
                        className="btn--danger"
                        disabled={bulkBusy}
                        onClick={() => openRowDelete(id)}
                        aria-label={`Delete ${id}`}
                        title="Delete this agent's registry row"
                      />
                    </span>
                  </div>
                  </>
                )}
              </div>
            )
          })}
        </section>

        {/* ── Push bundle ─────────────────────────────────────────────── */}
        <section className="target-col">
          <div className="target-col__title">
            <span className="plane-dot plane-dot--stitched" /> Push bundle
          </div>
          <button
            type="button"
            className={'target-card target-card--push' + (isSel('push', 'bundle') ? ' is-selected' : '')}
            onClick={() => onSelectTarget({ kind: 'push', id: 'bundle', label: 'Offline push bundle' })}
          >
            <div className="target-card__head">
              <span className="target-card__title">Offline bundle</span>
            </div>
            {/* What is always true is that SimCore can GENERATE a bundle with no
                agent. Whether the bundle runs is a property of the target. */}
            <p className="target-card__sub" title="Self-contained: bash for a clean Ubuntu 22.04 host, PowerShell 5.1 for Windows, or a K8s manifest. No inbound connection.">
              Bash, PowerShell or K8s · no agent needed
            </p>
            <p className="target-card__sub target-card__sub--caveat" data-testid="push-windows-caveat" title={WINDOWS_BUNDLE_UNPROVEN}>
              Windows .ps1 not yet run on a host
            </p>
            {isSel('push', 'bundle') && <span className="target-card__selected">Selected · push mode</span>}
          </button>
        </section>
      </div>

      {deployOpen && (
        <div className="deploy-backdrop" onMouseDown={closeDeploy}>
          <div
            className="deploy-modal"
            ref={deployRef}
            onMouseDown={(e) => e.stopPropagation()}
            role="dialog"
            aria-modal="true"
            aria-labelledby="deploy-modal-title"
          >
            <div className="deploy-modal__head">
              <h2 id="deploy-modal-title">Deploy a pull agent</h2>
              <button type="button" className="deploy-modal__close" onClick={closeDeploy} aria-label="Close">×</button>
            </div>
            <p className="deploy-modal__lede">
              Mint an enrollment token, run one line on the jumpbox, and SimCore assigns the
              agent id when the token is redeemed — nobody has to invent one. Tokens expire
              and can be revoked. The target needs <strong>no toolchain</strong> — SimCore
              serves a prebuilt, checksummed beacon —{' '}
              <span data-testid="deploy-prereqs">
                {isWindows
                  ? <>only Windows PowerShell 5.1 (built into Windows) and outbound reach to this SimCore.</>
                  : <>only <code>curl</code>, <code>bash</code> and outbound reach to this SimCore.</>}
              </span>
            </p>
            {originIsLoopback && (
              <p className="deploy-warn" role="status" data-testid="deploy-loopback-warn">
                ⚠ This console is open at <code className="mono">{origin}</code>, so the
                one-liner below carries a loopback address. On the jumpbox that resolves to
                the <strong>jumpbox itself</strong> — the beacon would install and call
                nothing. Reopen the console on the control plane's reachable address (e.g.
                <code className="mono">http://&lt;control-plane-ip&gt;:8888</code>) before
                copying. SimCore does not refuse a loopback address — installing on this same
                machine is legitimate — so nothing downstream stops the paste.
              </p>
            )}
            {isWindows
              ? (ship.state === 'missing' ? <WindowsBeaconMissing /> : <WindowsCaveats ship={ship} />)
              : <PosixBeaconGap ship={ship} />}

            <div className="deploy-field">
              <span className="launch-field__label">Target OS</span>
              <div className="deploy-os-toggle">
                {/* macOS is a real target: /api/agents/install accepts it and
                    the darwin beacons ship in the dist matrix. One POSIX script
                    covers Linux and macOS — it branches on `uname -s`. */}
                {[['linux', '🐧 Linux'], ['macos', '🍎 macOS'], ['windows', '🪟 Windows'], ['kubernetes', '☸ Kubernetes']].map(([o, label]) => (
                  <button
                    key={o}
                    type="button"
                    className={'deploy-os' + (deployOS === o ? ' is-active' : '')}
                    aria-pressed={deployOS === o}
                    onClick={() => setDeployOS(o)}
                  >{label}</button>
                ))}
              </div>
            </div>

            {deployOS === 'kubernetes' ? (
              <DeployAgentKubernetes origin={origin} copyText={copyText} />
            ) : !token ? (
              <>
                <div className="deploy-token-form">
                  <label className="deploy-field">
                    <span className="launch-field__label">Label (optional)</span>
                    <input
                      className="launch-select deploy-input"
                      value={tokenLabel}
                      onChange={(e) => setTokenLabel(e.target.value)}
                      placeholder="acme-pov jumpbox"
                      maxLength={80}
                      spellCheck={false}
                    />
                  </label>
                  <label className="deploy-field">
                    <span className="launch-field__label">Valid for</span>
                    <select
                      className="launch-select"
                      value={tokenTtl}
                      onChange={(e) => setTokenTtl(Number(e.target.value))}
                    >
                      <option value={900}>15 minutes</option>
                      <option value={3600}>1 hour</option>
                      <option value={86400}>1 day</option>
                      <option value={604800}>7 days</option>
                    </select>
                  </label>
                  <label className="deploy-field">
                    <span className="launch-field__label">Max uses</span>
                    <select
                      className="launch-select"
                      value={tokenUses}
                      onChange={(e) => setTokenUses(Number(e.target.value))}
                    >
                      <option value={1}>1 host</option>
                      <option value={5}>5 hosts</option>
                      <option value={25}>25 hosts</option>
                    </select>
                  </label>
                </div>

                {mintError && (
                  <p className="deploy-error" role="alert">{mintError}</p>
                )}

                <div className="deploy-actions">
                  <button
                    type="button"
                    className="btn btn--primary btn--lg"
                    onClick={mint}
                    disabled={minting}
                  >
                    {minting ? 'Minting…' : 'Mint enrollment token ▸'}
                  </button>
                </div>
              </>
            ) : (
              <>
                <div className="deploy-field">
                  <span className="launch-field__label">
                    Enrollment token — shown once, copy it now
                  </span>
                  <div className="deploy-snippet">
                    <code data-testid="deploy-token" ref={tokenRef}>{token.token}</code>
                    <CopyButton copier={deployCopy} id="token" text={token.token} selectRef={tokenRef} what="enrollment token" />
                  </div>
                  <span className="deploy-hint mono">
                    {token.remaining_uses ?? token.max_uses} use(s)
                    {token.expires_at && ` · expires ${serverDate(token.expires_at).toLocaleString()}`}
                  </span>
                  <span className="deploy-hint" data-testid="deploy-token-history-note">{TOKEN_HISTORY_NOTE}</span>
                </div>

                <div className="deploy-field">
                  <span className="launch-field__label">
                    {isWindows ? WINDOWS_ELEVATION_LABEL : 'Run this on the target (bash)'}
                  </span>
                  <div className="deploy-snippet">
                    <code data-testid="deploy-one-liner" ref={oneLinerRef}>{oneLiner}</code>
                    <CopyButton copier={deployCopy} id="one-liner" text={oneLiner} selectRef={oneLinerRef} what="install command" />
                  </div>
                </div>

                <div className="deploy-actions">
                  <a
                    className="btn btn--primary btn--lg"
                    href={installUrl}
                    download={isWindows ? WINDOWS_INSTALLER_FILE : POSIX_INSTALLER_FILE}
                  >
                    ↓ Download installer ({isWindows ? '.ps1' : '.sh'})
                  </a>
                  {downloadRunLine && (
                    <div className="deploy-run-line">
                      <span className="launch-field__label">
                        Then, in Windows PowerShell as Administrator, from the download folder
                      </span>
                      <div className="deploy-snippet">
                        <code data-testid="deploy-download-run" ref={runLineRef}>{downloadRunLine}</code>
                        <CopyButton
                          copier={deployCopy}
                          id="run-line"
                          text={downloadRunLine}
                          selectRef={runLineRef}
                          what="run line"
                          name="run line for the downloaded installer"
                        />
                      </div>
                      <span className="deploy-hint">
                        Without the token it falls back to the legacy shared id
                        (<code className="mono">jumpbox-01</code>), so every host run that way claims the same agent.
                      </span>
                    </div>
                  )}
                  <button type="button" className="btn" onClick={() => { setToken(null); setMintedAtMs(null) }}>
                    Mint another
                  </button>
                </div>
                {/* The beacon as a file too: nothing is piped into iex, and
                    the script downloads nothing the DC has not checked. The
                    modal has no address field, so the download names the same
                    origin the one-liner above carries. */}
                {isWindows && <WindowsNoCradle token={token.token} copier={deployCopy} server={origin} />}
                {latestAttempt ? (
                  <LatestAttempt attempt={latestAttempt} />
                ) : (
                  <p className="deploy-hint">
                    The beacon appears in this list within one poll interval (~10s) once the
                    token is redeemed. If the installer fails, its code shows here.
                  </p>
                )}
              </>
            )}
            <CopyLive feedback={deployCopy.feedback} testId="deploy-copy-live" />

            {/* Legacy path — kept because a host that cannot redeem a token
                (no outbound POST, pre-seeded image) still needs an installer. */}
            {deployOS !== 'kubernetes' && (
            <div className="deploy-legacy">
              <button
                type="button"
                className="linklike deploy-legacy__toggle"
                aria-expanded={showLegacy}
                onClick={() => setShowLegacy((v) => !v)}
              >
                {showLegacy ? '▾' : '▸'} Legacy: self-asserted agent id
              </button>
              {showLegacy && (
                <div className="deploy-legacy__body">
                  <label className="deploy-field">
                    <span className="launch-field__label">Agent ID</span>
                    <input
                      className="launch-select deploy-input"
                      value={deployId}
                      onChange={(e) => setDeployId(e.target.value)}
                      placeholder="jumpbox-01"
                      spellCheck={false}
                      maxLength={64}
                      aria-invalid={legacyIdProblem ? 'true' : 'false'}
                      aria-describedby={legacyIdProblem ? 'deploy-legacy-id-error' : undefined}
                    />
                  </label>
                  {legacyIdProblem ? (
                    // No command is offered for an id SimCore will refuse: on the
                    // target the refusal is only "error: 400", with the code gone.
                    <p className="deploy-error" role="alert" id="deploy-legacy-id-error" data-testid="deploy-legacy-id-error">
                      SimCore will not build an installer for this id (<code className="mono">400 BAD_AGENT_ID</code>):{' '}
                      {legacyIdProblem}. On the target that refusal shows only as{' '}
                      <code className="mono">curl: (22) … error: 400</code>, so no command is offered until the id is valid.
                    </p>
                  ) : (
                    <>
                      <div className="deploy-snippet">
                        <code data-testid="deploy-legacy-one-liner" ref={legacyRef}>{legacyOneLiner}</code>
                        <CopyButton copier={deployCopy} id="legacy" text={legacyOneLiner} selectRef={legacyRef} what="legacy install command" />
                      </div>
                      <span className="deploy-hint mono" data-testid="deploy-legacy-run-hint">
                        or, once installed: <code>{legacyRunHint({ os: deployOS, origin, id: deployId })}</code>
                      </span>
                    </>
                  )}
                </div>
              )}
            </div>
            )}

            <InstallAttemptsList state={attemptsState} />
          </div>
        </div>
      )}
    </div>
  )
}
