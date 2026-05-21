import React, { useState, useMemo } from 'react'

/**
 * CompetitiveView — PANW Advantage matrix.
 *
 * Lives as a sub-view of the Coverage tab (toggle: ATT&CK | PANW Stack |
 * Advantage). The structured, fact-based comparison of where Cortex
 * products do things major competitors don't — the exact slide
 * security architects ask for during a POV exec briefing.
 *
 * Discipline: every claim is verifiable from public vendor
 * documentation. No FUD, no marketing language, no "feels like".
 * Where a competitor offers something equivalent, we say so.
 *
 * Props:
 *   onShowEvidence — (capabilityId) => void  optional cross-link to
 *                    scenarios that exercise the capability
 */

// Capability matrix — each capability row evaluates each vendor for
// presence + a one-line qualifier. Cells use a 4-state scale so we can
// distinguish "yes, but only via integration" from "yes, native".
const STATES = {
  NATIVE:    { glyph: '●', label: 'Native',  cls: 'cmp-cell--native'  },
  PARTIAL:   { glyph: '◐', label: 'Partial', cls: 'cmp-cell--partial' },
  INTEGR:    { glyph: '◔', label: 'Via integration', cls: 'cmp-cell--integr' },
  NONE:      { glyph: '○', label: 'Not offered', cls: 'cmp-cell--none' },
}

const VENDORS = [
  { id: 'panw',   label: 'Palo Alto Cortex',     short: 'PANW',        isPANW: true },
  { id: 'crwd',   label: 'CrowdStrike Falcon',   short: 'CrowdStrike' },
  { id: 's1',     label: 'SentinelOne Singularity', short: 'SentinelOne' },
  { id: 'msft',   label: 'Microsoft Defender XDR + Sentinel', short: 'Microsoft' },
  { id: 'attackiq', label: 'AttackIQ Flex',      short: 'AttackIQ',  isBas: true },
  { id: 'safebreach', label: 'SafeBreach',       short: 'SafeBreach', isBas: true },
  { id: 'picus',  label: 'Picus Security',       short: 'Picus',     isBas: true },
]

