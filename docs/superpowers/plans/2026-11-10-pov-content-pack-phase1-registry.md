# POV Content Pack Phase 1 (Dataset Requirement Registry) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every dataset a TTP card reads has a stated prerequisite (what the tenant and the source product need), derived per scenario and served read-only, with the hunt queries served as paste-ready text.

**Architecture:** One hand-kept registry, `spec/dataset_requirements.yml`, loaded and validated by a pure module in the `identity_spec` pattern (malformed raises, absent degrades). A second pure module walks `scenario -> cited cards -> xql_queries[].dataset -> registry` and returns a manifest dict. Three GET routes and one health component expose it. No outbound call anywhere.

**Tech Stack:** Python 3.11, FastAPI, PyYAML (already a dependency), pytest.

**Spec:** `docs/superpowers/specs/2026-11-10-pov-content-pack-design.md` (§3, §5 query library, §6, §7, §9, §10 phase 1, §11).

**Branch:** `feature/api-dataset-requirements`, cut from `dev`. Stage by name. No PR until asked.

## Global Constraints

- No write path to Cortex or any firewall; no outbound call in any code added here.
- A missing registry entry is degraded, never "nothing required". An absent registry file returns 200 with `registry_loaded: false`.
- An unrecognised shape raises. No tolerant default for an unknown `produced_by`, `kind`, `product` or `requirement`.
- Row state is printed in words: `confirmed in a tenant` or `from documentation`. `tenant_verified` is the integer `0` in every response.
- No scenario YAML and no card JSON changes.
- Registry names a tenant shows are never invented: `name: null` until someone confirms it.
- Each new guard is observed failing with its change reverted, and the commit message says so.
- Counts in docs come from a command, not from this plan.

## Deviations from the spec, decided here

1. **Own route, not a manifest section.** The sibling spec's `/tenant-content` manifest is not built. Phase 1 serves `GET /api/scenarios/{id}/prerequisites`. `core/engine/prerequisites.py::derive` is the function that manifest embeds later.
2. **No `emulated` row state yet.** Knowing a run used an emitter needs the per-step `emitter` field (sibling spec, phase 2). Phase 1 prints `can_be_emulated_by` from the registry and never marks a row satisfied.
3. **Drift guard direction.** `KNOWN_DATASETS` also holds non-dataset tokens (`benign`, `risky_drive`). The guard is: registry keys are a subset of `KNOWN_DATASETS`, and a superset of the datasets card queries name.

## Review Focus

1. A card query with no `dataset` field (13 of 626 today): the manifest must list it under `notes` as `DATASET_UNDECLARED`, not drop it. Test in Task 3.
2. A scenario whose steps cite no card: `NO_CARD_BINDING`, never an empty list that reads as "no prerequisites". Test in Task 3.
3. The same dataset reached through three cards: one row, with all three cards listed. Test in Task 3.
4. A registry file that is present but empty, or a YAML list at the root: raises, and `/api/health` reports the component degraded with the path. Tests in Tasks 1 and 4.
5. An unknown scenario id on each new route: 404 in the structured error shape, not a 200 with empty sections. Test in Task 4.

---

### Task 1: Registry loader and vocabulary

**Files:**
- Create: `core/engine/dataset_requirements.py`
- Test: `tests/engine/test_dataset_requirements.py`

