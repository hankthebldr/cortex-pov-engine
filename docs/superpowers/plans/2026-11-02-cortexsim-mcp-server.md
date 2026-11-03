# CortexSim MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give an agent (Claude Code today, `cortex-pov-agent` next) a Model Context Protocol surface over CortexSim's REST API and a projection of the 116-operation XSIAM catalog, where every result carries the honesty envelope and no argument an agent can pass turns a write into a live tenant call.

**Architecture:** `core/mcp_server/` is a REST client of SimCore (it never imports `core.api`); one set of 41 tool functions serves both stdio (`python -m mcp_server`, real httpx to `CORTEXSIM_URL`) and an opt-in Streamable HTTP mount at `/mcp` (`httpx.ASGITransport(app)`, in-process). Six small REST changes make SimCore itself produce every number the envelope carries (`queries_issued`, ledger snapshot, `tenant_verified`) so the MCP layer only reads and sums. Writes and destructives through MCP always return the dry-run preview; `dry_run: false` on them is an error with the preview attached.

**Tech Stack:** Python 3.11, FastAPI 0.141.1 / Starlette 1.6.0, `mcp==1.30.0` (reference SDK, `FastMCP`), httpx 0.27.2, uvicorn 0.31.1, pytest, vitest (one parity test), Docker (`python:3.11-slim` image gate).

**Spec:** `docs/superpowers/specs/2026-11-02-cortexsim-mcp-server-design.md` (commit 994e733). Section numbers below (§5.2, §7, Appendix D …) refer to it.

## Global Constraints

- **No write path to Cortex.** `CORTEXSIM_XSIAM_ALLOW_WRITE` and `CORTEXSIM_XSIAM_ALLOW_DESTRUCTIVE` stay default-off; through MCP a write or destructive op *always* previews even when both are `True` (§6, G6).
- **Authored is not proven; tenant-verified is 0.** No test, doc line or tool result may report authored and proven as one number. Every envelope ends its statement with `tenant-verified: N` (§7).
- **A zero is degraded, not ok** for counts that should not be zero; an opt-in feature left off is not that (§4.5, D-17).
- **Tolerance hides bugs.** An unrecognised SimCore or tenant shape raises `SIMCORE_SHAPE_UNKNOWN` / `XSIAM_PARSE_ERROR`; it never reads as empty (A4).
- **Package is `core/mcp_server/`**, never `core/mcp/` (shadows the SDK under `PYTHONPATH=core`, D-2). The tool layer never imports `core.api.*` (D-1).
- **Tool names:** canonical ids are dotted (`xsiam.dataset.list`); wire names are derived by `naming.py` only, `.`→`_`, matching `^[a-z0-9_]{1,48}$` (D-4).
- **SDK pin `mcp==1.30.0`**; it forces `uvicorn[standard]==0.31.1` and `httpx==0.27.2` (resolved 2026-11-02 against the untouched pins `fastapi==0.141.1 starlette==1.6.0 pydantic==2.13.4 pydantic-settings==2.15.0 jsonschema==4.23.0 python-multipart==0.0.32`).
- **`queries_issued` is in ledger units** (one tenant operation; an XQL query is 1 including its polls; `OP-XQL-GET-QUERY-RESULTS` alone is 0); `http_round_trips` is a separate nullable field (D-15).
- **Tenants are addressed by vault name only**; no tool takes a URL, key id or credential (A3, D-6).
- **Lists cap at 50 default / 200 max** with `truncated` + `total`; the generic execute reply caps at 256 KiB (§5.5).
- **Env:** `CORTEXSIM_MCP_HTTP` (default off), `CORTEXSIM_MCP_ALLOWED_HOSTS` (comma list, default empty), `CORTEXSIM_MCP_MAX_TENANT_CALLS_PER_MINUTE` (default 30), `CORTEXSIM_URL` (default `http://127.0.0.1:8888`) — all four are `Settings` fields *and* `.env.example` lines, or `tests/test_config.py` fails.
- **Branch and commits:** everything lands on `feature/api-mcp-server`; stage by name (`git add <path>`), never `-A`/`.`/`-a`; subject ≤ 72 chars, imperative, repo style (`feat(api): …`, `feat(mcp): …`, `test(mcp): …`, `docs(reference): …`); every Gate A commit body states what was observed red before the fix; trailer `Co-Authored-By: Claude Mythos 5.1 <noreply@anthropic.com>`. **No PR** unless asked. Never bare `git stash` in this worktree.
- **Suites must stay green under both zones:** `PYTHONPATH=core .venv/bin/python -m pytest tests/ --ignore=tests/smoke --ignore=tests/e2e_isolated` and `make test-backend-non-utc` (`TZ=America/Los_Angeles`).

## Review Focus

Inputs the spec implies but names no test for; each is pinned in the owning task.

1. **A dataset name with XQL syntax in it** (`xdr_data | target type = lookup x`) passed to `xsiam.dataset.schema` must be refused before any query text is built, never interpolated — Task 11.
2. **A tenant name containing `/` or `..`** used in `/api/xsiam/tenants/{name}/…` must reach SimCore as one percent-encoded segment or be refused, never splice a path — Task 8.
3. **SimCore answering 200 with HTML** (the dev overlay serves a 404 console on `:8888`, see memory) must surface as `SIMCORE_SHAPE_UNKNOWN`, not a JSON decode traceback or an empty list — Task 8.
4. **A tenant reply over 256 KiB, or one that is not JSON,** through `xsiam.operation.execute` must come back cut with `truncated: true` and the byte count, or as `XSIAM_PARSE_ERROR` — Task 11.
5. **No `Date` header on a SimCore response, or a non-UTC host zone,** must still yield an `as_of` with an explicit `+00:00` offset — Task 8.

---

### Task 1: SDK pin, settings and the image still builds

