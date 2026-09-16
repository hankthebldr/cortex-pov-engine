/**
 * Tenant API probes and the validation sheets derived from their responses.
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

export function apiCatalog() {
  return [
    {
      key: 'alerts', method: 'POST', name: 'Alerts by BIOC name',
      path: '/public_api/v1/alerts/get_alerts_multi_events',
      validates: 'BIOC · ABIOC claims',
      status: '200 OK', timing: '312ms', rows: '0 rows',
      inputs: [
        { k: 'Filter field', v: 'alert_name' },
        { k: 'Filter value', v: 'bioc-edr-001-memory-read-service-acct' },
        { k: 'Window', v: 'run window · 24h' },
        { k: 'Scope needed', v: 'read:alerts' },
      ],
      body: '{\n  "request_data": {\n    "filters": [\n      { "field": "alert_source", "operator": "in", "value": ["BIOC"] },\n      { "field": "creation_time", "operator": "gte", "value": 1789404000000 }\n    ],\n    "search_from": 0,\n    "search_to": 100\n  }\n}',
      response: '{\n  "reply": {\n    "total_count": 0,\n    "result_count": 0,\n    "alerts": []\n  }\n}',
      note: 'empty result, 200',
      reads: 'Zero rows with a 200 means the alert did not exist in the window — recorded as awaiting, not as a miss, until the probe is re-run after the content gap closes.',
    },
    {
      key: 'xql-start', method: 'POST', name: 'Start XQL query',
      path: '/public_api/v1/xql/start_xql_query',
      validates: 'XQL claims',
      status: '200 OK', timing: '96ms', rows: 'query id',
      inputs: [
        { k: 'Query', v: 'preset = xdr_process' },
        { k: 'Time frame', v: 'relative · 24h' },
        { k: 'Scope needed', v: 'read:datasets' },
      ],
      body: '{\n  "request_data": {\n    "query": "preset = xdr_process | filter action_process_image_name = \\"gcore\\" | fields _time, agent_hostname, action_process_image_command_line",\n    "tenants": [],\n    "timeframe": { "relativeTime": 86400000 }\n  }\n}',
      response: '{\n  "reply": "3f9a1c7e-48b2-4d10-9f55-1c2d3e4f5a6b"\n}',
      note: 'async · returns a query id',
      reads: 'The call returns only a handle. Nothing is proven until the results call below returns rows, which is why a started query is never treated as a verification.',
    },
    {
      key: 'xql-results', method: 'POST', name: 'Get XQL query results',
      path: '/public_api/v1/xql/get_query_results',
      validates: 'XQL claims · MTTD',
      status: '200 OK', timing: '1.4s', rows: '2 rows',
      inputs: [
        { k: 'Query id', v: '3f9a1c7e-48b2…5a6b' },
        { k: 'Limit', v: '1000' },
        { k: 'Scope needed', v: 'read:datasets' },
      ],
      body: '{\n  "request_data": {\n    "query_id": "3f9a1c7e-48b2-4d10-9f55-1c2d3e4f5a6b",\n    "pending_flag": false,\n    "limit": 1000\n  }\n}',
      response: '{\n  "reply": {\n    "status": "SUCCESS",\n    "number_of_results": 2,\n    "results": { "data": [\n      { "_time": "2026-09-14T17:42:07Z",\n        "agent_hostname": "jumpbox-lin-01",\n        "action_process_image_command_line": "gcore -o /tmp/.cache/dump 2214" },\n      { "_time": "2026-09-14T17:42:54Z",\n        "agent_hostname": "jumpbox-lin-01",\n        "action_process_image_command_line": "strings /tmp/.cache/dump.2214" }\n    ] }\n  }\n}',
      note: '2 rows returned',
      reads: 'Two rows with timestamps. The first _time minus dispatch gives the measured MTTD that populates the sheet — the only number in the readout the tenant itself produced.',
    },
    {
      key: 'incidents', method: 'POST', name: 'Incidents for the run window',
      path: '/public_api/v1/incidents/get_incidents',
      validates: 'Correlation claims',
      status: '403 Forbidden', timing: '88ms', rows: 'denied',
      inputs: [
        { k: 'Filter', v: 'creation_time gte run start' },
        { k: 'Scope needed', v: 'read:incidents' },
        { k: 'Scope held', v: 'read:alerts' },
      ],
      body: '{\n  "request_data": {\n    "filters": [\n      { "field": "creation_time", "operator": "gte", "value": 1789404000000 }\n    ]\n  }\n}',
      response: '{\n  "reply": {\n    "err_code": 403,\n    "err_msg": "Forbidden: key scope does not include incidents",\n    "err_extra": null\n  }\n}',
      note: 'permissions artefact',
      reads: 'A 403 is not an absence of signal. The sheet records this claim as unverifiable with the reason, so nobody later reads it as a detection that failed to fire.',
    },
    {
      key: 'rules', method: 'GET', name: 'Correlation rules installed',
      path: '/public_api/v1/rules/correlations',
      validates: 'content presence',
      status: '200 OK', timing: '204ms', rows: '2 rows',
      inputs: [
        { k: 'Filter', v: 'rule_id in [corr-edr-001, corr-cdr-014]' },
        { k: 'Scope needed', v: 'read:correlations' },
      ],
      body: '—  (query string only)\n?rule_ids=corr-edr-001,corr-cdr-014',
      response: '{\n  "reply": { "rules": [\n    { "rule_id": "corr-edr-001", "enabled": false, "installed": true },\n    { "rule_id": "corr-cdr-014", "enabled": false, "installed": true }\n  ] }\n}',
      note: 'installed, disabled',
      reads: 'Both rules exist but are disabled. That distinction is the entire difference between NOT PRESENT and MISSED, and it comes from this call rather than from our assumption.',
    },
  ]
}

export function validationSheets() {
  return {
    verdicts: {
      label: 'Detection verdicts', n: '8',
      cols: '64px minmax(0,1.3fr) 74px minmax(0,1fr) 72px 96px',
      head: ['Type', 'Detection object', 'Door', 'Probe', 'Rows', 'Verdict'],
      rows: [
        ['BIOC', 'bioc-edr-001-memory-read-service-acct', 'AGT', 'get_alerts_multi_events', '0', 'awaiting'],
        ['ABIOC', 'abioc-edr-001-svc-acct-anomaly', 'ENG', 'get_alerts_multi_events', '0', 'awaiting'],
        ['XQL', 'xql-edr-001-enum-tokens', 'AGT', 'get_query_results', '2', 'verified'],
        ['BIOC', 'bioc-edr-001-tool-drop', 'AGT', 'get_alerts_multi_events', '0', 'awaiting'],
        ['Correlation', 'corr-edr-001-cross-plane-stitch', 'ENG', 'rules/correlations', '0', 'not present'],
        ['XQL', 'xql-cdr-014-bucket-exfil', 'CC', 'get_query_results', '0', 'awaiting'],
        ['BIOC', 'bioc-ndr-004-dns-entropy', 'BVM', 'rules/correlations', '0', 'not present'],
        ['IOC', 'ioc-edr-001-akia-key', 'API', 'get_incidents', '—', 'unverifiable'],
      ],
    },
    mttd: {
      label: 'MTTD table', n: '3',
      cols: 'minmax(0,1.3fr) 128px 128px 86px 96px',
      head: ['Detection object', 'Dispatched', 'First _time', 'MTTD', 'Source'],
      rows: [
        ['xql-edr-001-enum-tokens', '17:41:20Z', '17:42:07Z', '47s', 'tenant'],
        ['xql-edr-001-gcore-dump', '17:41:20Z', '17:42:54Z', '94s', 'tenant'],
        ['abioc-edr-001-svc-acct-anomaly', '17:41:20Z', '—', '—', 'awaiting'],
      ],
    },
    content: {
      label: 'Content presence', n: '4',
      cols: 'minmax(0,1.2fr) 96px 96px 104px minmax(0,1fr)',
      head: ['Object', 'Installed', 'Enabled', 'Verdict', 'From'],
      rows: [
        ['corr-edr-001-cross-plane-stitch', 'yes', 'no', 'not present', 'rules/correlations'],
        ['corr-cdr-014-secret-then-egress', 'yes', 'no', 'not present', 'rules/correlations'],
        ['bioc-edr-001-memory-read', 'yes', 'yes', 'installed', 'rules/correlations'],
        ['ioc-pack-marketplace', 'no', '—', 'not installed', 'rules/correlations'],
      ],
    },
    uctc: {
      label: 'UC / TC verdicts', n: '6',
      cols: '92px minmax(0,1.2fr) 104px 96px minmax(0,1fr)',
      head: ['Test case', 'Assertion', 'Measured', 'Verdict', 'Evidence'],
      rows: [
        ['TC-EDR-01', 'credential read raises a BIOC', '0 alerts', 'pending', 'alerts probe'],
        ['TC-EDR-03', 'mttd_p95 under 120s', '47s', 'pass', 'xql results'],
        ['TC-EDR-04', 'enumeration visible in xdr_process', '2 rows', 'pass', 'xql results'],
        ['TC-CDR-02', 'role assumption correlates to host', 'n/a', 'not present', 'rules probe'],
        ['TC-NDR-01', 'dns entropy raises a detection', 'n/a', 'not present', 'rules probe'],
        ['TC-ITDR-02', 'incident opened for the chain', 'denied', 'unverifiable', 'incidents 403'],
      ],
    },
  }
}