**Interfaces:**
- Produces:
  - `class DatasetRequirementsError(ValueError)`
  - `PRODUCED_BY: frozenset[str]` = `cortex-xdr-agent, cortex-xsiam, cortex-cloud, cortex-xpanse, strata-ngfw, prisma-browser, prisma-airs, idira, okta, microsoft-entra, microsoft-365, microsoft-windows, google-workspace, cloud-provider-audit, kubernetes, proofpoint, cisco-asa, ivanti, koi, saas-generic, cortexsim-synthetic`
  - `CORTEX_SIDE_KINDS: frozenset[str]` = `built_in, data_source, marketplace_pack, collector, broker_applet`
  - `SOURCE_REQUIREMENTS: dict[str, frozenset[str]]`; `strata-ngfw` maps to `log_forwarding, threat_profile_attached, url_filtering_logged, dns_security_logged, decryption_for_app_id, enhanced_application_logs, globalprotect_logs, allow_rule_for_sim_traffic`; every other product maps to `audit_log_export, api_integration, agent_installed, log_forwarding`
  - `@dataclass(frozen=True) class Verified: by: str; on: str; how: str` with `@property state -> str` returning `"confirmed in a tenant"` when all three are non-blank, else `"from documentation"`
  - `@dataclass(frozen=True) class CortexSide: kind: str; name: str | None; verified: Verified`
  - `@dataclass(frozen=True) class SourceSide: product: str; requirement: str; detail: str; verified: Verified`
  - `@dataclass(frozen=True) class DatasetRequirement: dataset: str; produced_by: str; cortex_side: tuple[CortexSide, ...]; source_side: tuple[SourceSide, ...]; emulated_by: tuple[str, ...]` with `to_dict() -> dict`
  - `parse_registry(data: object, path: str) -> dict[str, DatasetRequirement]`
  - `load() -> dict[str, DatasetRequirement] | None` (lru-cached; `None` when the file is absent)
  - `registry_source() -> dict[str, str]` returning `{"path", "source"}` with source `"file"` or `"absent"`
  - `registry_path() -> str` = `{CORTEXSIM_BASE_DIR}/spec/dataset_requirements.yml`

- [ ] **Step 1: Write the failing tests**

```python
GOOD = {"panw_ngfw_traffic_raw": {
    "produced_by": "strata-ngfw",
    "cortex_side": [{"kind": "data_source", "name": None, "verified": {"by": "", "on": "", "how": ""}}],
    "source_side": [{"product": "strata-ngfw", "requirement": "log_forwarding",
                     "detail": "Traffic logs forwarded to the Cortex tenant",
                     "verified": {"by": "", "on": "", "how": ""}}],
    "emulated_by": ["ngfw_eal_emitter"]}}

def test_parses_a_valid_entry():
    reg = parse_registry(GOOD, "p")
    row = reg["panw_ngfw_traffic_raw"]
    assert row.produced_by == "strata-ngfw"
    assert row.cortex_side[0].name is None
    assert row.source_side[0].verified.state == "from documentation"

def test_verified_needs_all_three_fields():
    assert Verified("hr", "2026-11-12", "").state == "from documentation"
    assert Verified("hr", "2026-11-12", "saw it in deathstar").state == "confirmed in a tenant"

@pytest.mark.parametrize("mutate, needle", [
    (lambda d: d["panw_ngfw_traffic_raw"].update(produced_by="strata"), "produced_by"),
    (lambda d: d["panw_ngfw_traffic_raw"]["cortex_side"][0].update(kind="pack"), "kind"),
    (lambda d: d["panw_ngfw_traffic_raw"]["source_side"][0].update(requirement="logs"), "requirement"),
    (lambda d: d["panw_ngfw_traffic_raw"]["source_side"][0].update(detail=" "), "detail"),
    (lambda d: d["panw_ngfw_traffic_raw"].update(extra=1), "extra"),
    (lambda d: d["panw_ngfw_traffic_raw"]["cortex_side"][0].update(name=""), "name"),
])
def test_rejects_unrecognised_shape(mutate, needle):
    data = copy.deepcopy(GOOD); mutate(data)
    with pytest.raises(DatasetRequirementsError, match=needle):
        parse_registry(data, "p")

@pytest.mark.parametrize("root", [None, [], "x", {}])
def test_rejects_non_mapping_or_empty_root(root):
    with pytest.raises(DatasetRequirementsError):
        parse_registry(root, "p")

def test_absent_file_is_none_not_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CORTEXSIM_BASE_DIR", str(tmp_path))
    load.cache_clear()
    assert load() is None
    assert registry_source()["source"] == "absent"

def test_present_but_unparseable_raises(tmp_path, monkeypatch):
    (tmp_path / "spec").mkdir(); (tmp_path / "spec" / "dataset_requirements.yml").write_text(": [")
    monkeypatch.setattr(settings, "CORTEXSIM_BASE_DIR", str(tmp_path)); load.cache_clear()
    with pytest.raises(DatasetRequirementsError):
        load()
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=core .venv/bin/python -m pytest tests/engine/test_dataset_requirements.py -q`
Expected: collection error, `No module named 'engine.dataset_requirements'`.

