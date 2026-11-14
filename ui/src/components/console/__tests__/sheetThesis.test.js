/**
 * sheetThesis — "Why this matters" (design v33 C10), read off the documents.
 *
 * Fixtures are trimmed from the real corpus: scenario SIM-NDR-006
 * (scenarios/ndr/ndr-006-ftp-cleartext-egress.yml, as GET /api/scenarios/{id}
 * serves it) and card TTP-2026-0061 (detection_scanner/ttps/). Every line the
 * module writes must be traceable to one of their fields; a part with none of
 * its fields says "Not written for this … yet." and nothing else.
 */
import { describe, it, expect } from 'vitest'
import {
  THESIS_PARTS, KIND_TYPE, notWritten, firstSentence, datasetAnchor,
  scenarioThesis, ttpThesis, detectionThesis,
} from '../sheetThesis.js'
import { OWNERSHIP_MEANING } from '../composerContent.js'

const SCENARIO = {
  scenario_id: 'SIM-NDR-006',
  name: 'NDR — FTP Cleartext Egress + Credentials EAL Validation',
  plane: 'NDR',
  uc_ref: 'UCS-NDR-02',
  tc_ref: 'TC-NDR-03',
  tc_refs: ['TC-NDR-03', 'TC-NDR-04', 'TC-DLP-01'],
  uc_name: 'Network Detection & Response',
  tc_name: 'Validate network analytics engine filters traffic noise via EAL',
  mitre_tactic: 'TA0011',
  mitre_tactic_name: 'Command and Control',
  mitre_technique: 'T1071',
  mitre_technique_name: 'Application Layer Protocol',
  threat_report: 'Unit42 - Cleartext Protocol Abuse for Exfiltration',
  execution_identity: { default: 'container-runtime', options: ['container-runtime', 'root'] },
  external_tools: [{ name: 'cortexsim-eal-cli', type: 'script', install_inline: false }],
  validation_methodology: 'Network Signal Injection & Detection Accuracy',
  methodology_family: 'F1',
  primary_kpi: 'MTTD',
  threshold: { kpi: 'MTTD', op: '≤', value: 300, unit: 'seconds' },
  success_criteria: 'The per-protocol egress signal injected by this scenario produces the\nexpected NGFW App-ID / XSIAM detection within the MTTD budget.\n',
  required_planes_in_incident: [],
  required_base_platform: ['XSIAM'],
  required_addons: [],
  steps: [
    { id: 'step-01', expected_detections: [{ type: 'XQL' }] },
    { id: 'step-02', expected_detections: [{ type: 'BIOC' }, { type: 'IOC' }, { type: 'Correlation' }] },
  ],
}

const BIOC = {
  name: 'NGFW EAL outbound FTP STOR file transfer with data payload',
  description: 'NGFW EAL — outbound file transfer via FTP STOR command + 4 KB data payload',
  logic: 'dataset = panw_ngfw_traffic_raw\n| filter app = "ftp" or dst_port = 21',
  severity: 'high',
  mitre_technique_ids: ['T1048.003', 'T1071'],
}
const XQL_PROOF = {
  purpose: 'validation',
  name: 'NGFW App-ID FTP outbound session from non-admin endpoint',
  query: 'dataset = panw_ngfw_traffic_raw\n| filter app = "ftp"',
  expected_rows_min: 1,
  dataset: 'panw_ngfw_traffic_raw',
}
const XQL_PREFLIGHT = {
  purpose: 'validation',
  name: 'Pre-flight FTP sinkhole reachable on port 21',
  query: 'dataset = panw_ngfw_traffic_raw\n| filter dst_port = 21',
  expected_rows_min: 0,
  dataset: 'panw_ngfw_traffic_raw',
}
const CORRELATION = {
  name: 'FTP Cleartext Egress — Credential Exposure Plus STOR Upload',
  rule_id: 'CR-NDR-0005',
  logic: 'IOC(ftp-cleartext-credentials-user-pass) OR BIOC(NGFW EAL outbound FTP STOR file transfer with data payload)',
}
const IOC = {
  ioc_type: 'user-agent',
  value: 'ftp-cleartext-credentials-user-pass',
  context: 'NGFW EAL — cleartext FTP credentials observed (USER + PASS commands in session).',
}
const ANALYTICS = 'Cortex XSIAM Network Security Analytics — Cleartext Protocol Exfiltration'

