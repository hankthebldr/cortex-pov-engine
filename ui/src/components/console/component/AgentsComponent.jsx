import React, { useCallback, useMemo } from 'react'
import { useEnvironment } from '../../../context/EnvironmentContext.jsx'
import { agentInstallUrl, getAgents, getInstallAttempts, mintEnrollmentToken } from '../../../api/client.js'
import { parseServerTime } from '../../../api/time.js'
import ComponentPage from './ComponentPage.jsx'
import ConfigureFrame from './ConfigureFrame.jsx'
import TargetsView from '../TargetsView.jsx'
import useComponentStatus from '../useComponentStatus.js'
import { componentByKey } from '../componentModel.js'
import { agentLiveness, LIVENESS } from '../setupProgress.js'
import { attemptKind, attemptsSince, installerOS, pullOneLiner, CONTINUED_NOTE } from '../deployCommands.js'

/**
 * Agents — the component page for the beacons (design v26 §4.2, slice 3).
 *
 * Overview: one service row per enrolled agent, with SimCore's own liveness.
 * Setup: the v26 steps — two common, then four per OS — each verified by
 * something POVengine actually reads (the token it minted, the installer's
 * telemetry, the agent list) or ticked by the DC. Fleet: today's agent list
 * (TargetsView), unchanged.
 *
 * Deviation from the design, on purpose: v26 marks "Check the service
 * accounts" as POVengine-verified. No beacon reports a host's accounts, so
 * that step is the DC's to tick, and its Configuration row reads Can't check.
 */

const KEY = 'agent'
const PATHS = [['linux', 'Linux'], ['macos', 'macOS'], ['windows', 'Windows']]
const INSTALLER = { linux: 'linux', macos: 'darwin', windows: 'windows' }
const pathOfAgent = (a) => ({ darwin: 'macos', windows: 'windows' }[installerOS(a.os || a.platform)] || 'linux')
const NO_SUPERVISOR = new Set(['DEGRADED_NO_SUPERVISOR', 'SERVICE_START_FAILED'])

// SimCore's liveness on the v26 status scale; its own word (stale, offline)
// rides in the row's since line.
const LIVE_STATUS = {
  [LIVENESS.ONLINE]: { status: 'up' },
  [LIVENESS.STALE]: { status: 'degraded' },
  [LIVENESS.OFFLINE]: { status: 'down' },
  [LIVENESS.ENROLLED]: { status: 'degraded' },
  [LIVENESS.UNKNOWN]: { status: 'unknown' },
}

function seenText(live) {
  if (live.state === LIVENESS.ENROLLED) return 'enrolled — waiting for the first poll'
  if (live.ageSeconds == null) return `${live.label} · never seen`
  const s = Math.round(live.ageSeconds)
  const ago = s < 90 ? `${s}s` : s < 5400 ? `${Math.round(s / 60)}m` : `${Math.round(s / 3600)}h`
  return `${live.label} · last seen ${ago} ago`
}

/** Installer reports since the token this page created, newest first. */
async function attemptsForToken(shared) {
  const list = await getInstallAttempts()
  if (list === null) return { unsupported: true, attempts: [] }
  const since = parseServerTime(shared.token?.created_at)
  const sinceMs = Number.isFinite(since) ? since : shared.mintedAt
  return { unsupported: false, attempts: attemptsSince(list, sinceMs) }
}

const attemptRow = (a) => {
  const kind = attemptKind(a)
  return {
    label: `${a.stage || 'install'} · ${a.code}`,
    state: kind === 'ok' ? 'passed' : kind === 'continued' ? 'degraded' : 'failed',
    detail: [a.hostname, a.detail].filter(Boolean).join(' — ') || undefined,
  }
}