- [ ] **Step 3: Implement the module**

Follow `core/engine/identity_spec.py` for the load and error-message style. `cortex_side[].name` is `None` or a non-blank string. An empty `source_side` list is valid (first-party datasets such as `xsiam_incidents`); an empty `cortex_side` list is not. Unknown keys at any level raise. `emulated_by` entries are non-blank strings; they are checked against the plugin registry in Task 2, not here.

- [ ] **Step 4: Run to verify pass**

Run the Step 2 command. Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add core/engine/dataset_requirements.py tests/engine/test_dataset_requirements.py
git commit -m "feat(api): dataset requirement registry loader"
```

---

### Task 2: Seed the registry and pin it to the corpus

**Files:**
- Create: `spec/dataset_requirements.yml`
- Test: `tests/engine/test_dataset_requirements_corpus.py`

**Interfaces:**
- Consumes: `load()`, `PRODUCED_BY` from Task 1; `engine.ttp_catalog.catalog.all_raw()`; `KNOWN_DATASETS` from `detection_scanner/scripts/validate.py`; the EAL plugin names from `core/eal_simulator` (use the same registry `GET /api/eal/plugins` reads).
- Produces: `card_datasets(all_raw: dict[str, dict]) -> dict[str, set[str]]` in `core/engine/dataset_requirements.py`, mapping dataset to the card ids whose `detections.xql_queries[].dataset` names it. Blank and missing values are skipped.

- [ ] **Step 1: Write the failing tests**

```python
def test_every_card_dataset_is_registered():
    missing = sorted(set(card_datasets(catalog.all_raw())) - set(load()))
    assert missing == [], f"datasets read by cards with no requirement entry: {missing}"

def test_registry_keys_are_known_datasets():
    assert sorted(set(load()) - KNOWN_DATASETS) == []

def test_emulated_by_names_real_plugins():
    names = eal_plugin_names()
    bad = {(k, p) for k, r in load().items() for p in r.emulated_by if p not in names}
    assert bad == set()

def test_seed_claims_no_tenant_confirmation_it_does_not_have():
    confirmed = [k for k, r in load().items()
                 for row in (*r.cortex_side, *r.source_side)
                 if row.verified.state == "confirmed in a tenant"]
    assert confirmed == []   # edit this list deliberately when a DC verifies a row

