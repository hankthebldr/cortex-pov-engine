# HTTP collector backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The five backend additions the HTTP collector's Configure steps need: a lab-tenant matcher, a server-computed route verdict stored as the target's `declaration`, a `last_arrival` column, a detections-by-source read, a read-only arrival check, and the canary on `email_emitter`.

**Architecture:** Two pure modules carry the rules (`engine/lab_tenants.py`, `eal_simulator/collector_declaration.py`); a third (`eal_simulator/arrival.py`) turns one XQL reply into a counted / pending / not_checked result. The existing collector-targets router gains a `declaration` on PUT and a `POST /{name}/arrival`; the EAL router gains `GET /data-streams/{source_key}/detections`. Two nullable JSON columns join `collector_targets` through an additive `_migrate_*` step. No UI: the screens are v26 slice 4, after slice 3's Configure frame lands.

**Tech Stack:** Python 3.11, FastAPI, async SQLAlchemy over SQLite, pydantic v2, pytest. No new dependency.

**Spec:** `docs/superpowers/specs/2026-11-12-http-collector-wizard-design.md` (§5.2 verdicts and lab rule, §5.6 arrival table, §6 backend, §8 tests).

## Global Constraints

- Branch `feature/api-http-collector-backend`, cut from `origin/dev`. First commits: cherry-pick the spec and plan commits from `docs/docs-http-collector-wizard`. Work in a **new worktree**, not `funny-bartik-cd5ed4`: that one backs the running SimCore's bind mounts.
- Stage by name only (`git add <path>`); one commit per task; no PR until Henry asks.
- Test command (from the worktree root), written `PYTEST` below: `PATH=/Users/henry/Github/Github_desktop/cortex-pov-engine/.venv/bin:$PATH PYTHONPATH=core python -m pytest`.
- Gate A: every new test is run and seen failing before its code exists.
- No tenant write. The arrival query is fixed text, read-only, sent through the existing client (`start_xql_query`, which refuses write stages itself).
- Errors are structured: `{"error", "code", "detail"}` via `HTTPException(detail=…)`, as `collector_targets._err` does.
- The collector key is never returned, logged or echoed (existing guarantee; do not weaken it).
- Canary tokens match `^[a-z0-9][a-z0-9-]{5,63}$` (`analytics_emitter.CANARY_TOKEN_RE`); the raw field is `cortexsim_canary` (`CANARY_RAW_FIELD`).
- Native datasets: `panw_ngfw_traffic_raw`, `panw_ngfw_threat_raw`, `panw_ngfw_globalprotect_raw`.
- Test channel: `source_key` `test_channel`, vendor `cortexsim`, product `door_http`, dataset `cortexsim_door_http_raw`.
- `CORTEXSIM_DOOR_PROBE_TENANTS: str = ""` exactly as the #227 plan defines it (comma-separated hosts or URLs, matched by host, case-insensitive). If #227 has landed first, reuse its field and matcher instead of adding them.
- Timestamps: compare and store timezone-aware UTC (`datetime.now(timezone.utc)`); `connectors/base.py::coerce_utc` for anything parsed.
- Do not add "tenant-verified is 0" wording to docs, commits or the PR.

## Review Focus

1. **A PENDING reply read as zero.** `result_rows` returns `[]` when a reply has no `results`, so arrival must check `status` before counting: PENDING is `pending`, never `found: 0`. Test in Task 6.
2. **A typed vendor/product that names a native dataset for a non-native source** (`panw` / `ngfw_traffic` on the Okta source) writes into the firewall's dataset just the same. The verdict checks the resulting dataset as well as the card's. Test in Task 2, refusal in Task 4.
3. **A target edited from the old Collector targets panel** (PUT with no `declaration`) that changes `dataset` leaves a declaration describing a different dataset. It is cleared, and `last_arrival` with it. Test in Task 3.
4. **The lab setting written loosely**: `https://API-Deathstar.xdr.us.paloaltonetworks.com/ , ,x`. It matches by host, and empty entries are skipped. An empty setting makes nothing a lab tenant. Test in Task 1.
5. **A compose deployment.** A value in `.env` never reaches the container unless `docker-compose.yml` names it, so the lab tenant would be refused on the lab itself. Test in Task 1.