const CARD = {
  id: 'TTP-2026-0061',
  identity: {
    name: 'FTP Cleartext Egress + Credential Exposure — Outbound STOR Exfiltration (T1071 / T1048.003)',
    summary: 'A non-admin endpoint opens an outbound FTP control session to a host outside the corporate range.',
    description: 'FTP remains a favored exfiltration channel precisely because it is unencrypted, ubiquitous on internal file-transfer appliances, and frequently allowed outbound through legacy firewall rules. The control channel carries credentials in the clear (USER <name> / PASS <secret>).',
  },
  mitre_attack: {
    techniques: [
      { technique_id: 'T1071', name: 'Application Layer Protocol' },
      { technique_id: 'T1048', subtechnique_id: 'T1048.003', name: 'Exfiltration Over Unencrypted Non-C2 Protocol' },
    ],
  },
  execution: {
    target_platform: 'linux',
    privilege_required: 'user',
    prerequisites: [
      { description: 'Lab environment matching scenario SIM-NDR-006 requirements (NGFW logging outbound traffic from the lab subnet to XSIAM, FTP sinkhole on 10.50.0.42:21).' },
      { description: 'NGFW forwards outbound traffic logs into XSIAM (panw_ngfw_traffic_raw / network_story carries app, src_ip, dst_ip, dst_port).' },
    ],
  },
  detections: {
    iocs: [IOC], biocs: [BIOC], xql_queries: [XQL_PREFLIGHT, XQL_PROOF],
    correlation_rules: [CORRELATION], analytics_modules: [ANALYTICS],
  },
  panw_mapping: {
    threat_scenarios: [{
      threat_scenario_id: 'TS-EXFIL-061',
      name: 'Cleartext FTP Exfiltration Detection via NGFW EAL',
      description: 'Validate that NGFW App-ID + XSIAM Network Security Analytics detect outbound FTP with cleartext credentials and STOR data egress from a non-admin endpoint.',
      threat_steps: [
        {
          objective: 'Outbound FTP App-ID and cleartext credentials are detected from NGFW telemetry.',
          success_criteria: [
            'XQL `NGFW App-ID FTP outbound session from non-admin endpoint` returns >= 1 row.',
            'IOC `NGFW EAL cleartext FTP credentials USER PASS in session` matches the session.',
          ],
        },
        {
          objective: 'STOR upload and credential exposure stitch into a single exfiltration incident.',
          success_criteria: [
            'BIOC `NGFW EAL outbound FTP STOR file transfer with data payload` returns >= 1 row with bytes_out > 0.',
            'Correlation `CR-NDR-0005` raises a single data-exfiltration incident for the source.',
          ],
        },
      ],
    }],
  },
}

const byKey = (parts) => Object.fromEntries(parts.map((p) => [p.key, p]))

describe('the four parts', () => {
  it('are Thesis · Why we simulate it · What proves it · What it needs, in that order', () => {
    expect(THESIS_PARTS.map((p) => p.label)).toEqual(['Thesis', 'Why we simulate it', 'What proves it', 'What it needs'])
    for (const parts of [scenarioThesis(SCENARIO), ttpThesis(CARD), detectionThesis('biocs', BIOC, CARD)]) {
      expect(parts.map((p) => p.key)).toEqual(['thesis', 'why', 'proves', 'needs'])
    }
  })

  it('maps every card detection family the corpus carries to its detection type', () => {
    expect(KIND_TYPE).toEqual({
      biocs: 'BIOC', abiocs: 'ABIOC', xql_queries: 'XQL', correlation_rules: 'Correlation',
      iocs: 'IOC', analytics_modules: 'Analytics', prevention_modules: 'Prevention',
    })
  })
})