def test_ngfw_datasets_require_log_forwarding():
    for k, r in load().items():
        if r.produced_by == "strata-ngfw":
            assert "log_forwarding" in {s.requirement for s in r.source_side}, k
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=core .venv/bin/python -m pytest tests/engine/test_dataset_requirements_corpus.py -q`
Expected: FAIL, `load()` returns `None`.

- [ ] **Step 3: Implement `card_datasets` and author the registry**

Get the dataset list from the command, not from this plan:

```bash
PYTHONPATH=core .venv/bin/python -c "from engine.ttp_catalog import catalog, default_corpus_dir; from config import settings; catalog.load(default_corpus_dir(settings.CORTEXSIM_BASE_DIR)); from engine.dataset_requirements import card_datasets; [print(k, len(v)) for k, v in sorted(card_datasets(catalog.all_raw()).items())]"
```

Authoring rules for each entry:
- `produced_by`: the product that emits the records. `xdr_data` is `cortex-xdr-agent`; `panw_ngfw_*`, `pan_ngfw_traffic`, `pan_dns` are `strata-ngfw`; `network_story`, `xsiam_incidents`, `threat_intel` are `cortex-xsiam` with an empty `source_side`; `cortexsim_*` are `cortexsim-synthetic`.
- `cortex_side`: one row. `kind: built_in` for first-party Cortex datasets, otherwise `data_source`. `name: null` on every row. Do not write a marketplace pack name.
- `source_side`: the conditions from spec §7 for Strata (`panw_ngfw_threat_raw` adds `threat_profile_attached`; `pan_dns` adds `dns_security_logged`; `panw_ngfw_globalprotect_raw` adds `globalprotect_logs`); `agent_installed` for `xdr_data`; `audit_log_export` or `api_integration` for third-party audit sources. `detail` is one plain sentence.
- `emulated_by`: only plugins listed for that data source in `docs/reference/eal-plugin-catalog.md`.
- Every `verified` is `{by: "", on: "", how: ""}`.
- No `idira` entry: no card reads an Idira dataset.

- [ ] **Step 4: Run to verify pass, then prove the guard**

Run the Step 2 command; expected: 5 passed. Then delete one entry from the YAML, re-run, confirm `test_every_card_dataset_is_registered` fails naming it, and restore.

- [ ] **Step 5: Commit**

```bash
git add spec/dataset_requirements.yml core/engine/dataset_requirements.py tests/engine/test_dataset_requirements_corpus.py
git commit -m "feat(api): seed dataset requirements for every card dataset"
```

---

### Task 3: Derive a scenario's prerequisites

**Files:**
- Create: `core/engine/prerequisites.py`
- Test: `tests/engine/test_prerequisites.py`

**Interfaces:**
- Consumes: `DatasetRequirement`, `load()` (Task 1).
- Produces: `derive(scenario: dict, cards: dict[str, dict], registry: dict[str, DatasetRequirement] | None) -> dict`. `scenario` is `Scenario.to_dict()`; `cards` is `catalog.all_raw()`. Pure: no I/O, no globals.

Return shape (exact keys):

```json
{"scenario_id": "SIM-MP-001", "registry_loaded": true, "tenant_verified": 0, "write_path": false,
 "cards": ["TTP-2026-0047"],
 "datasets": [{"dataset": "panw_ngfw_traffic_raw", "registered": true, "produced_by": "strata-ngfw",
               "cards": ["TTP-2026-0047"],
               "cortex_side": [{"kind": "data_source", "name": null, "state": "from documentation"}],
               "source_side": [{"product": "strata-ngfw", "requirement": "log_forwarding",
                                "detail": "...", "state": "from documentation"}],
               "can_be_emulated_by": ["ngfw_eal_emitter"]}],
 "summary": {"datasets": 1, "unregistered": 0, "confirmed_rows": 0, "documentation_rows": 2},
 "notes": []}
```

`notes` entries are `{"code", "detail"}` with codes `NO_CARD_BINDING`, `CARD_NOT_FOUND` (one per missing card id), `DATASET_UNDECLARED` (one per query, naming card and query), `UNREGISTERED_DATASET` (one per dataset), `REGISTRY_ABSENT`. `datasets` and `cards` are sorted.

- [ ] **Step 1: Write the failing tests**

```python
def scen(*refs):
    return {"id": "SIM-T-001", "steps": [{"id": f"s{i}", "expected_detections": [{"ttp_ref": r}]}
                                         for i, r in enumerate(refs)]}
def card(*datasets):
    return {"detections": {"xql_queries": [{"name": f"q{i}", "dataset": d, "query": "x"}
                                            for i, d in enumerate(datasets)]}}
REG = parse_registry(GOOD, "p")   # GOOD from Task 1's test module

def test_one_row_per_dataset_listing_every_card():
    out = derive(scen("A", "B", "C"), {k: card("panw_ngfw_traffic_raw") for k in "ABC"}, REG)
    assert [d["dataset"] for d in out["datasets"]] == ["panw_ngfw_traffic_raw"]
    assert out["datasets"][0]["cards"] == ["A", "B", "C"]

