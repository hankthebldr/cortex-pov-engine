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
 * DC can see — the Broker VM's format, logs arriving in the tenant — have no
 * read here, so the DC ticks them and they read "You confirmed".
 *
 * Overview rows come from the last check this session ran; without one they
 * read Can't check. Sources: today's per-agent Sources step, fed by that same
 * check. The registry stores only "blocked or not", so between checks the
 * header can say Down or "Not blocked", never Up.
 */

const KEY = 'ngfw'
const PATHS = [['syslog', 'Through the Broker VM (syslog, CEF)'], ['sls', 'Through Strata Logging Service']]

function defaultPath(fw) {
  return fw?.forwarding_path === 'sls' ? 'sls' : 'syslog'
}

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

  const fw = firewalls.find((f) => f.name === selected) || null
  const path = PATHS.some(([p]) => p === params.path) ? params.path : defaultPath(fw)
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
      {
        id: 'rule',
        title: 'Create the POV test rule',
        why: 'A test zone holding only POV agents, and a rule that allows with security profiles set to alert — a blocked step must not read as a missed detection. You make this change; Test reads the rule each enrolled agent\'s traffic meets.',
        where: 'Policies › Security',
        verifier: 'pov',
        fields: [['Zone', 'pov-test'], ['Rule', 'pov-alert-only · allow'], ['Profile group', 'pov-alert · virus · spyware · vulnerability → alert']],
        command: 'set rulebase security rules pov-alert-only from pov-test to any action allow',
        test: async () => {
          const r = await runCheck()
          if (!r) return noFirewall
          if (!answered(r)) return reportResult(r)
          const rows = (r.sources || []).filter((s) => s && s.agent_id).flatMap((s) => (s.stages || [])
            .filter((st) => st.id === 'policy_match' || st.id === 'rule_profiles')
            .map((st) => stageRow(st, `${s.agent_id} · ${st.id}`)))
          if (!rows.length) return { state: 'cant_check', detail: 'No enrolled agent to look the rule up for — enroll one, then Test again.' }
          const bad = rows.find((x) => x.state === 'failed') || rows.find((x) => x.state === 'degraded')
          return { state: bad ? bad.state : 'passed', issue: bad ? bad.label : undefined, rows, step: 'rule' }
        },
      },
    ]
    const syslog = [
      {
        id: 'syslog.hostname',
        title: 'Set the syslog host name format',
        why: 'The Broker VM keys each source by the host name the firewall sends, so use the address.',
        where: 'Device › Setup › Management › Logging and Reporting Settings › Log Export and Reporting',
        verifier: 'you',
        fields: [['Syslog HOSTNAME Format', 'ipv4-address']],
      },
      {
        id: 'syslog.profile',
        title: 'Add a syslog server profile',
        why: 'Point it at the Broker VM and paste the CEF custom log format for Traffic, Threat, URL and File Data (remove line breaks first). Test reads the syslog forwarding the firewall has configured.',
        where: 'Device › Server Profiles › Syslog › Add',
        verifier: 'pov',
        fields: [['Syslog server', 'the Broker VM address'], ['Transport / port', 'UDP 514'], ['Facility', 'LOG_USER']],
        test: stageTest(['forwarding_syslog'], 'syslog.profile'),
      },
      {
        id: 'syslog.forward',
        title: "Forward the rule's logs",
        why: 'Create a log forwarding profile that uses the syslog profile, attach it to pov-alert-only, and commit. Test reads the log forwarding profiles.',
        where: 'Objects › Log Forwarding',
        verifier: 'pov',
        fields: [['Profile', 'pov-to-cortex'], ['Attached to', 'pov-alert-only']],
        test: stageTest(['log_forwarding_profiles'], 'syslog.forward'),
      },
      {
        id: 'syslog.format',
        title: "Match the broker's format",
        why: 'On the Broker VM\'s Syslog Collector, set the firewall source\'s format to CEF. POVengine has no read of the Broker VM.',
        where: 'Cortex › Broker VMs › APPS › Syslog Collector',
        verifier: 'cortex',
        fields: [['Format', 'CEF']],
      },
      {
        id: 'syslog.arrive',
        title: 'Confirm logs arrive',
        why: 'Generate traffic from a POV agent and look for it in Cortex. POVengine has no read of the tenant\'s firewall datasets yet.',
        where: 'Cortex › XQL',
        verifier: 'cortex',
        fields: [['Query', 'dataset = panw_ngfw_traffic_raw | limit 5']],
      },
    ]
    const sls = [
      {
        id: 'sls.add',
        title: 'Add the firewall in Cortex',
        why: 'Add an NGFW device, choose the log types to ingest, then Connect. Only tenants that already have Strata Logging Service can use this path.',
        where: 'Cortex › Data Sources & Integrations › + Add New › NGFW',
        verifier: 'cortex',
        fields: [['Log types', 'Traffic · Threat · URL · File Data']],
      },
      {
        id: 'sls.logging',
        title: 'Turn on cloud logging',
        why: 'Enable Strata Logging Service and Enhanced Application Logging. Test reads both settings.',
        where: 'Device › Setup › Management › Cloud Logging',
        verifier: 'pov',
        fields: [['Strata Logging Service', 'on'], ['Enhanced Application Logging', 'on']],
        test: stageTest(['forwarding_sls', 'eal_device'], 'sls.logging'),
      },
      {
        id: 'sls.cert',
        title: 'Install the certificate',
        why: 'PAN-OS 10.1 and later: one certificate per firewall, from the Customer Support portal. Cortex does not validate it.',
        where: 'Customer Support portal',
        verifier: 'you',
        fields: [['Certificate', 'one per firewall']],
      },
      {
        id: 'sls.forward',
        title: "Forward the rule's logs",
        why: 'Map a log forwarding profile to pov-alert-only, then commit and push. Test reads the log forwarding profiles.',
        where: 'Objects › Log Forwarding',
        verifier: 'pov',
        fields: [['Profile', 'pov-to-cortex'], ['Attached to', 'pov-alert-only']],
        test: stageTest(['log_forwarding_profiles'], 'sls.forward'),
      },
      {
        id: 'sls.arrive',
        title: 'Confirm logs arrive',
        why: 'The NGFW entry in Cortex shows the device status; the firewall shows its last communication. POVengine has no read of either yet.',
        where: 'Cortex › XQL',
        verifier: 'cortex',
        fields: [['Query', 'dataset = panw_ngfw_system_raw | filter log_source_id = "<serial>"']],
      },
    ]
    return [...common, ...(path === 'sls' ? sls : syslog)]
  }, [path, firewalls, sources.firewalls, sources.firewallError, selected, refresh, runCheck, stageTest]) // eslint-disable-line react-hooks/exhaustive-deps

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

  const forwardPathIds = path === 'sls' ? ['forwarding_sls'] : ['forwarding_syslog']
  const config = [
    {
      id: 'registered',
      label: 'A firewall is registered',
      state: firewalls.length ? 'ok' : 'missing',
      detail: firewalls.length ? firewalls.map((f) => f.name).join(', ') : 'None registered',
      drill: { tab: 'setup', step: 'register' },
    },
    { id: 'lfp', label: "Log forwarding profile on the POV rule", ...configState(reportFor, ['log_forwarding_profiles']), drill: { tab: 'setup', step: `${path}.forward` } },
    {
      id: 'path',
      label: path === 'sls' ? 'Strata Logging Service forwarding' : 'Syslog forwarding to the Broker VM',
      ...configState(reportFor, forwardPathIds),
      drill: { tab: 'setup', step: path === 'sls' ? 'sls.logging' : 'syslog.profile' },
    },
    { id: 'eal', label: 'Enhanced Application Logging', ...configState(reportFor, ['eal_device']), drill: { tab: 'setup', step: 'sls.logging', path: 'sls' } },
    { id: 'clock', label: 'Firewall clock in step with POVengine', ...configState(reportFor, ['clock']), drill: { tab: 'setup', step: 'register' } },
    {
      id: 'arrive',
      label: 'Logs reach Cortex',
      state: 'cant_check',
      detail: 'POVengine has no read of the tenant\'s firewall datasets yet',
      drill: { tab: 'setup', step: `${path}.arrive` },
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
          pathPicker={{ label: 'Logs reach Cortex', options: PATHS, value: path, onChange: (p) => setParams({ path: p, step: null }) }}
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
