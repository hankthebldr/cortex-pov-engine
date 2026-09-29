/**
 * sheets.js — builds an ObjectSheet for any object kind.
 *
 * THE FALL-THROUGH IS GUARDED, AND THAT IS THE POINT OF THIS MODULE.
 * An earlier revision had one unguarded scenario builder that every unknown
 * kind fell into, ending in `|| SCENARIOS[0]`. So opening a TTP card or a data
 * stream rendered SIM-EDR-001 — not an error, not an empty state, but a
 * confident, complete, wrong object page. Two separate bugs shipped that way
 * (ttp/stream, then tenant) before the cause was fixed rather than the
 * symptoms.
 *
 * Here, ONLY `kind === 'scenario'` builds a scenario sheet. Every other kind
 * has an explicit branch, and anything unrecognised gets `namedDefault` — a
 * sheet that says what it is and admits it has no detail yet. A new kind can
 * no longer silently render as somebody else's object.
 *
 * EVERY LIST-BACKED KIND READS THE SAME CATALOG ITS GRID READS.
 * components, tenants, TTP cards, CLI items and streams each have exactly one
 * catalog (see povdata/), consumed by both the grid and the sheet. The bug this
 * prevents is specific: the tenant branch used to emit Acme's literals, so
 * every tenant row opened as a green BOUND tenant with Acme's host, scope and
 * content gaps regardless of which one you clicked.
 */
import {
  componentCatalog, cliCatalog, tenantCatalog, ttpCatalog, streamCatalog,
  toolCatalog, FAMILY_LABEL,
} from './povdata/catalogs.js'
import { SCENARIOS, CHAIN, WORKFLOWS } from './povdata/corpus.js'
import { detectionLib } from './povdata/detections.js'
import { planeLabel, sourceName } from './povdata/planes.js'

const f = (k, v, edit = false, multiline = false) => ({ k, v, edit, multiline })
const act = (label, go, primary = false) => ({ label, go, primary })

/** Status word → pill tone. Tone derives from STATE, never from the kind, so
 *  nothing that is not actually healthy ever wears the positive token — the
 *  offline tenant reading green was exactly this bug. */
function toneFor(word) {
  const w = String(word || '').toUpperCase()
  if (['READY', 'PASS', 'BOUND', 'DELIVERED', 'CONFIRMED', 'ONLINE', 'VERIFIED', 'FEEDING'].includes(w)) return 'pos'
  if (['PARTIAL', 'WARN', 'DRAFT', 'UNSAVED', 'AWAITING', 'EXPECTED', 'CONSENT'].includes(w)) return 'warn'
  if (['MISSING', 'FAIL', 'BLOCKED', 'BROKEN', 'CRITICAL', 'NOT PRESENT', 'ABSENT'].includes(w)) return 'crit'
  return 'dim'
}

/** Detection chips for the aside. `id` is the detection-library key when one
 *  exists, so the chip can drill into the real rule rather than a label. */
function detChips(names = []) {
  const lib = detectionLib()
  const keys = Object.keys(lib)
  return names.map((n) => ({
    label: n,
    id: keys.find((k) => lib[k].type === n) || null,
  }))
}

/**
 * @param {string} kind
 * @param {string} id
 * @param {Object} opts  { tab, go }  `go(destination)` for cross-surface actions
 */
export function sheetFor(kind, id, opts = {}) {
  const tab = opts.tab || 0
  const go = opts.go || (() => () => {})

  switch (kind) {
    case 'component': return componentSheet(id, tab, go)
    case 'scenario': return scenarioSheet(id, go)
    case 'step': return stepSheet(id, go)
    case 'package': return packageSheet(id, go)
    case 'cli': return cliSheet(id, go)
    case 'tenant': return tenantSheet(id, go)
    case 'ttp': return ttpSheet(id, go)
    case 'stream': return streamSheet(id, go)
    case 'agent': return agentSheet(id, go)
    case 'target': return targetSheet(id, go)
    case 'detection': return detectionSheet(id, go)
    default: return namedDefault(kind, id)
  }
}