---

### Task 1: Lab-tenant setting and host matcher

**Files:**
- Create: `core/engine/lab_tenants.py`
- Modify: `core/config.py` (Settings field), `.env.example`, `docker-compose.yml` (environment list, beside `CORTEXSIM_TRUSTED_PROXIES`)
- Test: `tests/engine/test_lab_tenants.py`, `tests/test_compose_hardening.py`

**Interfaces:**
- Produces: `lab_tenant_hosts(raw: str | None = None) -> frozenset[str]` (reads `settings.CORTEXSIM_DOOR_PROBE_TENANTS` at call time when `raw` is None, never cached, so a test's monkeypatch is seen); `is_lab_tenant_host(host: str | None, raw: str | None = None) -> bool`; `host_of(url: str) -> str | None` (lowercased hostname, None when unparsable).

- [ ] **Step 1: Write the failing tests**

```python
# tests/engine/test_lab_tenants.py
from engine.lab_tenants import host_of, is_lab_tenant_host, lab_tenant_hosts

def test_entries_are_hosts_matched_case_insensitively():
    raw = "https://API-Deathstar.xdr.us.paloaltonetworks.com/ , ,lab.example.test"
    assert lab_tenant_hosts(raw) == frozenset(
        {"api-deathstar.xdr.us.paloaltonetworks.com", "lab.example.test"})
    assert is_lab_tenant_host("api-deathstar.XDR.us.paloaltonetworks.com", raw)

def test_empty_setting_makes_nothing_a_lab_tenant():
    assert lab_tenant_hosts("") == frozenset()
    assert not is_lab_tenant_host("api-deathstar.xdr.us.paloaltonetworks.com", "")

def test_unknown_or_missing_host_is_not_lab():
    assert not is_lab_tenant_host(None, "lab.example.test")
    assert not is_lab_tenant_host("other.example.test", "lab.example.test")

def test_host_of_reads_the_collector_url():
    assert host_of("https://API-x.example.test/logs/v1/event") == "api-x.example.test"
    assert host_of("not a url") is None
```

```python
# tests/test_compose_hardening.py (add)
def test_compose_passes_the_lab_tenant_setting(simcore):
    env = simcore["environment"]
    assert "CORTEXSIM_DOOR_PROBE_TENANTS=${CORTEXSIM_DOOR_PROBE_TENANTS:-}" in env
```

- [ ] **Step 2: Run them and see them fail**

Run: `PYTEST tests/engine/test_lab_tenants.py tests/test_compose_hardening.py -q`
Expected: FAIL (`ModuleNotFoundError: engine.lab_tenants`; the compose assertion fails).

- [ ] **Step 3: Implement**

`CORTEXSIM_DOOR_PROBE_TENANTS: str = ""` in `Settings`. In `lab_tenants.py`, an entry containing `://` is parsed with `urlsplit(...).hostname`; any other entry is stripped and lowercased; empty entries are dropped. `.env.example`: `CORTEXSIM_DOOR_PROBE_TENANTS=` with the comment "Lab tenants' API hosts (comma list). Set in the lab .env only, never in a POV image. Collector setup may write into native firewall datasets only on these." `docker-compose.yml`: add `- CORTEXSIM_DOOR_PROBE_TENANTS=${CORTEXSIM_DOOR_PROBE_TENANTS:-}` with a one-line comment.

- [ ] **Step 4: Run the tests, plus the env coverage guard**

Run: `PYTEST tests/engine/test_lab_tenants.py tests/test_compose_hardening.py tests/test_env_example_coverage.py tests/test_config.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/engine/lab_tenants.py core/config.py .env.example docker-compose.yml tests/engine/test_lab_tenants.py tests/test_compose_hardening.py
git commit -m "feat(api): add the lab tenant setting and host matcher"
```

### Task 2: Route verdict and vendor/product prefill

**Files:**
- Create: `core/eal_simulator/collector_declaration.py`
- Test: `tests/eal_simulator/test_collector_declaration.py`

**Interfaces:**
- Produces:
  - constants `NATIVE_DATASETS: frozenset[str]`, `TEST_CHANNEL = "test_channel"`, `TEST_CHANNEL_VENDOR = "cortexsim"`, `TEST_CHANNEL_PRODUCT = "door_http"`, `LIMITED_KINDS = frozenset({"biocs", "abiocs"})`;
  - `dataset_for(vendor: str, product: str) -> str` → `f"{vendor}_{product}_raw"` (as typed);
  - `prefill(source_key: str, card_dataset: str | None) -> tuple[str, str]`;
  - `@dataclass(frozen=True) RouteVerdict(verdict: str, dataset: str, card_dataset: str | None, tenant_datasets: tuple[str, ...], reading_kinds: tuple[str, ...])` with `to_dict() -> dict`;
  - `route_verdict(source_key: str, vendor: str, product: str, *, card_dataset: str | None, tenant_datasets: Iterable[str], reading_kinds: Iterable[str]) -> RouteVerdict`.
- Verdict values: `native_dataset`, `test_channel`, `matches`, `xql_only`, `name_differs`, `name_disputed`, `cannot_feed`.

The order, which the tests pin (measured on the real catalogue 2026-11-13):

1. `dataset_for(vendor, product)` or `card_dataset`, lowercased, in `NATIVE_DATASETS` → `native_dataset`.
2. `source_key == TEST_CHANNEL` → `test_channel`.
3. `card_dataset` ends in `_raw`: the resulting dataset equals it case-insensitively → `xql_only` when `reading_kinds & LIMITED_KINDS`, else `matches`; otherwise → `name_differs` (**Claude's call**, added to spec §5.2 in this task: "Records will land in `<dataset>`; these detections read `<card dataset>`.").
4. `card_dataset` not `_raw` and `tenant_datasets` non-empty and `card_dataset` not in it → `name_disputed`.
5. Otherwise → `cannot_feed`.

`prefill`: `card_dataset` ending in `_raw` → strip `_raw`, split at the first `_`; otherwise split `source_key` at its first `_`; a part with no `_` gives `(part, "logs")` (**Claude's call**).

- [ ] **Step 1: Write the failing tests**

```python
from eal_simulator.collector_declaration import prefill, route_verdict

V = lambda key, v, p, card, tenant=(), kinds=(): route_verdict(
    key, v, p, card_dataset=card, tenant_datasets=tenant, reading_kinds=kinds).verdict

def test_raw_card_dataset_matches_when_the_names_agree():
    assert V("duo", "duo", "auth", "duo_auth_raw") == "matches"
    assert V("duo", "Duo", "Auth", "duo_auth_raw") == "matches"

def test_raw_card_dataset_read_by_biocs_is_xql_only():
    assert V("x", "a", "b", "a_b_raw", kinds={"biocs", "xql_queries"}) == "xql_only"

def test_raw_card_dataset_with_other_names_differs():
    assert V("duo", "duo", "logs", "duo_auth_raw") == "name_differs"

def test_documented_tenant_name_makes_a_non_raw_card_disputed():
    assert V("okta", "okta", "sso", "okta_sso", tenant={"okta_sso_raw"}) == "name_disputed"

def test_non_raw_card_without_a_different_tenant_name_cannot_feed():
    assert V("aws_audit_log", "aws", "audit_log", "cloud_audit_logs",
             tenant={"amazon_aws_raw", "cloud_audit_logs"}) == "cannot_feed"
    assert V("google_workspace_audit_logs", "google", "workspace",
             "google_workspace_audit") == "cannot_feed"

def test_native_dataset_by_card_or_by_typed_name():
    assert V("pan_firewall_traffic_logs", "panw", "ngfw_traffic",
             "panw_ngfw_traffic_raw") == "native_dataset"
    assert V("okta", "PANW", "ngfw_threat", "okta_sso",
             tenant={"okta_sso_raw"}) == "native_dataset"

def test_test_channel():
    assert V("test_channel", "cortexsim", "door_http", None) == "test_channel"

def test_prefill_splits_card_dataset_then_source_key():
    assert prefill("duo", "duo_auth_raw") == ("duo", "auth")
    assert prefill("aws_audit_log", "cloud_audit_logs") == ("aws", "audit_log")
    assert prefill("okta", "okta_sso") == ("okta", "logs")
```

And one test over the live catalogue, so a catalogue change that moves a verdict is seen:

```python
def test_verdict_split_over_the_real_catalogue():
    # 20 sources with a collector emitter, measured 2026-11-13
    assert split == {"native_dataset": 4, "matches": 4, "name_disputed": 9, "cannot_feed": 3}
```

Build `split` with the default registry: for each `coverage_report(reg)["sources"]` row whose `emitters + partial_emitters` meet `collector_plugin_names(reg)`, call `route_verdict(key, *prefill(key, row["dataset"]), card_dataset=row["dataset"], tenant_datasets=tenant_datasets(key), reading_kinds=<kinds from card_dataset_index(catalog.all_raw())>)` with the card corpus loaded (`catalog.load(default_corpus_dir(settings.CORTEXSIM_BASE_DIR))`).

- [ ] **Step 2: Run and see them fail**

Run: `PYTEST tests/eal_simulator/test_collector_declaration.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement the module as specified above**, and add the `name_differs` row to the spec's §5.2 verdict table.

- [ ] **Step 4: Run them and see them pass**

Run: `PYTEST tests/eal_simulator/test_collector_declaration.py -q`
Expected: PASS. If the real-catalogue split differs, stop and report it: it means the catalogue moved, not that the test is wrong.

- [ ] **Step 5: Commit**

```bash
git add core/eal_simulator/collector_declaration.py tests/eal_simulator/test_collector_declaration.py docs/superpowers/specs/2026-11-12-http-collector-wizard-design.md
git commit -m "feat(api): compute the HTTP collector route verdict"
```

### Task 3: `declaration` and `last_arrival` columns

**Files:**
- Modify: `core/models.py` (`CollectorTargetConfig`), `core/database.py` (new `_migrate_collector_target_columns`, called in `init_db` after `_migrate_case_ledger_columns`), `core/engine/collector_targets.py`
- Test: `tests/api/test_collector_targets_api.py`, `tests/api/test_collector_target_migration.py` (new)

**Interfaces:**
- Produces: columns `declaration: Mapped[Optional[dict]]`, `last_arrival: Mapped[Optional[dict]]` (JSON, nullable), both in `to_dict()`; `store.upsert(..., declaration: Optional[dict] = None)`; `store.record_arrival(db, name: str, result: dict) -> None`.
- Rules: `upsert` clears `last_arrival` whenever it clears `last_check`, and also when `dataset` changes. With `declaration` given it is stored. With none given, the stored one is kept only when `dataset` is unchanged; otherwise it is cleared (Review Focus 3). `record_arrival` stores `{state, found, delivered, token, checked_at, reason, code}` and nothing else.

- [ ] **Step 1: Write the failing tests**

```python
def test_declaration_and_arrival_survive_a_restart(db_file): ...
    # PUT with a declaration, record_arrival, reboot (_app twice): both read back equal

def test_a_legacy_row_reads_null(db_file): ...
    # PUT without declaration: GET shows "declaration": None, "last_arrival": None

def test_changing_dataset_without_a_declaration_clears_both(db_file): ...
    # declared target, then PUT dataset="other_raw" with no declaration:
    # declaration is None and last_arrival is None

def test_changing_the_url_clears_last_arrival(db_file): ...

def test_migration_adds_the_columns_to_an_old_table(tmp_path):
    # create collector_targets with the pre-change columns by raw SQL,
    # run _migrate_collector_target_columns; inspector lists declaration and last_arrival;
    # running it twice is a no-op
```

- [ ] **Step 2: Run and see them fail**

Run: `PYTEST tests/api/test_collector_targets_api.py tests/api/test_collector_target_migration.py -q`
Expected: FAIL (no such column / attribute).

- [ ] **Step 3: Implement** the columns, the migration (the `_migrate_case_ledger_columns` shape, one `ALTER TABLE collector_targets ADD COLUMN <col> JSON` per missing column), and the store rules above.

- [ ] **Step 4: Run them and see them pass** (same command). Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/models.py core/database.py core/engine/collector_targets.py tests/api/test_collector_targets_api.py tests/api/test_collector_target_migration.py
git commit -m "feat(api): store a collector target's declaration and last arrival"
```

### Task 4: PUT takes a declaration

**Files:**
- Modify: `core/api/collector_targets.py`, `docs/reference/api-and-agent-surface.md` §1.9.1 (PUT row)
- Test: `tests/api/test_collector_targets_api.py`

**Interfaces:**
- Consumes: Task 1 `is_lab_tenant_host`, `host_of`; Task 2 `route_verdict`, `dataset_for`, `TEST_CHANNEL`; Task 3 `store.upsert(declaration=…)`; `analytics_catalogue.get_source`, `ingestion_routes.tenant_datasets`, `dataset_requirements.card_dataset_index(catalog.all_raw())`.
- Produces: `TargetPut.declaration: Optional[DeclarationIn]` where `DeclarationIn(source_key: str, vendor: str = Field(min_length=1, max_length=64), product: str = Field(min_length=1, max_length=64))`. Stored declaration: `{source_key, vendor, product, verdict, card_dataset, lab_tenant}`; the stored `dataset` is `dataset_for(vendor, product)`.
- New refusals: 422 `DECLARATION_SOURCE_UNKNOWN` (not `test_channel` and `get_source` raises); 422 `TARGET_DATASET_DECLARATION_MISMATCH` (a body `dataset` that differs, case-insensitively, from the declared one); 409 `NATIVE_DATASET_NOT_LAB_TENANT` (verdict `native_dataset` and `is_lab_tenant_host(host_of(url))` false; the detail names the host it compared and the setting).

- [ ] **Step 1: Write the failing tests**

```python
def test_declared_put_stores_the_server_verdict_and_dataset(db_file):
    r = c.put("/api/collector-targets/duo", json={**BASE, "declaration":
        {"source_key": "duo", "vendor": "duo", "product": "auth"}})
    assert r.status_code == 200
    assert r.json()["dataset"] == "duo_auth_raw"
    assert r.json()["declaration"]["verdict"] == "matches"

def test_client_verdict_is_ignored(db_file):  # extra keys are not trusted
    ... "declaration": {..., "verdict": "matches"} for okta/sso -> stored "name_disputed"

def test_native_off_lab_is_refused_with_the_host(db_file, monkeypatch):
    monkeypatch.setattr(settings, "CORTEXSIM_DOOR_PROBE_TENANTS", "lab.example.test")
    r = put(native declaration, url="https://api-acme.example.test/logs/v1/event")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "NATIVE_DATASET_NOT_LAB_TENANT"
    assert "api-acme.example.test" in r.json()["detail"]["detail"]

def test_native_on_lab_is_stored_with_lab_flag(db_file, monkeypatch): ...  # lab_tenant True
def test_typed_native_name_on_another_source_is_refused_off_lab(db_file, monkeypatch): ...
def test_unknown_source_and_dataset_mismatch_are_422(db_file): ...
def test_put_without_declaration_behaves_as_before(db_file): ...  # existing panel body
```

`BASE` is the existing test's valid `xsiam_http_collector` body with `URL` and `KEY`.

- [ ] **Step 2: Run and see them fail.** Run: `PYTEST tests/api/test_collector_targets_api.py -q`. Expected: the new tests FAIL (unknown field ignored / no verdict).

- [ ] **Step 3: Implement** in `put_target`, before the plugin checks: resolve the source, compute `reading_kinds` from `card_dataset_index(catalog.all_raw()).get(card_dataset, {})` (union of the kinds sets; empty when the catalog is not loaded), call `route_verdict`, apply the refusals, then pass `dataset=dataset_for(...)` and `declaration=…` to `store.upsert`. Update the §1.9.1 PUT row: the `declaration` body field, the three codes, and that `declaration`/`last_arrival` appear on every target.

- [ ] **Step 4: Run them and see them pass.** Same command, plus `PYTEST tests/eal_simulator/test_api_collector_targets.py -q` (campaign paths still resolve targets). Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/api/collector_targets.py tests/api/test_collector_targets_api.py docs/reference/api-and-agent-surface.md
git commit -m "feat(api): accept a data declaration on collector targets"
```

### Task 5: Detections behind a data source

**Files:**
- Modify: `core/api/eal.py`, `docs/reference/api-and-agent-surface.md` (the data-streams table)
- Test: `tests/api/test_data_stream_detections.py`

**Interfaces:**
- Consumes: Task 1 `is_lab_tenant_host`; Task 2 `prefill`, `route_verdict`, `TEST_CHANNEL*`; `get_source`, `tenant_datasets`, `card_dataset_index`, `models.Scenario` (`status == "active"` rows, `to_dict()["steps"]`).
- Produces: `GET /api/eal/data-streams/{source_key}/detections?vendor=&product=&collector_host=` →
  `{source_key, name, card_dataset, tenant_datasets[], reading_kinds[], cards[{card_id, kinds[]}], detections[{scenario_id, step_id, detection_id, type, ttp_ref}], count, prefill{vendor, product}, verdict{…RouteVerdict.to_dict()}, lab_tenant: true|false|null, status, code}`.
  - `vendor`/`product` default to `prefill(...)`; `lab_tenant` is null without `collector_host`.
  - A detection is listed when its `ttp_ref` is a card that reads `card_dataset`.
  - `status`/`code`: `ok`/null with detections; `degraded`/`NO_DETECTION_READS_DATASET` with none; `degraded`/`SOURCE_HAS_NO_DATASET` when the card dataset is None; `not_applicable`/`TEST_CHANNEL` for `test_channel`. 404 `DATA_SOURCE_UNKNOWN` otherwise.

- [ ] **Step 1: Write the failing tests** (fixture: the `test_prerequisites_api.py` pattern, with the real card corpus loaded and `SIM-MP-001` plus one Okta-reading scenario seeded)

```python
def test_okta_lists_its_detections_with_types_and_a_disputed_verdict(client):
    body = client.get("/api/eal/data-streams/okta/detections").json()
    assert body["card_dataset"] == "okta_sso" and body["verdict"]["verdict"] == "name_disputed"
    assert body["count"] == len(body["detections"]) > 0
    assert all({"scenario_id", "detection_id", "type"} <= d.keys() for d in body["detections"])

def test_a_source_no_card_reads_is_degraded_not_ok(client):
    body = client.get("/api/eal/data-streams/duo/detections").json()
    assert (body["count"], body["status"], body["code"]) == (0, "degraded", "NO_DETECTION_READS_DATASET")
    assert body["verdict"]["verdict"] == "matches"

def test_edited_names_rerun_the_verdict(client):
    assert client.get("/api/eal/data-streams/duo/detections?vendor=duo&product=logs").json()["verdict"]["verdict"] == "name_differs"

def test_lab_flag_needs_a_host(client, monkeypatch): ...  # null without; true/false with collector_host
def test_test_channel_and_unknown_key(client): ...      # not_applicable/TEST_CHANNEL; 404 DATA_SOURCE_UNKNOWN
```

- [ ] **Step 2: Run and see them fail.** Run: `PYTEST tests/api/test_data_stream_detections.py -q`. Expected: FAIL (404 on the route).

- [ ] **Step 3: Implement** the route in `eal.py` beside `data_streams`. Register it so `/data-streams/{source_key}/detections` is not shadowed by an existing `/data-streams/...` path. Add the row to the API doc.

- [ ] **Step 4: Run them and see them pass.** Same command. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/api/eal.py tests/api/test_data_stream_detections.py docs/reference/api-and-agent-surface.md
git commit -m "feat(api): list the detections behind a data source"
```

### Task 6: Arrival check

**Files:**
- Create: `core/eal_simulator/arrival.py`
- Modify: `core/api/collector_targets.py` (route + `ArrivalBody`; import `load_xsiam_client` at module level so tests can patch it), `docs/reference/api-and-agent-surface.md` §1.9.1
- Test: `tests/eal_simulator/test_arrival.py`, `tests/api/test_collector_targets_api.py`

**Interfaces:**
- Consumes: Task 3 `store.record_arrival`; `queries.result_rows`; `guards.require_xql_capability`, `guards.tenant_budget`; `ledger.QuotaBreakerOpen`, `ledger.DailyBudgetExhausted`; `Xsiam*Error` classes; `CANARY_TOKEN_RE`.
- Produces:
  - `arrival_query(dataset: str, token: str) -> str`, exactly the spec §5.6 query;
  - `async def check_arrival(client, *, dataset: str, token: str, delivered: int, since: datetime | None, query_id: str | None) -> dict`, returning `{state, found, delivered, first_seen, last_seen, query_id, queries_issued, code, reason, proves[], does_not_prove[]}`;
  - `POST /api/collector-targets/{name}/arrival` with body `ArrivalBody(tenant: str, token: str, delivered: int = Field(ge=0), since: datetime | None = None, query_id: str | None = None)`.
- States: `counted` (SUCCESS, with one row carrying an int `found`); `pending` (PENDING or `None` status, carrying `query_id`); `not_checked` with a code.
- Codes: `TARGET_HAS_NO_DATASET`, `TENANT_NOT_CONFIGURED` (`XsiamConfigError`), `XQL_NOT_AUTHORISED` (`XsiamCapabilityError`), `QUOTA` (ledger refusals, `XsiamQuotaError`), `TENANT_UNAUTHORIZED` (`XsiamAuthError`, or `XsiamApiError` with upstream 401/403), `TENANT_ERROR` (other `XsiamApiError`), `QUERY_FAILED` (any other status), `UNRECOGNISED_REPLY` (`XsiamQueryError` from `result_rows`, or a row without an int `found`).
- Without `query_id`: one metered start inside `tenant_budget`, then one unmetered `get_query_results`, so `queries_issued` is 1. With `query_id`: only the read, so `queries_issued` is 0.
- Timeframe: `{"from": since_ms, "to": now_ms}` when `since` is given, else the last 24 h (`{"relativeTime": 86_400_000}`).
- `counted` and `not_checked` results go to `store.record_arrival`; `pending` does not. The 404 for an unknown target stays a 404; a bad token is a 422 from the body model.
- `proves` / `does_not_prove` are the spec §5.6 sentences.

- [ ] **Step 1: Write the failing tests** (`_StubClient` as in `tests/integration/xsiam/test_api_endpoints.py`, with scripted `start_xql_query` / `get_query_results` and a call counter)

```python
def test_pending_is_pending_never_zero():
    r = run(check_arrival(stub(reply={"status": "PENDING"}), dataset="d_raw", token="wiz-a-202611131200-ab12", delivered=3, since=None, query_id=None))
    assert r["state"] == "pending" and r["found"] is None and r["query_id"] == "q1"

def test_counted_reads_found_and_times():
    reply = {"status": "SUCCESS", "results": {"data": [{"found": 3, "first_seen": 1, "last_seen": 2}]}}
    r = run(check_arrival(stub(reply=reply), ...))
    assert (r["state"], r["found"], r["queries_issued"]) == ("counted", 3, 1)

def test_zero_rows_counts_zero_only_on_success(): ...            # SUCCESS + [] -> counted, found 0
def test_row_without_int_found_is_unrecognised(): ...           # {"found": "3"} -> not_checked UNRECOGNISED_REPLY
def test_every_refusal_is_not_checked_with_its_code(): ...      # parametrized over the code list above
def test_reading_a_started_query_issues_no_metered_call(): ...  # query_id given -> start not called, queries_issued 0
def test_query_text_is_the_spec_query(): ...                    # arrival_query("d_raw", "wiz-x-1") == exact text

# API level
def test_arrival_records_counted_and_not_pending(db_file, monkeypatch): ...  # last_arrival set after counted, unchanged after pending
def test_arrival_without_a_tenant_is_not_checked(db_file, monkeypatch): ...  # loader raises XsiamConfigError
```

- [ ] **Step 2: Run and see them fail.** Run: `PYTEST tests/eal_simulator/test_arrival.py tests/api/test_collector_targets_api.py -q`. Expected: FAIL.

- [ ] **Step 3: Implement** `arrival.py` and the route. Check `status` before calling `result_rows` (Review Focus 1). Catch only the exception classes named above; anything else propagates. The route records `{**result, "token": body.token, "checked_at": <aware UTC now, isoformat>}` through `store.record_arrival`, which keeps only its listed keys.

- [ ] **Step 4: Run them and see them pass, in UTC and in a non-UTC zone**

Run: `PYTEST tests/eal_simulator/test_arrival.py tests/api/test_collector_targets_api.py -q && TZ=America/Los_Angeles PYTEST tests/eal_simulator/test_arrival.py tests/api/test_collector_targets_api.py -q`
Expected: PASS twice.

- [ ] **Step 5: Commit**

```bash
git add core/eal_simulator/arrival.py core/api/collector_targets.py tests/eal_simulator/test_arrival.py tests/api/test_collector_targets_api.py docs/reference/api-and-agent-surface.md
git commit -m "feat(api): check that a collector's example records arrived"
```

### Task 7: Canary on `email_emitter`

**Files:**
- Modify: `core/eal_simulator/plugins/email_emitter.py`
- Test: `tests/eal_simulator/test_plugin_email_emitter.py`

**Interfaces:**
- Consumes: `analytics_emitter.CANARY_TOKEN_RE`, `CANARY_RAW_FIELD`, `plant_canary`.
- Produces: `EmailEmitterParams.canary_token: Optional[str] = None`, validated against `CANARY_TOKEN_RE` with the analytics family's error text. `_build_events_for_pattern` plants it with `plant_canary(event, token, {})` on every event it returns, so the wire path and `plan_offline_records` carry the same field.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize("provider", ["proofpoint", "microsoft365"])
def test_every_record_carries_the_canary(provider):
    recs = EmailEmitter().plan_offline_records(params(provider=provider, canary_token="wiz-m365-202611131200-ab12"), ...)
    assert recs and all(r["cortexsim_canary"] == "wiz-m365-202611131200-ab12" for r in recs)

def test_no_token_leaves_records_unchanged(): ...   # no "cortexsim_canary" key; equal to today's output
def test_a_bad_token_is_refused(): ...              # "Bad Token!" -> ValidationError
def test_wire_records_carry_the_canary(): ...       # through the existing _build_client capture
```

- [ ] **Step 2: Run and see them fail.** Run: `PYTEST tests/eal_simulator/test_plugin_email_emitter.py -q`. Expected: FAIL (`canary_token` rejected as an unknown field, or the key is missing).

- [ ] **Step 3: Implement** as above. If importing from `analytics_emitter` creates a cycle, import the three names inside `_build_events_for_pattern`.

- [ ] **Step 4: Run them and see them pass,** plus `PYTEST tests/eal_simulator/test_email_emitter_collector_auth.py tests/eal_simulator/test_roundtrip_canary.py -q`. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/eal_simulator/plugins/email_emitter.py tests/eal_simulator/test_plugin_email_emitter.py
git commit -m "fix(eal): carry the canary token on email emitter records"
```

### Task 8: Ground truth and the full gate

**Files:**
- Modify: `docs/reference/ground-truth.md`, `docs/reference/ground-truth.json` (regenerated, never hand-edited), `CHANGELOG.md` (Unreleased: the two routes, the PUT field, the setting, the email canary)

- [ ] **Step 1: Regenerate the ground truth.** Run: `PATH=/Users/henry/Github/Github_desktop/cortex-pov-engine/.venv/bin:$PATH make ground-truth`. Expected: route decorators 156 → 158, nothing else moved. Any other change means a count drifted: stop and report it.

- [ ] **Step 2: Run the host suite and the gates CI runs**

Run: `PYTEST tests/ --ignore=tests/smoke --ignore=tests/e2e_isolated -q`, then `PATH=…/.venv/bin:$PATH make check-ground-truth check-refs`.
Expected: no failures; the pass count is the dev baseline plus this plan's new tests.

- [ ] **Step 3: Commit**

```bash
git add docs/reference/ground-truth.md docs/reference/ground-truth.json CHANGELOG.md
git commit -m "docs(api): regenerate ground truth for the collector routes"
```

## Handoff to the UI plan (v26 slice 4)

- Step 2 cannot know the collector host (the URL arrives in step 3). It shows the native verdict as "allowed on the lab tenant only, checked when you add it", and step 4's PUT enforces it.
- The `name_differs` verdict is new (Task 2); give it the message added to spec §5.2.
- The Overview reads `last_check`, `last_arrival` and `declaration` from `GET /api/collector-targets` with no tenant call.
