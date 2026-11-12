# Ingestion Test Path Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every ingestion route Henry named (HTTP log collector, Broker VM Syslog / WEC / FTP / Files / CSV / DB, XDR Collector) a lab-only nonce probe with SimCore-sent negative controls, plus a read-only check that any tenant can run, so a broken data path is found before the POV instead of reading as "Cortex missed it".

**Architecture:** A `door_probes` table records each probe or control before it is sent. Probes go through the door itself (HTTP POST, `logger`, a file, a row), never through the tenant API, and only when the instance's tenant host is pinned in `CORTEXSIM_DOOR_PROBE_TENANTS`. `check_door` is read-only XQL, at most two calls. It picks the nonce method when a probe exists, recent rows for data doors, and freshness for transport doors. It appends a `DoorEvent` whose evidence names the method and the label. Transport doors carry `role="transport"` and never enter a detection's requirement.

**Tech Stack:** Python 3.11, FastAPI, async SQLAlchemy on SQLite, httpx, pytest; the Go beacon unchanged (probe steps are shell); React 18 + vitest for the two UI tasks.

**Spec:** `docs/superpowers/specs/2026-11-11-ingestion-test-path-design.md` (approved by Henry, 2026-11-11 22:36 CST). Read it with this plan. This plan **replaces Tasks 7–14** of `docs/superpowers/plans/2026-11-11-ingestion-doors.md`; that plan's Tasks 1–6 are built (#191) and untouched.

## How this plan replaces Tasks 7–14 of the ingestion-doors plan

| Original task | Disposition | New task(s) |
|---|---|---|
| 7 Door checks and attestation | **Amended and split.** Its HTTP-door slice is replaced by the P1 probe/check split. The rest (data-door recent rows, `xdr_agent`, NGFW, XTH, attestation) is kept, rebased on the new `check_door` and the amended `ROWS_QUERY`. Six of its tests move to Task 5. | 1–6, 8 |
| 8 Door requirement on every seeded result | **Kept** as written. Its two new tests (spec §9) land in Task 2. | 2, 9 |
| 9 Exclude silent rows | **Kept**, no rule change. Its two new tests land with their doors. | 10 (+15, 21) |
| 10 PASS withheld | **Kept**, no change. | 11 |
| 11 Door watcher | **Amended**: it never probes; `not_checked[]` names the probe route. | 12 |
| 12 Report and run readout | **Amended**: a "Data paths checked" block and `doors_verified_by_probe`. | 13 (+21) |
| 13 Get Started | **Amended**: one optional step per Broker VM target door, the 514 copy deleted, an XDR Collector step. | 27 |
| 14 Wizard links | **Amended**: a lab-only Probe beside "Send test record", and the NGFW syslog stage links its `broker_syslog` door. | 28 |

## Global Constraints

- Tenant API is read-only. `CORTEXSIM_XSIAM_ALLOW_WRITE` and `CORTEXSIM_XSIAM_ALLOW_DESTRUCTIVE` stay default-off and untouched.
- Probes and controls are ingestion writes *through the door*, never API writes. They run only when the instance's `xsiam_tenant` API host is in `CORTEXSIM_DOOR_PROBE_TENANTS` (comma-separated, default `""`). For HTTP targets, the target URL's host must also be in it.
- The HTTP collector canary (`write_canary`, and `POST /api/eal/campaigns/{id}/collectors/preflight`) stays on every tenant. It gains `cortexsim_door_nonce` and `sent_at` and nothing else. Its 2xx reads `accepted`, never `ingested`.
- Checks use the `xsiam_tenant` credential with `capabilities: ["xql"]`, through `resolve_tenant_runner` and the per-tenant ledger (`integrations.xsiam.guards.tenant_budget`). At most **two** tenant calls per check, and every check reports `queries_issued`. No xql, quota, 401 or 5xx records `unknown`.
- Nonce: `cxsdp-` + 32 lowercase hex (`uuid4().hex`). The scan-control nonce has the same shape. It is never sent, never stored in an event, and never returned by an API.
- Grace: push doors (HTTP, syslog, XDR Collector, WEC) 900 s; pull doors `collect_every_seconds` + 900 s; CSV 600 + 900 = 1,500 s. Q1 timeframe starts at `sent_at − 600 s`.
- No polling inside a check. A check inside grace returns `grace_remaining_seconds`, and the operator checks again.
- XQL timeframes are passed as `{"relativeTime": <ms>}`, the shape `resolve_tenant_runner` already uses. A query's `limit N` is also passed as `run_xql(..., limit=N)`, because `run_xql` truncates at 100 by default.
- Recent rows / freshness Q1: a 3,600 s window, `| sort desc _time | limit 20`. Rows carrying a truthy `cortexsim_preflight`, `cortexsim_door_probe` or `cortexsim_door_control` are dropped locally before counting, on every tenant.
- Labels, exact: `verified (probe)`, `verified (recent rows)`, `traffic seen (recent rows)` (`http_collector` only), `traffic seen`.
- `tenant_verified` stays `0` in every response. `doors_verified_by_probe` is a separate field, never summed with it, and `traffic seen` counts in neither.
- A never-probed door reads `unknown`, never `closed`. A freshness miss reads `unknown`. A control's expected miss writes no `DoorEvent`.
- Transport doors never score. Their `detections_needing` is `null`, never `0`.
- Unknown counts are `null`, never `0`: `records_sent`, `latency_seconds`, `rows_seen`, `metrics_last_seen`, and `queries_issued` when the ledger cannot say.
- An unrecognised tenant shape raises inside the check and records `unknown` with its code. It is never read as empty.
- Timestamps are naive UTC (`door_state._require_naive`). An epoch from a naive UTC datetime is `dt.replace(tzinfo=timezone.utc).timestamp()`, never `dt.timestamp()`. Tenant times go through `connectors.base.coerce_utc`.
- Henry's rule: functional fixes with behaviour tests, no guard-only tests or strict validators beyond what a send or a parse needs. Every behaviour change shows red before green, and the PR body carries that output.
- The beacon stays Go stdlib only. Probe steps are shell commands with `identity: "direct"`. No `/dev/tcp` or `nc` runs on a measured endpoint.
- No secrets in the repo, in a `DoorProbe` row, or on a command line. Target URLs never carry userinfo. Lab credentials live in the vault or in 1Password.
- Syslog Collector ports: not 0–1024 (except 514), not 63000–65000, and not 4052, 4369, 5671, 5672, 5986, 6379, 8000, 8888, 9100, 15672 or 28672.
- **Test runs**, from the worktree root:
  `export PATH="${PWD}/.venv/bin:${PATH}" CORTEXSIM_BASE_DIR="${PWD}" CORTEXSIM_ENV=development PYTHONPATH=core`
  then `TZ=UTC python -m pytest <files> -q && TZ=America/Los_Angeles python -m pytest <files> -q`. Below, "run X in both zones" means exactly that.
- A task that adds a route regenerates ground truth: `make build && make ground-truth && make check-ground-truth`.
- Before any PR: `make test-backend-non-utc`, `PATH="${PWD}/.venv/bin:${PATH}" make validate` (which includes `check-refs` and `check-ground-truth`), and `make ci`. No PR is opened until Henry asks.
- Branches are cut from `dev`, named `<type>/<area>-<slug>`. Stage files by name only.

## Review Focus

1. **A target saved without a `dataset`.** This is the most common omission. Check reads `unknown` with `TARGET_DATASET_UNSET` and `queries_issued: 0`, never `closed`. Test in Task 5.
2. **Probe clicked twice before the first lands.** The newest probe decides. An older probe's landing never verifies a newer one. Test in Task 6.
3. **Target URL, kind or dataset edited after a probe.** The old probe never verifies the edited target. The check reads `unknown` with `PROBE_TARGET_CHANGED` until a new probe is sent. Test in Task 6.
4. **`CORTEXSIM_DOOR_PROBE_TENANTS` written as a URL, in capitals or with spaces** (`https://API-Deathstar.xdr.us.paloaltonetworks.com/ , x`). The pin matches by host, case-insensitively, and the 403 detail names the host it compared. Test in Task 4.
5. **The nonce embedded inside a longer string** (a raw CEF line in `_raw_log`, a JSON string column, a nested object) rather than as a whole value. The dedicated-dataset scan finds it and names the column. Test in Task 18.

---

## File Structure

| File | Responsibility | Tasks |
|---|---|---|
| `core/eal_simulator/collector.py` (modify) | nonce helpers; the canary gains nonce + `sent_at`; `preflight_collector(record=)` | 1 |
| `core/eal_simulator/targets.py` (modify) | `HTTP_KINDS` filter in `apply_targets`; later the transport kinds, schemes, `params`, `DOOR_FOR_KIND` | 2, 14, 20, 23 |
| `core/engine/ingestion_doors.py` (modify) | `Door.role`, `Door.via`; the transport doors | 2, 14, 20, 23 |
| `core/models.py` (modify) | `DoorProbe` ORM; `CollectorTargetConfig.params` | 3, 14 |
| `core/engine/door_probes.py` (create) | probe rows, lab gate, run-live gate, senders, controls, grace, control gate, beacon door tasks | 3, 4, 6, 17, 18, 21, 24, 26 |
| `core/engine/door_checks.py` (create) | `TenantQuery`, `check_door`, the three methods, nonce scan | 5, 6, 8, 15, 18, 20, 23 |
| `core/engine/door_diagnostics.py` (create) | Q2 queries per door and the documented-description classifiers (pure) | 15, 20, 23, 24 |
| `core/engine/xdrc_validity.py` (create) | XDR Collector host facts and the pinned vendor OS list (pure) | 20 |
| `core/engine/door_state.py` (modify) | rung `traffic_seen`, basis `traffic`; `method`/`label`/`code` in `current()`; `configured_facts` by kind; `doors_verified_by_probe` | 5, 13, 14 |
| `core/api/doors.py` (modify) | `POST /api/doors/{key}/probe`, `/check`; row fields; `DoorTransports` | 2, 4, 5, 14, 15 |
| `core/api/readiness.py` (modify) | the `transport_doors` block | 16 |
| `core/api/runs.py`, `core/engine/orchestrator.py` (modify) | Run-less beacon door tasks | 17 |
| `docs/lab/<date>-ingestion-doors-lab-run.md` (create, on the lab branch) | live results per door | 7, 19, 22, 25 |

Branches this plan creates: `feature/api-door-checks` (Tasks 1–6, 8–13), `docs/docs-ingestion-doors-lab-run` (7, 19, 22, 25), `feature/api-broker-syslog-door` (14–18), `feature/api-xdr-collector-door` (20–21), `feature/api-broker-pull-doors` (23–24), `feature/api-wec-door-probe` (26), and `feature/ui-get-started-doors` (27–28, as the original plan named it).

---

## Phase P1 — the HTTP door's probe/check split (`feature/api-door-checks`, from `dev`)