/** The guard. Says what it is, admits it has no detail, offers nothing false. */
function namedDefault(kind, id) {
  return withDets({
    kindLabel: kind || 'Object',
    id: id || '—',
    name: id || 'Unknown object',
    status: null,
    groups: [{ label: 'Identity', fields: [f('Kind', kind || 'unknown'), f('Id', id || '—')] }],
    dets: [],
    side: [],
    hint: 'This object kind has no detail page yet. Nothing here is inferred from another object.',
  })
}

/** Derives `hasDets` once, where the sheet is assembled — never per branch. */
function withDets(sheet) {
  return { ...sheet, hasDets: (sheet.dets || []).length > 0 }
}

// ── Component — the one kind with tabs ───────────────────────────────────────
function componentSheet(id, tab, go) {
  const rows = componentCatalog()
  const c = rows.find((x) => x.code === id || x.name === id) || rows[0]
  const failure = {
    ready: 'Carrying normally. Claims behind it can be measured.',
    partial: 'Reachable but incomplete — the claims it cannot carry are exported as gaps, named individually.',
    missing: 'Nothing will flow until it is created. Claims behind it export as not-executed, never as a missed detection.',
  }[c.state]
  const provision = c.state === 'missing'

  const configure = [
    { label: 'Identity', fields: [
      f('Display name', c.name, true), f('Code', c.code),
      f('Product family', FAMILY_LABEL[c.family]),
      f('Instance', c.endpoint, true),
      f('Lifetime', c.group === 'sim' ? 'dies with the lab' : 'persists beyond the POV'),
    ] },
    { label: 'Settings', fields: c.facts.map(([k, v]) => f(k, v, true)) },
    { label: 'Scope', fields: [
      f('In scope for this POV', 'yes', true),
      f('Carries', c.carries),
      f('If it cannot carry', failure),
    ] },
  ]

  // Probe results DERIVE from the component's own state. A missing collector
  // reporting "pass" on its listener check would be the console lying about
  // the one thing this tab exists to answer.
  const probes = {
    cortex: [['Endpoint resolves', 'ready'], ['Credential accepted', 'ready'],
      ['Content present', c.state === 'missing' ? 'missing' : 'ready'],
      ['Signal observed in tenant', c.state === 'ready' ? 'ready' : 'partial']],
    collector: [['Listener bound', c.state === 'missing' ? 'missing' : 'ready'],
      ['TLS chain valid', c.state === 'missing' ? 'missing' : 'ready'],
      ['Test event accepted', c.state === 'missing' ? 'missing' : 'ready'],
      ['Forwarded to tenant', c.state === 'ready' ? 'ready' : 'partial']],
    // Exactly the C-03 constraint from the setup wizard, shown at component
    // level so the two surfaces agree about the same firewall.
    ngfw: [['Management reachable', 'ready'], ['Test zone policy readable', 'ready'],
      ['Alert-only rule present', 'missing'], ['Log forwarding to BVM', 'ready']],
    pov: [['Process up', 'ready'], ['Artifact shelf readable', 'ready'], ['Egress default-deny', 'ready'],
      ['Teardown hook registered', c.state === 'ready' ? 'ready' : 'partial']],
  }[c.family] || []

  const connectivity = [
    { label: `Last probe · ${c.state === 'missing' ? 'never' : '1m ago'}`,
      fields: probes.map(([k, st]) => f(k, st === 'ready' ? 'pass' : st === 'partial' ? 'partial' : 'fail')) },
    { label: 'Probe settings', fields: [
      f('Timeout', '5s', true), f('Retries', '2', true), f('Run on every launch', 'yes', true),
    ] },
  ]

  const dependencies = [
    { label: 'Detection claims behind it', fields: [
      f('Claims', c.family === 'cortex' ? '6 of 11' : c.family === 'ngfw' ? '2 of 11' : '3 of 11'),
      f('Planes', c.family === 'ngfw' ? 'NDR' : c.family === 'collector' ? 'NDR · DLP' : 'EDR · CDR · ANALYTICS'),
    ] },
    { label: 'Constraints referencing it', fields: c.family === 'ngfw'
      ? [f('C-03', 'the NGFW blocks the test zone outright')]
      : c.family === 'collector'
        ? [f('C-07', 'no HTTP collector exists on the Broker VM')]
        : [f('C-04', 'correlation content installed but not enabled'),
          f('C-05', 'the Azure connector is not enrolled')] },
  ]

  const tabDefs = ['Configure', 'Connectivity', 'Dependencies']
  return withDets({
    kindLabel: 'Component', id: c.code, name: c.name,
    status: c.state.toUpperCase(), statusTone: toneFor(c.state),
    hasTabs: true,
    tabs: tabDefs.map((label, i) => ({ label, active: tab === i })),
    // A MISSING component offers the only two honest options.
    actions: provision
      ? [act('Create it', () => {}, true), act('Drop from scope', go('setup'))]
      : [act('Run probe now', () => {}, true), act('Open the wizard', go('setup'))],
    groups: [configure, connectivity, dependencies][tab] || configure,
    dets: [],
    side: [
      { label: 'State', rows: [
        { k: 'Component', v: c.state, tone: toneFor(c.state) },
        { k: 'Family', v: FAMILY_LABEL[c.family] },
        { k: 'Side', v: c.group === 'sim' ? 'simulation' : 'Cortex' },
      ] },
      { label: 'Settings', rows: c.facts.map(([k, v]) => ({ k, v })) },
    ],
    hint: provision
      ? 'This component does not exist yet. Create it, or drop the claims behind it from scope — those are the only two honest options.'
      : failure,
  })
}

