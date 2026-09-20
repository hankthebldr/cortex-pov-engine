/**
 * The evidence collection and the analytics data-source breakdown.
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

export const COLL_GROUPS = [
  ['runs', 'Run records', 3, 'dispatch, steps, exit codes, wall clock', 0.4],
  ['verdicts', 'Detection verdicts', 11, 'observed · pending · not-executed per object', 0.1],
  ['probes', 'Tenant probes', 8, 'XQL probe text and row counts per claim', 0.2],
  ['topology', 'Topology snapshots', 3, 'lane graph per run, as rendered', 1.8],
  ['logs', 'Step output & runtime metadata', 142, 'stdout, pids, identities, clock skew', 6.2],
  ['digests', 'Package digests', 5, 'sha256 as carried in and as verified on host', 0.1],
  ['uctc', 'UC / TC verdicts', 34, 'per test case, with measured values', 0.3],
  ['shots', 'Console captures', 0, 'nothing captured yet', 0],
]

/* Analytics detections are scored by the Engine, but only over the datasets
   that actually arrive. An absent source is why an ABIOC never fired — which
   exports as not-addressable, not as a miss. */
export const ANALYTICS_SOURCES = [
  ['XDR Agent · process & network', 'AGT', 'xdr_data', 74, 'feeding', 'ABIOC anomaly scoring · cross-plane stitch'],
  ['Authentication · AD / LDAP', 'DC', 'authentication', 31, 'feeding', 'ITDR analytics · impossible-travel'],
  ['Cloud audit · AWS / GCP', 'CC', 'cloud_audit_logs', 28, 'feeding', 'CDR analytics · role-assumption anomaly'],
  ['Kubernetes audit', 'CC', 'k8s_audit', 14, 'feeding', 'container exec and secret-read analytics'],
  ['Identity provider · SSO', 'DC', 'saas_auth', 12, 'feeding', 'session anomaly · MFA fatigue'],
  ['EDR file & registry', 'AGT', 'xdr_file', 10, 'feeding', 'staging and persistence analytics'],
  ['Proxy / SWG', 'DC', 'http_log', 9, 'partial', 'exfil-to-personal-storage analytics'],
  ['DNS · resolver logs', 'BVM', 'dns_story', 6, 'partial', 'tunnel entropy · beacon periodicity'],
  ['Firewall traffic · NGFW', 'BVM', 'network_story', 0, 'absent', 'egress volume · session analytics'],
  ['Email · third-party', 'DC', 'msft_email', 0, 'absent', 'phishing-to-execution correlation'],
  ['Threat intel match', 'ENG', 'indicators', 0, 'absent', 'IOC-backed analytics enrichment'],
]