describe('scenarioThesis', () => {
  it('reads each part off the scenario document', () => {
    const p = byKey(scenarioThesis(SCENARIO))
    expect(p.thesis.lines).toEqual([
      'T1071 Application Layer Protocol (Command and Control).',
      'Modeled on Unit42 - Cleartext Protocol Abuse for Exfiltration.',
    ])
    expect(p.why.lines).toEqual([
      'Evidences test case TC-NDR-03 Validate network analytics engine filters traffic noise via EAL, under use case UCS-NDR-02 Network Detection & Response.',
      'Also evidences TC-NDR-04, TC-DLP-01.',
      'Method: Network Signal Injection & Detection Accuracy (F1).',
    ])
    expect(p.proves.lines).toEqual([
      'The per-protocol egress signal injected by this scenario produces the expected NGFW App-ID / XSIAM detection within the MTTD budget.',
      'Passes when MTTD ≤ 300 seconds.',
      '4 expected detections across 2 steps.',
    ])
    expect(p.needs.lines).toEqual([
      'Tools: cortexsim-eal-cli.',
      'Runs as container-runtime.',
      'Cortex platform: XSIAM.',
    ])
    for (const part of Object.values(p)) expect(part.written).toBe(true)
  })

  it('names the fields each part came from', () => {
    const p = byKey(scenarioThesis(SCENARIO))
    expect(p.thesis.sources).toEqual(['mitre_technique', 'mitre_technique_name', 'mitre_tactic_name', 'threat_report'])
    expect(p.proves.sources).toEqual(['success_criteria', 'threshold', 'steps[].expected_detections'])
    expect(p.needs.sources).toEqual(['external_tools', 'execution_identity.default', 'required_base_platform'])
  })

  it('a percentage threshold reads without a space; an adapter ref names the tool', () => {
    const p = byKey(scenarioThesis({
      ...SCENARIO,
      threshold: { kpi: 'Detection Accuracy', op: '≥', value: 62.5, unit: '%' },
      external_tools: [{ name: 'nmap', adapter_ref: 'TOOL-NMAP' }, { name: 'curl' }],
      required_addons: ['ITDR'],
      required_planes_in_incident: ['EDR', 'NDR'],
    }))
    expect(p.proves.lines).toContain('Passes when Detection Accuracy ≥ 62.5%.')
    expect(p.needs.lines).toEqual([
      'Tools: TOOL-NMAP, curl.',
      'Runs as container-runtime.',
      'Cortex platform: XSIAM.',
      'Add-ons: ITDR.',
      'One incident must span EDR, NDR.',
    ])
  })

  it('a scenario with none of a part\'s fields says so plainly, and nothing else', () => {
    const parts = scenarioThesis({ scenario_id: 'SIM-X-001', name: 'Bare', steps: [] })
    for (const part of parts) {
      expect(part.written).toBe(false)
      expect(part.lines).toEqual(['Not written for this scenario yet.'])
      expect(part.sources).toEqual([])
    }
  })

  it('tolerates no document at all without inventing one', () => {
    expect(scenarioThesis(null).every((p) => !p.written)).toBe(true)
  })
})

describe('ttpThesis', () => {
  it('reads each part off the card', () => {
    const p = byKey(ttpThesis(CARD))
    // The first sentence of identity.description — the summary keeps its own section.
    expect(p.thesis.lines).toEqual([
      'FTP remains a favored exfiltration channel precisely because it is unencrypted, ubiquitous on internal file-transfer appliances, and frequently allowed outbound through legacy firewall rules.',
    ])
    expect(p.why.lines).toEqual([
      'Validate that NGFW App-ID + XSIAM Network Security Analytics detect outbound FTP with cleartext credentials and STOR data egress from a non-admin endpoint.',
    ])
    expect(p.proves.lines).toEqual([
      'Outbound FTP App-ID and cleartext credentials are detected from NGFW telemetry.',
      'STOR upload and credential exposure stitch into a single exfiltration incident.',
    ])
    expect(p.needs.lines).toEqual([
      'Target: linux; privilege: user.',
      CARD.execution.prerequisites[0].description,
      CARD.execution.prerequisites[1].description,
    ])
  })

  it('never repeats the summary as the thesis', () => {
    const p = byKey(ttpThesis({ ...CARD, identity: { summary: CARD.identity.summary } }))
    expect(p.thesis.written).toBe(false)
    expect(p.thesis.lines).toEqual(['Not written for this card yet.'])
  })

  it('a card with no threat scenarios has nothing written for why or what proves it', () => {
    const p = byKey(ttpThesis({ ...CARD, panw_mapping: { products: [] } }))
    expect(p.why.lines).toEqual([notWritten('card')])
    expect(p.proves.lines).toEqual([notWritten('card')])
    expect(p.thesis.written).toBe(true)
  })
})