// ── Scenario — the ONLY kind that may build from SCENARIOS ───────────────────
function scenarioSheet(id, go) {
  const row = SCENARIOS.find((s) => s[0] === id)
  if (!row) return namedDefault('scenario', id)
  const [sid, name, plane, mitre, door, steps, dets, last] = row
  return withDets({
    kindLabel: 'Scenario', id: sid, name,
    status: last === 'pass' ? 'LAST RUN PASS' : last === 'fail' ? 'LAST RUN FAIL' : 'NEVER RUN',
    statusTone: last === 'pass' ? 'pos' : last === 'fail' ? 'crit' : 'dim',
    actions: [act('Arm and launch', go('guided'), true), act('Open in Composer', go('composer'))],
    groups: [
      { label: 'Identity', fields: [
        f('Name', name, true), f('Scenario id', sid), f('Plane', `${plane} · ${planeLabel(plane)}`),
        f('Primary technique', mitre), f('Ingestion door', `${door} · ${sourceName(door)}`),
      ] },
      { label: 'Shape', fields: [
        f('Steps', String(steps)), f('Expected detections', String(dets)),
        f('Last verdict', last),
      ] },
    ],
    dets: detChips(['BIOC', 'ABIOC', 'XQL', 'Correlation']),
    side: [{ label: 'Corpus', rows: [
      { k: 'Steps', v: String(steps) },
      { k: 'Detections', v: String(dets) },
      { k: 'Plane', v: plane },
    ] }],
    hint: 'Arming a scenario stages its packages and pins their digests; nothing is fetched at dispatch time.',
  })
}