def test_no_card_binding_is_a_note_not_an_empty_answer():
    out = derive({"id": "SIM-T-002", "steps": [{"id": "s", "expected_detections": []}]}, {}, REG)
    assert [n["code"] for n in out["notes"]] == ["NO_CARD_BINDING"]

def test_unregistered_dataset_is_listed_and_flagged():
    out = derive(scen("A"), {"A": card("mystery_raw")}, REG)
    assert out["datasets"][0] == {"dataset": "mystery_raw", "registered": False, "cards": ["A"]}
    assert out["summary"]["unregistered"] == 1
    assert "UNREGISTERED_DATASET" in [n["code"] for n in out["notes"]]

def test_query_without_dataset_is_reported():
    out = derive(scen("A"), {"A": card(None)}, REG)
    assert [n["code"] for n in out["notes"]] == ["DATASET_UNDECLARED"]

def test_missing_card_is_reported():
    assert "CARD_NOT_FOUND" in [n["code"] for n in derive(scen("ZZ"), {}, REG)["notes"]]

def test_absent_registry_is_degraded():
    out = derive(scen("A"), {"A": card("panw_ngfw_traffic_raw")}, None)
    assert out["registry_loaded"] is False and out["datasets"][0]["registered"] is False
    assert "REGISTRY_ABSENT" in [n["code"] for n in out["notes"]]

def test_never_claims_verification():
    out = derive(scen("A"), {"A": card("panw_ngfw_traffic_raw")}, REG)
    assert out["tenant_verified"] == 0 and out["write_path"] is False

def test_unrecognised_scenario_shape_raises():
    with pytest.raises(ValueError):
        derive({"id": "x", "steps": "nope"}, {}, REG)
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=core .venv/bin/python -m pytest tests/engine/test_prerequisites.py -q`
Expected: `No module named 'engine.prerequisites'`.

- [ ] **Step 3: Implement `derive`**

- [ ] **Step 4: Run to verify pass**

Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add core/engine/prerequisites.py tests/engine/test_prerequisites.py
git commit -m "feat(api): derive scenario prerequisites from card datasets"
```

---

### Task 4: Routes, health component, strict boot check

**Files:**
- Create: `core/api/prerequisites.py`
- Modify: `core/main.py` (router include beside `shelf_router`; boot check in the lifespan after the TTP catalog loads), `core/config.py` (new setting), `core/api/health.py` (new probe, registered where `identity_spec` is)
- Test: `tests/api/test_prerequisites_api.py`

**Interfaces:**
- Consumes: `derive` (Task 3), `load`, `registry_source`, `card_datasets` (Tasks 1 and 2).
- Produces:
  - `GET /api/prerequisites/datasets` returns `{"registry_loaded": bool, "path": str, "count": int, "datasets": [DatasetRequirement.to_dict() plus "cards": [...]], "unregistered": [str]}`
  - `GET /api/scenarios/{scenario_id}/prerequisites` returns the `derive` dict; unknown id is 404 `{"error", "code": "SCENARIO_NOT_FOUND", "detail"}`
  - `settings.CORTEXSIM_STRICT_DATASETS: bool = False`; when true, boot raises `DatasetRequirementsError` if any card dataset is unregistered
  - health component `dataset_requirements`: `ok` when loaded with zero unregistered; `degraded` with code `DATASET_REGISTRY_ABSENT`, `DATASET_REGISTRY_MALFORMED` or `DATASETS_UNREGISTERED` and a remediation line otherwise

- [ ] **Step 1: Write the failing tests**

Use the `client` fixture pattern from `tests/api/test_payloads_shelf.py`.

