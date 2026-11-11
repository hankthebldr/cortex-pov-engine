# Dataset requirement registry

Phase 1 of the POV content pack (design: `docs/superpowers/specs/2026-11-10-pov-content-pack-design.md`,
sections 3, 9 and 10). Counted 2026-11-10 on branch `feature/api-dataset-requirements`.

## What it is for

A detection card queries a dataset, and a dataset exists in a tenant only if a
specific source product is onboarded and configured to emit it. A DC who runs a
scenario without that source sees no detection, and the missing detection reads
as "Cortex missed it". The registry states, per dataset, what has to be true so
the DC can check before the run.

The one hand-authored file is `spec/dataset_requirements.yml`: one entry per
dataset that any card reads in `detections.xql_queries[].dataset`. Nothing is
authored per scenario. A scenario's prerequisites are derived at request time:
scenario, then the cards its `expected_detections[].ttp_ref` cite, then each
card's query datasets, then the registry entry.

Counted state: **31 datasets registered, 31 datasets read by the 191 cards, 0
unregistered, 0 registered but unread.** 31 cortex-side rows, 30 source-side
rows, **0 rows confirmed in a tenant.**

## What it does not prove

This is the phase 1 row of spec section 10. It proves that every dataset a card
reads has a stated requirement, or is flagged. It does **not** prove that any
requirement text is right in a tenant. Nothing in this feature contacts a
tenant; `tenant_verified` is 0 in every response. Every `cortex_side[].name` is
`null` because no one has read the exact name in a tenant, and no marketplace
pack or data-source name is written from memory.

## Entry schema

```yaml
panw_ngfw_traffic_raw:
  produced_by: strata-ngfw
  cortex_side:
    - kind: data_source          # one of the cortex-side kinds below
      name: null                 # exact name as the tenant shows it; null until read in a tenant
      verified: {by: "", "on": "", how: ""}
  source_side:
    - product: strata-ngfw
      requirement: log_forwarding
      detail: "One plain sentence."
      verified: {by: "", "on": "", how: ""}
  emulated_by: [ngfw_eal_emitter]
```

All four top-level keys are required; unknown keys are rejected. `cortex_side`
must not be empty. `source_side` may be empty (4 datasets: `asm_findings`,
`network_story`, `threat_intel`, `xsiam_incidents`, which Cortex produces itself).

### Vocabularies (from `core/engine/dataset_requirements.py`)

`produced_by` and `source_side[].product` (21 values):
`cisco-asa`, `cloud-provider-audit`, `cortex-cloud`, `cortex-xdr-agent`,
`cortex-xpanse`, `cortex-xsiam`, `cortexsim-synthetic`, `google-workspace`,
`idira`, `ivanti`, `koi`, `kubernetes`, `microsoft-365`, `microsoft-entra`,
`microsoft-windows`, `okta`, `prisma-airs`, `prisma-browser`, `proofpoint`,
`saas-generic`, `strata-ngfw`.

`cortex_side[].kind`: `built_in`, `data_source`, `marketplace_pack`,
`collector`, `broker_applet`. Today 24 rows are `data_source` and 7 are `built_in`.

`source_side[].requirement` depends on the product:

| Product | Allowed requirements |
|---|---|
| `strata-ngfw` | `log_forwarding`, `threat_profile_attached`, `url_filtering_logged`, `dns_security_logged`, `decryption_for_app_id`, `enhanced_application_logs`, `globalprotect_logs`, `allow_rule_for_sim_traffic` |
| every other product | `audit_log_export`, `api_integration`, `agent_installed`, `log_forwarding` |

### Authoring traps

- Quote the `on` key inside `verified` (`"on": ""`). Bare, YAML reads `on` as the
  boolean `true`, and the loader rejects a non-string key.
- Quote dates (`"on": "2026-11-12"`). Bare, YAML reads a date, and the loader rejects it.
- `cortex_side[].name` stays `null` until a DC has read the exact name in a tenant.
- `emulated_by` lists an EAL plugin only when that plugin's code names the dataset
  as the one it posts to. Cross-check `docs/reference/eal-plugin-catalog.md` and the plugin.