describe('detectionThesis', () => {
  it('a BIOC: its description, the step it serves, the criterion that proves it, what it needs', () => {
    const p = byKey(detectionThesis('biocs', BIOC, CARD))
    expect(p.thesis.lines).toEqual(['NGFW EAL — outbound file transfer via FTP STOR command + 4 KB data payload.'])
    expect(p.why.lines).toEqual([
      'STOR upload and credential exposure stitch into a single exfiltration incident.',
      'Covers T1048.003 Exfiltration Over Unencrypted Non-C2 Protocol, T1071 Application Layer Protocol.',
    ])
    expect(p.proves.lines).toEqual([
      'BIOC `NGFW EAL outbound FTP STOR file transfer with data payload` returns >= 1 row with bytes_out > 0.',
    ])
    expect(p.needs.lines).toEqual([OWNERSHIP_MEANING.deploy, 'Reads dataset panw_ngfw_traffic_raw.'])
  })

  it('an XQL query: no description, so its thesis is not written; its rows are what proves it', () => {
    const p = byKey(detectionThesis('xql_queries', XQL_PROOF, CARD))
    expect(p.thesis.lines).toEqual(['Not written for this detection yet.'])
    expect(p.why.lines).toEqual([
      'Outbound FTP App-ID and cleartext credentials are detected from NGFW telemetry.',
      'Purpose on this card: validation.',
    ])
    expect(p.proves.lines).toEqual([
      'XQL `NGFW App-ID FTP outbound session from non-admin endpoint` returns >= 1 row.',
      'Returns at least 1 row from panw_ngfw_traffic_raw.',
    ])
    expect(p.needs.lines).toEqual([OWNERSHIP_MEANING.run, 'Reads dataset panw_ngfw_traffic_raw.'])
  })

  it('a query that expects zero rows claims no proof of its own', () => {
    const p = byKey(detectionThesis('xql_queries', XQL_PREFLIGHT, CARD))
    expect(p.proves.written).toBe(false)
    expect(p.proves.lines).toEqual(['Not written for this detection yet.'])
  })

  it('a correlation rule is matched by its bare rule id', () => {
    const p = byKey(detectionThesis('correlation_rules', CORRELATION, CARD))
    expect(p.thesis.written).toBe(false)
    expect(p.why.lines).toEqual(['STOR upload and credential exposure stitch into a single exfiltration incident.'])
    expect(p.proves.lines).toEqual(['Correlation `CR-NDR-0005` raises a single data-exfiltration incident for the source.'])
    expect(p.needs.lines).toEqual([OWNERSHIP_MEANING.deploy])
  })

  it('an IOC: its context is the thesis; a criterion naming another string is not borrowed', () => {
    const p = byKey(detectionThesis('iocs', IOC, CARD))
    expect(p.thesis.lines).toEqual([IOC.context])
    expect(p.why.written).toBe(false)
    expect(p.proves.written).toBe(false)
    expect(p.needs.lines).toEqual([OWNERSHIP_MEANING.deploy])
  })

  it('an Analytics entry written as a bare string: only what it needs is known', () => {
    const p = byKey(detectionThesis('analytics_modules', ANALYTICS, CARD))
    expect(p.thesis.lines).toEqual([notWritten('detection')])
    expect(p.why.lines).toEqual([notWritten('detection')])
    expect(p.proves.lines).toEqual([notWritten('detection')])
    expect(p.needs.lines).toEqual([OWNERSHIP_MEANING['built-in']])
  })

  it('a name that is only a prefix of a backticked name does not match', () => {
    const short = { ...BIOC, name: 'NGFW EAL outbound FTP STOR', mitre_technique_ids: [] }
    const p = byKey(detectionThesis('biocs', short, CARD))
    expect(p.why.written).toBe(false)
    expect(p.proves.written).toBe(false)
  })

  it('without its card, a detection still reads what it carries', () => {
    const p = byKey(detectionThesis('biocs', BIOC, null))
    expect(p.thesis.written).toBe(true)
    expect(p.why.lines).toEqual(['Covers T1048.003, T1071.'])
    expect(p.proves.written).toBe(false)
  })
})

describe('helpers', () => {
  it('firstSentence stops at the first sentence end followed by a new sentence', () => {
    expect(firstSentence('One thing. Two things.')).toBe('One thing.')
    expect(firstSentence('Uses e.g. curl and wget. Then more.')).toBe('Uses e.g. curl and wget.')
    expect(firstSentence('No full stop at all')).toBe('No full stop at all')
    expect(firstSentence(undefined)).toBe('')
  })

  it('datasetAnchor reads a dataset or preset anchor, or nothing', () => {
    expect(datasetAnchor('preset = xdr_data\n| filter x')).toEqual({ kind: 'preset', name: 'xdr_data' })
    expect(datasetAnchor('dataset=cloud_audit_logs | filter y')).toEqual({ kind: 'dataset', name: 'cloud_audit_logs' })
    expect(datasetAnchor('BIOC(a) AND BIOC(b)')).toBeNull()
    expect(datasetAnchor(undefined)).toBeNull()
  })
})
