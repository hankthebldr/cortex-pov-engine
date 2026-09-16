/**
 * Scope refinement: environment constraints and the resource bill of materials.
 *
 * SEED CATALOG — carried over verbatim from the design prototype
 * (`POVengine Console.dc.html`, Claude Design project "Cortex POV Engine UI
 * Redesign"; see docs/design/DESIGN-SYNC.md).
 *
 * These are the authored corpus values the design was drawn against. Where
 * SimCore serves the same shape over the API, the surface prefers the API and
 * falls back to this; where it does not serve it yet, this IS the model, and
 * its shape is what the endpoint should eventually return. Do not "improve"
 * values here to make a screen look better — they are the numbers the design
 * conversation settled on, and several of them (12 components / 4 ready / 6
 * partial / 2 missing, 21 of 34 stream shapes, 6 of 9 datasets readable) are
 * quoted on more than one surface precisely so those surfaces cannot disagree.
 * Every tally is DERIVED from these rows, never restated as a literal.
 */

export function constraintRows(state) {
  const t = state.wizTargets, d = state.wizDets
  const need = (k) => k === 'always' || t.indexOf(k) >= 0 || d.indexOf(k) >= 0
  return [
    ['C-01', 'Windows endpoints run a Block prevention profile',
      'win-dc-01 · prevention profile "Corporate-Strict" · Report only elsewhere', ['AGT'], 'host',
      'Ask the customer to set the test host to Report only. Windows credential-theft claims stay in scope and become provable once changed.',
      'Prove the same technique on jumpbox-lin-01 and mac-dc-07 instead. The report claims Linux and macOS credential access, and states Windows was not exercised.',
      'Windows endpoint claims leave this POV. The readout carries no Windows credential-access assertion at all.'],
    ['C-02', 'An agent exclusion covers the artifact staging path',
      'Exclusion "tmp-build-cache" matches /tmp/** on all Linux hosts', ['AGT'], 'host',
      'Ask for a narrow exemption on the lab path. Tool-drop and staging BIOCs stay in scope.',
      'Stage artifacts under /var/lib/pov instead, outside the exclusion. Same claim, different path — noted in the report.',
      'Drop tool-drop BIOC claims. The POV proves execution but not the drop that preceded it.'],
    ['C-03', 'The NGFW blocks the test zone outright',
      'Security profile "Outbound-Strict" · test zone · no alert-only rule', ['BVM'], 'net',
      'Ask for an alert-only rule scoped to the test zone. Egress observed on the wire becomes provable.',
      'Prove egress against the lab DNS sink instead of through the NGFW. Real tunnel behaviour, lab path — the report says which.',
      'The network plane leaves scope. Egress is claimed only as host-side behaviour, never as observed traffic.'],
    ['C-04', 'Correlation content is installed but not enabled',
      '2 rules present, both disabled · corr-edr-001, corr-cdr-014', ['ENG'], 'Correlation',
      'Ask for the two rules to be enabled before the run. The cross-plane stitch becomes provable.',
      'Prove each half separately — endpoint theft and cloud use — and state plainly that the stitch was not demonstrated.',
      'Correlation claims leave scope. The POV proves the planes independently and claims no joined story.'],
    ['C-05', 'The Azure connector is not enrolled',
      'acme-azure-01 · no Cortex Cloud Connector', ['CC'], 'cloud',
      'Ask for connector enrolment on the Azure subscription. All three cloud environments come into scope.',
      'Prove the cloud plane on AWS and GCP only. Azure is listed as not covered rather than not detected.',
      'Cloud environments narrow to AWS and GCP. Azure does not appear in the readout.'],
    ['C-06', 'Seven third-party stream shapes have no emitter',
      '21 of 34 shapes covered · 7 authored but unemitted', ['DC'], 'stream',
      'Author the seven missing emitters before the run. Full stream coverage becomes claimable.',
      'Claim only the 21 covered shapes. The coverage table shows the remaining 7 as gaps, not as failures.',
      'Third-party streams leave scope entirely. No data-plane claim is made.'],
    ['C-07', 'No HTTP collector exists on the Broker VM',
      'Syslog intake present on :514 · no HTTP/JSON endpoint', ['BVM', 'DC'], 'net',
      'Ask for an HTTP collector on the BVM. Push-style third-party events come into scope.',
      'Send the same events through syslog intake instead. Shape differs from production, and the report records that.',
      'Push-style stream claims leave scope. Only syslog-shaped sources are exercised.'],
    ['C-08', 'Tenant alert exclusions match the lab host',
      '2 suppression rules match jumpbox-lin-01', ['ENG'], 'always',
      'Ask for the lab host to be exempted from suppression. Alert claims become trustworthy.',
      'Target mac-dc-07, which no suppression rule covers. Same claims, different host.',
      'Accept that endpoint alerts may be suppressed. Every endpoint verdict in the readout carries that caveat.'],
  ].map(([id, label, found, lanes, gate, prove, around, drop]) => {
    const choice = (state.scopeChoice || {})[id] || 'prove'
    return {
      id, label, found, lanes, choice, needed: need(gate),
      effects: { prove, around, drop },
    }
  })
}