- Every dataset name must also be in `KNOWN_DATASETS` in
  `detection_scanner/scripts/validate.py`; a test pins the registry to it.

### Row states

Each row prints one of two states in words:

- `from documentation`: the default; at least one of `by`, `on`, `how` is blank.
- `confirmed in a tenant`: all three of `by`, `on` and `how` are non-blank.

There is no `emulated` state yet. The response carries `can_be_emulated_by`, the
`emulated_by` list, as a separate field. An `emulated` state needs a per-step
`emitter` field that does not exist. When an EAL emitter stands in for a source,
the source-side row still reads `from documentation`: emulated is not configured.

## How a DC confirms a row

1. In the tenant, read the exact object name or confirm the source-side setting.
2. In `spec/dataset_requirements.yml`, fill the row's `verified`: `by` (who),
   `"on"` (ISO date, quoted), `how` (what was looked at). For a cortex-side row,
   also set `name` to the name the tenant shows.
3. Edit the pinned empty list in
   `test_seed_claims_no_tenant_confirmation_it_does_not_have`
   (`tests/engine/test_dataset_requirements_corpus.py`) to include that row. The
   test exists so that no row is marked confirmed by accident.
4. A row confirmed in a tenant is still evidence of that tenant on that date,
   not of any other tenant.

## Routes

All three are read-only, read local files and the scenario table only, and make
no outbound call. Router: `core/api/prerequisites.py`, mounted before the
scenarios router so the sub-paths are not swallowed.

### `GET /api/prerequisites/datasets`

The registry, each entry joined to the card ids that read the dataset.
Envelope: `registry_loaded`, `path`, `count`, `datasets[]`, `unregistered[]`
(card datasets with no entry; all of them when the registry is absent). Real
output, last entry, trimmed:

```json
{
  "registry_loaded": true,
  "count": 31,
  "unregistered": [],
  "datasets": [
    {
      "dataset": "xsiam_incidents",
      "produced_by": "cortex-xsiam",
      "cortex_side": [
        {"kind": "built_in", "name": null,
         "verified": {"by": "", "on": "", "how": "", "state": "from documentation"}}
      ],
      "source_side": [],
      "emulated_by": [],
      "cards": ["TTP-2026-0142", "TTP-2026-0169", "TTP-2026-0170", "TTP-2026-0194", "TTP-2026-0196"]
    }
  ]
}
```

### `GET /api/scenarios/{scenario_id}/prerequisites`

What one scenario needs, derived from the registry. Real output for
`SIM-MP-001`, trimmed to the first dataset:

```json
{
  "scenario_id": "SIM-MP-001",
  "registry_loaded": true,
  "tenant_verified": 0,
  "write_path": false,
  "cards": ["TTP-2026-0047"],
  "datasets": [
    {
      "dataset": "pan_dns",
      "registered": true,
      "produced_by": "strata-ngfw",
      "cards": ["TTP-2026-0047"],
      "cortex_side": [{"kind": "data_source", "name": null, "state": "from documentation"}],
      "source_side": [
        {"product": "strata-ngfw", "requirement": "log_forwarding",
         "detail": "The firewall forwards its logs to the Cortex tenant.",
         "state": "from documentation"},
        {"product": "strata-ngfw", "requirement": "dns_security_logged",
         "detail": "DNS Security is enabled on the policy the test traffic matches, so DNS queries are logged.",
         "state": "from documentation"}
      ],
      "can_be_emulated_by": []
    }
  ],
  "summary": {"datasets": 3, "unregistered": 0, "confirmed_rows": 0, "documentation_rows": 7},
  "notes": []
}
```

Absence of evidence is a note, never an empty answer. Note codes:

| Code | Meaning |
|---|---|
| `NO_CARD_BINDING` | the scenario cites no card (no `ttp_ref` on any expected detection), so no dataset can be derived |
| `CARD_NOT_FOUND` | a cited card id is not in the catalog |
| `CARD_DECLARES_NO_QUERY` | a cited card carries no `xql_queries`; a card with only BIOC or IOC content still depends on a source, but it cannot be derived |
| `DATASET_UNDECLARED` | a card query names no dataset |
| `UNREGISTERED_DATASET` | the dataset has no registry entry (row has `registered: false`) |
| `REGISTRY_ABSENT` | the registry file is not loaded; every dataset reads as unregistered |

### `GET /api/scenarios/{scenario_id}/query-library`

The scenario's card queries as `text/plain; charset=utf-8`, to paste into XQL
Search. The header says the text is authored and not run against any tenant.
Cards come in id order and query text is verbatim. `?purpose=` takes a
comma-separated list from `hunt`, `investigation`, `validation`, `metric`; the
default is `hunt,investigation`. When nothing matches, the response adds a line
naming the purposes the cited cards do carry. Real output for `SIM-MP-001` with
the default purposes (the cards carry only validation queries):

```text
# SIM-MP-001 — query library (hunt, investigation)
# Paste into XQL Search. Authored, not run against any tenant.
# No hunt or investigation query on this scenario's cards.
# The cited cards carry 6 validation queries: add ?purpose=validation
```

With `?purpose=validation`, the first block, trimmed:

```text
## MP-001 NGFW Repetitive HTTP Beacon To testmynids
# card: TTP-2026-0047   purpose: validation   dataset: pan_ngfw_traffic
dataset = pan_ngfw_traffic
| filter _time > to_timestamp(current_time() - 1800)
...
```

A name, dataset, scenario id or card id containing a line-break character is
refused rather than rendered, because it would break the comment structure.

### Error codes

These routes return the error object at the **top level**:
`{"error", "code", "detail"}`. The older scenario routes nest it under
`"detail"`, so a client that reads one shape must not assume the other.

| HTTP | Code | When |
|---|---|---|
| 404 | `SCENARIO_NOT_FOUND` | unknown `scenario_id` |
| 422 | `PURPOSE_UNRECOGNISED` | `?purpose=` has an unknown or repeated purpose |
| 500 | `DATASET_REGISTRY_MALFORMED` | the registry file exists but cannot be read as a registry |
| 500 | `CARD_SHAPE_UNRECOGNISED` | a card, a card query or a scenario step has a shape the code does not recognise |

An absent registry is **not** an error: the datasets route answers 200 with
`registry_loaded: false`, and the prerequisites route answers 200 with
`registry_loaded: false` and a `REGISTRY_ABSENT` note.

## Health and boot

`GET /api/health` carries a `dataset_requirements` component. It is `ok` only
when the registry is loaded and no card dataset is unregistered. Degraded codes:
`DATASET_REGISTRY_ABSENT`, `DATASET_REGISTRY_MALFORMED`, `DATASETS_UNREGISTERED`,
`CARD_SHAPE_UNRECOGNISED`. A zero is degraded, not ok.

Boot: an absent or malformed registry does **not** stop boot by default; the
fault is logged and health plus the routes report it. With
`CORTEXSIM_STRICT_DATASETS=true` (default `false`), a malformed registry, a
malformed card shape or a card dataset with no entry refuses boot. The file is
read once per process; edit it, then restart SimCore. `core/Dockerfile` carries
`COPY spec/ /app/spec/`; the built image has not been checked for this file.

## Counting snippet

```bash
PYTHONPATH=core .venv/bin/python -c "from engine.ttp_catalog import catalog, default_corpus_dir; from config import settings; catalog.load(default_corpus_dir(settings.CORTEXSIM_BASE_DIR)); from engine.dataset_requirements import card_datasets; [print(k, len(v)) for k, v in sorted(card_datasets(catalog.all_raw()).items())]"
```

Run it from the repo root (it needs `CORTEXSIM_BASE_DIR` to resolve to the
checkout; pass it explicitly in a worktree). Count, do not quote: the dataset
list is what a card query reads today, and it moves when cards change.