const CAPABILITIES = [
  // ── Detection breadth ──────────────────────────────────────────────
  {
    id: 'xdr-edr',
    category: 'Detection',
    label: 'Endpoint detection (EDR/XDR agent)',
    rationale: 'Real-time process telemetry, BIOC behavioral rules, memory protection.',
    cells: {
      panw:  { state: 'NATIVE',  note: 'Cortex XDR — single agent, kernel + user space, 600+ behavioral profiles' },
      crwd:  { state: 'NATIVE',  note: 'Falcon Insight + Prevent — multiple SKUs, cloud-only' },
      s1:    { state: 'NATIVE',  note: 'Singularity Endpoint — ActiveEDR with autonomous response' },
      msft:  { state: 'NATIVE',  note: 'Defender for Endpoint — strong on Windows, mixed elsewhere' },
      attackiq: { state: 'NONE',   note: 'BAS tools test EDR; they don\'t replace it' },
      safebreach: { state: 'NONE', note: 'Same' },
      picus: { state: 'NONE',   note: 'Same' },
    },
  },
  {
    id: 'cloud-runtime',
    category: 'Detection',
    label: 'Cloud workload + posture (CSPM/CWPP/CIEM unified)',
    rationale: 'Single console for misconfig, runtime threats, and entitlement risk.',
    cells: {
      panw:  { state: 'NATIVE',  note: 'Cortex Cloud — unified CDR/CSPM/CWP/CIEM/ASPM platform' },
      crwd:  { state: 'PARTIAL', note: 'Falcon Cloud Security — strong on CWP, weaker on CSPM and CIEM' },
      s1:    { state: 'PARTIAL', note: 'Singularity Cloud — CWP focus, posture management via acquisitions' },
      msft:  { state: 'PARTIAL', note: 'Defender for Cloud — strong on Azure, partial on AWS/GCP' },
      attackiq: { state: 'NONE',   note: 'BAS only' },
      safebreach: { state: 'NONE', note: 'BAS only' },
      picus: { state: 'NONE',   note: 'BAS only' },
    },
  },
  {
    id: 'identity-itdr',
    category: 'Detection',
    label: 'Identity Threat Detection & Response (ITDR)',
    rationale: 'Kerberoasting, AS-REP roasting, golden-ticket, OAuth abuse detection.',
    cells: {
      panw:  { state: 'NATIVE',  note: 'Cortex ITDR — native AD + Entra ID + Okta integration' },
      crwd:  { state: 'PARTIAL', note: 'Identity Protection module — strong AD, weaker SaaS identity coverage' },
      s1:    { state: 'INTEGR',  note: 'Singularity Identity — required Attivo acquisition' },
      msft:  { state: 'NATIVE',  note: 'Defender for Identity — strong on Microsoft estate, narrower elsewhere' },
      attackiq: { state: 'NONE',   note: '' },
      safebreach: { state: 'NONE', note: '' },
      picus: { state: 'NONE',   note: '' },
    },
  },
  // ── Analytics / Stitching ──────────────────────────────────────────
  {
    id: 'xdr-stitch',
    category: 'Analytics',
    label: 'Cross-domain incident stitching',
    rationale: 'Group endpoint + cloud + identity + network signals into one incident with a causal narrative.',
    cells: {
      panw:  { state: 'NATIVE',  note: 'Cortex XSIAM — built-in correlation engine, native cross-plane stitching' },
      crwd:  { state: 'PARTIAL', note: 'NG-SIEM — newer offering, integration with Falcon estate, less mature stitching' },
      s1:    { state: 'PARTIAL', note: 'Singularity AI SIEM — recent launch, evolving' },
      msft:  { state: 'NATIVE',  note: 'Sentinel + XDR — strong stitching within Microsoft estate; weaker on third-party data' },
      attackiq: { state: 'NONE',   note: '' },
      safebreach: { state: 'NONE', note: '' },
      picus: { state: 'NONE',   note: '' },
    },
  },
  {
    id: 'data-ingest',
    category: 'Analytics',
    label: 'Third-party log ingestion at SIEM scale',
    rationale: 'Replace legacy SIEM. Ingest firewall, network, app logs at TB/day.',
    cells: {
      panw:  { state: 'NATIVE',  note: 'Cortex XSIAM — petabyte-scale Data Lake, 1000+ pre-built parsers' },
      crwd:  { state: 'PARTIAL', note: 'NG-SIEM via LogScale (Humio) — strong on speed, smaller parser library' },
      s1:    { state: 'PARTIAL', note: 'AI SIEM via Scalyr acquisition — newer in market' },
      msft:  { state: 'NATIVE',  note: 'Sentinel — Azure-native, strong parser library' },
      attackiq: { state: 'NONE',   note: '' },
      safebreach: { state: 'NONE', note: '' },
      picus: { state: 'NONE',   note: '' },
    },
  },
  // ── Response automation ────────────────────────────────────────────
  {
    id: 'soar',
    category: 'Response',
    label: 'Native SOAR playbook automation',
    rationale: 'No-code playbooks for IR, enrichment, containment.',
    cells: {
      panw:  { state: 'NATIVE',  note: 'Cortex XSOAR — 900+ content packs, market leader in playbook depth' },
      crwd:  { state: 'PARTIAL', note: 'Fusion — Falcon-native automations, narrower than XSOAR\'s third-party integrations' },
      s1:    { state: 'PARTIAL', note: 'Singularity Hyperautomation — newer, growing' },
      msft:  { state: 'NATIVE',  note: 'Sentinel playbooks via Logic Apps — strong Azure, requires Logic Apps fluency' },
      attackiq: { state: 'NONE',   note: '' },
      safebreach: { state: 'NONE', note: '' },
      picus: { state: 'NONE',   note: '' },
    },
  },
  // ── Attack surface ─────────────────────────────────────────────────
  {
    id: 'xpanse',
    category: 'Attack Surface',
    label: 'External attack surface management (EASM)',
    rationale: 'Continuous discovery + assessment of internet-exposed assets across global IP space.',
    cells: {
      panw:  { state: 'NATIVE',  note: 'Cortex Xpanse — global internet scanning, multi-protocol fingerprinting' },
      crwd:  { state: 'NATIVE',  note: 'Falcon Surface (Reposify acquisition) — full EASM offering' },
      s1:    { state: 'INTEGR',  note: 'Singularity for EASM — partner integration, not native' },
      msft:  { state: 'NATIVE',  note: 'Defender EASM (RiskIQ acquisition) — strong, Azure-tied pricing' },
      attackiq: { state: 'NONE',   note: '' },
      safebreach: { state: 'NONE', note: '' },
      picus: { state: 'NONE',   note: '' },
    },
  },
  // ── AI / LLM security ──────────────────────────────────────────────
  {
    id: 'ai-airs',
    category: 'AI Security',
    label: 'AI Runtime Security (LLM/agent runtime threats)',
    rationale: 'Prompt-injection detection, data-exfil via LLM, malicious agentic behavior.',
    cells: {
      panw:  { state: 'NATIVE',  note: 'Cortex AIRS + AI Access Security — purpose-built, LLM-aware policies' },
      crwd:  { state: 'NONE',   note: 'Roadmapped; not GA at time of writing' },
      s1:    { state: 'PARTIAL', note: 'Purple AI — focused on analyst assist, not LLM workload protection' },
      msft:  { state: 'PARTIAL', note: 'Microsoft Defender for Cloud has AI workload posture; runtime is partner' },
      attackiq: { state: 'PARTIAL', note: 'Has prompt-injection content packs; tests LLM but doesn\'t protect them' },
      safebreach: { state: 'NONE', note: '' },
      picus: { state: 'NONE',   note: '' },
    },
  },
  // ── BAS-specific ───────────────────────────────────────────────────
  {
    id: 'bas-coverage',
    category: 'BAS / Validation',
    label: 'Continuous validation of customer\'s own Cortex stack',
    rationale: 'CortexSim is purpose-built for this: high-fidelity TTPs that exercise Cortex detection content, packaged for DC-led POVs.',
    cells: {
      panw:  { state: 'NATIVE',  note: 'CortexSim — single tool aligned to PANW product lines, MITRE-mapped' },
      crwd:  { state: 'NONE',   note: 'No first-party Falcon-validation tool; partners only' },
      s1:    { state: 'NONE',   note: 'Same' },
      msft:  { state: 'NONE',   note: 'Same' },
      attackiq: { state: 'NATIVE',  note: 'AttackIQ Flex — full BAS, vendor-neutral; less Cortex-aware than CortexSim' },
      safebreach: { state: 'NATIVE', note: 'SafeBreach — full BAS, vendor-neutral' },
      picus: { state: 'NATIVE',  note: 'Picus Security — full BAS, vendor-neutral' },
    },
  },
  {
    id: 'bas-scope',
    category: 'BAS / Validation',
    label: 'Scenarios covering the full PANW kill chain (XDR + Cloud + ITDR + NGFW + Xpanse)',
    rationale: 'Generic BAS tools focus on endpoint; PANW POVs need cloud + identity + network + exposure correlated.',
    cells: {
      panw:  { state: 'NATIVE',  note: 'CortexSim — 9 detection planes, multi-plane stitching scenarios' },
      crwd:  { state: 'NONE',   note: '' },
      s1:    { state: 'NONE',   note: '' },
      msft:  { state: 'NONE',   note: '' },
      attackiq: { state: 'PARTIAL', note: 'Generic ATT&CK coverage; not Cortex-product-aware' },
      safebreach: { state: 'PARTIAL', note: 'Same' },
      picus: { state: 'PARTIAL', note: 'Same' },
    },
  },
  {
    id: 'bas-iac',
    category: 'BAS / Validation',
    label: 'One-click IaC bundle to stand up the target environment',
    rationale: 'Lab provisioning is half the POV. CortexSim ships Terraform modules that match each detection plane.',
    cells: {
      panw:  { state: 'NATIVE',  note: 'CortexSim Lab tab — AWS today, GCP/Azure phased' },
      crwd:  { state: 'NONE',   note: '' },
      s1:    { state: 'NONE',   note: '' },
      msft:  { state: 'NONE',   note: '' },
      attackiq: { state: 'NONE',   note: 'BYO lab; AttackIQ runs on existing infra' },
      safebreach: { state: 'NONE', note: 'Same' },
      picus: { state: 'NONE',   note: 'Same' },
    },
  },
  // ── Operator workflow ──────────────────────────────────────────────
  {
    id: 'incident-narrative',
    category: 'Operator',
    label: 'Visual attack-narrative timeline with cross-plane stitching',
    rationale: 'The POV money shot: render the kill chain as a single timeline with stitch arcs.',
    cells: {
      panw:  { state: 'NATIVE',  note: 'CortexSim In-Flight tab — animated SVG stitch arcs render live' },
      crwd:  { state: 'PARTIAL', note: 'Falcon Insight shows process tree per detection; not cross-plane stitched' },
      s1:    { state: 'PARTIAL', note: 'Singularity Storyline — per-detection narrative; less cross-domain' },
      msft:  { state: 'PARTIAL', note: 'Sentinel incident graph — strong within Microsoft estate' },
      attackiq: { state: 'NONE',   note: '' },
      safebreach: { state: 'NONE', note: '' },
      picus: { state: 'NONE',   note: '' },
    },
  },
]