// ── Chain step ───────────────────────────────────────────────────────────────
function stepSheet(id, go) {
  const all = WORKFLOWS.flatMap((w) => w.chain)
  const row = all.find((r) => r[0] === id) || CHAIN[0]
  const [sid, label, plane, mitre, identity, door, dets, state, kindWord, cmd] = row
  return withDets({
    kindLabel: 'Chain step', id: sid, name: label,
    status: state, statusTone: toneFor(state),
    actions: [act('Run this step', () => {}, true), act('Remove from chain', () => {})],
    groups: [
      { label: 'Step', fields: [
        f('Label', label, true), f('Kind', kindWord), f('Plane', `${plane} · ${planeLabel(plane)}`),
        f('Technique', mitre, true),
      ] },
      { label: 'Execution', fields: [
        f('Run as', identity, true),
        f('Ingestion door', `${door} · ${sourceName(door)}`),
      ] },
    ],
    cmd, cmdLabel: 'Command',
    dets: detChips(dets),
    side: [{ label: 'Lineage', rows: [
      { k: 'State', v: state, tone: toneFor(state) },
      { k: 'Door', v: door },
      { k: 'Identity', v: identity },
    ] }],
    // A step that declares no expected detection will execute and produce no
    // claim. That is a legitimate thing to want (a setup step) and a very easy
    // thing to do by accident, so the sheet says which one this is.
    hint: dets.length
      ? 'This step declares expected detections, so a silence here is a measurable result.'
      : 'This step declares no expected detection. It will execute and produce no claim — intentional for setup steps, a gap otherwise.',
  })
}

// ── Package ──────────────────────────────────────────────────────────────────
function packageSheet(id, go) {
  const p = toolCatalog().find((t) => t.id === id)
  if (!p) return namedDefault('package', id)
  return withDets({
    kindLabel: 'Package', id: p.id, name: p.name,
    status: p.host.toUpperCase(), statusTone: toneFor(p.host),
    actions: [act('Deploy to host', () => {}, true), act('Verify digest', () => {})],
    groups: [
      { label: 'Artifact', fields: [
        f('Name', p.name, true), f('Package id', p.id), f('Tier', p.tier),
        f('Digest', p.sha),
      ] },
      { label: 'Delivery', fields: [
        f('State on host', p.host),
        f('Used by', `${p.usedBy} scenarios`),
      ] },
      { label: 'What it drives', fields: [
        f('Detection types', p.dets.join(' · ')),
        f('Outcomes', p.outcomes.join(' · ')),
        ...p.outcomeText.map((t, i) => f(`Produces ${i + 1}`, t)),
      ] },
    ],
    dets: detChips(p.dets),
    side: [
      { label: 'Delivery ladder', rows: [
        { k: 'Staged', v: 'yes', tone: 'pos' },
        { k: 'Delivered', v: p.host === 'delivered' ? 'yes' : 'no', tone: p.host === 'delivered' ? 'pos' : 'dim' },
        { k: 'Digest verified', v: p.host === 'delivered' ? 'yes' : 'pending', tone: p.host === 'delivered' ? 'pos' : 'warn' },
      ] },
      { label: 'Planes', rows: p.planes.map((pl) => ({ k: pl, v: planeLabel(pl) })) },
    ],
    hint: 'The digest is recomputed from shelf bytes at compose time and again on the host, so a package that changed in transit cannot pass as the one that was reviewed.',
  })
}

// ── CLI item ─────────────────────────────────────────────────────────────────
function cliSheet(id) {
  const c = cliCatalog().find((x) => x.id === id)
  if (!c) return namedDefault('cli', id)
  return withDets({
    kindLabel: 'CLI item', id: c.id, name: c.name,
    status: c.state.toUpperCase(), statusTone: toneFor(c.state),
    actions: [act('Add to chain', () => {}, true), act('Run standalone', () => {})],
    groups: [
      { label: 'Script', fields: [f('Name', c.name, true)] },
      { label: 'Execution', fields: [
        f('Shell', c.shell, true), f('Target platform', c.platform, true),
        f('Run as', c.identity, true), f('Timeout', c.timeout, true),
        f('Staged path', c.path, true), f('Digest', c.sha),
      ] },
      { label: 'Teardown', fields: [f('On completion', c.teardown, true)] },
    ],
    cmd: c.body, cmdLabel: `${c.shell} script`,
    dets: detChips(c.dets),
    side: [{ label: 'Binding', rows: [
      { k: 'Plane', v: c.plane },
      { k: 'Technique', v: c.technique },
      { k: 'Used by', v: `${c.usedBy} chains` },
    ] }],
    hint: 'Authored here, staged and digest-pinned like a package, then written to the host by the beacon and executed in place.',
  })
}