Waits on (spec §10): the `email_emitter` auth fix, which is **satisfied** (#197, merged into dev as 0290b873), and deathstar for Task 7. P1 is API only; the console controls wait for Tasks 27–28.

### Task 1: The canary carries a nonce and `sent_at`

**Files:**
- Modify: `core/eal_simulator/collector.py` (`canary_record` :229-242, `preflight_collector` :254-321)
- Test: `tests/eal_simulator/test_canary_nonce.py` (create)

**Interfaces:**
- Produces:
  - `NONCE_RE = re.compile(r"^cxsdp-[0-9a-f]{32}$")` and `new_nonce() -> str`, returning `"cxsdp-" + uuid.uuid4().hex`.
  - `canary_record(campaign_id: str, *, dataset: Optional[str] = None, nonce: Optional[str] = None, sent_at: Optional[datetime] = None, extra: Optional[Mapping[str, Any]] = None) -> dict[str, Any]`. It adds `cortexsim_door_nonce` (`nonce` or `new_nonce()`) and `sent_at` (naive-UTC `sent_at` or now, rendered `…+00:00`). `extra` is merged last.
  - `preflight_collector(..., record: Optional[Mapping[str, Any]] = None)`: when `record` is given it is POSTed in place of `canary_record(campaign_id, dataset=target.dataset)`. The result dict keys are unchanged.

- [ ] **Step 1: Write the failing test** `test_canary_body_gains_only_nonce_and_sent_at`. Capture the POSTed body with an `httpx.MockTransport` factory on both shipped paths: `preflight_target(spec=..., token="k", write_canary=True, connect=<no-op>, client_factory=f)`, and `preflight_collector(target, campaign_id="CMP-1", client_factory=f)`.

```python
OLD = {"cortexsim_preflight", "cortexsim_campaign_id", "event_type", "message", "dataset"}
assert set(body) - OLD == {"cortexsim_door_nonce", "sent_at"}
assert NONCE_RE.match(body["cortexsim_door_nonce"])
sent = datetime.fromisoformat(body["sent_at"])
assert sent.utcoffset() == timedelta(0)
assert abs((datetime.now(timezone.utc) - sent).total_seconds()) < 5
assert second_body["cortexsim_door_nonce"] != body["cortexsim_door_nonce"]
assert result["verdict"] == "accepted" and set(result) == TARGET_RESULT_KEYS   # keys captured before the change
assert set(campaign_result) == {"step_id", "url", "reachable", "delivered", "status_code",
                                "code", "error", "latency_ms", "bytes_sent", "remediation"}
```

- [ ] **Step 2: Run it in both zones.** Expected: FAIL, because `cortexsim_door_nonce` is absent (the set difference is empty).
- [ ] **Step 3: Implement** `new_nonce`, `NONCE_RE`, the `canary_record` arguments and `preflight_collector(record=)` in `core/eal_simulator/collector.py`. Change nothing else on either path.
- [ ] **Step 4: Run it, plus `tests/eal_simulator/test_collector.py tests/eal_simulator/test_collector_target_preflight.py`, in both zones.** Expected: all pass.
- [ ] **Step 5: Commit.**

```bash
git add core/eal_simulator/collector.py tests/eal_simulator/test_canary_nonce.py
git commit -m "feat(api): the collector canary carries a nonce and sent_at"
```

### Task 2: Door `role` and the HTTP-kind filter (original Task 8's two tests)

**Files:**
- Modify: `core/engine/ingestion_doors.py` (`Door` :95-106, `_validate` :190-211)
- Modify: `core/eal_simulator/targets.py` (`apply_targets` :223-269)
- Modify: `core/api/doors.py` (`_door_rows` :88-112)
- Test: `tests/engine/test_door_corpus.py`, `tests/eal_simulator/test_collector_targets_resolver.py`

**Interfaces:**
- Produces:
  - `ROLES = ("data", "transport")` and `Door.role: str = "data"`, a keyword field after `wizard`. Every existing door stays `data`, and `_validate` checks the role against `ROLES`, as it already checks scope.
  - `HTTP_KINDS: frozenset[str] = frozenset({"xsiam_http_collector", "http_forwarder"})`. `apply_targets` resolves against `[t for t in targets if t.kind in HTTP_KINDS]` only, for resolution, `by_name` and allowlist pins alike.
  - `/api/doors` rows gain `role`.

- [ ] **Step 1: Write the failing tests.**
  - `test_transport_doors_never_in_requirement` (in `test_door_corpus.py`, using its `corpus` fixture). For every corpus detection, `{get_door(d).role for el in req.all_of for d in el} <= {"data"}`.
  - `test_non_http_kinds_never_resolve_as_collector_destination`. `TargetSpec` refuses non-HTTP kinds until Task 14, so the test uses a frozen test dataclass with `TargetSpec`'s attributes: `kind="broker_syslog"`, `url="syslog+tcp://bvm-01:5514"`, `is_default=True`, `plugins=()` and `allowlist_pin="bvm-01:5514"`. Then:

```python
out, res = apply_targets(_spec(("cloud_audit_emitter", {})), REG, [stand_in])
assert res[0].source == "none" and res[0].code == "COLLECTOR_TARGET_NOT_CONFIGURED"
assert out["steps"][0]["params"]["collector_url"] == UNRESOLVED_COLLECTOR_URL
assert "bvm-01:5514" not in out["target_allowlist"]
```

- [ ] **Step 2: Run both files in both zones.** Expected: the first fails with `AttributeError: 'Door' object has no attribute 'role'`. The second fails because `source` reads `'default'` and the URL is the syslog URL.
- [ ] **Step 3: Implement** `ROLES`, `Door.role`, `HTTP_KINDS`, the filter and the row field.
- [ ] **Step 4: Run both files, plus `tests/engine/test_ingestion_doors.py tests/api/test_doors_api.py`, in both zones.** Expected: all pass.
- [ ] **Step 5: Commit.**

```bash
git add core/engine/ingestion_doors.py core/eal_simulator/targets.py core/api/doors.py \
        tests/engine/test_door_corpus.py tests/eal_simulator/test_collector_targets_resolver.py
git commit -m "feat(api): doors carry a role; only HTTP kinds receive collector steps"
```

### Task 3: `DoorProbe` rows and the HTTP probe sender

**Files:**
- Modify: `core/models.py` (new `DoorProbe` after `DoorEvent` :1173)
- Create: `core/engine/door_probes.py`
- Test: `tests/engine/test_door_probes.py` (create)

**Interfaces:**
- Consumes: `canary_record`, `new_nonce`, `preflight_collector(record=)` (Task 1); `CollectorTarget`, as `target_preflight.py:130-135` builds it.
- Produces:
  - `DoorProbe` ORM, `__tablename__ = "door_probes"`. Columns:
    - `id`
    - `nonce` (unique, indexed) and `control_nonce` (the scan-control nonce)
    - `control: str | None`, `expected: str | None` (`"absent"` for every control) and `control_result: str | None` (`absent` | `landed`)
    - `control_result_at: datetime | None` and `scan_control_result: str | None` (`clean` | `matched`)
    - `door_key` (indexed), `door_id` and `target`
    - `sender_kind` (`simcore` | `beacon`) and `sender_id`
    - `sent_at` (naive UTC, NOT NULL) and `sent_at_sender: datetime | None`
    - `send_status` (`queued` | `sent` | `failed` | `refused`) and `send_code: str | None`
    - `records_sent: int | None` and `detail: JSON`

    `to_dict()` returns every column **except** `control_nonce`. `create_all` makes the table, so no migration is needed.
  - `PROBEABLE = frozenset({"http_collector"})`, `CONTROLS = {"http_collector": ("wrong_key", "wrong_format")}`, `GRACE_SECONDS = {"http_collector": 900}` and `CLOCK_SKEW_MARGIN_SECONDS = 600`.
  - `grace_for(door_id: str, params: Mapping[str, Any] | None = None) -> int`.
  - `async def send_http_probe(db, spec: TargetSpec, *, door_key: str, control: str | None, token: str | None, client_factory=None, now: datetime | None = None) -> DoorProbe`. The row is committed with `send_status="queued"` **before** the POST. The body is `canary_record(f"collector-target:{spec.name}", dataset=spec.dataset, nonce=row.nonce, sent_at=row.sent_at, extra=...)`:
    - A probe's `extra` is `{"cortexsim_door_probe": True, "door_key": door_key, "sent_from": "simcore"}`.
    - A control adds `cortexsim_door_control: True` and `cortexsim_door_control_id: <id>`.
    - `wrong_key` replaces the key with `"cortexsim-wrong-key-" + secrets.token_hex(8)`.
    - `wrong_format` uses the stored key. It is sent to a target the operator configured for CEF.

    Outcomes:
    - 2xx: `sent`, `records_sent=1`.
    - Reachable but refused: `refused`, `send_code` = the delivery code, `records_sent=0`.
    - Unreachable: `failed`, `send_code` = the delivery code, `records_sent=0`.

    `detail = {"url", "kind", "dataset", "status_code", "latency_ms"}` holds the snapshot Task 6 compares.

- [ ] **Step 1: Write the failing tests.**
  - `test_probe_row_written_before_send`. An async `MockTransport` handler opens a second session, selects `DoorProbe` by the body's `cortexsim_door_nonce`, and asserts the row exists with `send_status == "queued"`. After the call it reads `"sent"`.
  - `test_probe_body_carries_row_nonce_and_markers`. The body's nonce equals `row.nonce`, `cortexsim_door_probe is True`, `sent_from == "simcore"`, and `sent_at` equals `row.sent_at` rendered as UTC. For a `wrong_key` control, `cortexsim_door_control is True`, `cortexsim_door_control_id == "wrong_key"` and `row.expected == "absent"`.
  - `test_send_outcomes_mapped`, parametrized:
    - 202 → (`sent`, `None`, 1)
    - 401 → (`refused`, `"collector_unauthorized"`, 0)
    - `httpx.ConnectError` → (`failed`, `"collector_unreachable"`, 0)
  - `test_wrong_key_control_never_sends_the_stored_key`. The header value differs from the stored token and starts with `cortexsim-wrong-key-`.
  - `test_no_secret_or_scan_nonce_in_row_dict`. Neither the stored token nor `row.control_nonce` appears in `json.dumps(row.to_dict())`.
- [ ] **Step 2: Run the file in both zones.** Expected: collection error (module missing).
- [ ] **Step 3: Implement** the model and the module.
- [ ] **Step 4: Run the file in both zones.** Expected: all pass.
- [ ] **Step 5: Commit.**

```bash
git add core/models.py core/engine/door_probes.py tests/engine/test_door_probes.py
git commit -m "feat(api): door probe rows and the HTTP probe sender"
```

### Task 4: `POST /api/doors/{key}/probe` and its gates

**Files:**
- Modify: `core/config.py` (`CORTEXSIM_DOOR_PROBE_TENANTS: str = ""`), `.env.example`
- Modify: `core/engine/door_probes.py`, `core/api/doors.py`
- Modify: `docs/reference/api-and-agent-surface.md` §1.16b, `docs/reference/ground-truth.{json,md}`
- Test: `tests/engine/test_door_probes.py`, `tests/api/test_doors_api.py`

**Interfaces:**
- Consumes: `send_http_probe` (Task 3); `integrations.xsiam.tenant_url.normalize_tenant_base_url`; `CredentialStore.list_integrations(kind="xsiam_tenant")`; `api.agents._derive_status`.
- Produces:
  - `pinned_hosts(raw: str) -> frozenset[str]`: split on `,`, strip, lowercase, and reduce anything containing `://` to its hostname.
  - `async def lab_api_host(db) -> str | None`: the first `xsiam_tenant` credential's normalised `base_url` host, the same one `resolve_tenant_runner` picks.
  - `async def instance_probe_pinned(db) -> bool`.
  - `class DoorProbeRefused(Exception)`, with `status: int`, `code: str` and `detail: str`.
  - `async def live_run_on(db, agent_id: str) -> str | None`: the `run_id` of a `Run` with `status in ("pending", "running")` whose `target == agent_id` or whose `channel_dispatch` has an entry with `target == agent_id`.
  - `async def probe_gate(db, *, door_id: str, spec: TargetSpec | None, sender_kind: str, sender_id: str) -> None`. Checks run in this order:
    1. `door_id not in PROBEABLE` → 409 `DOOR_NOT_PROBEABLE`.
    2. Lab tenant → 403 `DOOR_PROBE_NOT_LAB_TENANT`, when there is no credential, the pin is empty, the API host is not pinned, or an HTTP target's host is not pinned. The detail names the compared host.
    3. `sender_kind == "beacon"` and `live_run_on(...)` → 409 `DOOR_PROBE_RUN_LIVE`, naming the run.

    P1's HTTP sender is SimCore, so the route first reaches gate 3 in Task 17.
  - `@dataclass class DoorTransports` (`client_factory=None`) and the dependency `door_transports() -> DoorTransports`. Tests override it. Task 5 adds `tenant`, and Task 18 adds `syslog_sender`.
  - `POST /api/doors/{key}/probe`, body `ProbeBody(control: Optional[str] = None)`.
    - 200 → `{"probe": DoorProbe.to_dict(), "tenant_verified": 0, "queries_issued": 0}`.
    - Errors use `{"error","code","detail"}`: 404 `DOOR_NOT_FOUND`, 404 `TARGET_NOT_FOUND`, 422 `DOOR_CONTROL_UNKNOWN`, and the three gate refusals.
    - A refusal writes no `DoorProbe` and no `DoorEvent`.
  - `GET /api/doors` gains `probe_pinned: bool` (zero outbound calls).

- [ ] **Step 1: Write the failing tests.**
  - `test_probe_refused_on_non_lab_tenant`, three cases:
    - an empty pin;
    - a pin of `api-deathstar.xdr.us.paloaltonetworks.com` with the credential at `api-acme.xdr.us.paloaltonetworks.com`;
    - a pinned credential with an HTTP target on an unpinned host.

    Each raises 403 `DOOR_PROBE_NOT_LAB_TENANT`, and `DoorProbe` stays empty.
  - `test_probe_refused_while_run_live`, three cases:
    - `Run(status="running", target="pov-target-01")` with `probe_gate(..., sender_kind="beacon", sender_id="pov-target-01")` raises 409 `DOOR_PROBE_RUN_LIVE`;
    - a fan-out `channel_dispatch` entry `{"target": "pov-target-01"}` raises the same;
    - a `complete` run does not raise.
  - `test_pin_normalises_url_case_and_space` (Review Focus 4). `pinned_hosts("https://API-Deathstar.xdr.us.paloaltonetworks.com/ , x") == {"api-deathstar.xdr.us.paloaltonetworks.com", "x"}`. Through the gate, a pinned credential passes.
  - API `test_probe_route_writes_one_row_and_no_event`. With a lab-pinned instance and a 202 stub: status 200, one `DoorProbe`, zero `DoorEvent`, and `body["tenant_verified"] == 0`.
  - API `test_probe_route_refusal_shapes`. The 403, 404 and 422 bodies each carry `error`, `code` and `detail`.
- [ ] **Step 2: Run both files in both zones.** Expected: fail (names missing; 404 on the route).
- [ ] **Step 3: Implement.** Add the `.env.example` line `CORTEXSIM_DOOR_PROBE_TENANTS=` with a one-line comment: lab `.env` only, never in a POV image.
- [ ] **Step 4: Run in both zones, then `make build && make ground-truth && make check-ground-truth`.** Expected: green, and `http_routes.by_method.post` is one higher than on the branch base (40 on dev 6300ebc1).
- [ ] **Step 5: Commit.**

```bash
git add core/config.py .env.example core/engine/door_probes.py core/api/doors.py \
        docs/reference/api-and-agent-surface.md docs/reference/ground-truth.json \
        docs/reference/ground-truth.md tests/engine/test_door_probes.py tests/api/test_doors_api.py
git commit -m "feat(api): lab-only door probe route with tenant pin and run-live gate"
```

### Task 5: `check_door` and the recent-rows method (HTTP door)

**Files:**
- Create: `core/engine/door_checks.py`
- Modify: `core/engine/door_state.py`. `BASES` gains `"traffic"`. `RUNGS` becomes `("authored", "configured", "reachable", "traffic_seen", "verified")`. `current()` rows gain `method`, `label` and `code`, read from the shown event's evidence.
- Modify: `core/models.py` (`DoorEvent` comment strings only)
- Modify: `core/connectors/service.py` (`TENANT_REMEDIATION` :944, four keys)
- Modify: `core/api/doors.py` (`POST /api/doors/{key}/check`; rows gain `method`, `label`, `code`; `DoorTransports.tenant`)
- Modify: `docs/reference/api-and-agent-surface.md`, ground truth
- Test: `tests/engine/test_door_checks.py` (create), `tests/api/test_doors_api.py`

**Interfaces:**
- Consumes: `resolve_tenant_runner`, `load_xsiam_client`, `tenant_budget`, `result_rows`, `engine.collector_targets.load_specs`, `eal_simulator.targets.check_verdict`, `door_state.record`.
- Produces:
  - `@dataclass class TenantQuery`. Fields: `tenant: str | None`, `reason: str | None`, `fn: Callable[[str, dict, int], Awaitable[dict]] | None` (query, timeframe, limit → raw reply), and `issued: int = 0`. Methods:
    - the property `ok`;
    - `async rows(query: str, *, relative_ms: int, limit: int) -> list[dict]`. It increments `issued`, calls `fn` with `{"relativeTime": relative_ms}`, and applies `result_rows`. A `result_rows` failure is re-raised as `DoorShapeError(code="XQL_RESULT_SHAPE_UNRECOGNISED")`.
  - `async def resolve_tenant_query(db) -> TenantQuery`. It uses `resolve_tenant_runner` for the gate and reason. When the tenant is usable, `fn` runs `client.run_xql(query, timeframe, limit=limit)` under `tenant_budget(client)`.
  - `class DoorShapeError(ValueError)` (with `.code`) and `class DoorCheckNotFound(LookupError)`.
  - `ROWS_QUERY = 'dataset = {dataset} | filter _time >= to_timestamp({since_ms}, "MILLIS") | sort desc _time | limit 20'`, `RECENT_WINDOW_SECONDS = 3600` and `CORTEXSIM_MARKERS = ("cortexsim_preflight", "cortexsim_door_probe", "cortexsim_door_control")`.
  - `drop_cortexsim_rows(rows: list[dict]) -> list[dict]`.
  - `LABEL_PROBE`, `LABEL_RECENT`, `LABEL_TRAFFIC_RECENT` and `LABEL_TRAFFIC`, carrying the four exact labels.
  - `async def check_door(db, door_key: str, *, tenant: TenantQuery | None = None, now: datetime | None = None) -> DoorEvent`. Every event's evidence carries:
    - `method` (`recent_rows` | `nonce` | `freshness`)
    - `label`
    - `code`
    - `causes: list[str]`
    - `dataset`
    - `field_names: "from documentation"`
    - `tenant_verified: 0`

    `queries_issued` is written to the column.
  - `http_collector:<name>` rules, in order:
    1. An unknown target raises `DoorCheckNotFound`.
    2. `dataset` is unset → `unknown` / `check` / `configured`, `TARGET_DATASET_UNSET`, 0 queries.
    3. `check_verdict(last_check) != "accepted"` → `closed` / `check` / `configured`, `COLLECTOR_NOT_ACCEPTED`, with the verdict in evidence.
    4. The tenant is not ok → `unknown` with `code = tenant.reason`.
    5. Q1 `ROWS_QUERY`. One or more rows left after the drop → `open` / `traffic` / `traffic_seen`, label `traffic seen (recent rows)`, with evidence `rows_seen`, `truncated` (the raw page held 20) and the newest `_time`. Zero → `closed` / `check` / `reachable`, `NO_RECENT_ROWS`, with `rows_dropped_as_cortexsim`.
  - Exception mapping to `unknown`:
    - `XsiamQuotaError` or `QuotaBreakerOpen` → `quota_exhausted`
    - `XsiamAuthError` → `tenant_auth_failed`
    - `XsiamApiError` → `tenant_api_error`
    - `XsiamQueryError` → `xql_error`
    - `DoorShapeError` → its code
  - The four new `TENANT_REMEDIATION` entries, copy exact:
    - `quota_exhausted`: "The tenant's XQL quota or CortexSim's per-tenant budget is spent. The check recorded unknown; run it again after the quota resets."
    - `tenant_auth_failed`: "The tenant refused the API key (401/403). Re-enter the key or check its role."
    - `tenant_api_error`: "The tenant API returned a server error. The check recorded unknown; try again."
    - `xql_error`: "The tenant rejected the XQL query. The error is in the evidence; the check recorded unknown."
  - `POST /api/doors/{key}/check` → 200 `{"event": DoorEvent.to_dict(), "queries_issued": n, "tenant_verified": 0}`, or 404 `DOOR_NOT_FOUND` / `TARGET_NOT_FOUND`. Until Task 8, every door other than `http_collector` answers 409 `DOOR_CHECK_UNAVAILABLE`.

- [ ] **Step 1: Write the failing tests.** They use a `FakeTenant` helper in the test file: a `TenantQuery` whose `fn` returns `{"status": "SUCCESS", "results": {"data": rows}}`. It honours `filter cortexsim_door_nonce in (...)` and `= "..."` by regex, and returns every row for any other query.
  - `test_recent_rows_drop_cortexsim_rows`. Given only a canary, a probe and a control row: `closed`, `NO_RECENT_ROWS`, `rows_dropped_as_cortexsim == 3`. With one plain row added: `open`, rung `traffic_seen`, label `traffic seen (recent rows)`, `rows_seen == 1`.
  - `test_recent_rows_query_window_is_utc`. With `now=datetime(2026, 11, 11, 12, 0, 0)`, the query contains `to_timestamp(1794394800000, "MILLIS")` and `sort desc _time | limit 20`, with `relative_ms == 3_600_000` and `limit == 20`. This test is the one the `TZ=America/Los_Angeles` run exists for.
  - `test_target_without_dataset_unknown_no_query` (Review Focus 1). `unknown`, `TARGET_DATASET_UNSET`, `tenant.issued == 0`.
  - `test_collector_not_accepted_closed`, moved from the original Task 7. `last_check={"verdict": "reachable"}` → `closed`, `COLLECTOR_NOT_ACCEPTED`, 0 queries.
  - `test_no_xql_unknown`, moved from the original Task 7. A credential whose capabilities lack `xql` → `unknown`, code `xql_not_authorised`, remediation equal to `TENANT_REMEDIATION["xql_not_authorised"]`.
  - `test_quota_unknown`, moved from the original Task 7. `fn` raises `XsiamQuotaError` → `unknown` / `quota_exhausted`, and a prior `open` stays the effective state.
  - `test_unrecognised_envelope_unknown_not_empty`. This replaces `test_unrecognised_envelope_raises`. The reply `{"results": "garbage"}` → `unknown` / `XQL_RESULT_SHAPE_UNRECOGNISED`, never `closed`.
  - `test_two_call_cap`, moved from the original Task 7. Parametrized over every path above, `tenant.issued <= 2`.
  - API `test_check_records_queries_issued`, moved from the original Task 7. The response `queries_issued == event["queries_issued"] == 1`, and the `GET /api/doors` row shows `method == "recent_rows"` and `label == "traffic seen (recent rows)"`.
- [ ] **Step 2: Run both files in both zones.** Expected: fail (module missing; 404).
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run them, plus `tests/engine/test_door_state.py`, in both zones, then regenerate ground truth.** Expected: green.
- [ ] **Step 5: Commit.**

```bash
git add core/engine/door_checks.py core/engine/door_state.py core/models.py core/connectors/service.py \
        core/api/doors.py docs/reference/api-and-agent-surface.md docs/reference/ground-truth.json \
        docs/reference/ground-truth.md tests/engine/test_door_checks.py tests/api/test_doors_api.py
git commit -m "feat(api): read-only check for the HTTP collector door"
```

### Task 6: The nonce method, scan control and control gate (HTTP door)

**Files:**
- Modify: `core/engine/door_checks.py`, `core/engine/door_probes.py`
- Modify: `CLAUDE.md` (the route-count line), `docs/reference/api-and-agent-surface.md` (§0 count), `CHANGELOG.md` (`[Unreleased]`)
- Test: `tests/engine/test_door_checks.py`

**Interfaces:**
- Consumes: Tasks 3–5.
- Produces:
  - `NONCE_QUERY = 'dataset = {dataset} | filter cortexsim_door_nonce in ({nonces}) | limit 10'`. `nonces` are comma-separated and double-quoted: the probe plus the door kind's pending controls.
  - `SCAN_CONTROL_QUERY = 'dataset = {dataset} | filter cortexsim_door_nonce = "{nonce}" | limit 1'`.
  - In `door_probes`:
    - `async latest_probe(db, door_key) -> DoorProbe | None` (`control IS NULL`, newest `sent_at`);
    - `async pending_controls(db, door_id) -> list[DoorProbe]`;
    - `async resolve_controls(db, door_id, *, found: set[str], now, truncated: bool) -> list[DoorProbe]`;
    - `async control_gate(db, door_id) -> bool`, which is True iff the door kind's latest control by `control_result_at` reads `absent`.
  - Method selection: when `instance_probe_pinned(db)` and `latest_probe(db, key)` exists, the nonce method runs. Otherwise Task 5's path runs.
  - Nonce-method rules, in order:
    1. The newest probe decides (Review Focus 2). `queued` → `unknown`, `DOOR_PROBE_SEND_UNCONFIRMED`. `failed` or `refused` → `closed` / `check` / `configured`, with `code = send_code`.
    2. If the probe's `detail` (`url`, `kind`, `dataset`) differs from the current spec → `unknown`, `PROBE_TARGET_CHANGED`, 0 queries (Review Focus 3).
    3. Q1 `NONCE_QUERY` with `relative_ms` from `min(sent_at) − 600 s` to `now`, and `limit=10`. An `XsiamQueryError` naming `cortexsim_door_nonce` → `unknown`, `PROBE_FIELD_UNKNOWN`.
    4. A returned row without `cortexsim_door_nonce` → `unknown`, `PROBE_ROW_SHAPE_UNRECOGNISED`.
    5. Controls:
       - A found control nonce → its row reads `landed`, and the check records a `DoorEvent` of `unknown` / `NEGATIVE_CONTROL_MATCHED` and stops.
       - A control past grace and not found → its row reads `absent`. No `DoorEvent` is written. Controls resolve `absent` only when the page was not full.
    6. Probe nonce not found:
       - inside grace → `unknown` / `check` / `reachable`, `PROBE_PENDING`, with `grace_remaining_seconds`;
       - past grace → `closed` / `check` / `reachable`, code `PROBE_NOT_FOUND`, `causes: ["HTTPC_ACCEPTED_NOT_INGESTED"]`.
    7. Probe nonce found, and no row of this `door_key` has `scan_control_result == "clean"`:
       - Q2 `SCAN_CONTROL_QUERY` runs with the probe's `control_nonce`.
       - One or more rows: the row records `matched`, the check records `unknown` / `NEGATIVE_CONTROL_MATCHED`, and `logger.error` fires.
       - None: the row records `clean`.
    8. `control_gate` false → `unknown` / `check` / `reachable`, `NO_CONTROL_MISS_RECORDED`, with the hit kept in evidence. True → `open` / `verified` / `verified`, label `verified (probe)`, with evidence:
       - `nonce`, `sent_from`, `sent_at`, `dataset` and `column: "cortexsim_door_nonce"`;
       - `latency_seconds`: `coerce_utc(_insert_time) − sent_at`. It is `null` when `_insert_time` is absent. When negative it is `null`, with `clock_skew_suspected: true`.
       - `superseded_probes: [ids]`.

- [ ] **Step 1: Write the failing tests** (lab-pinned instance, accepted target, `FakeTenant`):
  - `test_old_canary_without_nonce_does_not_open`. The tenant holds an earlier canary: `cortexsim_preflight: True`, no nonce, 5 minutes before a probe sent 60 s ago. Assert `state == "unknown"`, `code == "PROBE_PENDING"` and `method == "nonce"`.
  - `test_inside_grace_unknown` (probe 300 s ago, no row) → `PROBE_PENDING`, `grace_remaining_seconds == 600`.
  - `test_accepted_without_row_closed_after_grace` (probe 901 s ago) → `closed`, `PROBE_NOT_FOUND`, `causes == ["HTTPC_ACCEPTED_NOT_INGESTED"]`.
  - `test_control_nonce_match_unknown`. The tenant also holds the probe's `control_nonce` → `unknown` / `NEGATIVE_CONTROL_MATCHED`, `scan_control_result == "matched"`.
  - `test_unrecognised_row_shape_unknown` (a row without `cortexsim_door_nonce`) → `unknown` / `PROBE_ROW_SHAPE_UNRECOGNISED`.
  - `test_hit_without_control_miss_not_verified`. A probe hit with no control ever sent → `unknown` / `NO_CONTROL_MISS_RECORDED`, and `evidence["nonce"] == probe.nonce`.
  - `test_control_miss_changes_no_door_state`. A `wrong_key` control 901 s ago, not found → `control_result == "absent"`. The only new `DoorEvent` is the probe's, and no event's evidence holds the control's nonce.
  - `test_control_landed_records_negative_control_matched`.
  - `test_verified_needs_hit_scan_clean_and_control_miss`. The happy path reads `open` / `verified` / `verified`, label `verified (probe)`, `queries_issued == 2`. A second check reads `queries_issued == 1`, because the scan control is cached per target.
  - `test_queued_probe_unknown_send_unconfirmed`.
  - `test_unknown_nonce_field_error_unknown` → `PROBE_FIELD_UNKNOWN`.
  - `test_newest_probe_decides` (Review Focus 2). An older probe's nonce is in the tenant; the newest, sent 60 s ago, is not. Expect `PROBE_PENDING`, with the older id in `superseded_probes`.
  - `test_probe_before_target_edit_does_not_verify` (Review Focus 3). The URL is edited after the probe → `PROBE_TARGET_CHANGED`, `tenant.issued == 0`.
- [ ] **Step 2: Run the file in both zones.** Expected: the new tests fail, because the check takes the recent-rows path (`method == "recent_rows"`). For the spec §11 red, make a scratch edit (not committed) that replaces `door_checks.drop_cortexsim_rows` with `lambda rows: rows`. Run `test_old_canary_without_nonce_does_not_open` and see `state == 'open'`: the recent-rows-only reading. Save the output for the PR body, then revert.
- [ ] **Step 3: Implement the nonce method without `control_gate`**: on a hit with a clean scan, record `open`.
- [ ] **Step 4: Run the file in both zones.** Expected: all pass except `test_hit_without_control_miss_not_verified`, which FAILS with `state == 'open'`. Save that output for the PR body: it is the guard seen failing.
- [ ] **Step 5: Add `control_gate`, then run the file plus `tests/engine/test_door_probes.py tests/api/test_doors_api.py` in both zones.** Expected: all pass.
- [ ] **Step 6: Update the docs.** In `CLAUDE.md`, recount the route line with the walker in `docs/reference/api-and-agent-surface.md` §0 (expected 167 if nothing else landed), and put the same count in §0. Add a CHANGELOG `[Unreleased]` entry: "Ingestion doors: lab-only HTTP collector probe with negative controls; read-only door check; the canary carries a nonce and sent_at. tenant-verified is 0."
- [ ] **Step 7: Commit, as two commits.**

```bash
git add core/engine/door_checks.py core/engine/door_probes.py tests/engine/test_door_checks.py
git commit -m "feat(api): nonce read-back verifies the HTTP door only after a control miss"
git add CLAUDE.md docs/reference/api-and-agent-surface.md CHANGELOG.md
git commit -m "docs(api): door probe and check routes, counts and changelog"
```

### Task 7: Lab run — the HTTP door on deathstar (runbook steps 1–2)

**Files:**
- Create: `docs/lab/<run date>-ingestion-doors-lab-run.md` on `docs/docs-ingestion-doors-lab-run` (cut from `dev`)

**Interfaces:**
- Consumes: a SimCore built from `feature/api-door-checks` at Task 6, on a parallel compose project (`:8889`), never by replacing `:8888`.

- [ ] **Step 1: Tenant.** `GET /api/readiness` shows the deathstar `xsiam_tenant` with `xql_capability_granted: true`. Set `CORTEXSIM_DOOR_PROBE_TENANTS=<deathstar API host>` in the lab `.env` only. Run `POST /api/connectors/xsiam_tenant/preflight` and record the clock skew.
- [ ] **Step 2: Precondition for P3/P4, read-only.** Record whether the Data Collection add-on and XDR Collectors are enabled on deathstar, from the console's license page or the Cortex MCP oracle's read-only `get_tenant_info`. Write the answer and its source in the lab doc. P3 and P4 stay parked until this is recorded as enabled.
- [ ] **Step 3: HTTP door.** On deathstar, add an HTTP collector (JSON, uncompressed, Vendor `cortexsim`, Product `door_http`). `PUT /api/collector-targets/lab-http` with the key, `dataset` `cortexsim_door_http_raw`, and `write_canary` preflight → `accepted`.
- [ ] **Step 4: Run the sequence.**
  1. Control: `POST /api/doors/http_collector:lab-http/probe {"control":"wrong_key"}`. Expect `send_status: refused`, `send_code: collector_unauthorized`.
  2. Probe: the same route with `{}`.
  3. After 900 s: `POST /api/doors/http_collector:lab-http/check`.

  Expect `open`, `verified (probe)`, `queries_issued: 2`. If a CEF-configured collector exists, also send `wrong_format` to it (expect a 500 at send). Then confirm "Send test record" still reads `accepted`, and that its body carries `cortexsim_door_nonce` and `sent_at`.
- [ ] **Step 5: Record** the probe id, nonce, `sent_at`, `sent_from`, dataset, landed `_insert_time`, latency, the column that held the nonce, the control result and `queries_issued`. Note whether the collector host equals the API host (spec §14); if they differ, pin both. `tenant-verified` stays 0 in the doc. Once the doc records the observed field names, that door's `field_names` evidence changes to cite the doc. The change is one line, carried by the next code branch: Task 14 carries `http_collector`'s.
- [ ] **Step 6: Commit.**

```bash
git add docs/lab/<run date>-ingestion-doors-lab-run.md
git commit -m "docs(lab): first live HTTP door probe and control on deathstar"
```

---

## Original tasks kept on `feature/api-door-checks` (they proceed on top of P1, as planned)

### Task 8: Data-door checks, `xdr_agent`, NGFW, XTH and attestation (original Task 7, remainder)

**Files:** as the original Task 7 (`core/engine/door_checks.py`, `core/integrations/xsiam/client.py::find_endpoints`, `core/api/doors.py` `POST /api/doors/{key}/attest`, `tests/engine/test_door_checks.py`, `tests/api/test_doors_api.py`), plus the ground truth regeneration for the attest route.

**Interfaces:**
- Consumes: `check_door`, `TenantQuery`, `ROWS_QUERY` and `drop_cortexsim_rows` (Tasks 5–6). No second query constant is added.
- Produces the original Task 7 behaviour, with these deltas:
  - `check_door(..., client: XsiamClient | None = None)`.
  - Data doors' recent rows (after the drop) read `open` / `verified` / `verified`, label `verified (recent rows)`, method `recent_rows`. Zero rows read `closed` / `check`.
  - The `DOOR_CHECK_UNAVAILABLE` branch is removed, and `xpanse` reads `unknown` as planned.
  - `windows_events` attestation `producer` accepts `broker_wec`, `xdr_agent_xth` and `xdr_collector` (P3 relies on `xdr_collector`).

- [ ] **Step 1: Write the failing tests.** These are the original Task 7 list minus the six now in Task 5: `test_rows_open`, `test_zero_rows_closed`, `test_xdr_agent_not_in_tenant_closed`, `test_xdr_agent_pod_uses_node_name`, `test_xdr_agent_pod_without_node_unknown`, `test_ngfw_forwarding_off_closed`, `test_attest_requires_name`, `test_attest_shown_attested`, `test_windows_attest_requires_producer` and `test_xth_needs_agent_open_and_attestation`. Add `test_data_door_recent_rows_label`, which asserts `label == "verified (recent rows)"` and that a canary-only page reads `closed`. Extend `test_two_call_cap` to these paths.
- [ ] **Step 2: Run in both zones.** Expected: fail.
- [ ] **Step 3: Implement**, with every transport injectable.
- [ ] **Step 4: Run in both zones, then regenerate ground truth.** Expected: green.
- [ ] **Step 5: Commit** `feat(api): read-only data-door checks and attestation`, staging the files above by name.

### Task 9: Door requirement on every seeded result (original Task 8) — kept

Execute the original Task 8 exactly as written: same files, interfaces, tests and commit. Its two added tests already landed in Task 2.

### Task 10: Exclude silent rows behind a closed door (original Task 9) — kept

Execute the original Task 9 exactly as written. Its two added tests land with their doors: `test_closed_transport_door_excludes_nothing` in Task 15, and `test_agent_door_closed_after_xdrc_install_excludes_not_misses` in Task 21.

### Task 11: PASS withheld on door exclusion (original Task 10) — kept, unchanged

### Task 12: Opt-in door watcher (original Task 11) — amended

**Files:** as the original Task 11, plus `core/api/health.py` (`NOT_CHECKED` :61).

**Interfaces:** as the original Task 11, plus:
- The sweep calls `check_door` only. It never calls `send_http_probe`, the probe route, or any control sender.
- Transport doors are re-checked like any other door; `check_door` chooses freshness for them (Task 15).
- `NOT_CHECKED` gains a second entry, besides the original one for the watcher being off. Copy exact:

  `{"what": "transport_door_verified_rung", "why": "a probe writes one record through the door and is a lab-only operator action; no sweep sends one", "checked_by": "POST /api/doors/{key}/probe, then POST /api/doors/{key}/check"}`

- [ ] **Step 1: Write the failing tests.** These are the original five, plus:
  - `test_watcher_never_probes`: the probe senders are monkeypatched to raise, and `DoorProbe` row count is unchanged after a sweep;
  - `test_not_checked_names_probe_route`.
- [ ] **Step 2: Run in both zones.** Expected: fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run in both zones.** Expected: green.
- [ ] **Step 5: Commit** `feat(api): opt-in door watcher for long-running streams`.

### Task 13: Report and run readout (original Task 12) — amended

**Files:** as the original Task 12, plus `core/engine/door_state.py`.

**Interfaces:** as the original Task 12, plus:
- `door_state.doors_verified_by_probe(current: dict[str, dict]) -> int`: keys whose effective state is `open` with `method == "nonce"`.
- `/api/runs/{id}` gains:
  - `data_paths: [{door_key, door_id, label, method, nonce, dataset, sent_from, latency_seconds, queries_issued, field_names}]`, covering the `http_collector:<target>` doors the run resolved and every transport door with a configured target on this instance;
  - `doors_verified_by_probe`.

  `tenant_verified` stays 0.
- The markdown report gains a "Data paths checked (transport, not coverage)" section. Neither block enters any count.
- The `CLAUDE.md` paragraph under Measurement loop also names probe vs check, the four labels and `doors_verified_by_probe`.

- [ ] **Step 1: Write the failing tests.** These are the original three, plus:
  - `test_data_paths_block_excluded_from_counts`: coverage, missed, verdict and MTTD are identical with and without the block;
  - `test_doors_verified_by_probe_separate_from_tenant_verified`: one probe-verified door gives `doors_verified_by_probe == 1` and `tenant_verified == 0`.
- [ ] **Step 2: Run in both zones.** Expected: fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the full host suite in both zones, then `make validate` and `make test-backend-non-utc`.** Expected: all green.
- [ ] **Step 5: Commit** the code and the docs as separate `feat` and `docs` commits.

---

## Phase P2 — Broker VM syslog (`feature/api-broker-syslog-door`, from `dev` after P1 merges)

Waits on (spec §10): P1 merged, and Henry deploying the Broker VM. Only Task 19 touches the VM; Tasks 14–18 use injected transports.

### Task 14: Broker VM target kinds, `params`, transport doors and `configured_facts` by kind

**Files:**
- Modify: `core/eal_simulator/targets.py`
- Modify: `core/eal_simulator/target_preflight.py`
- Modify: `core/models.py` (`CollectorTargetConfig.params: JSON`, nullable)
- Modify: `core/database.py` (new `_migrate_collector_target_columns`, called from `_init_db_inner` after `_migrate_eal_run_columns`)
- Modify: `core/engine/collector_targets.py` (`to_spec`, `upsert(params=...)`)
- Modify: `core/api/collector_targets.py` (`TargetPut.params: dict = {}`; `_public` returns `params`)
- Modify: `core/engine/ingestion_doors.py`, `core/engine/door_state.py::configured_facts` (:130-132), `core/api/doors.py`
- Modify: `docs/reference/api-and-agent-surface.md`
- Test: `tests/eal_simulator/test_collector_targets_resolver.py`, `tests/engine/test_door_state.py`, `tests/api/test_collector_targets_api.py`, `tests/api/test_doors_api.py`, `tests/engine/test_door_corpus.py`

**Interfaces:**
- Produces:
  - Kinds and schemes:
    - `KINDS` adds `broker_syslog` and `broker_wec`.
    - `SCHEMES_FOR_KIND`: `broker_syslog` → `syslog+tcp`, `syslog+udp`, `syslog+tls`; `broker_wec` → `wec`. HTTP kinds keep `http`/`https`.
    - `_DEFAULT_PORTS` adds `syslog+tcp` 514, `syslog+udp` 514, `syslog+tls` 6514 and `wec` 5986.
  - `DOOR_FOR_KIND = {"xsiam_http_collector": "http_collector", "http_forwarder": "http_collector", "broker_syslog": "broker_syslog", "broker_wec": "broker_wec"}`.
  - `TargetSpec.params: Mapping[str, Any]` (default empty; `compare=False`). `warnings()` adds two entries:
    - `SYSLOG_PORT_DISALLOWED`, with the spec §7.1 remediation, for a port in the reserved set;
    - `SYSLOG_SOURCE_UNCALIBRATED` while `params.expected_source_cidr` is unset.
  - `preflight_target` stops non-HTTP kinds at a new verdict, `configured`, which is added to `CHECK_VERDICTS`. `reachable` and `accepted` read `not_checked`, with the detail "a Broker VM row is reached from its sender; use Probe (lab) or Check".
  - `upsert`:
    - A change to `params` discards `last_check`.
    - A non-HTTP kind is stored with `is_default=False` and never demotes the HTTP default.
    - Kinds outside `HTTP_KINDS` keep `plugins=[]`.
  - The `cortexsim_` dataset-prefix warning is added in `_public` only when `instance_probe_pinned(db)` is true.
  - Doors (`role="transport"`, `scope="target"`, evidence = the spec §14 activation page URL for each applet):
    - `Door("broker_syslog", "Broker VM Syslog Collector", "target", role="transport")`
    - `Door("broker_wec", "Broker VM Windows Event Collector", "target", role="transport")`
  - `Door.via: str | None = None` (informational). The four `syslog_*` data doors carry `via="broker_syslog"`.
  - `configured_facts` keys each target as `door_key(DOOR_FOR_KIND[s.kind], s.name)`.
  - `/api/doors` rows:
    - Transport doors read `detections_needing: null`.
    - Rows gain `via`.
    - A `broker_wec` with no target reads `unknown` / `authored` with `code: "WEC_NO_WINDOWS_SOURCE"`.

- [ ] **Step 1: Write the failing tests.**
  - Swap Task 2's stand-in for a real `TargetSpec(kind="broker_syslog", url="syslog+tcp://bvm-01:5514", is_default=False, ...)`, keeping its assertions.
  - `test_target_door_facts_keyed_by_kind`. An HTTP target `lab-http` plus a `broker_syslog` target `bvm-5514` give `"http_collector:bvm-5514" not in facts` and `facts["broker_syslog:bvm-5514"] == "configured"`.
  - `test_broker_syslog_target_round_trips_params` (`{"framing": "octet", "format": "cef", "sender_agent_id": "pov-target-01"}`).
  - `test_non_http_default_does_not_displace_http_default`.
  - `test_cortexsim_prefix_warning_only_when_probe_pinned`.
  - `test_migration_adds_params_column`: an old SQLite file gains `params`.
  - `test_syslog_port_disallowed_warns`. 5514 and 514 carry no warning; 4369 and 63500 do.
  - `test_broker_target_preflight_stops_at_configured`.
  - `test_transport_door_detections_needing_null`.
  - `test_wec_without_windows_source_unknown`. Besides the row, `POST /api/doors/broker_wec:x/probe` answers 409 `DOOR_NOT_PROBEABLE`.
- [ ] **Step 2: Run in both zones.** Expected: `test_target_door_facts_keyed_by_kind` FAILS on today's `configured_facts`, with `http_collector:bvm-5514` present. Save that output for the PR body. The rest fail on the missing kinds and doors.
- [ ] **Step 3: Implement.** `test_transport_doors_never_in_requirement` (Task 2) is now meaningful. Show it red once with a scratch edit (not committed) that names `broker_syslog` in one `spec/dataset_requirements.yml` row's `doors:`, save that output, then revert.
- [ ] **Step 4: Run these files plus `tests/eal_simulator tests/engine/test_door_resolution.py` in both zones.** Expected: green.
- [ ] **Step 5: Commit.**

```bash
git add core/eal_simulator/targets.py core/eal_simulator/target_preflight.py core/models.py \
        core/database.py core/engine/collector_targets.py core/api/collector_targets.py \
        core/engine/ingestion_doors.py core/engine/door_state.py core/api/doors.py \
        docs/reference/api-and-agent-surface.md tests/eal_simulator/test_collector_targets_resolver.py \
        tests/engine/test_door_state.py tests/api/test_collector_targets_api.py \
        tests/api/test_doors_api.py tests/engine/test_door_corpus.py
git commit -m "feat(api): Broker VM syslog and WEC targets as transport doors"
```

### Task 15: The freshness rung and the `collection_auditing` classifier

**Files:**
- Create: `core/engine/door_diagnostics.py`
- Modify: `core/engine/door_checks.py`, `core/api/doors.py`
- Test: `tests/engine/test_door_checks.py`, `tests/engine/test_door_diagnostics.py` (create), `tests/connectors/test_door_exclusion.py`

**Interfaces:**
- Produces:
  - `COLLECTOR_TYPE = {"broker_syslog": "Syslog Collector", "broker_wec": "Windows Event Collector"}`. The values are unverified, and the evidence says so.
  - `COLLECTION_AUDIT_QUERY = 'dataset = collection_auditing | filter collector_type = "{collector_type}" | sort desc _time | limit 20'`.
  - `COLLECTION_AUDIT_COLUMNS = ("collector_type", "description")`. A row missing either raises `DoorShapeError("COLLECTION_AUDIT_SHAPE_UNRECOGNISED")`.
  - `classify_collection_audit(door_id: str, rows: list[dict]) -> list[dict]`, returning `{"code", "description", "ip"}` entries:
    - `Connection rejected from (?P<ip>\S+)` → `SYSLOG_SOURCE_IP_REJECTED`
    - `does not match the expected format` → `SYSLOG_FORMAT_MISMATCH`
    - `first log` → `SYSLOG_FIRST_LOG_TYPE_MISMATCH`
    - `octet` → `SYSLOG_OCTET_FRAMING_FAILED`
    - `could not reach the receptor` → `BROKER_RECEPTOR_UNREACHABLE`
    - `Failed starting WEC applet` → `WEC_APPLET_START_FAILED`

    Anything else → `COLLECTION_AUDIT_UNCLASSIFIED`, with the description verbatim.
  - The freshness method runs for `role == "transport"` doors with no probe, or off the lab tenant:
    - Q1 is `ROWS_QUERY` on `spec.dataset` (3,600 s, `limit 20`, CortexSim rows dropped).
    - Q2 is `COLLECTION_AUDIT_QUERY` every time.
    - A non-CortexSim row → `open` / `traffic` / `traffic_seen`, label `traffic seen`, plus `TRAFFIC_SEEN_WITH_COLLECTION_ERRORS` when Q2 classified any row.
    - None → `unknown` / `traffic` / `configured`, `NO_RECENT_TRAFFIC`, with the classified codes in `causes` and `observed_source_ip` when one was rejected.
    - An `XsiamQueryError` on Q1 whose message names the dataset → `unknown`, `TRAFFIC_DATASET_NOT_FOUND`. The error shape is unverified, and the evidence says so.
    - For `broker_wec`, the evidence carries `rows_not_attributable: true`.
  - Override rule: when the key's current conclusive event has `method == "nonce"`, the freshness reading is recorded as `state="unknown"`, with `evidence["would_read"]` and `evidence["superseded_by_probe_event"]`.
  - `/api/doors` rows:
    - Before any event, a transport target reads `code: "NO_PROBE_SENT"`. The remediation reads "run Check; probes are lab-only" off the lab.
    - A data door with `via` gains `via_line`, only while that data door is `closed`. Exact copy:
      - probe-verified: `"the Broker VM row {target} delivers from {sent_from}; the source is not sending"`
      - traffic seen: `"the Broker VM row {target} has recent traffic"`

- [ ] **Step 1: Write the failing tests:**
  - `test_transport_door_without_probe_unknown_not_closed`
  - `test_freshness_reads_traffic_seen_never_verified` (`label == "traffic seen"`, `basis == "traffic"`, `rung == "traffic_seen"`)
  - `test_freshness_miss_unknown_not_closed`. Q2 holds "Connection rejected from 192.168.122.77", giving `causes == ["SYSLOG_SOURCE_IP_REJECTED"]` and `observed_source_ip == "192.168.122.77"`.
  - `test_freshness_never_overrides_probe_reading`. With a probe-basis `closed` on record, a freshness reading with traffic leaves the effective state `closed`, and the new event reads `unknown` with `would_read == "open"`.
  - `test_traffic_seen_not_in_doors_verified_by_probe`
  - `test_collection_audit_row_missing_columns_unknown`
  - `test_unclassified_audit_description_reported_verbatim`
  - `test_freshness_two_calls_and_queries_issued` (== 2)
  - `test_via_line_only_when_data_door_closed`
  - `test_closed_transport_door_excludes_nothing` (original Task 9's addition). A `broker_syslog` door is closed. A run's silent rows get no `DOOR_NOT_VERIFIED`, and `measures` counts nothing for it.
- [ ] **Step 2: Run in both zones.** Expected: fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run in both zones.** Expected: green.
- [ ] **Step 5: Commit** `feat(api): freshness rung for transport doors, read-only on any tenant`, staging the files above by name.

### Task 16: Readiness states each transport kind

**Files:**
- Modify: `core/api/readiness.py` (`get_readiness` :59)
- Modify: `docs/reference/api-and-agent-surface.md`
- Test: `tests/api/test_readiness.py`

**Interfaces:**
- Consumes: `door_state.current`, `configured_facts`, `DOORS`.
- Produces: the `GET /api/readiness` response gains two fields.
  - `transport_doors`: `[{"door_id", "label", "status": "not_configured" | "ok" | "degraded", "statement", "targets": [{"key", "state", "rung", "label", "code"}]}]`.
  - `transport_degraded: [keys]`.

  Status rules:
  - No target → `not_configured`, with an exact statement: Broker VM doors `f"This POV routes no data through the Broker VM {label}."`; XDR Collector (Task 20) `"This POV routes no data through an XDR Collector."`.
  - Every target `open` at rung `traffic_seen` or `verified` → `ok`.
  - Otherwise → `degraded`, with `f"{n} configured target(s) have no reading at traffic seen or above."`.

  The route still makes zero outbound calls.

- [ ] **Step 1: Write the failing tests:**
  - `test_readiness_not_degraded_without_transport_targets`. Every transport entry reads `not_configured`, `transport_degraded == []`, and the statement contains "routes no data through".
  - `test_configured_transport_target_without_reading_degrades`
  - `test_readiness_transport_block_makes_no_outbound_call` (`socket.connect` patched to raise)
- [ ] **Step 2: Run in both zones.** Expected: fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run in both zones.** Expected: green.
- [ ] **Step 5: Commit** `feat(api): readiness states transport doors without degrading an unused one`.

### Task 17: Run-less beacon door tasks, and the run-live gate through the route

**Files:**
- Modify: `core/engine/orchestrator.py` (next to `_enqueue` :1159)
- Modify: `core/api/runs.py` (`/output` :1308, `/complete` :1397, `/control` :1644)
- Modify: `core/engine/door_probes.py`, `core/api/doors.py`
- Test: `tests/engine/test_door_beacon_dispatch.py` (create), `tests/api/test_door_runs_bypass.py` (create)

**Interfaces:**
- Produces:
  - `DOOR_RUN_PREFIX = "doorprobe-"`. `Orchestrator.enqueue_door_task(agent_id: str, probe_id: int, steps: list[dict]) -> str` builds `Task(task_id, run_id=f"doorprobe-{probe_id}", scenario_id="doorprobe", steps, identity_context=None)`. The task lives in the in-memory queue only: never a `queued_tasks` row, never a `Run`, never a `Result`. After a restart its probe reads `DOOR_PROBE_SEND_UNCONFIRMED`.
  - `BEACON_STEP_BUILDERS: dict[str, Callable[[TargetSpec, DoorProbe], list[dict]]]`. Each step is `{"id": "door-probe", "command": str, "identity": "direct"}`.
  - `async def dispatch_beacon_probe(db, spec, *, door_key, control, now=None) -> DoorProbe`. The row is written `queued`. When the sender (`params.sender_agent_id`) is not `online` by `_derive_status`, the row reads `failed` / `DOOR_PROBE_SENDER_UNAVAILABLE`, an `unknown` / `configured` `DoorEvent` is recorded, and no task is queued.
  - `async def record_sender_result(db, probe_id: int, *, exit_code: int, output: str) -> DoorProbe`. It parses `cortexsim_door_sent_at=<ISO>` into `sent_at_sender` and `cortexsim_door_tool_missing=<tool>`. Then:
    - exit 0 → `sent`;
    - 127 with a tool-missing line → `failed`, `DOOR_PROBE_SENDER_UNAVAILABLE`;
    - other non-zero → `failed`, `SEND_FAILURE_CODE[door_id]`.

    Output is kept in `detail["output"]`, capped at 8 KB.
  - `runs.py`:
    - `/output` and `/complete` short-circuit `run_id.startswith(DOOR_RUN_PREFIX)` into `record_sender_result`, and touch no `Run`.
    - `/control` answers `{"abort": False, "run_id", "status": "running"}` until the result is recorded, then `{"abort": True, ..., "status": "complete"}`.

- [ ] **Step 1: Write the failing tests:**
  - `test_door_task_seeds_no_run_or_result`
  - `test_door_task_never_listed_in_runs` (`GET /api/runs` is unchanged)
  - `test_control_does_not_abort_door_task_until_done`. Today a vanished run reads `abort: True`.
  - `test_sender_offline_unavailable`
  - `test_tool_missing_maps_sender_unavailable`
  - `test_probe_route_refused_while_run_live`. Make `broker_syslog` probeable with a monkeypatched builder; a running `Run` targeting the sender gives 409 `DOOR_PROBE_RUN_LIVE`, with no row.
- [ ] **Step 2: Run in both zones.** Expected: fail. `test_control_does_not_abort_door_task_until_done` reads `abort: True`: keep that output.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run them plus `tests/engine/test_orchestrator_queue.py tests/api` in both zones.** Expected: green.
- [ ] **Step 5: Commit** `feat(api): beacon door tasks that are not scenario runs`.

### Task 18: Syslog probe, controls, calibration and the dedicated-dataset nonce scan

**Files:**
- Modify: `core/engine/door_probes.py`, `core/engine/door_checks.py`, `core/engine/door_diagnostics.py`, `core/api/doors.py` (`DoorTransports.syslog_sender`)
- Test: `tests/engine/test_door_syslog.py` (create), `tests/engine/test_door_checks.py`

**Interfaces:**
- Produces:
  - Registration:
    - `PROBEABLE` adds `broker_syslog`. A `syslog+tls` target is refused with 409 `DOOR_NOT_PROBEABLE`, detail "TLS is deferred", and its freshness miss reads `unknown`, `SYSLOG_TLS_DOC_CONFLICT`.
    - `CONTROLS["broker_syslog"] = ("calibration", "wrong_source", "wrong_format")`.
    - `GRACE_SECONDS["broker_syslog"] = 900`.
    - `SEND_FAILURE_CODE["broker_syslog"] = "SYSLOG_TCP_UNREACHABLE"`.
  - `cef_message(nonce: str, rt_ms: int, *, control_id: str | None = None) -> str` returns `CEF:0|cortexsim|door_syslog|1|door-probe|CortexSim door probe <nonce>|1|cs1Label=cortexsim_door_nonce cs1=<nonce> msg=CortexSim door probe - safe to discard rt=<rt_ms>`. A control appends `cs2Label=cortexsim_door_control cs2=<control_id>`.
  - `syslog_probe_steps(spec, row) -> list[dict]` emits one step. Its command:
    1. `command -v logger >/dev/null 2>&1 || { echo cortexsim_door_tool_missing=logger; exit 127; }`
    2. `echo "cortexsim_door_sent_at=$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)"`
    3. `logger -n <host> -P <port> -T --octet-count --rfc3164 -t cortexsim '<cef>'`

    UDP uses `-d` and drops `--octet-count`. The payload is `shlex.quote`d. `wrong_format` sends RAW text `CortexSim door control <nonce> control_id=wrong_format`, with no `CEF:` prefix.
  - `async def send_syslog_inprocess(host: str, port: int, message: bytes, *, framing: str) -> None` (stdlib asyncio streams). `wrong_source` is sent with it from SimCore. The probe, `calibration` and `wrong_format` go through the beacon `params.sender_agent_id`.
  - Sender result:
    - exit 0 on TCP → probe evidence `connect: "ok"`, rung `reachable`.
    - UDP never reads `reachable`, and the evidence carries `SYSLOG_UDP_UNCONFIRMABLE`.
  - The dedicated-dataset nonce method for every door except `http_collector`:
    - Q1 is `DEDICATED_QUERY = 'dataset = {dataset} | sort desc _insert_time | limit 200'`, with `limit=200` passed to `run_xql`.
    - `scan_rows_for_nonces(rows: list[dict]) -> dict[str, str]` maps nonce → column path. It recurses through nested dicts and lists and matches `cxsdp-[0-9a-f]{32}` as a substring of string values.
    - The probe's scan-control nonce is scanned in Q1 at no extra call.
    - Unmatched `cxsdp-` values go to `evidence.unmatched_nonces`, with `logger.error`. They are never a hit.
    - Rows present with no token → `PROBE_ROW_SHAPE_UNRECOGNISED`.
    - Controls resolve `absent` only when the page held fewer than 200 rows.
    - Q2 on a probe miss past grace is `COLLECTION_AUDIT_QUERY`. The event reads code `PROBE_NOT_FOUND`, with `causes` from the classifier.

- [ ] **Step 1: Write the failing tests:**
  - `test_probe_command_is_logger_tcp_octet_rfc3164`
  - `test_no_dev_tcp_or_nc_in_probe_steps`
  - `test_logger_exit_nonzero_connect_error_unreachable` (`SYSLOG_TCP_UNREACHABLE`, then the check reads `closed` / `check` / `configured`)
  - `test_udp_target_never_reachable`
  - `test_wrong_source_sent_inprocess_from_simcore` (injected `syslog_sender` called; no beacon task)
  - `test_calibration_is_a_control_and_counts_as_control_miss`
  - `test_nonce_found_inside_raw_string` (Review Focus 5). A row `{"_raw_log": "<13>... CEF:0|...|cs1=<nonce> ..."}` and a nested `{"event": {"msg": "... <nonce>"}}` are both found, with the column paths in evidence.
  - `test_unmatched_nonce_never_a_hit`
  - `test_dedicated_rows_without_token_unrecognised`
  - `test_full_page_leaves_controls_pending`
  - `test_probe_miss_after_grace_runs_collection_audit`
  - `test_dedicated_scan_passes_limit_200`
  - `test_tls_target_not_probeable_doc_conflict`
- [ ] **Step 2: Run in both zones.** Expected: fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the door test files in both zones, then `make validate`.** Expected: green.
- [ ] **Step 5: Commit** `feat(api): syslog door probe, controls and calibration`. Then commit a CHANGELOG entry as `docs(api): broker syslog door in the changelog`.

### Task 19: Lab run — Broker VM syslog (runbook steps 3–4)

**Files:** append to the lab-run doc on `docs/docs-ingestion-doors-lab-run`.

- [ ] **Step 1: Broker VM.** Record where bvm-01 sits (libvirt network or LAN), its IP, its version, and its Pi-hole FQDN. The console password goes in 1Password, never in the doc.
- [ ] **Step 2: Syslog row.** In the console: TCP 5514, CEF, Vendor `cortexsim`, Product `door_syslog`, Source Network `192.0.2.1/32`. Then `PUT /api/collector-targets/bvm-5514` with `kind=broker_syslog`, `url=syslog+tcp://<bvm>:5514`, `dataset=cortexsim_door_syslog_raw` and `params={"framing":"octet","format":"cef","sender_agent_id":"pov-target-01-4dcdce"}`. Use the beacon id current at bring-up.
- [ ] **Step 3: Calibrate.**
  1. Send `{"control":"calibration"}`, then Check. Expect `NO_RECENT_TRAFFIC` with `observed_source_ip`.
  2. PUT `params.expected_source_cidr="<ip>/32"` and `params.expected_source_observed_by="rejection"`.
  3. Set Source Network to `<ip>/32` in the console.
- [ ] **Step 4: Probe and controls.** Probe; Check after 900 s (expect `verified (probe)`); then `wrong_source` and `wrong_format`; then one freshness check.
- [ ] **Step 5: Record** the per-probe fields as in Task 7. Also record:
  - the observed `collector_type` value;
  - whether RFC 3164 parsed (else retry `--rfc5424`, and record it);
  - whether `logger -T` exited non-zero on a stopped Broker VM;
  - whether `_reporting_device_ip` is filled;
  - the console's Logs Received and Logs Sent for the row, as the lab witness.
- [ ] **Step 6: Commit** `docs(lab): Broker VM syslog door probe, calibration and controls`.

---

## Phase P3 — XDR Collector on pov-target-01 (`feature/api-xdr-collector-door`) — parked

Waits on (spec §10): P1 and Task 8 (the `xdr_agent:<agent>` check and the `windows_events` producer attestation), plus Task 7 Step 2 recording XDR Collectors as enabled on deathstar. **Do not start until that answer is recorded.**

### Task 20: `xdr_collector` kind, door, freshness and validity facts

**Files:**
- Modify: `core/eal_simulator/targets.py`, `core/engine/ingestion_doors.py`, `core/engine/door_diagnostics.py`, `core/engine/door_checks.py`, `core/engine/door_probes.py`, `core/api/doors.py`
- Modify: ground truth (one route)
- Create: `core/engine/xdrc_validity.py`
- Test: `tests/engine/test_xdrc_validity.py` (create), `tests/engine/test_door_checks.py`, `tests/api/test_doors_api.py`

**Interfaces:**
- Produces:
  - The `xdr_collector` kind uses scheme `file` (`file://<host>/var/log/cortexsim/door-xdrc/probe.log`) and maps to door `xdr_collector` (`role="transport"`). Params: `host_agent_id`, `collector_ip`, `validity`.
  - `METRICS_VIEW_QUERY = 'preset = metrics_view | filter _collector_ip = "{collector_ip}" | sort desc last_seen | limit 5'`. In the freshness Q2:
    - no row adds `XDRC_NO_METRICS`;
    - `last_seen` older than 3,600 s adds `XDRC_METRICS_STALE`;
    - otherwise `metrics_last_seen` is recorded (`null` when unmeasured).
  - `xdrc_validity.SUPPORTED_LINUX` is the pinned copy of the vendor list (Ubuntu Server 12, 14, 16, 18, 20 and 22, plus the RHEL, SLES and Oracle versions), with `SOURCE_URL` and `FETCHED = "2026-11-11"`.
  - `validity_facts(os_release: str, uname_m: str, *, agent_present: bool | None, panw_before: dict[str, str], panw_after: dict[str, str] | None, installed: bool | None) -> dict` returns `host_os`, `arch`, `host_supported`, `coinstall`, `installed`, `panw_diff` and `codes`. The codes it can set: `XDRC_HOST_UNSUPPORTED`, `XDRC_COINSTALL_UNDOCUMENTED`, `XDRC_AGENT_FILES_TOUCHED`, `XDRC_NOT_INSTALLED`.
  - `POST /api/doors/{key}/facts` (`xdr_collector` only; read-only on the host; no tenant call) queues a Run-less task with prefix `doorfacts-`. It runs `cat /etc/os-release; uname -m; dpkg -l | grep xdr-collector; find /etc/panw -type f -exec sha256sum {} +`, and the result is stored in `params.validity` with `collected_at`. `agent_present` is read from the `xdr_agent:<host_agent_id>` door's current state.

- [ ] **Step 1: Write the failing tests:**
  - `test_ubuntu_2404_unsupported` (`VERSION_ID="24.04"` gives `host_supported is False` and `XDRC_HOST_UNSUPPORTED`)
  - `test_ubuntu_22_supported`
  - `test_non_x86_64_unsupported`
  - `test_agent_present_sets_coinstall_undocumented`
  - `test_panw_diff_flags_agent_files_touched`
  - `test_metrics_view_no_row_unknown_not_closed`
  - `test_metrics_stale_code`
  - `test_facts_route_queues_doorfacts_task_no_tenant_call`
- [ ] **Step 2: Run in both zones.** Expected: fail.
- [ ] **Step 3: Implement**, then regenerate ground truth.
- [ ] **Step 4: Run in both zones.** Expected: green.
- [ ] **Step 5: Commit** `feat(api): XDR Collector door with host validity facts`.

### Task 21: XDR Collector probe, `uncovered_file` control, agent regression and the run label

**Files:**
- Modify: `core/engine/door_probes.py`, `core/engine/door_checks.py`, `core/api/runs.py`
- Test: `tests/engine/test_door_xdrc.py` (create), `tests/connectors/test_door_exclusion.py`, `tests/api/test_run_door_readout.py`

**Interfaces:**
- Produces:
  - `PROBEABLE` adds `xdr_collector`, `CONTROLS["xdr_collector"] = ("uncovered_file",)` and `GRACE_SECONDS["xdr_collector"] = 900`. Every probe or control task re-collects the facts first.
  - The probe step runs `mkdir -p /var/log/cortexsim/door-xdrc` and appends one JSON line (spec §6.2 fields) to `probe.log`. The control appends to `excluded.log` instead.
  - States:
    - A probe hit on an unsupported host reads `open`, with evidence `host_supported: false, coinstall: true`.
    - Any miss, or a failed install, reads `unknown`, `XDRC_HOST_UNSUPPORTED`, never `closed`.
  - `check_door("xdr_agent:<agent>")`: when an `xdr_collector` target has that `host_agent_id` and `validity.installed` is true, a `closed` reading adds `XDRC_COINSTALL_SUSPECTED` to its evidence. The agent-regression checks are operator actions, run after install and before the next endpoint run (runbook). The launch path stays tenant-free (original Global Constraint).
  - The `/api/runs/{id}` readout gains `labels: ["HOST_RUNS_XDR_COLLECTOR_UNSUPPORTED"]` while the run's target agent hosts an installed collector with `host_supported: false`. It is a label only.

- [ ] **Step 1: Write the failing tests:**
  - `test_xdrc_unsupported_host_miss_unknown`
  - `test_xdrc_hit_on_unsupported_host_open_with_flags`
  - `test_every_xdrc_task_recollects_facts`
  - `test_agent_door_closed_after_xdrc_install_excludes_not_misses` (original Task 9's addition). The row is excluded as `DOOR_NOT_VERIFIED` and the evidence carries `XDRC_COINSTALL_SUSPECTED`.
  - `test_xdrc_run_label_changes_no_score` (original Task 12's addition). Verdict, counts and MTTD are identical with and without the label.
- [ ] **Step 2: Run in both zones.** Expected: fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run in both zones, then `make validate`.** Expected: green.
- [ ] **Step 5: Commit** `feat(api): XDR Collector probe on an unsupported host reads unknown on a miss`, plus a CHANGELOG `docs` commit.

### Task 22: Lab run — XDR Collector on pov-target-01 (runbook step 5) — parked

- [ ] **Step 1: Before install.** `POST /api/doors/xdr_collector:pov-xdrc/facts`, then Check `xdr_agent:<agent>`. Record both.
- [ ] **Step 2: Install.** Install the DEB per spec §7.7: `tar xvf`, `mkdir -p /etc/panw`, copy `collector.conf`, `apt-get install ./<file>.deb`, and confirm with `dpkg -l | grep xdr-collector`. Re-run facts (the `/etc/panw` diff) and the `xdr_agent` check.
- [ ] **Step 3: Profile.** Apply the profile `CortexSim door probe` (one `filestream` input, `add_fields` vendor `cortexsim`, product `door_xdrc`) and its policy.
- [ ] **Step 4: Probe.** Send `uncovered_file`, probe, and Check after 900 s. Then run one freshness check, and record `metrics_view`'s `last_seen`.
- [ ] **Step 5: Before the next endpoint run, Check `xdr_agent:<agent>` again.** Commit `docs(lab): XDR Collector door on pov-target-01`.

---

## Phase P4 — Broker VM pull applets (`feature/api-broker-pull-doors`) — parked

Waits on (spec §10): P2, plus Task 7 Step 2 recording the Data Collection add-on as enabled. **Do not start until that answer is recorded.**

### Task 23: Pull-applet kinds, doors and freshness (FTP, Files and Folders, CSV, Database)

**Files:**
- Modify: `core/eal_simulator/targets.py`, `core/engine/ingestion_doors.py`, `core/engine/door_diagnostics.py`, `core/engine/door_checks.py`, `core/engine/door_probes.py`
- Test: `tests/engine/test_door_pull_applets.py` (create), `tests/api/test_readiness.py`

**Interfaces:**
- Produces:
  - Kinds:
    - `broker_ftp`: `sftp`, `ftps`, `ftp`
    - `broker_files`: `nfs`, `smb`
    - `broker_csv`: `smb`
    - `broker_db`: `postgresql`, `mysql`, `mssql`, `oracle`
  - Each kind maps to its own transport door. Params: `collect_every_seconds`, `sender_agent_id`, `db_ssl`.
  - `COLLECTOR_TYPE` adds `"FTP Collector"`, `"Files and Folders Collector"` and `"Database Collector"` (unverified).
  - `grace_for` returns `collect_every_seconds + 900` for pull doors, and 1,500 for `broker_csv`.
  - `TenantQuery` gains `audit: Callable[[dict], Awaitable[dict]] | None`. It calls `client.request("POST", "/public_api/v1/audits/management_logs", json={"request_data": {"filters": [{"field": "timestamp", "operator": "gte", "value": <since_ms>}], "search_from": 0, "search_to": 100}})`, the `OP-AUDITS-MANAGEMENT-LOGS` path, and counts toward the two-call cap. The filter field names come from that op's `doc_link` and are confirmed in Task 25.
  - The CSV freshness Q2 is that audit log. It is filtered locally to descriptions containing `Applet Activated`, `Applet Deactivated`, `License Expired` or `connection_test`. An empty result adds `CSV_DIAGNOSTIC_UNAVAILABLE`.
  - A `broker_csv` target carries `CSV_SOURCE_NOT_WINDOWS`, and any miss on it reads `unknown`, never `closed`.

- [ ] **Step 1: Write the failing tests:**
  - `test_pull_door_grace_is_interval_plus_900`
  - `test_csv_grace_1500`
  - `test_csv_freshness_uses_audit_log_q2`
  - `test_csv_empty_audit_adds_diagnostic_unavailable`
  - `test_csv_miss_unknown_source_not_windows`
  - `test_pull_freshness_states` (parametrized over the four kinds: traffic → `traffic seen`, none → `NO_RECENT_TRAFFIC`)
  - `test_readiness_states_pull_kinds_without_targets`
- [ ] **Step 2: Run in both zones.** Expected: fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run in both zones.** Expected: green.
- [ ] **Step 5: Commit** `feat(api): Broker VM pull-applet doors with read-only freshness`.

### Task 24: Pull-applet probes, controls and the lab witness

**Files:**
- Modify: `core/engine/door_probes.py`, `core/engine/door_diagnostics.py`
- Test: `tests/engine/test_door_pull_applets.py`

**Interfaces:**
- Produces: every task below runs through the beacon on `params.sender_agent_id` (door-src-01).
  - **FTP:** writes `incoming/door-probe-<nonce>.json`, one JSON line.
  - **Files:**
    - The same line plus a `pad` field that takes it past 256 bytes.
    - A probe file of 256 bytes or fewer is refused at send with `FILES_PROBE_TOO_SMALL`. This applies to probes only.
  - **CSV:** writes `incoming/door-probe-<nonce>.csv`, with the header `cortexsim_door_probe,cortexsim_door_nonce,door_key,sent_at,sent_from,message` and one row.
  - **DB:**
    - Inserts one row: `psql -d cortexsim_door -v nonce=... -c "INSERT INTO cortexsim_door_probe(sent_at, cortexsim_door_nonce, door_key, sent_from, message) VALUES (...)"`.
    - It runs as the owning role over local peer auth, so no secret appears on the command line.
  - **Controls:**
    - `uncollected_folder` (FTP, Files, CSV) writes the same payload, with its own nonce and the control marker, to `control/door-probe-<control nonce>.<ext>`.
    - `under_size_floor` (Files) writes a 200-byte control into `incoming/`.
    - `excluded_table` (DB) inserts into `cortexsim_door_probe_excluded`.
  - **Lab witness:**
    - A follow-up step tests for `incoming/door-probe-<nonce>.json.ingested` and for an un-renamed control file, and records `evidence.lab_witness`.
    - The witness never opens a door.
    - No rename after the interval plus grace adds `FTP_FILE_NOT_PICKED_UP` or `FILES_FILE_NOT_PICKED_UP`.
  - **Classifiers:** the documented FTP, Files and DB error strings (spec §7.3, §7.4, §7.6) map to their codes.

- [ ] **Step 1: Write the failing tests:**
  - `test_files_probe_padded_past_256_bytes`
  - `test_files_probe_at_or_under_256_refused` (`FILES_PROBE_TOO_SMALL`)
  - `test_under_size_floor_control_not_refused`
  - `test_controls_write_to_control_folder_with_own_nonce`
  - `test_db_probe_command_carries_no_secret`
  - `test_lab_witness_never_opens_a_door`
  - `test_ftp_no_rename_adds_not_picked_up`
  - `test_pull_classifier_rows` (parametrized over the documented strings)
- [ ] **Step 2: Run in both zones.** Expected: fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run in both zones, then `make validate`.** Expected: green.
- [ ] **Step 5: Commit** `feat(api): pull-applet door probes and controls`, plus a CHANGELOG `docs` commit.

### Task 25: Lab run — door-src-01 and the pull applets (runbook steps 6–7) — parked

- [ ] **Step 1: Build door-src-01.** Ubuntu 22.04 on ms-01, enrolled beacon, no XDR agent. Install with apt: `openssh-server`, `nfs-kernel-server`, `samba`, `postgresql`.
- [ ] **Step 2: Stage the sources.** Create the users, the `incoming/` and `control/` folders, the tables and the grants from spec §7.3–7.6. Credentials go in the vault and the applet only.
- [ ] **Step 3: Activate the applets.** FTP, Files and Folders, CSV and DB on bvm-01, with the spec configs. Run each applet's Test Connection and Generate Preview.
- [ ] **Step 4: Probe each applet.** Send its control, probe, and Check after grace. Record the fields as in Task 7, plus the lab witness.
- [ ] **Step 5: Commit** `docs(lab): Broker VM pull-applet doors on door-src-01`.

---

## Phase P5 — WEC probe (`feature/api-wec-door-probe`) — parked until a Windows Server 2012 R2+ source exists

### Task 26: WEC probe and the `uncollected_log` control

**Files:**
- Modify: `core/engine/door_probes.py`, `core/engine/door_checks.py`, `core/api/doors.py`
- Test: `tests/engine/test_door_wec.py` (create)

**Interfaces:**
- Produces:
  - `PROBEABLE` adds `broker_wec`, but only for a target whose `params.sender_agent_id` is an `os == "windows"` beacon. Other targets keep 409 `DOOR_NOT_PROBEABLE` and `WEC_NO_WINDOWS_SOURCE`.
  - The probe step runs `eventcreate /t INFORMATION /id 999 /l APPLICATION /so CortexSimDoorProbe /d "cortexsim_door_nonce=<nonce> sent_at=<t>"`.
  - `CONTROLS["broker_wec"] = ("uncollected_log",)`, which writes the same event to `/l SYSTEM`.
  - Q1 is the dedicated scan of `microsoft_windows_raw`, and Q2 is `collection_auditing` for the WEC.

- [ ] **Step 1: Write the failing tests:**
  - `test_eventcreate_command_application_log_id_999`
  - `test_uncollected_log_control_targets_system_log`
  - `test_wec_probeable_only_with_windows_sender`
- [ ] **Step 2: Run in both zones.** Expected: fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run in both zones.** Expected: green.
- [ ] **Step 5: Commit** `feat(api): WEC door probe from a Windows source`.

Runbook step 8 (the WEC lab run) follows this task, on the lab-run branch, once the Windows host exists. It must:
- give the standalone Broker VM an FQDN the forwarder resolves;
- export the PFX for mutual TLS;
- open outbound TCP 5986;
- add a Collected Events row for `Application` at minimum level Information, keeping the Security row.

---

## Phase 3 — console (`feature/ui-get-started-doors`, after P1 merges; transport steps after P2)

### Task 27: Get Started rebuilt from `/api/doors` (original Task 13) — amended

**Files:** as the original Task 13, plus `ui/src/components/console/ReadinessView.jsx` and `ui/src/api/client.js` (`probeDoor(key, control)`, `checkDoor(key)`).

**Interfaces:** as the original Task 13, with these deltas:
- The `bvm` step becomes one optional step per Broker VM target door. Each shows its label (`traffic seen` or `verified (probe)`), a Check button, and a Probe button that is disabled unless `probe_pinned`. With no Broker VM target, the step reads "Not used in this POV", never incomplete.
- The `collector` step lists the `http_collector` targets.
- Delete the 514 copy at `GetStartedView.jsx:258-259`.
- An optional XDR Collector step on the beacon's host shows `host_supported`.
- `ReadinessView` renders `transport_doors[].statement` verbatim, with a degraded chip only for `status == "degraded"`.
- The grace countdown shows `grace_remaining_seconds` and a "Check again" button. It does no polling.

- [ ] **Step 1: Write the failing tests.** These are the original four, plus:
  - `bvm_step_not_used_without_target`
  - `no_514_copy` (the rendered text has no "514")
  - `xdrc_step_shows_host_supported`
  - `readiness_transport_statement_rendered`
  - `probe_button_disabled_off_lab`
- [ ] **Step 2: Run `cd ui && npx vitest run src/components/console`.** Expected: fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run vitest and `npx vite build`, then the Get Started e2e through Chrome on the parallel `:8889` project.** Expected: green.
- [ ] **Step 5: Commit** `feat(ui): Get Started checks each door, including the XDR agent`.

### Task 28: Wizard links and the re-check button (original Task 14) — amended

**Files:** as the original Task 14, plus `ui/src/components/console/firewall/CheckStep.jsx`.

**Interfaces:** as the original Task 14, with these deltas:
- In `CollectorTargetsPanel`:
  - "Send test record" is unchanged on every tenant and still reads `accepted`.
  - A separate "Probe (lab tenant only)" button calls `POST /api/doors/http_collector:<name>/probe`. Off the lab it is disabled, with `DOOR_PROBE_NOT_LAB_TENANT` as its title.
- `CheckStep`'s `forwarding_syslog` stage (6b) links to the `broker_syslog:<target>` door whose host, port and protocol match a server in the firewall's syslog server profile. `PN_SYSLOG_DELIVERY_UNVERIFIED` stays degraded.

- [ ] **Step 1: Write the failing tests.** These are the original two, plus:
  - `send_test_record_still_reads_accepted`
  - `lab_probe_button_separate_and_disabled_off_lab`
  - `syslog_stage_links_matching_broker_door`
- [ ] **Step 2: Run vitest.** Expected: fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run vitest, `vite build` and both e2e specs in Chrome on `:8889`.** Expected: green.
- [ ] **Step 5: Commit** `feat(ui): setup wizards and launch re-check update door state`.

---

## Not verified

`tenant-verified` is 0. No probe, control or check in this plan has run against a live tenant. Tasks 7, 19, 22 and 25 are the first live runs, and until a door has its lab-run entry its evidence reads `field names: from documentation`. Open questions 1 and 2 of the spec keep Claude's defaults: no PA-440 leg, and the other data doors keep `verified (recent rows)`.