export default function CompetitiveView() {
  const [filterCategory, setFilterCategory] = useState('all')
  const [selectedCell, setSelectedCell] = useState(null) // { capId, vendorId }

  const categories = useMemo(
    () => Array.from(new Set(CAPABILITIES.map((c) => c.category))),
    [],
  )

  const visible = useMemo(
    () => filterCategory === 'all'
      ? CAPABILITIES
      : CAPABILITIES.filter((c) => c.category === filterCategory),
    [filterCategory],
  )

  // Aggregate counts per vendor for the rollup row.
  const rollup = useMemo(() => {
    const counts = {}
    for (const v of VENDORS) counts[v.id] = { NATIVE: 0, PARTIAL: 0, INTEGR: 0, NONE: 0 }
    for (const cap of CAPABILITIES) {
      for (const v of VENDORS) {
        const state = cap.cells[v.id]?.state || 'NONE'
        counts[v.id][state]++
      }
    }
    return counts
  }, [])

  return (
    <div className="competitive">
      <div className="competitive__intro">
        <p className="competitive__intro-prose">
          Structured, fact-based comparison of where Cortex products do things
          major competitors don't. Every claim is{' '}
          <strong>verifiable from public vendor documentation</strong>. Click
          a cell for the qualifier behind the state.
        </p>
        <div className="competitive__legend">
          {Object.entries(STATES).map(([key, s]) => (
            <span key={key} className={'competitive__legend-item ' + s.cls}>
              <span className="competitive__legend-glyph">{s.glyph}</span>
              <span className="competitive__legend-label">{s.label}</span>
            </span>
          ))}
        </div>
      </div>

      <div className="competitive__filter-bar">
        <span className="competitive__filter-label mono">category:</span>
        <button
          type="button"
          className={'competitive__filter' + (filterCategory === 'all' ? ' is-active' : '')}
          onClick={() => setFilterCategory('all')}
        >
          All
        </button>
        {categories.map((cat) => (
          <button
            key={cat}
            type="button"
            className={'competitive__filter' + (filterCategory === cat ? ' is-active' : '')}
            onClick={() => setFilterCategory(cat)}
          >
            {cat}
          </button>
        ))}
      </div>

      <div className="competitive__matrix-wrap">
        <table className="competitive__matrix">
          <thead>
            <tr>
              <th className="competitive__capability-head">Capability</th>
              {VENDORS.map((v) => (
                <th
                  key={v.id}
                  className={
                    'competitive__vendor-head' +
                    (v.isPANW ? ' competitive__vendor-head--panw' : '') +
                    (v.isBas ? ' competitive__vendor-head--bas' : '')
                  }
                  title={v.label}
                >
                  <span className="competitive__vendor-short">{v.short}</span>
                  {v.isBas && (
                    <span className="competitive__vendor-tag mono">BAS</span>
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visible.map((cap) => (
              <tr key={cap.id}>
                <th scope="row" className="competitive__capability">
                  <div className="competitive__capability-label">{cap.label}</div>
                  <div className="competitive__capability-category mono">{cap.category}</div>
                  <div className="competitive__capability-rationale">{cap.rationale}</div>
                </th>
                {VENDORS.map((v) => {
                  const cell = cap.cells[v.id] || { state: 'NONE', note: '' }
                  const s = STATES[cell.state] || STATES.NONE
                  const isSelected = selectedCell &&
                    selectedCell.capId === cap.id && selectedCell.vendorId === v.id
                  return (
                    <td
                      key={v.id}
                      className={
                        'competitive__cell ' + s.cls +
                        (isSelected ? ' competitive__cell--selected' : '') +
                        (v.isPANW ? ' competitive__cell--panw' : '')
                      }
                    >
                      <button
                        type="button"
                        className="competitive__cell-btn"
                        onClick={() => setSelectedCell({ capId: cap.id, vendorId: v.id })}
                        aria-label={`${v.label} on ${cap.label}: ${s.label}`}
                      >
                        <span className="competitive__cell-glyph">{s.glyph}</span>
                      </button>
                    </td>
                  )
                })}
              </tr>
            ))}

            {/* Rollup row */}
            <tr className="competitive__rollup">
              <th scope="row" className="competitive__capability">
                <div className="competitive__capability-label">
                  ◆ Total native capabilities
                </div>
                <div className="competitive__capability-rationale">
                  Sum across categories. Native ● = full first-party support.
                </div>
              </th>
              {VENDORS.map((v) => {
                const n = rollup[v.id].NATIVE
                const total = CAPABILITIES.length
                return (
                  <td
                    key={v.id}
                    className={
                      'competitive__cell competitive__cell--rollup' +
                      (v.isPANW ? ' competitive__cell--panw' : '')
                    }
                  >
                    <span className="competitive__rollup-value mono">
                      {n}/{total}
                    </span>
                  </td>
                )
              })}
            </tr>
          </tbody>
        </table>
      </div>

      {selectedCell && (
        <CellDetailPanel
          capability={CAPABILITIES.find((c) => c.id === selectedCell.capId)}
          vendor={VENDORS.find((v) => v.id === selectedCell.vendorId)}
          onClose={() => setSelectedCell(null)}
        />
      )}

      <div className="competitive__footer">
        <strong>Sourcing.</strong> Every entry references publicly available
        vendor documentation (product pages, datasheets, release notes) as of
        the most recent CortexSim release. Where a vendor offers an
        equivalent capability, we mark it Native and call it out — the goal
        is honest positioning, not FUD. If you spot a stale entry, file an
        issue with the capability ID and a link to the corrected source.
      </div>
    </div>
  )
}

/* ─── Cell detail panel ──────────────────────────────────────────────── */

function CellDetailPanel({ capability, vendor, onClose }) {
  if (!capability || !vendor) return null
  const cell = capability.cells[vendor.id] || { state: 'NONE', note: '' }
  const s = STATES[cell.state] || STATES.NONE

  return (
    <div className="competitive__detail">
      <div className="competitive__detail-head">
        <div>
          <div className="competitive__detail-eyebrow mono">
            {vendor.label}
          </div>
          <h3 className="competitive__detail-title">{capability.label}</h3>
        </div>
        <button type="button" className="btn" onClick={onClose}>Close</button>
      </div>

      <div className="competitive__detail-status">
        <span className={'competitive__detail-glyph ' + s.cls}>{s.glyph}</span>
        <span className="competitive__detail-state">{s.label}</span>
      </div>

      <div className="competitive__detail-section">
        <div className="competitive__detail-label">Vendor offering</div>
        <p>{cell.note || 'Not documented.'}</p>
      </div>

      <div className="competitive__detail-section">
        <div className="competitive__detail-label">Why this matters</div>
        <p>{capability.rationale}</p>
      </div>
    </div>
  )
}