export function resources(state) {
  const t = state.wizTargets, d = state.wizDets
  const need = (k) => k === 'always' || t.indexOf(k) >= 0 || d.indexOf(k) >= 0
  return [
    ['Lab infrastructure', 'what you stand up in the customer or lab environment', [
      ['SimCore host', 'jumpbox / VM', 'Runs POVengine itself and serves the staged artifact shelf. Dies with the lab.', 'simcore-01', 'ready', 'always'],
      ['Cortex Agent / workload', 'endpoint beacon', 'Executes command steps and produces endpoint-plane telemetry.', '4 enrolled', 'ready', 'host'],
      ['Kubernetes workload node', 'container host', 'Container runtime steps: pods/exec, secret reads, node process lineage.', 'k8s-node-3', 'offline', 'host'],
      ['Broker VM (BVM)', 'appliance', 'Carries syslog and third-party streams into the tenant on the network plane.', 'broker-relay-01', 'partial', 'net'],
      ['HTTP collector', 'BVM collector', 'HTTP/JSON push endpoint for third-party events that have no syslog form.', 'not created', 'missing', 'net'],
      ['Syslog collector', 'BVM collector', 'UDP/TCP 514 intake for appliance and network device logs.', 'bvm :514', 'ready', 'net'],
      ['EAL emitter host', 'simulator', 'Emits external activity logs at customer-true shape and rate.', 'eal-collector', 'ready', 'stream'],
      ['Cloud environment', 'AWS / GCP / Azure', 'Cloud-plane targets: role assumption, object access, control-plane calls.', '3 environments', 'partial', 'cloud'],
      ['DNS sink', 'lab collector', 'Captures tunnel and entropy traffic for network-plane proof.', 'dns-sink-01', 'ready', 'net'],
    ]],
    ['Cortex integrations', 'what has to exist on the Cortex side', [
      ['XSIAM / XDR tenant', 'tenant', 'Where all signal is judged. Read-only API credential, never writes.', 'acme-prod.xdr.us', 'ready', 'always'],
      ['Cortex API key', 'API · read-only', 'Reads alerts, incidents and datasets for validation probes.', 'scoped: alerts', 'partial', 'always'],
      ['Cortex Agent install package', 'distribution profile', 'Per-OS installer and profile used to enroll each target.', '3 platforms', 'ready', 'host'],
      ['Cortex Cloud Connector', 'agentless onboarding', 'Ingests cloud audit and runtime signal with no beacon on the workload.', '2 of 3 live', 'partial', 'cloud'],
      ['Cortex Data Connector', 'third-party ingest', 'Normalises third-party streams so they score like native telemetry.', '21 of 34 shapes', 'partial', 'stream'],
      ['Broker VM registration', 'activation token', 'Binds the BVM to the tenant before any relay can forward.', 'registered', 'ready', 'net'],
      ['Engine content packs', 'BIOC / correlation content', 'The detection objects the run expects to fire must be installed.', '2 missing', 'missing', 'Correlation'],
      ['Marketplace indicator pack', 'content pack', 'Atomic indicator matching for IOC-class claims.', 'not installed', 'missing', 'IOC'],
      ['Cortex BVM / API integrations', 'marketplace integrations', 'Vendor integrations that carry third-party detections into the engine.', '4 enabled', 'ready', 'stream'],
    ]],
  ].map(([label, sub, rows]) => ({
    label, sub,
    rows: rows.map(([name, kind, purpose, instance, state, gate]) => ({
      name, kind, purpose, instance, needed: need(gate), state,
    })),
  }))
}