**Files:**
- Modify: `core/requirements.txt` (pins: `mcp==1.30.0`; `uvicorn[standard]==0.29.0` → `0.31.1`; `httpx==0.27.0` → `0.27.2`)
- Modify: `core/config.py` (four new `Settings` fields after `CORTEXSIM_AGENT_ALLOW_RUNTIME_INSTALL`)
- Modify: `.env.example` (four commented blocks, in the file's existing comment style)
- Test: `tests/test_mcp_sdk_pin.py`

**Interfaces:**
- Produces: `config.settings.CORTEXSIM_MCP_HTTP: bool = False`, `CORTEXSIM_MCP_ALLOWED_HOSTS: str = ""`, `CORTEXSIM_MCP_MAX_TENANT_CALLS_PER_MINUTE: int = 30`, `CORTEXSIM_URL: str = "http://127.0.0.1:8888"`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_mcp_sdk_pin.py
import importlib.metadata, re
from pathlib import Path

REQ = Path(__file__).resolve().parents[1] / "core" / "requirements.txt"

def test_mcp_sdk_is_pinned_and_importable():
    assert "mcp==1.30.0\n" in REQ.read_text()
    assert importlib.metadata.version("mcp") == "1.30.0"
    from mcp.server.fastmcp import FastMCP  # noqa: F401  (1.x import path)

def test_transitive_pins_follow_the_sdk():
    text = REQ.read_text()
    assert re.search(r"^uvicorn\[standard\]==0\.31\.1$", text, re.M)
    assert re.search(r"^httpx==0\.27\.2$", text, re.M)

def test_mcp_settings_exist_with_safe_defaults():
    from config import Settings
    s = Settings(_env_file=str(REQ.parents[1] / ".env.example"))   # same construction as tests/test_config.py:114
    assert s.CORTEXSIM_MCP_HTTP is False
    assert s.CORTEXSIM_MCP_ALLOWED_HOSTS == ""
    assert s.CORTEXSIM_MCP_MAX_TENANT_CALLS_PER_MINUTE == 30
    assert s.CORTEXSIM_URL == "http://127.0.0.1:8888"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `PYTHONPATH=core .venv/bin/python -m pytest tests/test_mcp_sdk_pin.py -v`
Expected: FAIL — `mcp` not installed / `Settings` has no attribute `CORTEXSIM_MCP_HTTP`.

- [ ] **Step 3: Edit the pins, add the settings fields and the `.env.example` blocks**

Settings comments say what each does and that the mount is opt-in because the port has no auth (§4.4). `.env.example` blocks are commented-out defaults like the `CORTEXSIM_CANARY_SCENARIO_LINUX` block.

- [ ] **Step 4: Install into the host venv and run the tests**

Run: `.venv/bin/pip install -r core/requirements.txt` then the Step 2 command, then `PYTHONPATH=core .venv/bin/python -m pytest tests/test_config.py -v`
Expected: PASS (the env-example/Settings parity test includes the four new keys).

- [ ] **Step 5: Prove the image resolves and boots on the bumped uvicorn**

Run: `make build`, then read the resolved image tag from `make -n check-agent-shelf` and run `docker run --rm --entrypoint sh <tag> -c 'python -c "import mcp, uvicorn; print(mcp.__name__, uvicorn.__version__)"'`
Expected: image builds; prints `mcp 0.31.1`.

- [ ] **Step 6: Commit**

```bash
git add core/requirements.txt core/config.py .env.example tests/test_mcp_sdk_pin.py
git commit -m "chore(deps): pin mcp==1.30.0 and add the four MCP settings"
```
Body: the uvicorn/httpx bumps the SDK forces, the resolver output summary, `make build` green.

---

### Task 2: One XQL guard module (capabilities, write stage, ledger budget)

**Files:**
- Create: `core/integrations/xsiam/guards.py`
- Modify: `core/integrations/xsiam/codes.py` (add `XSIAM_CAPABILITY_MISSING`, `XQL_WRITE_STAGE_REFUSED` to the constants, `ALL_CODES`, `REMEDIATION`)
- Modify: `core/integrations/xsiam/exceptions.py` (two subclasses in the existing pattern: `XsiamCapabilityError` → code `XSIAM_CAPABILITY_MISSING`, http 403; `XqlWriteStageRefused` → `XQL_WRITE_STAGE_REFUSED`, http 400)
- Modify: `core/integrations/xsiam/client.py:28-44` (`XsiamClient.__init__` gains `capabilities: Optional[list[str]] = None`, stored as `self.capabilities`; add `@property base_url -> str` returning `self._base`)
- Modify: `core/integrations/xsiam/loader.py:19-29` (`load_xsiam_client` passes `capabilities=(row.config or {}).get("capabilities")`)
- Modify: `core/api/connectors.py:320-325` (`xql_authorised = xql_capability_granted(config)`)
- Modify: `core/engine/verifier.py:584-615` (`_tenant_name` and `_tenant_budget` become `from integrations.xsiam.guards import ledger_key_for as _tenant_name, tenant_budget as _tenant_budget`; delete the bodies)
- Test: `tests/integration/xsiam/test_guards.py`

**Interfaces:**
- Produces (`guards.py`):
  - `XQL_OPERATION_IDS: frozenset[str] = {"OP-XQL-START-XQL-QUERY", "OP-XQL-GET-QUERY-RESULTS"}`
  - `xql_capability_granted(config: Optional[Mapping]) -> bool` — `True` only when `config["capabilities"]` is a list containing `"xql"` (case-insensitive). **Absent or non-list → `False`.** This is what `core/api/connectors.py:321` *comment* says ("Absent capabilities == the legacy default, read-only") and the opposite of what its expression did (`not isinstance(caps, list) or …` authorised an absent field). Spec §5.6 and the comment win; the commit body states the inversion.
  - `require_xql_capability(capabilities: Optional[list[str]], *, tenant: str) -> None` — raises `XsiamCapabilityError` naming the tenant and `config.capabilities`.
  - `refuse_xql_write_stage(query: str) -> None` — raises `XqlWriteStageRefused` when `re.search(r"target\s+type\s*=\s*(dataset|lookup)\b", query, re.I)` matches; never rewrites the text.
  - `ledger_key_for(client) -> str` = `tenant_ledger_key(str(getattr(client, "_base", "") or ""))` (identical to today's `verifier._tenant_name`).
  - `tenant_budget(client)` — async context manager; body is today's `verifier._tenant_budget` verbatim (charge before, trip on `XsiamQuotaError`).
- Consumed by Tasks 3, 11.

- [ ] **Step 1: Write the failing tests**

```python
# tests/integration/xsiam/test_guards.py
import ast, asyncio, pathlib, pytest
from integrations.xsiam import guards, codes
from integrations.xsiam.exceptions import XsiamCapabilityError, XqlWriteStageRefused

@pytest.mark.parametrize("config,granted", [
    (None, False), ({}, False), ({"capabilities": "xql"}, False),
    ({"capabilities": ["read_alerts"]}, False), ({"capabilities": ["XQL"]}, True),
    ({"capabilities": ["read_alerts", "xql"]}, True)])
def test_xql_capability_granted_only_when_listed(config, granted):
    assert guards.xql_capability_granted(config) is granted

def test_require_xql_capability_raises_403_with_remediation():
    with pytest.raises(XsiamCapabilityError) as ei:
        guards.require_xql_capability(["read_alerts"], tenant="acme")
    assert ei.value.code == codes.XSIAM_CAPABILITY_MISSING and ei.value.http_status == 403
    assert "config.capabilities" in codes.REMEDIATION[codes.XSIAM_CAPABILITY_MISSING]

@pytest.mark.parametrize("q", ["dataset = x | target type = dataset out",
                               "dataset=x|TARGET TYPE=lookup l", "x | target  type = dataset"])
def test_write_stages_are_refused_not_rewritten(q):
    with pytest.raises(XqlWriteStageRefused) as ei:
        guards.refuse_xql_write_stage(q)
    assert ei.value.code == codes.XQL_WRITE_STAGE_REFUSED and ei.value.http_status == 400

def test_plain_queries_pass():
    guards.refuse_xql_write_stage("dataset = xdr_data | filter a = 1 | limit 2000")

def test_preflight_uses_the_shared_rule():
    src = pathlib.Path("core/api/connectors.py").read_text()
    assert "xql_capability_granted(" in src and "not isinstance(caps, list)" not in src

def test_verifier_aliases_the_shared_budget_and_key():
    from engine import verifier
    assert verifier._tenant_budget is guards.tenant_budget
    assert verifier._tenant_name is guards.ledger_key_for

@pytest.mark.parametrize("name", ["tenant_budget", "ledger_key_for", "xql_capability_granted",
                                  "refuse_xql_write_stage", "require_xql_capability"])
def test_exactly_one_definition_under_core(name):
    hits = [p for p in pathlib.Path("core").rglob("*.py")
            if any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
                   for n in ast.walk(ast.parse(p.read_text())))]
    assert hits == [pathlib.Path("core/integrations/xsiam/guards.py")]

def test_client_carries_capabilities_and_base_url():
    from integrations.xsiam.client import XsiamClient
    from integrations.xsiam.config import XsiamTenantConfig
    c = XsiamClient(XsiamTenantConfig(base_url="https://api-t.xdr.us.paloaltonetworks.com",
                                      region="us", api_key_id="1"), "k", capabilities=["xql"])
    assert c.capabilities == ["xql"] and c.base_url.startswith("https://api-t")
```

- [ ] **Step 2: Run to verify they fail**

Run: `PYTHONPATH=core .venv/bin/python -m pytest tests/integration/xsiam/test_guards.py -v`
Expected: FAIL — `guards` has no module; `test_preflight_uses_the_shared_rule` red on the old expression.

- [ ] **Step 3: Implement `guards.py`, the two exceptions and codes, the client/loader fields, re-point preflight and verifier**

The `REMEDIATION` lines: `XSIAM_CAPABILITY_MISSING` → "add \"xql\" to this credential's config.capabilities via PUT /api/credentials/integrations; configuring reconcile does not authorise XQL"; `XQL_WRITE_STAGE_REFUSED` → "remove the `target type = dataset|lookup` stage; XQL through CortexSim is read-only".

- [ ] **Step 4: Run the guard tests, then the preflight, verifier and connector suites**

Run: `PYTHONPATH=core .venv/bin/python -m pytest tests/integration/xsiam/test_guards.py tests/connectors/test_preflight.py tests/engine -q -k "verif or preflight or budget"`
Expected: PASS. If a preflight test pinned "absent capabilities ⇒ XQL authorised", it is the bug this task fixes: update it to assert `PF_XQL_NOT_AUTHORISED` and say so in the commit body.

- [ ] **Step 5: Commit**

```bash
git add core/integrations/xsiam/guards.py core/integrations/xsiam/codes.py core/integrations/xsiam/exceptions.py core/integrations/xsiam/client.py core/integrations/xsiam/loader.py core/api/connectors.py core/engine/verifier.py tests/integration/xsiam/test_guards.py
git commit -m "feat(xsiam): one guard module for XQL capability, write stage and budget"
```

---

### Task 3: The executor and XQL routes report, charge and refuse

**Files:**
- Modify: `core/api/xsiam.py:79-110` (`tenant_metrics`, `start_xql`, `get_xql`) and `:203-228` (`execute_operation`)
- Modify: `tests/integration/xsiam/test_operations_api.py::_StubClient` and `tests/integration/xsiam/test_api_endpoints.py::_StubClient` — add class attribute `capabilities = ["xql"]` and `_base = "https://api-stub.xdr.us.paloaltonetworks.com"` (a stub now models a credential's grant)
- Test: `tests/integration/xsiam/test_operations_budget_and_gates.py`

**Interfaces:**
- Consumes: Task 2 (`guards.*`, `XsiamClient.capabilities`).
- Produces (REST): `execute_operation` live reply gains `queries_issued: int` (1 for a live read, **0** for `OP-XQL-GET-QUERY-RESULTS`, 0 for a preview) and `ledger: dict | None` (`ledger.snapshot(ledger_key_for(client), limit=tuning.xsiam_max_queries_per_day())`, `None` on preview). `start_xql` reply gains `queries_issued: 1`, `ledger`; `get_xql` gains `queries_issued: 0`, `ledger`, and a `limit: int = Query(100, ge=1, le=1000)` passed to `client.get_query_results(query_id, limit=limit)`. Gate order on every XQL-class path: capability → write-stage (start only) → `tenant_budget` around the tenant call. `tenant_metrics` is capability-gated too (it runs XQL).

- [ ] **Step 1: Write the failing tests** (reuse `_build_app` from `test_operations_api.py`; monkeypatch `integrations.xsiam.ledger.ledger` with a fresh `TenantQuotaLedger()` per test)

```python
def test_live_read_reports_one_query_and_a_ledger_snapshot(tmp_path, monkeypatch): ...
    # POST OP-XQL-GET-DATASETS → body["queries_issued"] == 1; body["ledger"]["queries_last_24h"] == 0  (datasets op is not XQL-class: not charged)
def test_xql_start_is_charged_and_results_poll_is_free(tmp_path, monkeypatch): ...
    # start → queries_issued 1, ledger.queries_last_24h 1; GET-QUERY-RESULTS → queries_issued 0, ledger unchanged
def test_preview_reports_zero_and_no_ledger(tmp_path, monkeypatch): ...
    # OP-ALERTS-INSERT-PARSED-ALERTS without flags → dry_run True, queries_issued 0, ledger None
def test_capability_missing_is_403_with_zero_stub_calls(tmp_path, monkeypatch): ...
    # stub.capabilities = ["read_alerts"]; OP-XQL-START-XQL-QUERY, POST /xql, GET /metrics → 403 XSIAM_CAPABILITY_MISSING; stub.calls == []
def test_write_stage_is_refused_on_both_xql_paths(tmp_path, monkeypatch): ...
    # "dataset=x | target type = lookup y" via /xql and via OP-XQL-START-XQL-QUERY body → 400 XQL_WRITE_STAGE_REFUSED; stub.calls == []
def test_exhausted_budget_is_429_before_the_tenant_is_called(tmp_path, monkeypatch): ...
    # ledger pre-charged to the daily limit → start → 429 XSIAM_QUOTA_ERROR; stub.calls == []
def test_get_xql_limit_is_capped_at_1000(tmp_path, monkeypatch): ...
    # GET /xql/q1?limit=5000 → 422; ?limit=1000 → 200 and stub saw limit=1000
```

- [ ] **Step 2: Run to verify they fail**

Run: `PYTHONPATH=core .venv/bin/python -m pytest tests/integration/xsiam/test_operations_budget_and_gates.py -v`
Expected: FAIL — `queries_issued` missing; 200 where 403/400/429 expected; **record the 200s** for the commit body (Gate A).

- [ ] **Step 3: Implement in `core/api/xsiam.py`**

`execute_operation`: after `_effective_dry_run`, if `op.op_id in XQL_OPERATION_IDS` → `require_xql_capability(getattr(client, "capabilities", None), tenant=name)`; for `OP-XQL-START-XQL-QUERY` also `refuse_xql_write_stage(<query text from body>)` (read the pack to see where the query sits in `body["request_data"]`) and wrap the live call in `async with tenant_budget(client)`. Map `QuotaBreakerOpen`/`DailyBudgetExhausted` to `XsiamQuotaError` where `tenant_budget` does not already (check `ledger.charge` raises them). Same three on `start_xql`; capability + `limit` on `get_xql`; capability on `tenant_metrics`.

- [ ] **Step 4: Run the new file plus every existing xsiam suite**

Run: `PYTHONPATH=core .venv/bin/python -m pytest tests/integration/xsiam tests/connectors/test_xql_envelope.py -q`
Expected: PASS (the two stubs now declare `capabilities`).

- [ ] **Step 5: Commit**

```bash
git add core/api/xsiam.py tests/integration/xsiam/test_operations_api.py tests/integration/xsiam/test_api_endpoints.py tests/integration/xsiam/test_operations_budget_and_gates.py
git commit -m "feat(api): executor and XQL routes report queries_issued, charge the ledger, gate on capabilities"
```
Body names the behaviour change (a credential without `"xql"` now gets 403 on the console's XQL panel and `/metrics` — D-18, Henry may veto) and the observed-red 200s.

---

### Task 4: Reconcile reports `queries_issued`

**Files:**
- Modify: `core/connectors/service.py:155-215` (`reconcile_run`, summary assembly)
- Test: `tests/connectors/test_reconcile_queries_issued.py`

**Interfaces:**
- Produces: `ReconcileOutcome.summary["queries_issued"] = int((pull.detail or {}).get("pages") or 1) + (1 if an incidents pull was attempted else 0)` — `connectors/xsiam.py:221` already writes `detail["pages"]`.

- [ ] **Step 1: Write the failing tests** (follow the fake-connector pattern already used in `tests/connectors/` for `reconcile_run`)

```python
def test_single_page_pull_is_one_query(...):        # summary["queries_issued"] == 1
def test_three_pages_truncated_is_three(...):        # detail pages=3 → 3
def test_correlation_scenario_incident_read_adds_one(...):  # pull_incidents attempted → pages + 1
```

- [ ] **Step 2: Run to verify they fail** — `KeyError: 'queries_issued'`.
- [ ] **Step 3: Implement** the one line after `summary["pulled"] = …`, incrementing inside the incidents branch.
- [ ] **Step 4: Run** `PYTHONPATH=core .venv/bin/python -m pytest tests/connectors -q` — PASS.
- [ ] **Step 5: Commit**

```bash
git add core/connectors/service.py tests/connectors/test_reconcile_queries_issued.py
git commit -m "feat(connectors): reconcile summary reports queries_issued in ledger units"
```

---

### Task 5: `GET /api/runs` filters (optional in spec; done here)

**Files:**
- Modify: `core/api/runs.py:773-780` (`list_runs`)
- Test: `tests/api/test_runs_list_filters.py`

**Interfaces:**
- Produces: `list_runs(status: Optional[str] = Query(None), scenario_id: Optional[str] = Query(None), limit: Optional[int] = Query(None, ge=1, le=500), db=…)`. `total` stays the **unfiltered-by-limit** count of matching rows; `runs` is cut to `limit`. No params → byte-identical to today.

- [ ] **Step 1: Write the failing tests** (use the `memory_db` / `make_client` fixtures the runs tests already use; seed 3 runs, two of one scenario, one `completed`)

```python
def test_no_params_is_unchanged(...)            # 3 runs, total 3
def test_status_filter(...)                      # status=completed → 1
def test_scenario_filter_and_limit(...)          # scenario_id=X&limit=1 → len 1, total 2
def test_limit_above_500_is_422(...)
```

- [ ] **Step 2: Run to verify they fail** — filters ignored (3 returned).
- [ ] **Step 3: Implement** with `stmt.where(...)` per present param; compute `total` before `.limit()`.
- [ ] **Step 4: Run** `PYTHONPATH=core .venv/bin/python -m pytest tests/api -q -k runs` — PASS.
- [ ] **Step 5: Commit** `feat(api): GET /api/runs accepts status, scenario_id and limit`.

---

### Task 6: Provenance in Python and `GET /api/readiness`

**Files:**
- Create: `core/connectors/provenance.py`
- Create: `core/api/readiness.py` (router, prefix `/readiness`)
- Modify: `core/main.py:416-493` (`app.include_router(readiness_router, prefix="/api")` beside `uctc_router`)
- Create: `tests/fixtures/provenance_parity.json`
- Test: `tests/connectors/test_provenance.py`, `tests/api/test_readiness.py`
- Test (UI): `ui/src/components/console/readiness/__tests__/connectorState.provenanceParity.test.js`

**Interfaces:**
- Produces (`provenance.py`):
  - `verdict_provenance(row: Mapping[str, Any]) -> str` — `'none' | 'local' | 'tenant'`, a line-for-line port of `connectorState.js::verdictProvenance` (`:97-120`) over a `to_dict()` row: terminal `tc_verdict` in `{pass, fail}` only; `tc_verdict_detail.verification.queries_issued > 0` or `.tenant`/`.integration` → tenant; `detail.queries_issued > 0` or `detail.verified_against_tenant is True` → tenant; `row.tenant` set and `row.dry_run is not True` → tenant; else local.
  - `tenant_verified_names(rows: Iterable[Mapping]) -> list[str]` — port of `tenantVerifiedNames`, sorted.
  - `async tenant_verified(session) -> TenantVerified(count: int, names: list[str])` over all `Run` + `AssertionRun` rows' `to_dict()`.
  - `async count_tenant_verified(session) -> int` = `.count` (the name the spec uses).
- Produces (route) `GET /api/readiness` →
  ```
  {server_time: iso+00:00, tenant_verified: int, tenant_verified_names: [str],
   integrations: [{name, kind, capabilities: list|null, xql_capability_granted: bool,
                   last_verified_ok, last_verified_at, last_verified_error,
                   ledger: snapshot|null,      # xsiam_tenant rows only, key tenant_ledger_key(config.base_url)
                   rungs: {authored: bool, configured: true, reachable: bool, verified: bool}}],
   proves: {authored: {proves, does_not_prove}, configured: {...}, reachable: {...}, verified: {...}},
   outbound_calls: 0, statement: "No tenant call is made by this route. tenant-verified: N"}
  ```
  `authored` = scenario count > 0; `reachable` = `last_verified_ok is True`; `verified` = name in `tenant_verified_names`; `proves` text copied verbatim from `connectorState.js:275-300`; every timestamp through `connectors.base.coerce_utc` then `.isoformat()` with `+00:00`. **Never `config` values** beyond `capabilities`.
- Consumed by Tasks 10, 11 (`cortexsim.readiness.get`, `xsiam.session.index`, tenant resolution).

- [ ] **Step 1: Write the fixture** — `{"runs": [...], "assertion_runs": [...], "expected_names": [...], "expected_tenant_count": N}` with at least: a `pass` run with `verification.queries_issued: 3` (tenant); a `pass` run with `queries_issued: 0` (local); a `pending` run with a tenant (none); an assertion run `fail` + `tenant: "lab"` + `dry_run: false` (tenant); one `dry_run: true` (local).

- [ ] **Step 2: Write the failing tests**

```python
# tests/connectors/test_provenance.py
def test_parity_with_console_fixture():        # names == expected_names; count == expected_tenant_count
def test_pending_and_not_applicable_never_count()
def test_exactly_one_definition_of_each():     # ast walk: verdict_provenance, count_tenant_verified once under core/
# tests/api/test_readiness.py  (build app with readiness router + memory db; monkeypatch load_xsiam_client to raise AssertionError("outbound"))
def test_readiness_makes_no_outbound_call_and_carries_server_time_offset()
def test_ledger_matches_snapshot_for_same_key()   # seed an xsiam_tenant row; ledger.charge once; body ledger == ledger.snapshot(key, limit=…)
def test_no_credential_values_leak()               # "api_key_id" and "base_url" not in json.dumps(body)
def test_tenant_verified_is_zero_on_an_empty_db()
```

```js
// ui/.../__tests__/connectorState.provenanceParity.test.js
// readFileSync(resolve(__dirname, '../../../../../../tests/fixtures/provenance_parity.json'))
// expect(tenantVerifiedNames({runs, assertionRuns}).sort()).toEqual(expected_names)
// expect([...runs, ...assertion_runs].filter(r => verdictProvenance(r) === 'tenant').length).toBe(expected_tenant_count)
```

- [ ] **Step 3: Run both sides to verify they fail** — Python: module missing; vitest: `cd ui && npx vitest run src/components/console/readiness` — the new file fails only if the fixture's expectations are wrong (it is the oracle; fix the fixture, not the JS).
- [ ] **Step 4: Implement `provenance.py` and `readiness.py`; mount the router.**
- [ ] **Step 5: Run** the two Python files, `tests/api/test_health.py` (route count unaffected), and the vitest file — PASS.
- [ ] **Step 6: Commit**

```bash
git add core/connectors/provenance.py core/api/readiness.py core/main.py tests/fixtures/provenance_parity.json tests/connectors/test_provenance.py tests/api/test_readiness.py ui/src/components/console/readiness/__tests__/connectorState.provenanceParity.test.js
git commit -m "feat(api): GET /api/readiness with tenant_verified counted in Python"
```

---

### Task 7: Health `mcp` component

**Files:**
- Modify: `core/api/health.py:706-722` (`COMPONENT_KEYS` gains `"mcp"` after `"eal_collectors"`), add `probe_mcp()` and wire it in `component_health()` via `_guard("mcp", probe_mcp)`
- Test: `tests/api/test_health.py` (append three tests)

**Interfaces:**
- Produces: `probe_mcp() -> dict`: flag off → `{"status": "ok", "code": "MCP_DISABLED", "mounted": False, "path": "/mcp", "transport": "streamable-http", "tools": 0, "sdk": "mcp==<version>", "detail": "opt-in; set CORTEXSIM_MCP_HTTP=1 to mount"}`; flag on → import `mcp_server.naming.CANONICAL_IDS` (Task 8): `ImportError` → `_degraded("MCP_SDK_IMPORT_FAILED", …)`, `len == 0` → `_degraded("MCP_NO_TOOLS", …)`, else `_ok(mounted=True, tools=len, …)`. `sdk` via `importlib.metadata.version("mcp")`, `"unavailable"` on failure. Zero outbound calls.

- [ ] **Step 1: Write the failing tests**

```python
def test_mcp_off_is_ok_with_disabled_code(monkeypatch)       # CORTEXSIM_MCP_HTTP False → ok, mounted False, code MCP_DISABLED
def test_mcp_on_but_sdk_missing_is_degraded(monkeypatch)     # monkeypatch builtins.__import__ to raise for "mcp_server" → degraded, code MCP_SDK_IMPORT_FAILED, detail has "Fix:"
def test_mcp_on_reports_tool_count(monkeypatch)              # → ok, mounted True, tools == len(CANONICAL_IDS)
```
The existing component-set guard at `tests/api/test_health.py:77` and the vocabulary test keep passing unchanged (spec test 10).

- [ ] **Step 2: Run** `PYTHONPATH=core .venv/bin/python -m pytest tests/api/test_health.py -v -k mcp` — FAIL (`probe_mcp` missing); the component-set test also FAILS once `"mcp"` is in `COMPONENT_KEYS` without a probe — that is the structural guard working.
- [ ] **Step 3: Implement.** (Until Task 8 lands, `test_mcp_on_reports_tool_count` stays red: that is expected; note it and move on, or land Task 8 first — the plan orders it next.)
- [ ] **Step 4: Run** `tests/api/test_health.py` fully — PASS after Task 8.
- [ ] **Step 5: Commit** `feat(api): /api/health reports the mcp component (off reads ok, on-but-empty degraded)`.

---

### Task 8: `core/mcp_server/` foundation — naming, errors, envelope, rate limit, client

**Files:**
- Create: `core/mcp_server/__init__.py`, `naming.py`, `errors.py`, `envelope.py`, `ratelimit.py`, `client.py`
- Test: `tests/mcp_server/__init__.py`, `tests/mcp_server/test_naming.py`, `test_envelope.py`, `test_client.py`, `test_ratelimit.py`

**Interfaces:**
- `naming.py`: `CANONICAL_IDS: tuple[str, ...]` — the 41 ids of Appendix A, in that order; `WIRE_PATTERN = re.compile(r"^[a-z0-9_]{1,48}$")`; `wire_name(canonical: str) -> str` (`.`→`_`, `ValueError` if the result fails the pattern); `canonical_id(wire: str) -> str` (reverse lookup over `CANONICAL_IDS`; `ValueError` if unknown); `GROUP = {"cortexsim", "xsiam"}` and `group_of(canonical) -> str`.
- `errors.py`: constants `MCP_WRITE_NOT_EXECUTABLE, TOOL_NOT_BACKED, NO_TENANT_CONFIGURED, TENANT_AMBIGUOUS, MCP_RATE_LIMITED, SIMCORE_UNREACHABLE, SIMCORE_SHAPE_UNKNOWN`; `REMEDIATION: dict[str, str]` (one line each, §8 / Appendix B wording); `class McpToolError(Exception)`: `code: str, error: str, detail: str, remediation: str | None = None, basis: str = "authored", tenant: str | None = None, data: Any = None` (`data` carries the preview for `MCP_WRITE_NOT_EXECUTABLE`), `.as_structured(evidence: Evidence) -> dict` = `{error, code, detail, remediation, evidence, **({"data": data} if data is not None else {})}`.
- `envelope.py`: `Basis = Literal["authored","configured","reachable","verified","preview","simcore","derived"]`; `class Ledger(BaseModel)`: `key, used_today: int, budget: int, remaining: int, breaker_open: bool, breaker_until: str | None`; `ledger_from_snapshot(snap: dict | None) -> Ledger | None` (maps `tenant→key, queries_last_24h→used_today, limit→budget, breaker_open_until→breaker_until` via `coerce_utc`-equivalent ISO with offset); `class Evidence(BaseModel)`: the nine fields of §7 with defaults `queries_issued=0, http_round_trips=None, tenant=None, ledger=None, tenant_verified=0, as_of=None, truncated=False`; `class Envelope(BaseModel)`: `data: Any = None`, `evidence: Evidence` (data optional so an error's structured content validates against the advertised output schema; pydantic ignores extra keys); `statement(*, basis, queries_issued, tenant, tenant_verified, tool_id=None, not_backed=False, simcore_unreachable=False) -> str` returning exactly the §7 table sentences (`Tenant <name> answered <n> operation(s). …`; preview; not backed; `No tenant answered this call. SimCore itself was unreachable. tenant-verified: N`); `as_of_from_date_header(value: str | None, *, now=None) -> str` (`email.utils.parsedate_to_datetime` → UTC → `.isoformat()`; fallback `datetime.now(timezone.utc)`; always ends `+00:00`).
- `ratelimit.py`: `class TokenBucket(per_minute: int, *, clock=time.monotonic)`; `.acquire() -> float` returns `0.0` when a token was taken, else `retry_after_seconds` (ceil, ≥ 1).
- `client.py`: `class SimCoreError(Exception)`: `status: int, code: str, error: str, detail: str`; subclasses `SimCoreUnreachable` (code `SIMCORE_UNREACHABLE`), `SimCoreShapeUnknown` (code `SIMCORE_SHAPE_UNKNOWN`); `encode_segment(value: str) -> str` (`urllib.parse.quote(value, safe="")`, `ValueError` on empty or `"."`/`".."`); `class SimCoreClient(base_url: str, *, transport: httpx.AsyncBaseTransport | None = None, timeout: float = 30.0)`: `async get(path: str, params: dict | None = None) -> dict`, `async post(path: str, json: Any = None, params: dict | None = None) -> dict`, `last_date: str | None` (the `Date` header of the last response), `base_url: str`. Non-2xx with a JSON `{error, code, detail}` body → `SimCoreError(status, code, …)`; non-2xx without → `SimCoreError(status, "SIMCORE_HTTP_<status>", …)`; `httpx.ConnectError | ConnectTimeout` → `SimCoreUnreachable`; body not JSON or not a dict → `SimCoreShapeUnknown` (detail names the content-type and first 80 bytes).

- [ ] **Step 1: Write the failing tests**

```python
# test_naming.py
def test_every_canonical_id_round_trips_and_fits():   # for id in CANONICAL_IDS: wire_name → canonical_id → id; WIRE_PATTERN.match; len ≤ 48
def test_there_are_41_ids_25_cortexsim_16_xsiam()
def test_longest_wire_name_is_29_chars()
def test_no_wire_name_carries_a_dot_and_unknown_wire_is_rejected():   # all("." not in wire_name(i)); canonical_id("no_such_tool") raises ValueError
# test_envelope.py
def test_statement_sentences_match_the_spec_table()   # four conditions, exact strings, each ends "tenant-verified: 0"
def test_ledger_from_snapshot_maps_fields_and_none_stays_none()
def test_as_of_has_offset_with_and_without_date_header(monkeypatch)   # monkeypatch TZ=America/Los_Angeles + time.tzset(); both end with "+00:00"  (Review Focus 5)
def test_error_structured_content_validates_as_envelope()            # Envelope.model_validate({error, code, detail, remediation, evidence})
# test_client.py  (httpx.MockTransport)
def test_2xx_json_dict_is_returned_and_date_header_kept()
def test_rest_error_body_maps_to_simcore_error_with_code()           # 409 {"error","code":"BUNDLE_TARGET_UNSATISFIABLE","detail"} → .code
def test_html_200_is_shape_unknown_not_empty()                       # Review Focus 3
def test_connect_error_is_simcore_unreachable()
def test_tenant_name_with_separators_is_one_encoded_segment()        # encode_segment("a/b") == "a%2Fb"; encode_segment("..") raises  (Review Focus 2)
# test_ratelimit.py
def test_bucket_allows_n_per_minute_then_reports_retry_after()       # fake clock
```

- [ ] **Step 2: Run** `PYTHONPATH=core .venv/bin/python -m pytest tests/mcp_server -v` — FAIL (package missing).
- [ ] **Step 3: Implement the five modules.** `CANONICAL_IDS` is typed out once, from Appendix A.
- [ ] **Step 4: Run** the same command — PASS. Also re-run `tests/api/test_health.py` (Task 7's third test goes green).
- [ ] **Step 5: Commit**

```bash
git add core/mcp_server/__init__.py core/mcp_server/naming.py core/mcp_server/errors.py core/mcp_server/envelope.py core/mcp_server/ratelimit.py core/mcp_server/client.py tests/mcp_server/
git commit -m "feat(mcp): naming, envelope, error contract and the SimCore client"
```

---

### Task 9: Tool input schemas as drift-tested copies

**Files:**
- Create: `core/mcp_server/schemas.py`
- Test: `tests/mcp_server/test_schema_drift.py`

**Interfaces:**
- Produces (pydantic models, all `extra="forbid"`):
  - `EmptyInput`
  - `ScopeRequestIn` — same four fields/defaults as `api.pov.ScopeRequest`.
  - `OperationExecuteIn(op_id: str, tenant: str | None = None, path_params: dict[str, Any] = {}, query: dict[str, Any] | None = None, body: Any = None, dry_run: bool | None = None)` — `api.xsiam.OperationExecuteRequest` minus `consent`, plus `op_id`/`tenant`; `MCP_EXECUTE_DROPPED = {"consent": "agent-settable consent is cortex-bot's F2 (A2); writes always preview"}`.
  - `XqlQueryIn(query: str, timeframe: dict[str, Any] = {"relativeTime": 86400000}, limit: int = Field(100, ge=1, le=1000), tenant: str | None = None, max_wait_seconds: float = Field(30.0, ge=1, le=120), poll_interval_seconds: float = Field(1.5, ge=0.5, le=10))`; `XQL_TIMEFRAME_MAX_MS = 7 * 86400000` validated on `timeframe["relativeTime"]` when present.
  - `McpLaunchRequest(scenario_id: str, mode: str = "pull", target_agent_id: str | None = None, identity: str | None = None, paired_with_run_id: str | None = None, canary_run_id: str | None = None, simulation_authorized: bool = False)`; `.to_launch_body() -> dict` → `{scenario_id, mode, target_agent_id, identity, paired_with_run_id, canary_run_id, consent: {"simulation_authorized": bool}}` (None fields omitted).
  - `MCP_LAUNCH_PROJECTED: dict[str, str]` (REST field → MCP field; `"consent"` → `"simulation_authorized (consent.simulation_authorized only)"`), `MCP_LAUNCH_EXCLUDED: dict[str, str]` (field → reason) for `allow_runtime_install, agent_profile_mode, bas_mode_attested, soakless_attested, attested_by`; `MCP_LAUNCH_CONSENT_EXCLUDED = {"c2_authorized", "cluster_privilege_authorized", "node_access_authorized"}`.
  - `ListIn(limit: int = Field(50, ge=1, le=200), offset: int = Field(0, ge=0))` plus per-tool filter models (`ScenarioListIn(plane, uc_ref, ttp_ref, entitlement, status)`, `RunListIn(status, scenario_id)`, `AssertionListIn(validation_class, tc_ref)`, `OperationListIn(category, access_class)`), each extending `ListIn`.
  - `TenantIn(tenant: str | None = None)`; `IdIn(id: str)`; `RunIdIn(run_id: str)`; `ReconcileIn(run_id, tenant=None, window_seconds: int = Field(3600, ge=60, le=86400))`; `VerifyIn(run_id, tenant=None, force: bool = False, timeframe_seconds: int | None = Field(None, ge=60, le=86400))`; `PreflightIn(kind: Literal["xsiam","xsiam_tenant"], tenant=None, datasets: str | None = None)`; `AssertionRunIn(id, dry_run: bool = True, tenant=None, context: dict[str,str] = {})`; `DatasetSchemaIn(dataset: str = Field(pattern=r"^[A-Za-z0-9_]{1,128}$"), tenant=None)`; `ReportIn(run_id, format: Literal["markdown"] = "markdown")`.

- [ ] **Step 1: Write the failing drift tests** (these import `api.pov`, `api.xsiam`, `api.runs` — allowed in *tests*, forbidden in `mcp_server`)

```python
def test_scope_request_fields_match():        # set(ScopeRequestIn.model_fields) == set(api.pov.ScopeRequest.model_fields)
def test_execute_request_fields_match_minus_dropped():
def test_xql_request_fields_are_a_superset_of_rest():   # {"query","timeframe"} ⊆ XqlQueryIn fields
def test_launch_projection_is_total():        # set(LaunchRequest.model_fields) == set(MCP_LAUNCH_PROJECTED) | set(MCP_LAUNCH_EXCLUDED)
def test_launch_consent_keys_are_all_decided():   # {"simulation_authorized"} | MCP_LAUNCH_CONSENT_EXCLUDED == the four names; each appears in core/api/runs.py source
def test_mcp_server_never_imports_core_api():     # ast walk core/mcp_server/**: no Import/ImportFrom whose module starts with "api" or "core.api"
def test_launch_body_omits_excluded_and_none():   # to_launch_body() keys ⊆ projected; consent has exactly simulation_authorized
```

- [ ] **Step 2: Run** — FAIL (module missing).
- [ ] **Step 3: Implement `schemas.py`.**
- [ ] **Step 4: Run** `tests/mcp_server -q` — PASS.
- [ ] **Step 5: Commit** `feat(mcp): tool input schemas as drift-tested copies of the REST models`.

---

### Task 10: Registry wrapper and the `cortexsim.*` group (25 tools)

**Files:**
- Create: `core/mcp_server/registry.py`, `core/mcp_server/tools/__init__.py`, `core/mcp_server/tools/cortexsim.py`
- Test: `tests/mcp_server/conftest.py` (the `fake_simcore` harness), `tests/mcp_server/test_registry.py`, `tests/mcp_server/test_tools_cortexsim.py`

**Interfaces:**
- `registry.py`:
  - `@dataclass class ToolContext`: `client: SimCoreClient`, `bucket: TokenBucket`, `tenant_verified_cache_seconds: float = 5.0`.
  - `@dataclass(frozen=True) class ToolSpec`: `id: str` (canonical), `input_model: type[BaseModel]`, `impl: Callable[[ToolContext, BaseModel], Awaitable[Envelope]]`, `klass: Literal["read","write","destructive"]`, `network: Literal["none","simcore","tenant","host"]`, `scopes: tuple[str, ...]`, `description: str`, `idempotent: bool = True`.
  - `annotations_for(spec) -> mcp.types.ToolAnnotations` — `readOnlyHint = klass == "read"`, `destructiveHint = klass == "destructive"`, `idempotentHint = spec.idempotent`, `openWorldHint = network in {"tenant", "host"}`.
  - `flat_signature(input_model) -> inspect.Signature` — one keyword-only parameter per model field with its default and annotation.
  - `wrap(spec, ctx) -> Callable[..., Awaitable[CallToolResult]]` — the one place results and errors are shaped: builds `spec.input_model(**kwargs)`, awaits `impl`, returns `CallToolResult(content=[TextContent(text=f"{env.evidence.statement}\n{json}")], structuredContent=env.model_dump(mode="json"))`; catches `McpToolError` → `CallToolResult(isError=True, content=[TextContent(text=f"{code}: {error} — {detail}")], structuredContent=err.as_structured(evidence))`; catches `SimCoreError` → the same with the REST code passed through (basis `simcore`); catches anything else → code `MCP_INTERNAL_ERROR` with `type(exc).__name__` (never a bare exception). Sets `__signature__`, `__annotations__` (fields + `"return": Envelope`), `__name__`, `__doc__` so FastMCP advertises the flat input schema and the `Envelope` output schema while the function returns a `CallToolResult` (SDK 1.30.0 `func_metadata.convert_result` passes a `CallToolResult` through after `Envelope.model_validate(structuredContent)`).
  - `register(mcp: FastMCP, specs: Iterable[ToolSpec], ctx) -> list[ToolSpec]` — `mcp.tool(name=wire_name(spec.id), title=spec.id, description=…, annotations=annotations_for(spec))(wrap(spec, ctx))`; asserts the set of ids registered equals `set(CANONICAL_IDS)` filtered to the group(s) passed.
  - `evidence_for_simcore(ctx, *, basis="authored", truncated=False) -> Evidence` and `evidence_for_tenant(ctx, *, tenant, queries_issued, ledger_snapshot, http_round_trips=None) -> Evidence` — both read `tenant_verified` from `GET /api/readiness` (cached `tenant_verified_cache_seconds`) and `as_of` from `ctx.client.last_date`.
  - `cap_list(items: list, *, limit: int, offset: int) -> tuple[list, bool]` and `cap_text(text: str, *, max_bytes=262144) -> tuple[str, bool, int]`.
- `tools/cortexsim.py`: `def specs() -> list[ToolSpec]` — the 25 rows of §5.2 with the REST calls of Appendix A. Rules that are not obvious from the table: `uctc.*` raise `McpToolError("UCTC_INDEX_NOT_LOADED", …, basis="authored")` when the response has `index_loaded: false` (never "0 test cases"); `run.launch` posts `McpLaunchRequest.to_launch_body()` and, for `mode == "push"`, returns `{run_id, bundle_id, download_url}` only; `run.report` goes through `cap_text`; `run.list` passes `status/scenario_id/limit` to the route (Task 5); `run.reconcile` and `run.verify` are tenant-class (bucket + `queries_issued` read off the response); `connector.preflight` carries `http_round_trips = body["queries_issued"]` and `queries_issued = 0` unless the datasets stage ran (then the stage's count) — state the mapping in the tool description; `assertion.run` passes `dry_run` through (default `True`); `readiness.get` is a pass-through with basis `configured`.

- [ ] **Step 1: Write the `fake_simcore` harness in `tests/mcp_server/conftest.py`**

`fake_simcore(routes: dict[tuple[str, str], Callable | dict])` → `(SimCoreClient over httpx.MockTransport, calls: list[httpx.Request])`; unknown route → 404 `{"error","code":"NOT_FOUND","detail"}`; every response carries a `Date` header; a `readiness_body(tenant_verified=0, integrations=[])` helper. Also `mcp_with(specs, ctx) -> FastMCP` and `call(mcp, canonical, **args) -> CallToolResult` (`asyncio.run(mcp.call_tool(wire_name(canonical), args))`).

- [ ] **Step 2: Write the failing tests**

```python
# test_registry.py
def test_registered_wire_names_equal_the_canonical_set_for_the_group()
def test_input_schema_is_flat_and_output_schema_is_envelope()       # mcp.list_tools(): inputSchema.properties keys == model fields; outputSchema.required ⊇ ["evidence"]
def test_text_content_starts_with_the_statement_line()
def test_simcore_error_code_passes_through_as_is_error()             # 409 BUNDLE_TARGET_UNSATISFIABLE
def test_unexpected_exception_never_escapes()                        # impl raises RuntimeError → isError, code MCP_INTERNAL_ERROR
# test_tools_cortexsim.py
def test_health_get_keeps_not_checked_verbatim()
def test_scenario_list_caps_and_flags_truncation()                   # 60 rows, default limit → 50, truncated True, total 60
def test_uctc_index_not_loaded_is_degraded_never_zero()
def test_run_launch_posts_the_projection_only()                      # body has no allow_runtime_install / attestation keys; consent == {"simulation_authorized": True}
def test_run_launch_rejects_excluded_fields_before_any_call()        # extra kwarg allow_runtime_install → isError, calls == []   (spec test 13)
def test_run_report_is_capped_at_256_kib()
def test_simcore_only_tools_have_null_ledger_and_round_trips()       # spec test 4 (half)
def test_reconcile_reads_queries_issued_off_the_route()
def test_preflight_maps_queries_issued_to_http_round_trips()
def test_every_cortexsim_tool_returns_an_envelope()                   # iterate specs with minimal valid args against the fake
```

- [ ] **Step 3: Run** — FAIL (modules missing).
- [ ] **Step 4: Implement `registry.py` and `tools/cortexsim.py`.**
- [ ] **Step 5: Run** `tests/mcp_server -q` — PASS.
- [ ] **Step 6: Commit**

```bash
git add core/mcp_server/registry.py core/mcp_server/tools/__init__.py core/mcp_server/tools/cortexsim.py tests/mcp_server/conftest.py tests/mcp_server/test_registry.py tests/mcp_server/test_tools_cortexsim.py
git commit -m "feat(mcp): registry wrapper and the 25 cortexsim.* tools"
```

---

### Task 11: The `xsiam.*` projection group (16 tools) and the honesty tests

**Files:**
- Create: `core/mcp_server/tools/xsiam.py`
- Modify: `tests/mcp_server/conftest.py` (add the `stubbed_app` harness)
- Test: `tests/mcp_server/test_tools_xsiam.py`, `tests/mcp_server/test_write_gate.py`

**Interfaces:**
- Consumes: Task 3's REST shapes; `guards.refuse_xql_write_stage` is **not** imported (D-1) — the REST route refuses and the tool passes the code through; the tool only pre-checks `dataset` names (schema regex, Task 9).
- `stubbed_app` harness: `tests.integration.xsiam.test_operations_api._build_app(tmp_path, monkeypatch)` → `TestClient.app`; add the readiness router (Task 6) and a memory-DB credentials seed helper `seed_tenant(name, kind="xsiam_tenant", capabilities=["xql"])`; `SimCoreClient("http://simcore", transport=httpx.ASGITransport(app=app))`. The `_StubClient.calls` list is the **tenant** witness.
- `tools/xsiam.py`: `def specs() -> list[ToolSpec]` — 16 rows of §5.3 / Appendix A. Shared helpers in the same module: `resolve_tenant(ctx, tenant: str | None, *, kind: str) -> str` (zero → `NO_TENANT_CONFIGURED`; many without a name → `TENANT_AMBIGUOUS` listing names; a named one must exist with that kind) reading `GET /api/readiness`; `take_token(ctx)` → `MCP_RATE_LIMITED` with `retry_after_seconds`; `execute(ctx, *, tenant, op_id, path_params, query, body, dry_run) -> dict` posting `OperationExecuteRequest`-shaped JSON **always with `consent: {}`**, and for a write/destructive op (looked up via `GET /api/xsiam/operations/{op_id}` → `access_class`) **always `dry_run: true`** — if the caller passed `dry_run is False` on such an op, raise `McpToolError(MCP_WRITE_NOT_EXECUTABLE, …, basis="preview", data=preview)` *after* fetching only the preview (one SimCore call, zero tenant calls); `preview_with_consent(preview, op, tenant) -> dict` adds `consent_required` and `execute_as_human` (the §6 shape, URL from `ctx.client.base_url`). `xsiam.xql.query`: `POST /api/xsiam/tenants/{t}/xql` then `GET …/xql/{query_id}?limit=N` every `poll_interval_seconds` until status is terminal or `max_wait_seconds` elapses → `data.status: "PENDING", query_id` with `reason: XQL_RESULT_PENDING`; `queries_issued` = start's 1 + each poll's 0. `xsiam.dataset.schema`: one `xql.query` of `f"dataset = {dataset} | limit 1"`, `fields = sorted(row.keys())` of the first row, `[]` + `reason: POPULATION_EMPTY` on no rows, basis `derived` with `derivation` text. `xsiam.session.index`: from `/api/readiness` only — `tenant_bound: bool`, `scopes_held: [..]` with `xql.execute` only when `xql_capability_granted`, `degradations: [..]`, `tenant_verified`. The four not-backed tools raise `McpToolError(TOOL_NOT_BACKED, …)` with the Appendix B sentences. `xsiam.healthcheck`: a 403 from the route is reported as `license_gated: true`, not unreachable. Every named tool returns allowlisted fields (enumerate them in each spec's description); only `xsiam.operation.execute` returns `reply` raw, via `cap_text` on its JSON, with `truncated` + `bytes`.

- [ ] **Step 1: Write the failing honesty tests**

```python
# test_write_gate.py   (spec test 1 — Gate A: write first against a pass-through execute and observe red)
def test_write_op_with_every_key_set_still_previews_and_tenant_saw_nothing(stubbed_app, monkeypatch):
    # monkeypatch config.settings.CORTEXSIM_XSIAM_ALLOW_WRITE/DESTRUCTIVE = True
    # call xsiam.operation.execute op_id=OP-ALERTS-INSERT-PARSED-ALERTS dry_run=False body={...}
    # → isError, code MCP_WRITE_NOT_EXECUTABLE, structuredContent["data"]["dry_run"] is True,
    #   ["data"]["consent_required"]["consent"] == {"write_authorized": True}, stub.calls == []
def test_destructive_op_same(stubbed_app, monkeypatch)              # an access_class destructive op from the catalog
def test_consent_kwarg_is_rejected_by_the_schema(stubbed_app)        # extra field → isError before any call
def test_write_preview_without_dry_run_false_is_a_preview_not_an_error(stubbed_app)   # basis preview, queries_issued 0
# test_tools_xsiam.py
def test_read_op_routes_through_executor_and_traversal_guard(stubbed_app)   # spec test 2: path_params {"group_id": "../delete/123"} → the executor's "must be a single path segment" text in detail; a plain id reaches stub at the expected path
def test_queries_issued_sums_ledger_units(stubbed_app)               # spec test 4: xql.query with a stub that needs 3 polls → 1; execute GET-QUERY-RESULTS alone → 0
def test_four_unbacked_tools_return_tool_not_backed_never_empty(stubbed_app)   # spec test 6
def test_tenant_resolution_zero_one_many(stubbed_app)                # spec test 7
def test_xql_write_stage_refused_and_limit_capped_not_rewritten(stubbed_app)   # spec test 8: code XQL_WRITE_STAGE_REFUSED from REST; "| limit 2000" stays in the text sent; results call carried limit=1000
def test_capabilities_gate_on_xql_tool_and_route_alike(stubbed_app)  # spec test 15: ["read_alerts"] → XSIAM_CAPABILITY_MISSING, stub.calls == []; add "xql" → both pass
def test_dataset_schema_refuses_query_syntax_in_the_name(stubbed_app)   # Review Focus 1: "xdr_data | target type = lookup x" → isError (validation), stub.calls == []
def test_dataset_schema_empty_population_says_so(stubbed_app)        # fields [], reason POPULATION_EMPTY
def test_execute_reply_over_256_kib_is_truncated_with_byte_count(stubbed_app)   # Review Focus 4
def test_execute_non_json_reply_is_parse_error_not_empty(stubbed_app)           # Review Focus 4
def test_rate_limit_hits_tenant_tools_only(stubbed_app)              # bucket of 1 → second xsiam.dataset.list → MCP_RATE_LIMITED with retry_after_seconds; cortexsim.health.get still ok
def test_session_index_scopes_come_from_capabilities_not_kind(stubbed_app)
def test_healthcheck_403_is_license_gated_not_unreachable(stubbed_app)
def test_every_xsiam_tool_returns_an_envelope_or_typed_refusal(stubbed_app)
```

- [ ] **Step 2: Run** — FAIL; **record** that the write-gate test passes a real request through to the stub when the gate is absent (the commit body's Gate A line).
- [ ] **Step 3: Implement `tools/xsiam.py`.**
- [ ] **Step 4: Run** `tests/mcp_server -q` and `tests/integration/xsiam -q` — PASS.
- [ ] **Step 5: Commit**

```bash
git add core/mcp_server/tools/xsiam.py tests/mcp_server/conftest.py tests/mcp_server/test_tools_xsiam.py tests/mcp_server/test_write_gate.py
git commit -m "feat(mcp): xsiam.* projection; writes always preview, zero tenant calls proven"
```

---

### Task 12: Server, both transports, manifest, and the image boot gate

**Files:**
- Create: `core/mcp_server/server.py`, `core/mcp_server/__main__.py`, `core/mcp_server/manifest.py`
- Modify: `core/main.py` — lifespan (`:90`) enters `session_manager.run()` when mounted; mount at `/mcp` **before** `app.mount("/", ConsoleStatic…)` (`:542`)
- Modify: `Makefile` (new `check-mcp-boot`, added to `validate` after `check-ui-shelf`), `.github/workflows/ci.yml` (one step in the backend job after "Build simcore image", mirroring the "UI image-parity gate" step at `:136`)
- Test: `tests/mcp_server/test_server.py`, `tests/mcp_server/test_manifest.py`, `tests/mcp_server/test_main_cli.py`

**Interfaces:**
- `server.py`: `build(ctx: ToolContext, *, stateless: bool = True, allowed_hosts: Sequence[str] = ()) -> FastMCP` — `FastMCP("cortexsim", json_response=True, stateless_http=stateless, transport_security=TransportSecuritySettings(allowed_hosts=[...], allowed_origins=[...]))` where the host list is `["localhost", "localhost:*", "127.0.0.1", "127.0.0.1:*", "[::1]", "[::1]:*"]` + each configured host bare and with `:*`; registers `cortexsim.specs() + xsiam.specs()`; `registered_ids(mcp) -> list[str]`. `mount(app: FastAPI, *, settings) -> FastMCP | None` — returns `None` when `settings.CORTEXSIM_MCP_HTTP` is false; else builds with `SimCoreClient(settings.CORTEXSIM_URL, transport=httpx.ASGITransport(app=app))` and `TokenBucket(settings.CORTEXSIM_MCP_MAX_TENANT_CALLS_PER_MINUTE)`, `app.mount("/mcp", mcp.streamable_http_app())`, `app.state.mcp = mcp`. `lifespan_for(app)` — async context manager: `async with app.state.mcp.session_manager.run()` when present, else no-op; `main.lifespan` wraps its existing body in it.
- `__main__.py`: `python -m mcp_server [--server URL] [--list-tools] [--manifest] [--max-tenant-calls-per-minute N]`; `--server` defaults to `CORTEXSIM_URL` env or `http://127.0.0.1:8888`; `--list-tools` prints `canonical\twire\tclass\tnetwork` per tool then `N tools`; `--manifest` prints YAML; else `mcp.run(transport="stdio")`. Logs one line per tenant-class call: tool id, tenant, `queries_issued`, duration ms — never a body.
- `manifest.py`: `build_manifest(specs) -> dict` = `{manifest_version: 2, kind: "mcp-server", id: "core/xsiam", target: "xsiam", server: {name: "cortexsim", sdk: "mcp==1.30.0"}, tools: [{id, wire, class, effect: network, scopes, backed: bool}]}`; `MANIFEST_SCHEMA` (jsonschema) pins those fields and `scopes ⊆ {dataset.read, xql.execute, content.read, alert.read}`; `validate_manifest(doc) -> None`.
- Makefile: `check-mcp-boot: docker run --rm --entrypoint sh $(IMAGE) -c 'cd /app/core && n=$$(python -m mcp_server --list-tools | tail -1 | cut -d" " -f1); test "$$n" -ge 41 && echo "mcp boot OK: $$n tools"'`.

- [ ] **Step 1: Write the failing tests**

```python
# test_server.py
def test_mount_off_registers_nothing_and_health_says_disabled(monkeypatch)        # CORTEXSIM_MCP_HTTP False → no /mcp route; GET /api/health components.mcp.mounted False
def test_mount_on_initialize_then_tools_list_over_asgi(monkeypatch):              # spec test 3 (ASGI half)
    # CORTEXSIM_MCP_HTTP True; build main.app fresh (reimport_core inside core_modules_restored); run lifespan via TestClient context;
    # POST /mcp {"jsonrpc":"2.0","id":1,"method":"initialize",...} with Host: localhost, Accept: application/json, text/event-stream → 200;
    # POST tools/list → names == sorted(wire_name(id) for id in CANONICAL_IDS)
def test_mcp_mount_precedes_the_static_catch_all(monkeypatch)                       # route order in main.app.routes
def test_unlisted_host_header_is_refused(monkeypatch)                               # Host: evil.example → 421
# test_manifest.py
def test_manifest_validates_against_local_p2_schema_and_lists_41_tools()           # spec test 12
def test_not_backed_tools_are_marked_backed_false()
# test_main_cli.py  (subprocess with PYTHONPATH=core, cwd=core)
def test_list_tools_prints_41()                                                     # last line "41 tools"
def test_manifest_flag_prints_yaml_that_validates()
```

- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement `server.py`, `__main__.py`, `manifest.py`; wire `main.py`; add the Makefile target and CI step.**
- [ ] **Step 4: Run** `tests/mcp_server -q`, `tests/api/test_health.py -q`, then `make build && make check-mcp-boot` — PASS; `mcp boot OK: 41 tools`.
- [ ] **Step 5: Commit**

```bash
git add core/mcp_server/server.py core/mcp_server/__main__.py core/mcp_server/manifest.py core/main.py Makefile .github/workflows/ci.yml tests/mcp_server/test_server.py tests/mcp_server/test_manifest.py tests/mcp_server/test_main_cli.py
git commit -m "feat(mcp): stdio + opt-in /mcp mount, P-2 manifest, image boot gate"
```

---

### Task 13: Docs, full suites, non-UTC pass, hand-over

**Files:**
- Modify: `docs/reference/api-and-agent-surface.md` (new `### 1.16 Readiness (core/api/readiness.py)` after §1.15 connector; new top-level `## 9. MCP server` after §8: transport table §4.2, Appendix A tool table verbatim, the envelope, the codes, the `claude mcp add` lines, the `tenant-verified: 0` line)
- Modify: `CHANGELOG.md` `[Unreleased]` — `### Added` (MCP server, `/api/readiness`, runs filters, `check-mcp-boot`), `### Changed` (executor/XQL routes charge the ledger and gate on `capabilities` — behaviour change for credentials without `"xql"`; `uvicorn` 0.31.1, `httpx` 0.27.2; preflight's absent-capabilities inversion), `### Security` (writes through MCP preview by construction)
- Modify: `README.md` (one short "Agent access (MCP)" block with the two `claude mcp add` lines from Appendix C)
- Modify: `/Users/henry/Library/Application Support/Claude/side-session-notes/local_1529627c-0951-4235-95d6-1b90eb3e1d9d/2026-11-02-mcp-server-spec-handover.md` (Phase 2 section: commits, counts, what is still owed)

- [ ] **Step 1: Write the three doc edits** (counts are copied from `--list-tools` and the test output, never typed from memory).
- [ ] **Step 2: Run the full host suite and the image suites**

Run: `PYTHONPATH=core .venv/bin/python -m pytest tests/ --ignore=tests/smoke --ignore=tests/e2e_isolated -q` then `make test-backend-non-utc` then `make validate`
Expected: all green; the non-UTC pass is spec test 11. Record the counts.

- [ ] **Step 3: Commit docs** (`docs(reference): MCP server section, readiness route, changelog`), one commit per file if the diffs are large.
- [ ] **Step 4: Write the hand-over note** with: branch tip, commit list, measured counts, the two behaviour changes Henry may veto (D-18 capabilities gate; preflight absent-capabilities inversion), and what is **still unproven** (no stdio session against a live SimCore has been driven by Claude Code in this pass unless Task 12 Step 4 was also run against `:8888`; tenant-verified remains 0).
- [ ] **Step 5: Report back** — nothing pushed, no PR.

---

## Self-review notes (done while writing)

- **Spec coverage:** §4.1–4.5 → Tasks 8, 12, 7; §5.1 → 8; §5.2 → 10 (launch rule → 9/10); §5.3 → 11; §5.4 → 11; §5.5 → 10/11 (`cap_list`, `cap_text`, `as_of`); §5.6 → 2/3/11; §6 → 11; §7 → 8/10/11 (summation), 6 (`tenant_verified` source); §8 → 8/10; §9 → 1; §10 tests 1–16 → 11, 11, 12, 10+11, 8, 11, 11, 11, 6, 7, 13, 12, 9+10, 9, 3+11, 6; §11 → 12/13; Appendix D items 1–6 → 3, 3, 4, 7, 5, 6.
- **Type consistency:** `SimCoreClient`, `ToolContext`, `ToolSpec`, `Envelope`, `Evidence`, `McpToolError`, `wire_name`, `CANONICAL_IDS`, `xql_capability_granted`, `tenant_budget`, `ledger_key_for` are spelled the same in every task that uses them.
- **Deliberate deviations from the spec's file list:** `errors.py`, `ratelimit.py`, `registry.py` added (the spec left the split to the builder); `xsiam.xql.query` backs onto the two XQL REST routes rather than the two catalog ops — same guards, same ledger, one less body shape to author; `cap_list` also takes `offset` so a capped list can be paged.
- **Proportion:** signatures and test names, no bodies; the §6 preview JSON and the §7 statement table are referenced, not restated.