// ── Tenant ───────────────────────────────────────────────────────────────────
function tenantSheet(id) {
  const t = tenantCatalog().find((x) => x.name === id || x.host === id)
  if (!t) return namedDefault('tenant', id)
  const bound = t.state === 'bound'
  const offline = t.state === 'offline'
  return withDets({
    kindLabel: 'Tenant', id: t.host, name: t.name,
    status: t.state.toUpperCase(), statusTone: toneFor(t.state),
    actions: bound
      ? [act('Probe the tenant', () => {}, true)]
      : offline ? [act('Score offline', () => {}, true)] : [act('Bind this tenant', () => {}, true)],
    groups: [
      { label: 'Binding', fields: [
        f('Display name', t.name, true), f('Host', t.host), f('Region', t.region),
        f('State', t.state),
      ] },
      // Facts carry their own tone: 'read:alerts' is a warn, '2 match lab host'
      // is a crit, and flattening them to plain text loses the whole point of
      // the row, which is whether this tenant will distort a verdict.
      { label: 'Read-back surface', fields: t.facts.map(([k, v]) => f(k, v)) },
    ],
    dets: [],
    side: [{ label: 'Facts that judge signal', rows: t.facts.map(([k, v, tone]) => ({ k, v, tone })) },
    { label: 'What the tenant decides', rows: [
      { k: 'Detection existence', v: 'installed vs enabled' },
      { k: 'Read-back scope', v: 'what the key may read' },
      { k: 'Re-askable window', v: 'dataset retention' },
      { k: 'Alert survival', v: 'suppression rules' },
    ] }],
    hint: offline
      ? 'Runs score against the corpus only. The readout says authored, never verified.'
      : bound
        ? 'Every claim is re-asked of this tenant as an XQL probe, so the readout can be re-derived after this instance is gone.'
        : 'Binding this tenant moves every claim to its content, scope and retention.',
  })
}

// ── TTP card ─────────────────────────────────────────────────────────────────
function ttpSheet(id, go) {
  const t = ttpCatalog().find((x) => x.id === id)
  if (!t) return namedDefault('ttp', id)
  return withDets({
    kindLabel: 'TTP card', id: t.id, name: `${t.name} · ${t.technique}`,
    status: t.plane, statusTone: 'dim',
    actions: [act('Add to chain', go('composer'), true), act('Run scenarios using it', go('library'))],
    groups: [
      { label: 'Technique', fields: [
        f('Name', t.name, true), f('MITRE', t.technique),
        f('Plane', `${t.plane} · ${planeLabel(t.plane)}`),
        f('Platforms', t.platforms),
      ] },
      { label: 'Behaviour', fields: [f('Describes', t.behaviour)] },
    ],
    dets: detChips(t.dets),
    side: [{ label: 'Binding', rows: [
      { k: 'Detection objects', v: String(t.objects) },
      { k: 'Scenarios using it', v: String(t.usedBy) },
    ] }],
    hint: 'A TTP card is authored content, not proof output — it describes a behaviour and names the detection objects that should answer it.',
  })
}

