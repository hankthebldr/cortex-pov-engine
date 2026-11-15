import React, { useCallback, useEffect, useMemo, useState } from 'react'
import '../../../styles/destinations/readiness.css'
import '../../../styles/destinations/firewall.css'
import { useEnvironment } from '../../../context/EnvironmentContext.jsx'
import { firewallPreflight } from '../../../api/client.js'
import { serverDate } from '../../../api/time.js'
import ComponentPage from './ComponentPage.jsx'
import ConfigureFrame from './ConfigureFrame.jsx'
import ConnectStep from '../firewall/ConnectStep.jsx'
import SourcesStep from '../firewall/SourcesStep.jsx'
import useComponentStatus, { currentComponentSources } from '../useComponentStatus.js'
import { componentByKey } from '../componentModel.js'
import { answered, configState, headerResult, reportResult, stageRow } from './checks.js'

/**
 * Firewall — a PAN-OS NGFW, read-only (design v26 §4.2, slice 3).
 *
 * POVengine NEVER writes to the firewall. The design's `pov` steps here mean
 * CHECK, not apply (v26 §10.6): each runs the read-only preflight
 * (`show`/`test` commands and config reads; policy lookups send no packets)
 * and reports the stages that step is about. Steps that only Cortex or the
 * DC can see — the NGFW entry in Cortex, the certificate, logs arriving in the
 * tenant — have no read here, so the DC ticks them and they read "You
 * confirmed".
 *
 * One path: the firewall streams its logs through Strata Logging Service and
 * Cortex reads them as panw_ngfw_* datasets (Data Sources & Integrations ›
 * NGFW). That is the data a POV is after. Syslog through a Broker VM is the
 * fallback the Cortex docs keep for when that collector can't be used, so
 * this page does not walk it. The rest of the setup is the firewall's own policy: what it
 * logs, where it forwards, and whether its profiles alert rather than block.
 *
 * Overview rows come from the last check this session ran; without one they
 * read Can't check. Sources: today's per-agent Sources step, fed by that same
 * check. The registry stores only "blocked or not", so between checks the
 * header can say Down or "Not blocked", never Up.
 */

const KEY = 'ngfw'
// The per-agent stages that read a rule: which one the agent's traffic meets
// (its action, log at session end, log forwarding) and its security profiles.
const AGENT_POLICY_STAGES = ['policy_match', 'rule_profiles']