```python
def test_scenario_prerequisites_ok(client):
    r = client.get("/api/scenarios/SIM-MP-001/prerequisites")
    body = r.json()
    assert r.status_code == 200 and body["registry_loaded"] is True
    assert body["tenant_verified"] == 0 and body["write_path"] is False
    assert any(d["produced_by"] == "strata-ngfw" for d in body["datasets"])

def test_unknown_scenario_is_structured_404(client):
    r = client.get("/api/scenarios/SIM-NOPE-999/prerequisites")
    assert r.status_code == 404 and r.json()["code"] == "SCENARIO_NOT_FOUND"

def test_registry_listing_counts_and_has_no_unregistered(client):
    body = client.get("/api/prerequisites/datasets").json()
    assert body["count"] == len(body["datasets"]) > 0 and body["unregistered"] == []

def test_absent_registry_is_200_degraded(client, monkeypatch):
    monkeypatch.setattr("api.prerequisites.load", lambda: None)
    body = client.get("/api/scenarios/SIM-MP-001/prerequisites").json()
    assert body["registry_loaded"] is False

def test_health_reports_absent_registry_as_degraded(client, monkeypatch):
    monkeypatch.setattr("api.health.dataset_requirements_load", lambda: None)
    comp = client.get("/api/health").json()["components"]["dataset_requirements"]
    assert comp["status"] == "degraded" and comp["code"] == "DATASET_REGISTRY_ABSENT"

def test_no_outbound_call(client, monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: pytest.fail("outbound call"))
    assert client.get("/api/scenarios/SIM-MP-001/prerequisites").status_code == 200

def test_strict_flag_refuses_boot_on_unregistered_dataset(monkeypatch):
    monkeypatch.setattr(settings, "CORTEXSIM_STRICT_DATASETS", True)
    with pytest.raises(DatasetRequirementsError, match="mystery_raw"):
        check_datasets_registered({"mystery_raw": {"TTP-X"}}, {})
```

`check_datasets_registered(card_ds: dict[str, set[str]], registry: dict | None) -> list[str]` lives in `core/engine/dataset_requirements.py`: returns the sorted unregistered names, and raises when the strict setting is on and the list is non-empty.

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=core .venv/bin/python -m pytest tests/api/test_prerequisites_api.py -q`
Expected: 404s and an import error for `check_datasets_registered`.

- [ ] **Step 3: Implement**

Check that `GET /api/scenarios/{scenario_id}/prerequisites` is not shadowed: register the sub-path route in `core/api/prerequisites.py` and include that router **before** `scenarios_router` in `core/main.py`, as `drafts_router` is.

- [ ] **Step 4: Run to verify pass, then the wider suites**

Run the Step 2 command (7 passed), then `PYTHONPATH=core .venv/bin/python -m pytest tests/api tests/engine -q`. Expected: no new failures.

- [ ] **Step 5: Commit**

```bash
git add core/api/prerequisites.py core/main.py core/config.py core/api/health.py core/engine/dataset_requirements.py tests/api/test_prerequisites_api.py
git commit -m "feat(api): serve scenario prerequisites and report registry health"
```

---

### Task 5: Query library as paste-ready text

**Files:**
- Create: `core/engine/query_library.py`
- Modify: `core/api/prerequisites.py`
- Test: `tests/engine/test_query_library.py`, extend `tests/api/test_prerequisites_api.py`

**Interfaces:**
- Produces:
  - `render(scenario: dict, cards: dict[str, dict], purposes: tuple[str, ...] = ("hunt", "investigation")) -> str`
  - `GET /api/scenarios/{scenario_id}/query-library` returning `text/plain; charset=utf-8`, with optional `?purpose=hunt,investigation,validation,metric`; an unknown purpose is 422

Text format, one block per query, cards in id order, queries in card order:

```
# SIM-MP-001 — query library (hunt, investigation)
# Paste into XQL Search. Authored, not run against any tenant.

## <query name>
# card: TTP-2026-0047   purpose: hunt   dataset: panw_ngfw_traffic_raw
<query text>
```

A scenario with no matching query returns the two header lines plus `# No hunt or investigation query on this scenario's cards.`

- [ ] **Step 1: Write the failing tests**