function buildSteps(path, origin) {
  const installer = INSTALLER[path]
  const label = PATHS.find(([p]) => p === path)[1]
  const needToken = { state: 'pending', detail: 'Create the token in step 1 first: this checks what the installer reported since then.' }

  const common = [
    {
      id: 'token',
      title: 'Create an enrollment token',
      why: 'A token works once, for 1 hour. POVengine assigns the agent its ID when it enrolls, so nothing on the host has to be named in advance.',
      verifier: 'pov',
      fields: ({ shared }) => [
        ['Token', shared.token ? 'created — the installer command in step 3 carries it' : 'Test creates it'],
        ['Valid for', '1 hour · 1 use'],
      ],
      test: async () => {
        const t = await mintEnrollmentToken({ label: 'configure-agents', ttl_seconds: 3600, max_uses: 1 })
        return {
          state: 'passed',
          detail: 'Created. It is good for one enrollment within the hour; the installer command in step 3 carries it.',
          share: { token: t, mintedAt: Date.now() },
        }
      },
    },
    {
      id: 'reach',
      title: 'Check the host can reach POVengine',
      why: 'The target needs this POVengine\'s port, by an address POVengine answers to. It needs no internet access and no build tools.',
      verifier: 'you',
      fields: [['Address', origin]],
      command: `curl -fsS ${origin}/api/health`,
    },
  ]

  const install = {
    id: `${path}.install`,
    title: 'Run the installer',
    why: path === 'windows'
      ? 'Run it in Windows PowerShell as Administrator (5.1 or later). It downloads the beacon from this POVengine and checks its SHA-256 before running it.'
      : 'Run it as root. It downloads the beacon from this POVengine and checks its SHA-256 before running it. The token rides an environment variable, so it stays out of the URL and proxy logs.',
    where: path === 'windows' ? 'target host · PowerShell as Administrator' : 'target host · root shell',
    verifier: 'pov',
    fields: ({ shared }) => (shared.token ? [] : [['Command', 'appears here once step 1 has created a token']]),
    command: ({ shared }) => (shared.token
      ? pullOneLiner({ os: installer, url: `${origin}${agentInstallUrl({ os: installer })}`, token: shared.token.token })
      : null),
    test: async ({ shared }) => {
      if (!shared.token) return needToken
      const { unsupported, attempts } = await attemptsForToken(shared)
      if (unsupported) return { state: 'cant_check', detail: 'This POVengine does not record installer reports.' }
      if (!attempts.length) {
        return { state: 'pending', detail: 'No installer report since the token was created. Run the command on the host, then Test again.' }
      }
      const newest = attempts[0]
      const kind = attemptKind(newest)
      return {
        state: kind === 'failed' ? 'failed' : 'passed',
        issue: kind === 'failed' ? newest.code : undefined,
        detail: kind === 'failed'
          ? `The installer stopped at ${newest.stage || 'a stage'}: ${newest.detail || newest.code}`
          : `The installer reported ${newest.code}${newest.hostname ? ` from ${newest.hostname}` : ''}.`,
        rows: attempts.slice(0, 5).map(attemptRow),
      }
    },
  }

  const service = {
    id: `${path}.service`,
    title: 'Confirm the service',
    why: {
      linux: 'The beacon installs as a systemd unit, so it survives the SSH session and a reboot. With no systemd it falls back to a background process and says so.',
      macos: 'The beacon installs as a launchd job, so it survives logout and a reboot.',
      windows: 'The beacon installs as a Windows service running as SYSTEM.',
    }[path],
    where: 'target host',
    verifier: 'pov',
    command: { linux: 'systemctl status cortexsim-agent', macos: 'sudo launchctl list | grep cortexsim', windows: 'Get-Service cortexsim-agent' }[path],
    test: async ({ shared }) => {
      if (!shared.token) return needToken
      const { unsupported, attempts } = await attemptsForToken(shared)
      if (unsupported) return { state: 'cant_check', detail: 'This POVengine does not record installer reports.' }
      const bare = attempts.find((a) => NO_SUPERVISOR.has(String(a.code).toUpperCase()))
      if (bare) {
        return {
          state: 'degraded',
          issue: bare.code,
          detail: CONTINUED_NOTE[String(bare.code).toUpperCase()] || bare.detail,
          rows: attempts.slice(0, 5).map(attemptRow),
        }
      }
      if (attempts.some((a) => String(a.code).toUpperCase() === 'OK')) {
        return { state: 'passed', detail: 'The installer finished without reporting a missing supervisor.', rows: attempts.slice(0, 5).map(attemptRow) }
      }
      return { state: 'pending', detail: 'No finished install reported since the token was created.' }
    },
  }

  const accounts = path === 'windows'
    ? {
      id: 'windows.identity',
      title: 'Know the identity limit',
      why: 'Windows cannot switch to another account without its password, so every step runs as SYSTEM. POVengine writes "identity not honoured" on the run record instead of pretending.',
      verifier: 'you',
      fields: [['Steps run as', 'NT AUTHORITY\\SYSTEM']],
    }
    : {
      id: `${path}.accounts`,
      title: 'Check the service accounts',
      why: path === 'macos'
        ? 'Steps that name a service account run through sudo -u. The accounts must exist on the Mac. No beacon reports a host\'s accounts, so this one takes your word.'
        : 'Steps run as real service accounts (www-data, postgres, svc-backup) so Cortex sees a realistic process chain. The accounts must exist on the host. No beacon reports a host\'s accounts, so this one takes your word.',
      where: 'target host',
      verifier: 'you',
      fields: [['Method', path === 'macos' ? 'sudo -u <account>' : 'runuser -l <account>']],
      command: path === 'macos' ? 'id _www' : 'id www-data postgres svc-backup',
    }

  const online = {
    id: `${path}.online`,
    title: 'Confirm it is online',
    why: 'The agent appears in Fleet within one poll. POVengine reads this from its own agent list.',
    verifier: 'pov',
    derive: ({ env }) => {
      const mine = (env.agents || []).filter((a) => pathOfAgent(a) === path)
      if (!mine.length) return null
      const up = mine.filter((a) => agentLiveness(a).state === LIVENESS.ONLINE)
      if (up.length) return { state: 'passed', detail: `${up.map((a) => a.hostname || a.agent_id).join(', ')} online.` }
      return { state: 'failed', issue: 'Not online', detail: `${mine.length} ${label} agent${mine.length === 1 ? '' : 's'} enrolled, none polling.` }
    },
    test: async ({ env }) => {
      const list = await getAgents()
      env.refreshAgents && env.refreshAgents()
      const mine = list.filter((a) => pathOfAgent(a) === path)
      const up = mine.filter((a) => agentLiveness(a).state === LIVENESS.ONLINE)
      if (up.length) return { state: 'passed', detail: `${up.map((a) => a.hostname || a.agent_id).join(', ')} online.` }
      return mine.length
        ? { state: 'failed', issue: 'Not online', detail: `${mine.length} ${label} agent(s) enrolled, none polling.` }
        : { state: 'pending', detail: `No ${label} agent has enrolled yet.` }
    },
  }

  return [...common, install, service, accounts, online]
}