// ── Data stream ──────────────────────────────────────────────────────────────
function streamSheet(id, go) {
  // NOTE the catalog's own naming: `name` is the product ("Okta"), `vendor`
  // is the category ("identity provider"). Matched by dataset because that is
  // the only value on this row that is unique and stable.
  const s = streamCatalog().find((x) => x.dataset === id || x.name === id)
  if (!s) return namedDefault('stream', id)
  const blocked = s.state === 'blocked'
  return withDets({
    kindLabel: 'Data stream', id: s.dataset, name: s.name,
    status: s.state.toUpperCase(), statusTone: toneFor(s.state),
    actions: blocked
      ? [act('Author an emitter', () => {}, true), act('Drop from scope', go('setup'))]
      : [act('Send a test event', () => {}, true)],
    groups: [
      { label: 'Source', fields: [
        f('Source', s.name), f('Category', s.vendor),
        f('Dataset', s.dataset), f('Intake path', s.intake),
      ] },
      { label: 'Coverage', fields: [
        f('Shapes authored', String(s.shapes)),
        f('Emitter', s.emitter),
        f('State', s.state),
      ] },
    ],
    dets: [],
    side: [{ label: 'Path into the tenant', rows: [
      { k: 'Intake', v: s.intake },
      { k: 'Door', v: 'Data Connector' },
      { k: 'State', v: s.state, tone: toneFor(s.state) },
    ] }],
    hint: s.note,
  })
}

// ── Agent / target ───────────────────────────────────────────────────────────
function agentSheet(id) {
  return withDets({
    kindLabel: 'Agent', id, name: id,
    status: 'ONLINE', statusTone: 'pos',
    actions: [act('Re-enrol', () => {}, true), act('Send a test command', () => {})],
    groups: [
      { label: 'Beacon', fields: [
        f('Hostname', id, true), f('Dispatch mode', 'pull · 4s'), f('Beacon version', '0.9.4'),
      ] },
      { label: 'Capability', fields: [
        f('Identity harness', 'runuser'), f('Tools on host', 'bash · python3 · aws-cli'),
      ] },
    ],
    dets: [],
    side: [{ label: 'Health', rows: [
      { k: 'Last seen', v: '2s' },
      { k: 'Heartbeat', v: 'long-poll active' },
    ] }],
    // The capability gap matters because of what it does to the RUN RECORD,
    // not because of what it does to the host.
    hint: 'A host with no identity harness collapses identity-wrapped steps to direct execution, and the run records IDENTITY NOT HONOURED rather than silently succeeding.',
  })
}

function targetSheet(id) {
  return withDets({
    kindLabel: 'Target', id, name: id,
    status: 'IN SCOPE', statusTone: 'pos',
    actions: [act('Include in scope', () => {}, true), act('Exclude', () => {})],
    groups: [{ label: 'Target', fields: [f('Name', id, true), f('Class', 'cloud environment')] }],
    dets: [],
    side: [{ label: 'Proves', rows: [{ k: 'Plane', v: 'CDR' }] }],
    hint: null,
  })
}

// ── Detection object — the Explain drill-down ────────────────────────────────
function detectionSheet(id) {
  const lib = detectionLib()
  const d = lib[id]
  if (!d) return namedDefault('detection', id)
  return withDets({
    kindLabel: 'Detection object', id, name: id,
    status: String(d.state || '').toUpperCase(), statusTone: toneFor(d.state),
    actions: [act('Re-ask the tenant', () => {}, true)],
    groups: [
      { label: 'Source', fields: [f('Rule', d.source, false, true)] },
      { label: 'Why it fires', fields: [f('Reasoning', d.why)] },
      { label: 'How it stays honest', fields: [
        f('False-positive guard', d.guard),
        f('Negative control', d.control),
      ] },
    ],
    dets: [],
    side: [
      { label: 'Object', rows: [
        { k: 'Type', v: d.type },
        { k: 'Door', v: `${d.door} · ${sourceName(d.door)}` },
        { k: 'Plane', v: d.plane },
        { k: 'MITRE', v: d.mitre },
        { k: 'Severity', v: d.severity },
      ] },
      { label: 'Keys on', rows: (d.keys || []).map((k) => ({ k, v: 'telemetry field' })) },
    ],
    hint: 'A claim whose logic cannot be read is not a claim a DC can defend in a room. This is the rule as it exists in the tenant, not a paraphrase of it.',
  })
}