export default function FirewallComponent({ params = {}, setParams = () => {}, onNavigate = () => {} }) {
  const env = useEnvironment()
  const { statuses, sources, refresh } = useComponentStatus()
  const meta = componentByKey(KEY)
  const firewalls = useMemo(() => (Array.isArray(sources.firewalls) ? sources.firewalls : []), [sources.firewalls])
  const [selected, setSelected] = useState(null)
  const [report, setReport] = useState(null)

  useEffect(() => {
    setSelected((cur) => (cur && firewalls.some((f) => f.name === cur) ? cur : (firewalls[0]?.name ?? null)))
  }, [firewalls])

  // A report belongs to the firewall it ran against.
  const reportFor = report && (!report.firewall || report.firewall === selected) ? report : null

  // Every Test here is the same read-only check, filtered to its stages.
  const runCheck = useCallback(async () => {
    let name = selected
    if (!name) {
      await refresh()
      name = currentComponentSources().firewalls?.[0]?.name || null
    }
    if (!name) return null
    const r = await firewallPreflight(name)
    setReport(r)
    refresh()
    return r
  }, [selected, refresh])

  const noFirewall = { state: 'pending', detail: 'Register a firewall first (step 1).' }
  const stageTest = useCallback((ids, step) => async () => {
    const r = await runCheck()
    if (!r) return noFirewall
    return reportResult(r, { ids, step })
  }, [runCheck]) // eslint-disable-line react-hooks/exhaustive-deps

  const steps = useMemo(() => {
    const common = [
      {
        id: 'register',
        title: 'Register read-only',
        why: 'Create an admin role limited to XML API read, and register the firewall with its key. POVengine never writes to the firewall.',
        where: 'Device › Admin Roles',
        verifier: 'pov',
        body: () => (
          <ConnectStep firewalls={sources.firewalls} loadError={sources.firewallError} selected={selected}
            onSelect={(n) => { if (n !== selected) setReport(null); setSelected(n) }} onChanged={refresh} />
        ),
        derive: () => (firewalls.length ? { state: 'passed', detail: `Registered: ${firewalls.map((f) => f.name).join(', ')}` } : null),
        test: stageTest(['config', 'dns_tls', 'auth', 'system'], 'register'),
      },
    ]
    const toCortex = [
      {
        id: 'cortex',
        title: 'Add the firewall in Cortex',
        why: 'This is the data path: the firewall streams its logs through Strata Logging Service, and Cortex reads them as the panw_ngfw_* datasets and stitches them with the endpoint into network_story. Add the device and select every log type. Panorama needs its Cloud Services plugin. Syslog through a Broker VM is the fallback the Cortex docs keep for when this collector cannot be used.',
        where: 'Cortex › Settings › Data Sources & Integrations › + Add New › NGFW',
        verifier: 'cortex',
        fields: [['Add', 'NGFW Device (or Panorama Device)'], ['Log types', 'Select all — at least Traffic, Threat, URL, File Data']],
      },
      {
        id: 'logging',
        title: 'Turn on cloud logging',
        why: 'Pull the cloud logging licence on the firewall first. Enhanced Application Logging is the extra network data Cortex analytics read. Turn on duplicate logging if Panorama keeps its own copy. Test reads both settings, whether a log collector connection is Active, and when Traffic and Threat logs were last forwarded.',
        where: 'Device › Setup › Management › Cloud Logging',
        verifier: 'pov',
        fields: [['Enable Strata Logging Service', 'on'], ['Enable Enhanced Application Logging', 'on'], ['Duplicate Logging', 'on, if Panorama also logs']],
        test: stageTest(['forwarding_sls', 'eal_device'], 'logging'),
      },
      {
        id: 'cert',
        title: 'Install the certificate',
        why: 'PAN-OS and Panorama 10.1 and later need one certificate per firewall, requested through the Customer Support portal (the NGFW entry in Cortex also offers Request Certificate); 10.0 and earlier take one global PSK. Cortex does not validate it, and approval can take an hour.',
        where: 'Customer Support portal · Device › Setup › Management',
        verifier: 'you',
        fields: [['PAN-OS 10.1 and later', 'one certificate per firewall'], ['PAN-OS 10.0 and earlier', 'one global PSK']],
      },
      {
        id: 'forward',
        title: 'Forward logs to Strata Logging Service',
        why: 'A log forwarding profile whose match lists send Traffic, Threat, URL, WildFire and Data logs to Strata Logging Service, unfiltered. Without it a rule logs locally and nothing reaches Cortex. Test reads every log forwarding profile.',
        where: 'Objects › Log Forwarding',
        verifier: 'pov',
        fields: [['Profile', 'pov-to-cortex'], ['Log types', 'traffic · threat · url · wildfire · data'], ['Forward to', 'Strata Logging Service']],
        test: stageTest(['log_forwarding_profiles'], 'forward'),
      },
      {
        id: 'rule',
        title: 'Log and alert on the POV traffic',
        why: 'Every security rule the POV agents\' traffic meets must log at session end through that profile, with its security profiles set to alert: a step the firewall blocks must not read as a missed detection. A test zone holding only POV agents and one allow rule is the simplest way. You make this change; Test looks up the rule each enrolled agent\'s traffic meets.',
        where: 'Policies › Security',
        verifier: 'pov',
        fields: [
          ['Rule', 'pov-alert-only · allow'],
          ['Actions', 'Log at Session End · Log Forwarding pov-to-cortex'],
          ['Profile group', 'pov-alert · antivirus · anti-spyware (DNS) · vulnerability · URL · WildFire → alert'],
        ],
        command: 'set rulebase security rules pov-alert-only from pov-test to any source any destination any application any service application-default action allow log-end yes log-setting pov-to-cortex profile-setting group pov-alert',
        test: async () => {
          const r = await runCheck()
          if (!r) return noFirewall
          if (!answered(r)) return reportResult(r)
          const rows = (r.sources || []).filter((s) => s && s.agent_id).flatMap((s) => (s.stages || [])
            .filter((st) => AGENT_POLICY_STAGES.includes(st.id))
            .map((st) => stageRow(st, `${s.agent_id} · ${st.id}`)))
          if (!rows.length) return { state: 'cant_check', detail: 'No enrolled agent to look the rule up for — enroll one, then Test again.' }
          const bad = rows.find((x) => x.state === 'failed') || rows.find((x) => x.state === 'degraded')
          return { state: bad ? bad.state : 'passed', issue: bad ? bad.label : undefined, rows, step: 'rule' }
        },
      },
      {
        id: 'decrypt',
        title: 'Decrypt the POV traffic',
        why: 'Without decryption the firewall inspects only what TLS leaves in the clear, so threat and file verdicts on HTTPS steps cannot fire. Test reads the decryption rules.',
        where: 'Policies › Decryption',
        verifier: 'pov',
        fields: [['Rule', 'pov-test → any · decrypt'], ['Type', 'SSL Forward Proxy']],
        test: stageTest(['decryption'], 'decrypt'),
      },
      {
        id: 'arrive',
        title: 'Commit, then confirm logs arrive',
        why: 'Commit (and push from Panorama), then send traffic from a POV agent. The NGFW entry in Cortex shows each device\'s Last communication, and these queries show its logs and the stitched story. POVengine has no read of the tenant\'s firewall datasets yet.',
        where: 'Cortex › XQL',
        verifier: 'cortex',
        fields: [
          ['System logs', 'dataset = panw_ngfw_system_raw | filter log_source_id = "<serial>"'],
          ['Traffic', 'dataset = panw_ngfw_traffic_raw | limit 5'],
          ['Stitched', 'preset = network_story | limit 5'],
        ],
      },
    ]
    return [...common, ...toCortex]
  }, [firewalls, sources.firewalls, sources.firewallError, selected, refresh, runCheck, stageTest]) // eslint-disable-line react-hooks/exhaustive-deps

  const services = firewalls.map((f) => {
    const mine = reportFor && reportFor.firewall === f.name && answered(reportFor) ? reportFor : null
    const at = serverDate(f.last_verified_at)
    const since = at ? `last check ${at.toLocaleString()}` : 'never checked'
    if (mine) {
      const status = mine.overall === 'ok' ? 'up' : mine.overall === 'degraded' ? 'degraded' : 'down'
      return { id: f.name, name: `Management API · ${f.name}`, status, since: `checked this session · ${mine.overall}`, drill: status === 'up' ? { tab: 'sources' } : { tab: 'setup', step: 'register' } }
    }
    if (f.last_verified_ok === false) {
      return { id: f.name, name: `Management API · ${f.name}`, status: 'down', since: `${since} — blocked${f.last_verified_error ? ` (${f.last_verified_error})` : ''}`, drill: { tab: 'setup', step: 'register' } }
    }
    return {
      id: f.name,
      name: `Management API · ${f.name}`,
      status: 'unknown',
      word: f.last_verified_ok === true ? 'Not blocked' : undefined,
      since,
      drill: { tab: 'setup', step: 'register' },
    }
  })

  // The rule the POV traffic meets is read per enrolled agent, so its row
  // reads those agents' stages, not the firewall-wide ones.
  const agentStages = (reportFor?.sources || []).filter((x) => x && x.agent_id).flatMap((x) => x.stages || [])
  const policyRow = reportFor && answered(reportFor) && !agentStages.some((st) => AGENT_POLICY_STAGES.includes(st.id))
    ? { state: 'cant_check', detail: 'No enrolled agent to look the rule up for' }
    : configState(reportFor && { ...reportFor, stages: agentStages }, AGENT_POLICY_STAGES)
  const config = [
    {
      id: 'registered',
      label: 'A firewall is registered',
      state: firewalls.length ? 'ok' : 'missing',
      detail: firewalls.length ? firewalls.map((f) => f.name).join(', ') : 'None registered',
      drill: { tab: 'setup', step: 'register' },
    },
    { id: 'sls', label: 'Logs forwarded to Strata Logging Service', ...configState(reportFor, ['forwarding_sls']), drill: { tab: 'setup', step: 'logging' } },
    { id: 'eal', label: 'Enhanced Application Logging', ...configState(reportFor, ['eal_device']), drill: { tab: 'setup', step: 'logging' } },
    { id: 'lfp', label: 'Log forwarding profile sends every log type', ...configState(reportFor, ['log_forwarding_profiles']), drill: { tab: 'setup', step: 'forward' } },
    { id: 'policy', label: 'POV traffic logs and alerts, not blocks', ...policyRow, drill: { tab: 'setup', step: 'rule' } },
    { id: 'decryption', label: 'Decryption', ...configState(reportFor, ['decryption']), drill: { tab: 'setup', step: 'decrypt' } },
    { id: 'clock', label: 'Firewall clock in step with POVengine', ...configState(reportFor, ['clock']), drill: { tab: 'setup', step: 'register' } },
    {
      id: 'arrive',
      label: 'Logs reach Cortex',
      state: 'cant_check',
      detail: 'POVengine has no read of the tenant\'s firewall datasets yet',
      drill: { tab: 'setup', step: 'arrive' },
    },
  ]

  // A check this session beats the stored "blocked or not".
  let status = statuses[KEY]
  if (reportFor && answered(reportFor)) {
    const r = headerResult(reportFor)
    status = r.state === 'passed'
      ? { status: 'up', detail: `${reportFor.firewall}: every stage ok in this session's check` }
      : { status: r.state === 'failed' ? 'down' : 'degraded', issue: r.issue, detail: r.detail || `${reportFor.firewall}: ${reportFor.overall}` }
  }

  const onTest = useCallback(async () => {
    const r = await runCheck()
    if (!r) return { state: 'failed', issue: 'No firewall registered', step: 'register' }
    return headerResult(r, { step: 'register' })
  }, [runCheck])

  return (
    <ComponentPage
      componentKey={KEY}
      name={meta.name}
      icon={meta.icon}
      status={status}
      issueStep="register"
      services={services}
      servicesEmpty="No firewall is registered."
      config={config}
      manageTab={{
        id: 'sources',
        label: 'Sources',
        render: () => <SourcesStep firewall={selected} report={reportFor} />,
      }}
      setup={() => (
        <ConfigureFrame
          confirmPrefix={KEY}
          steps={steps}
          current={params.step || null}
          onSelect={(step) => setParams({ step })}
          ctx={{ env }}
        />
      )}
      onTest={onTest}
      params={params}
      setParams={setParams}
      onNavigate={onNavigate}
      testId="component-firewall"
    />
  )
}
