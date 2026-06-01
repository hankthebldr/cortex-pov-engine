# CortexSim — API & Data-per-Screen Map

> What data each screen actually has to work with. All endpoints are served by
> SimCore (FastAPI) under `/api`. The React client wrapper is
> `ui/src/api/client.js` (it unwraps envelope shapes like `{scenarios:[…]}` →
> array). **Design to these fields — don't invent new ones** without flagging a
> backend change.
>
> Important system fact: **SimCore generates signals INTO Cortex; it does not
> read alerts OUT.** So "was it detected?" is set by the DC via the validate
> endpoint (or, future, an XQL hook) — never streamed live from the tenant.

## Endpoint → screen matrix

| Endpoint | Method | Feeds | Notes |
|---|---|---|---|
| `/api/health` | GET | Header env pill | `{status, version}` (sensor health is placeholder) |
| `/api/scenarios[?plane=]` | GET | Library, rail counts, palette | `{scenarios:[…], …}` — 58 items |
| `/api/scenario/{id}` | GET | Library inspector, Launch | full detail incl. steps + identity |
| `/api/agents` | GET | Targets, Launch (pull) | `{agents:[…], total}` |
| `/api/agents/{id}/tasks` | GET | (agent beacon, not UI) | pull-mode task envelope `{task: …}` |
| `/api/run` | POST | Launch | `{scenario_id, mode, identity, target_agent_id?}` → `{run_id, mode, download_url?}` |
| `/api/scenarios/{id}/download` | GET | Launch (push) | self-contained bash/k8s bundle |
| `/api/runs` | GET | Live, Evidence, ticker, history badges | run list |
| `/api/runs/{id}` | GET | Live, Evidence | run detail incl. status |
| `/api/runs/{id}/abort` | POST | Telemetry strip ABORT | may 404 on older builds (handled) |
| `/api/results/{runId}` | GET | Evidence, Live | `{results:[…], coverage:{observed,total,pct,by_type}, mttd}` |
| `/api/results/{id}/validate` | PUT | Evidence (validate) | `{observed, notes}` → sets `observed_at`, computes `mttd_seconds` |
| `/api/runs/{id}/report?format=` | GET | Evidence export | markdown / json |
| `/api/runs/{id}/report/matrix` | GET | Evidence export | detection matrix CSV |
| `/api/runs/{id}/report/navigator` | GET | Evidence, Coverage | ATT&CK Navigator layer JSON |
| `/api/runs/{id}/report/bundle` | GET | Evidence "Export POV" | gzip tarball (narrative + matrix + navigator) |
| `/api/mitre/coverage` | GET | Coverage matrix | `{techniques:[…], by_tactic:{…}, summary:{…}}` |
| `/api/infra/modules?provider=` | GET | Environments | 11 modules: `{name, description, providers, required_params, optional_params, dependencies, content_tools}` |
| `/api/infra/generate` | POST | Environments | → `{bundle_id, download_url}` |
| `/api/infra/bundles` | GET | Targets (labs), Environments | prior bundles (currently 0) |
| `/api/infra/bundles/{id}/download` | GET | Environments | tar.gz |
| `/api/tools`, `/api/tool-adapters` | GET | Coverage sub-tabs | tool registry + adapters |
| `/api/eal/plugins`, `/api/eal/campaigns` | GET | Coverage sub-tabs / EAL | EAL plugin + campaign data |
| `/api/ttps` | GET | Coverage→TTP Browser | TTP cards |

## Key response shapes (real, abbreviated)

**Scenario (list item / detail):**
```jsonc
{
  "scenario_id": "SIM-EDR-001",
  "name": "Credential Dumping — /etc/shadow and Mimipenguin",
  "plane": "EDR",                       // EDR|CDR|NDR|ITDR|CLOUD_APP|ANALYTICS|AIRS|AI_ACCESS|AI_SPM|BROWSER|KOI
  "detection_types": ["Analytics","BIOC","IOC"],
  "mitre_tactic": "TA0006", "mitre_tactic_name": "Credential Access",
  "mitre_technique": "T1003.008", "mitre_technique_name": "OS Credential Dumping…",
  "threat_report": "Unit42 — …", "threat_report_url": "https://…",
  "execution_identity": { "default": "www-data", "options": ["www-data","root",…] },
  "push_supported": true, "pull_supported": true,
  "steps": [ { "id":"step-01", "name":"…", "command":"…", "identity":"…",
              "mitre_technique":"T1003", "expected_detections":[
                { "plane":"EDR","type":"BIOC","description":"…",
                  "detection_id":null,"ttp_ref":null } ] } ],
  "tags": ["edr","credential-access"], "author":"…"
}
```
Catalog scale: **58 scenarios · 195 steps · 342 expected detections** (every
scenario has ≥1). Per-plane detection counts: EDR 47, CDR 45, NDR 31, ANALYTICS
48, ITDR 22, CLOUD_APP 23, AI_ACCESS 24, AI_SPM 24, AIRS 25, BROWSER 24, KOI 29.

**Results for a run** (`/api/results/{runId}`):
```jsonc
{
  "results": [ {
    "id": 383, "run_id":"…", "step_id":"step-01", "step_name":"…",
    "plane":"EDR", "signal_type":"Analytics", "expected_detection":"…",
    "observed": false, "executed_at":"…", "observed_at":null,
    "mttd_seconds": null, "notes":"", "detection_id":null,
    "mitre_technique":"T1003.008" } ],
  "coverage": { "observed":0, "total":10, "pct":0.0,
                "by_type": { "Analytics":{total,observed,pct}, "BIOC":…, "IOC":… } },
  "mttd": { "count":0, "avg_seconds":null, "min_seconds":null, "max_seconds":null }
}
```

**Agent** (`/api/agents` → `agents[]`): `{ id, hostname?, os?/platform?,
last_seen?, … }` — UI derives live/stale from `last_seen` age (<60s = live).

**MITRE coverage** (`/api/mitre/coverage`):
`{ techniques: [{techniqueID, score/state, scenarioIds…}], by_tactic:{TAxxxx:[…]},
   summary:{techniques, detected, runs, …} }`.

**Run launch** (`POST /api/run`) → `{ run_id, mode, message,
download_url? }`. Push runs stay `status: pending` (bundle runs offline);
pull runs transition as the beacon executes.

## States the data implies (design for all)

- **Empty:** no agents, no runs, no results (ingestion 30–120s), no infra bundles,
  zero filter matches. These are the *first* views a DC sees — design them well.
- **In-flight:** run `status: running/pending`, partial detections, MTTD
  accumulating.
- **Terminal:** complete / failed / aborted; coverage finalized.
- **Degraded:** abort endpoint 404 (older SimCore), agent stale, health pill warn.