export default function AgentsComponent({ params = {}, setParams = () => {}, onNavigate = () => {} }) {
  const env = useEnvironment()
  const { statuses } = useComponentStatus()
  const meta = componentByKey(KEY)
  const path = PATHS.some(([p]) => p === params.path) ? params.path : 'linux'
  const origin = typeof window !== 'undefined' ? window.location.origin : ''
  const steps = useMemo(() => buildSteps(path, origin), [path, origin])
  const agents = useMemo(() => env.agents || [], [env.agents])

  const services = agents.map((a) => {
    const live = agentLiveness(a)
    const look = LIVE_STATUS[live.state] || LIVE_STATUS[LIVENESS.UNKNOWN]
    const p = pathOfAgent(a)
    return {
      id: a.agent_id || a.hostname,
      name: a.hostname || a.agent_id,
      status: look.status,
      since: `${a.os || 'unknown OS'} · ${seenText(live)}`,
      drill: look.status === 'up' ? { tab: 'fleet' } : { tab: 'setup', step: `${p}.online`, path: p },
    }
  })

  const online = agents.filter((a) => agentLiveness(a).state === LIVENESS.ONLINE)
  const binaries = (env.healthModel?.components || []).find((c) => c.key === 'agent_binaries')
  const config = [
    {
      id: 'online',
      label: 'An agent is online',
      state: online.length ? 'ok' : 'missing',
      detail: online.length ? `${online.length} of ${agents.length} polling` : agents.length ? 'None polling' : 'None enrolled',
      drill: { tab: 'setup', step: `${path}.online` },
    },
    {
      id: 'binaries',
      label: 'This POVengine serves the beacon for each target',
      state: !binaries || binaries.status === 'unreported' ? 'cant_check' : binaries.status === 'ok' ? 'ok' : 'missing',
      detail: binaries?.detail || (binaries ? undefined : 'Not reported by /api/health'),
      drill: { tab: 'setup', step: `${path}.install` },
    },
    {
      id: 'service',
      label: 'Each beacon runs as a service',
      state: 'cant_check',
      detail: 'POVengine sees the polls, not the host\'s service manager',
      drill: { tab: 'setup', step: `${path}.service` },
    },
    {
      id: 'accounts',
      label: 'Service accounts exist on each host',
      state: 'cant_check',
      detail: 'No beacon reports a host\'s accounts',
      drill: path === 'windows' ? { tab: 'setup', step: 'windows.identity' } : { tab: 'setup', step: `${path}.accounts` },
    },
  ]

  const onTest = useCallback(async () => {
    const list = await getAgents()
    env.refreshAgents && env.refreshAgents()
    const up = list.filter((a) => agentLiveness(a).state === LIVENESS.ONLINE)
    if (up.length) return { state: 'passed' }
    return { state: 'failed', issue: list.length ? 'None online' : 'No agent enrolled', step: list.length ? `${path}.online` : 'token' }
  }, [env, path])

  return (
    <ComponentPage
      componentKey={KEY}
      name={meta.name}
      icon={meta.icon}
      status={statuses[KEY]}
      issueStep={`${path}.online`}
      services={services}
      servicesEmpty="No agent has enrolled."
      config={config}
      manageTab={{
        id: 'fleet',
        label: 'Fleet',
        render: () => <TargetsView embedded onGoToLab={() => onNavigate('environments')} />,
      }}
      setup={() => (
        <ConfigureFrame
          confirmPrefix={KEY}
          steps={steps}
          current={params.step || null}
          onSelect={(step) => setParams({ step })}
          pathPicker={{ label: 'Target runs', options: PATHS, value: path, onChange: (p) => setParams({ path: p, step: null }) }}
          ctx={{ env, origin }}
        />
      )}
      onTest={onTest}
      params={params}
      setParams={setParams}
      onNavigate={onNavigate}
      testId="component-agents"
    />
  )
}