```python
def test_only_requested_purposes():
    cards = {"A": {"detections": {"xql_queries": [
        {"name": "h", "purpose": "hunt", "dataset": "d", "query": "dataset = d"},
        {"name": "v", "purpose": "validation", "dataset": "d", "query": "dataset = d | limit 1"}]}}}
    text = render(scen("A"), cards)
    assert "## h" in text and "## v" not in text

def test_empty_result_says_so():
    assert "# No hunt or investigation query" in render(scen("A"), {"A": {"detections": {"xql_queries": []}}})

def test_query_text_is_verbatim():
    q = "dataset = d\n| filter a = \"b\"\n| comp count() as n"
    cards = {"A": {"detections": {"xql_queries": [{"name": "h", "purpose": "hunt", "dataset": "d", "query": q}]}}}
    assert q in render(scen("A"), cards)

def test_route_is_plain_text_and_rejects_unknown_purpose(client):
    r = client.get("/api/scenarios/SIM-MP-001/query-library")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    assert client.get("/api/scenarios/SIM-MP-001/query-library?purpose=fun").status_code == 422
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=core .venv/bin/python -m pytest tests/engine/test_query_library.py tests/api/test_prerequisites_api.py -q`

- [ ] **Step 3: Implement**

- [ ] **Step 4: Run to verify pass**

- [ ] **Step 5: Commit**

```bash
git add core/engine/query_library.py core/api/prerequisites.py tests/engine/test_query_library.py tests/api/test_prerequisites_api.py
git commit -m "feat(api): serve a scenario's hunt queries as paste-ready text"
```

---

### Task 6: Docs, counted ground truth, gates

**Files:**
- Create: `docs/reference/dataset-requirements.md`
- Modify: `docs/reference/api-and-agent-surface.md`, `docs/reference/README.md`, `CLAUDE.md`, `CHANGELOG.md`, and whichever ground-truth generator `make check-ground-truth` reads if the route count is counted there

- [ ] **Step 1: Write `docs/reference/dataset-requirements.md`**

Contents: what the registry is for; the entry schema and all three vocabularies copied from the module; how a DC confirms a row (fill `by`, `on`, `how`, and edit the pinned empty list in `test_seed_claims_no_tenant_confirmation_it_does_not_have`); the three routes with one example response each; what it does not prove (spec §10, phase 1 row); the counting snippet from Task 2 Step 3.

- [ ] **Step 2: Update the surface doc, README, CLAUDE.md and CHANGELOG**

Route count: take it from `PYTHONPATH=core .venv/bin/python -c "from main import app; print(len(app.routes))"` before and after. CLAUDE.md gains one short paragraph under Key Design Rules naming the registry, the "absent is degraded, malformed raises" rule and `tenant-verified: 0`. CHANGELOG entry under `[Unreleased]`.

- [ ] **Step 3: Run the gates**

```bash
make validate
PYTHONPATH=core .venv/bin/python -m pytest tests/ --ignore=tests/smoke --ignore=tests/e2e_isolated -q
```

Expected: both green. `check-adapters` may fail in a worktree with uninitialised Rust submodules; that is pre-existing and is reported, not fixed.

- [ ] **Step 4: Confirm the image ships the registry**

`core/Dockerfile` already has `COPY spec/ /app/spec/`. Verify with `grep -n "COPY spec/" core/Dockerfile`; no edit expected. Not verified in a built image by this plan: say so in the final report.

- [ ] **Step 5: Commit**

```bash
git add docs/reference/dataset-requirements.md docs/reference/api-and-agent-surface.md docs/reference/README.md CLAUDE.md CHANGELOG.md
git commit -m "docs(api): dataset requirement registry reference and counts"
```

---

## Not in this plan

- Indicator files, expected-findings lists, correlation field alignment (spec phase 2).
- Any console UI. The Readiness rows are spec phase 4.
- Marketplace pack names, PAN-OS config fragments, Idira entries.
- The `emulated` row state (needs the per-step `emitter` field).
