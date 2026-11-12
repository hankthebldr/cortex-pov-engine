# Pink (CL-CRI-1147) chain: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `SIM-MP-038` (Pink / CL-CRI-1147) as one SimCore run: an agent step on `pov-target-01` and three in-process collector steps. It is scored by Tier-2 verify against `deathstar`. It needs the engine and emitter changes that make that possible.

**Architecture:** Six independent changes land first, each on its own topic branch from `dev`:
1. verify queries the run's own time window (P-5);
2. live in-process EAL delivery behind consent and a keyed target (P-1);
3. S-25 scans `eal.params` (P-2);
4. an EAL step is never marker-only (P-3);
5. the IdP emitter gains a source IP, back-dated body time and a passkey pattern (E-0/E-1);
6. the M365 emitter gains a source IP, record user agents, storage targets, back-dated body time and an extortion pattern (E-2).

The card, the dataset registration and the scenario land last, on one branch rebased onto `dev` after the six merge. The §8 lab run closes it.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy async (SQLite), pydantic v2, pytest. XQL lives in TTP card JSON. Scenario YAML follows `scenarios/_schema.yml`.

**Status (2026-11-12):** Tasks 1–6 are built, task-reviewed and through a final whole-branch review with one fix wave. Their heads, all local and unpushed:
- `fix/api-verify-run-window` f7fc715b
- `feature/api-eal-live-in-process` b7f301d4
- `fix/scenarios-s25-eal-params` b81cb093
- `fix/scenarios-eal-step-not-marker-only` 4d118eaa
- `feature/api-emitters-pink-shapes` 6e92513d

Tasks 7–9 wait for those to merge into `dev`.

**Spec:** `docs/superpowers/specs/2026-11-11-pink-cl-cri-1147-chain-design.md` (draft v2, approved 2026-11-11). Section references (§N) below point into it.

## Global Constraints

- No write path to Cortex. `CORTEXSIM_XSIAM_ALLOW_WRITE` / `CORTEXSIM_XSIAM_ALLOW_DESTRUCTIVE` stay default-off. Verify is read-only XQL.
- No session to actor infrastructure. The lure leg is `dig @${LAB_RESOLVER} <domain> A +short` only: no fetch, no connect.
- The published actor user agents (ind-07..09) never go on SimCore's own HTTP requests. They live only inside record bodies.
- `96.232.20.66` appears only inside record bodies. Nothing is sent to it, and it is never written to an EDL or block rule.
- S-22..S-27 are never relaxed. P-2 extends S-25 to a new surface.
- Authored is not proven. `tenant-verified` stays 0. A green Pink run reads `not_applicable` ("evidence, not a scored pass"), never `pass`.
- Every new or changed record shape is byte-identical to today when its new param is absent or at its default.
- `push_supported: false` on SIM-MP-038.
- Branch naming is `<type>/<area>-<slug>` from `origin/dev`.
  - Stage by name (`git add <path>`); never `-A`, `.` or `-a`.
  - No PRs unless Henry asks.
  - Never commit to `main`.
  - Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Gate A: every behaviour test is observed failing before its fix.
- Functional fixes only. Add no new strict validators or guard-only tests beyond what a task names (Henry's standing rule).
- Host Python: put `/Users/henry/Github/Github_desktop/cortex-pov-engine/.venv/bin` first on `PATH`. Run tests as `PYTHONPATH=core python -m pytest <path> -q`.
- Timestamps in the DB are naive UTC. Convert them only through `connectors.base.coerce_utc`, the single coercer that an AST guard pins.
- Each task updates its governing doc in the same commit:
  - P-1: `docs/reference/api-and-agent-surface.md` (the launch and channel-dispatch sections).
  - P-5: the same file's verify section.
  - E-0/E-1/E-2: `docs/reference/eal-plugin-catalog.md`.
  - The scenario task: the regenerated ground truth.

## Review Focus

1. **A DC laptop in a non-UTC zone.** The verify window is built from naive-UTC `started_at` / `completed_at`. A `naive.timestamp()` call reads them as host-local time and shifts the window by the UTC offset; on `America/Los_Angeles` that is 8 h. Expect the same epoch ms whatever the host zone. Pinned in Task 1.
2. **A `verification_xql` that filters on `current_time() - N` itself.** The SIM-MP-034 card does this. It re-creates the late-verify false `fail` that P-5 removes. Expect no Pink verification query to use `current_time(`. Pinned in Task 8 (Pink only, not corpus-wide).
3. **A partial launch.** Expect the whole campaign to stay dry-run when consent is given but any one eal step is unresolved, keyless, failing or non-collector, or when `authorized_by` is blank. Every step carries a code; there is never a half-live campaign. Pinned in Task 2.
4. **An indicator nested inside `eal.params`.** For example, a list item or a sub-dict such as `extra: {dst: …}`. Expect S-25 to recurse and catch it under a non-collector plugin. Pinned in Task 3.
5. **Verifying a run with no `completed_at`, or whose scenario row is gone.** Running, aborted and orphaned runs fall here. Expect the window to be start − 5 min → start + window, with `to` clamped to now and the window falling back to the 3600 s default. Never an exception, never relativeTime-from-now. Pinned in Task 1.

## File Structure

| Path | Change | Responsibility |
|---|---|---|
| `core/connectors/verify_window.py` | create | pure: a run's absolute XQL timeframe |
| `core/connectors/service.py` | modify | verify and the sweep query each run's window |
| `core/engine/orchestrator.py` | modify | in-process EAL goes live behind consent + keyed targets |
| `core/api/runs.py` | modify | `LaunchRequest.authorized_by` passed through |
| `core/engine/scenario_loader.py` | modify | S-25 scans `eal.params` |
| `.claude/hooks/lint-scenario.py` | modify | stdlib mirror of S-25 on `eal.params`; eal steps never marker-only |
| `detection_scanner/scripts/coverage_report.py` | modify | eal steps never marker-only |
| `core/eal_simulator/analytics_emitter.py` | modify | `body_time()` helper |
| `core/eal_simulator/plugins/idp_signin_emulator.py` | modify | `source_ip`, body offset, `vishing_passkey_enrollment` |
| `core/eal_simulator/plugins/m365_activity_emitter.py` | modify | `source_ip`, `user_agents`, `storage_targets`, body offset, `internal_extortion_notice` |
| `detection_scanner/ttps/TTP-2026-0204-pink-cl-cri-1147-vishing-passkey-m365-extortion.json` | create | the card |
| `detection_scanner/scripts/validate.py`, `spec/dataset_requirements.yml` | modify | register `cortexsim_saas_raw` |
| `scenarios/multi_plane/mp-038-pink-cl-cri-1147-vishing-passkey-m365-extortion.yml` | create | the scenario |
| `scripts/uctc_crosswalk_v2.2.py` | modify | SIM-MP-038 crosswalk row |
| `tests/...` | create/modify | per task below |

Branches: Task 1 `fix/api-verify-run-window` · Task 2 `feature/api-eal-live-in-process` · Task 3 `fix/scenarios-s25-eal-params` · Task 4 `fix/scenarios-eal-step-not-marker-only` · Tasks 5–6 `feature/api-emitters-pink-shapes` · Tasks 7–9 `feature/scenarios-pink-cl-cri-1147`. Tasks 1–6 are independent of each other. Task 7 starts once Tasks 3–6 are on `dev`; Task 9 also needs Tasks 1–2 there.

---

### Task 1: P-5, verify queries the run's own window

**Branch:** `fix/api-verify-run-window` from `origin/dev`.

**Files:**
- Create: `core/connectors/verify_window.py`
- Modify: `core/connectors/service.py` (`TenantRunner` ~896, `resolve_tenant_runner` ~969, `verify_run_now` ~1138, `auto_verify_once` ~1258)
- Test: `tests/connectors/test_verify_window.py` (new), `tests/connectors/test_verification_service.py` (add)

**Interfaces:**
- Produces: `verify_window.LEAD_SECONDS = 300`.
- Produces: `verify_window.run_timeframe(started_at: datetime, completed_at: Optional[datetime], window_seconds: int, *, now: datetime) -> dict[str, int]`. It returns `{"from": <epoch ms>, "to": <epoch ms>}`, where:
  - `from` = started − 300 s;
  - `to` = min((completed or started) + window, now), and never earlier than `from`;
  - inputs go through `coerce_utc`; epoch is computed as UTC (`dt.replace(tzinfo=timezone.utc).timestamp()`).
- Produces: `TenantRunner.client: Optional[Any] = None`, set by `resolve_tenant_runner` whenever it returns a runner.
- Produces: `async def _run_window(db, run: Run, *, now: datetime) -> dict[str, int]` in `service.py`. It takes `Scenario.correlation_window_seconds` for `run.scenario_id`, falling back to `settings.CORTEXSIM_AUTO_VERIFY_TIMEFRAME_SECONDS` when the scenario is missing or the field is null.
- Behaviour: an explicit `timeframe_seconds` on `POST /api/runs/{id}/verify` or `auto_verify_once` keeps today's `{"relativeTime": s*1000}`. An injected `runner` is never replaced. Otherwise each run is queried with `xsiam_query_runner(client, await _run_window(...))`. The window is recorded at `tc_verdict_detail.verification.window`.

- [ ] **Step 1: Confirm the absolute timeframe shape.** Use the `cortex-docs-lookup` skill: the XQL `start_xql_query` `timeframe` absolute form. Expected: `{"from": <epoch ms>, "to": <epoch ms>}`. If the docs say otherwise, use their field names everywhere below and note it in the commit message.

- [ ] **Step 2: Write the failing tests** in `tests/connectors/test_verify_window.py`:
  - `test_window_spans_run_plus_correlation_window`. With started 2026-11-11 10:00:00, completed 10:02:00, window 3600 and now 14:00:00, assert `from` = 09:55:00 and `to` = 11:02:00, both as UTC epoch ms computed with `calendar.timegm`.
  - `test_to_is_clamped_to_now`. With now 10:30:00, `to` equals 10:30:00.
  - `test_no_completed_at_uses_start_plus_window`. Assert `to` = 11:00:00.
  - `test_window_epoch_is_utc_not_host_local`. Call `monkeypatch.setenv("TZ", "America/Los_Angeles")` and `time.tzset()`, then assert the same epoch ms as the first test.

  Add to `tests/connectors/test_verification_service.py`. Use a fake client with an `async run_xql(query, timeframe)` that records `timeframe` and returns `test_xql_envelope._reply(1)`. Monkeypatch `integrations.xsiam.loader.load_xsiam_client` as `test_honesty_invariant.py:365` does, against an `xsiam_tenant` integration whose config carries `"capabilities": ["xql"]`.
  - `test_late_verify_queries_the_runs_window_not_the_last_hour`. A run started 2 h ago and completed 2 h ago, with `correlation_window_seconds=3600`. Assert every recorded timeframe equals `run_timeframe(...)` and none has `relativeTime`.
  - `test_explicit_timeframe_seconds_still_wins`. With `timeframe_seconds=600`, assert `{"relativeTime": 600000}`.
  - `test_sweep_queries_each_runs_window`. Two runs with different `started_at` through `auto_verify_once(db, lookback_seconds=86400)`. Assert two distinct windows.
  - `test_missing_scenario_falls_back_to_default_window`. `scenario_id` names no row; assert the window uses 3600.

- [ ] **Step 3: Run them and see them fail.** Run `PYTHONPATH=core python -m pytest tests/connectors/test_verify_window.py tests/connectors/test_verification_service.py -q`. Expected: ImportError for `connectors.verify_window`, and the service tests fail with a recorded `relativeTime`.

- [ ] **Step 4: Implement** `run_timeframe` and `_run_window`, add `TenantRunner.client`, and wire `verify_run_now` and `auto_verify_once` per the Interfaces block. In `auto_verify_once`, capture `override = timeframe_seconds is not None` before the settings default is applied.

- [ ] **Step 5: Run the connector and API verify suites.** Run `PYTHONPATH=core python -m pytest tests/connectors tests/api/test_runs_verify.py -q`. Expected: all pass, including the existing `relativeTime` fixtures, which inject runners and are untouched.

- [ ] **Step 6: Update `docs/reference/api-and-agent-surface.md`.** In the verify section, describe the absolute run window, the `timeframe_seconds` override and `verification.window`. Note that this removes the late-verify false `fail` for the 15 existing `verification_xql` scenarios.

- [ ] **Step 7: Commit**
```bash
git add core/connectors/verify_window.py core/connectors/service.py tests/connectors/test_verify_window.py tests/connectors/test_verification_service.py docs/reference/api-and-agent-surface.md
git commit -m "fix(api): verify queries the run's own window, not the last hour"
```

---

### Task 2: P-1, live delivery for in-process EAL steps

**Branch:** `feature/api-eal-live-in-process` from `origin/dev`.

**Files:**
- Modify: `core/api/runs.py` (`LaunchRequest` ~379; the launch call ~674)
- Modify: `core/engine/orchestrator.py` (`launch` ~191, the `_handle_pull` call ~454, `_handle_pull` ~676, the dispatch call ~904, `_dispatch_eal_in_process` ~532)
- Test: `tests/engine/test_orchestrator_channel_dispatch.py`

**Interfaces:**
- Produces: `LaunchRequest.authorized_by: Optional[str] = None`, passed as `Orchestrator.launch(..., authorized_by=body.authorized_by)`.
- Produces: `Orchestrator.launch(..., authorized_by: Optional[str] = None)`. It computes `eal_authorized_by = authorized_by.strip()` only when `consent.get("simulation_authorized")` is true and `authorized_by.strip()` is non-empty; otherwise `None`. The value is threaded as `_handle_pull(..., eal_authorized_by=...)` and then `_dispatch_eal_in_process(..., authorized_by=eal_authorized_by)`.
- Produces: the module constant `EAL_IN_PROCESS_MAX_SLEEP_SECONDS = 30`. Total sleep is Σ over eal steps of `params.sleep_seconds × (params.iterations − 1)`, with defaults 0 and 1.
- Produces: the pure function `_eal_live_blockers(campaign_steps, resolutions, collector_plugins: set[str], *, authorized_by: Optional[str]) -> dict[str, tuple[str, str]]`, mapping `step_id` → `(code, detail)`. An empty dict means live is allowed. Codes:
  - `EAL_DRY_RUN_NO_CONSENT`: every step, when `authorized_by` is None.
  - `EAL_DRY_RUN_NOT_COLLECTOR`: the plugin is not in `targets.collector_plugin_names`.
  - `EAL_DRY_RUN_TARGET_UNRESOLVED`: the resolution source is `none`, `error` or `inline`.
  - `EAL_DRY_RUN_TARGET_NO_KEY`: `has_key` is False.
  - `EAL_DRY_RUN_TARGET_FAILING`: `check` is in `targets.FAILING_VERDICTS`.
  - `EAL_DRY_RUN_SLEEP_CAP`: total sleep is over the cap.
  - `EAL_DRY_RUN_HELD`: given to a clean step when any other step blocks. Its detail names the blocking step ids.
- Behaviour: any blocker keeps the whole campaign dry-run, and each step's ledger `code` is its blocker code. Today that code is always `None`. With no blocker:
  1. load keys with `engine.collector_targets.load_tokens(db, names)`;
  2. re-run `apply_targets(..., tokens=...)`;
  3. build `Campaign(dry_run=False, simulation_authorized=True, authorized_by=..., target_allowlist=resolved["target_allowlist"])`.

  Ledger statuses stay `dispatched` / `not_delivered` / `skipped` / `error`; delivery accounting stays `delivery.py`'s (2xx only).
- Behaviour: each eal step's `Result.executed_at` is that step's `StepResult.started_at` (through `coerce_utc`) when there is one, otherwise the launch `now`.

- [ ] **Step 1: Write the failing tests** in `tests/engine/test_orchestrator_channel_dispatch.py`. Seed a keyed `xsiam_http_collector` target bound to `m365_activity_emitter` the way `tests/eal_simulator/test_api_collector_targets.py` stores one with a key. Stub delivery by monkeypatching `eal_simulator.analytics_emitter.AnalyticsLogEmitter._build_client` to a recording client, copying `_RecordingClient` from `tests/eal_simulator/test_plugin_m365_activity_emitter.py`.
  - `test_eal_dry_run_without_consent_says_why`. No consent; assert status `dry_run`, code `EAL_DRY_RUN_NO_CONSENT`, and 0 POSTs.
  - `test_eal_consent_without_authorized_by_stays_dry_run`. Consent with `authorized_by="   "`; assert `EAL_DRY_RUN_NO_CONSENT`.
  - `test_eal_consent_with_unresolved_target_stays_dry_run_with_code`. No target configured; assert `EAL_DRY_RUN_TARGET_UNRESOLVED`, 0 POSTs.
  - `test_eal_one_keyless_step_holds_the_whole_campaign`. Two eal steps, one with a keyless target; assert codes `EAL_DRY_RUN_TARGET_NO_KEY` and `EAL_DRY_RUN_HELD`, and 0 POSTs.
  - `test_eal_sleep_over_cap_stays_dry_run`. `iterations=3, sleep_seconds=20`; assert `EAL_DRY_RUN_SLEEP_CAP`.
  - `test_eal_live_with_consent_and_keyed_target_posts`. Assert:
    - status `dispatched` and code `None`;
    - POSTs go to the target URL, with `Authorization` equal to the bare key;
    - `Result.executed_at` equals that step's recorded start, not the launch `now`. Monkeypatch `eal_simulator.executor._utcnow` to return `datetime(2030, 1, 1, tzinfo=timezone.utc)` and assert `Result.executed_at == datetime(2030, 1, 1)` (naive UTC).
  - `test_eal_live_collector_401_reads_not_delivered`. The recording client returns 401; assert status `not_delivered`.

- [ ] **Step 2: Run them and see them fail.** Run `PYTHONPATH=core python -m pytest tests/engine/test_orchestrator_channel_dispatch.py -q`. Expected: the new tests fail (code `None`, a `launch()` TypeError on `authorized_by`, 0 POSTs on the live test). The existing ones pass.

- [ ] **Step 3: Implement** per the Interfaces block. Replace the `dry_run=True` line (~611) and its comment.

- [ ] **Step 4: Run the dispatch and launch suites.** Run `PYTHONPATH=core python -m pytest tests/engine/test_orchestrator_channel_dispatch.py tests/engine/test_composer_channel_migration.py tests/api/test_launch_status_race.py tests/api -q`. Expected: all pass. If an existing test asserted `code is None` on a dry-run eal entry, update it to `EAL_DRY_RUN_NO_CONSENT`; that is the behaviour change.

- [ ] **Step 5: Update `docs/reference/api-and-agent-surface.md`.** Cover `authorized_by` on launch, the live gate, the seven `EAL_DRY_RUN_*` codes and the 30 s cap.

- [ ] **Step 6: Commit**
```bash
git add core/api/runs.py core/engine/orchestrator.py tests/engine/test_orchestrator_channel_dispatch.py docs/reference/api-and-agent-surface.md
git commit -m "feat(api): in-process EAL steps deliver live behind consent and a keyed target"
```

---

### Task 3: P-2, S-25 covers `eal.params`

**Branch:** `fix/scenarios-s25-eal-params` from `origin/dev`.

**Files:**
- Modify: `core/engine/scenario_loader.py` (`unsafe_indicator_lines` ~764)
- Modify: `.claude/hooks/lint-scenario.py` (`unsafe_indicator_lines` ~390)
- Test: `tests/engine/test_lint_scenario_hook.py`, `tests/engine/test_scenario_loader_indicators.py`

**Interfaces:**
- Produces: `scenario_loader._scan_eal_params(step: StepSchema, indicators: list[IndicatorSchema]) -> list[str]`. It walks `step.eal.params` recursively through dicts, lists and strings, matching each string against `_indicator_probes` with `_probe_pattern` for `domain`/`url`/`ipv4`/`ipv6` and url hosts. A hit is an error unless `step.eal.plugin in COLLECTOR_POST_PLUGINS`. Message: `S-25: step {id} eal.params.{dotted.path}: {type} {ind_id} in the params of non-collector plugin {plugin}`. Called from `unsafe_indicator_lines` for every step that has `eal`.
- Produces: a hook mirror of the same name and contract on dict steps (`st.get("eal", {}).get("params")`), called from the hook's `unsafe_indicator_lines`.

- [ ] **Step 1: Write the failing tests.**
  - Loader: `test_s25_ipv4_in_eal_params_of_non_collector_plugin_rejected`. A `channel: eal` step with `plugin: c2_http_beacon` and `params: {target_url: "http://172.93.100.252/"}`, plus indicator `172.93.100.252`; the loader raises with `S-25` and `eal.params.target_url`.
  - Loader: `test_s25_indicator_nested_in_eal_params_rejected`. `params: {extra: {hosts: ["passkeyadd.com"]}}` under `c2_http_beacon`; raises with path `eal.params.extra.hosts.0`.
  - Loader: `test_s25_indicator_in_collector_plugin_params_allowed`. `m365_activity_emitter` with `source_ip: 172.93.100.252`; loads.
  - Hook: `test_hook_eal_params_match_loader`. Parametrised over the three vectors above. Assert `_pairs(hook.unsafe_indicator_lines(...)) == _pairs(sl.unsafe_indicator_lines(...))`, building the loader step with `StepSchema.model_construct(id=..., command="true", channel="eal", eal=EalStepSchema(...))`.

- [ ] **Step 2: Run them and see them fail.** Run `PYTHONPATH=core python -m pytest tests/engine/test_lint_scenario_hook.py tests/engine/test_scenario_loader_indicators.py -q -k eal`. Expected: the rejects load cleanly today (no S-25).

- [ ] **Step 3: Implement** `_scan_eal_params` in both files.

- [ ] **Step 4: Run the hook and loader suites.** Run `PYTHONPATH=core python -m pytest tests/engine/test_lint_scenario_hook.py tests/engine -q -k "indicator or lint or s25"`, then `make check-refs`. Expected: all pass. `test_hook_passes_every_real_scenario` stays green.

- [ ] **Step 5: Commit**
```bash
git add core/engine/scenario_loader.py .claude/hooks/lint-scenario.py tests/engine/test_lint_scenario_hook.py tests/engine/test_scenario_loader_indicators.py
git commit -m "fix(scenarios): S-25 scans eal.params under non-collector plugins"
```

---

### Task 4: P-3, an EAL step is never marker-only

**Branch:** `fix/scenarios-eal-step-not-marker-only` from `origin/dev`.

**Files:**
- Modify: `detection_scanner/scripts/coverage_report.py:256` (`is_marker_only_step`)
- Modify: `.claude/hooks/lint-scenario.py` (the marker-only warning loop ~676)
- Test: `tests/engine/test_coverage_report_signal_quality.py`, `tests/engine/test_lint_scenario_hook.py`

**Interfaces:**
- Produces: `is_marker_only_step({"channel": "eal", "command": "true", ...}) is False`. The hook emits no marker-only warning for a step whose `channel == "eal"`. `is_marker_only(command)` keeps its signature; the hook loop checks `st.get("channel") != "eal"` first.

- [ ] **Step 1: Write the failing tests.**
  - `test_eal_channel_step_is_not_marker_only`. Assert `module.is_marker_only_step({"channel": "eal", "command": "true"}) is False` and `module.is_marker_only_step({"command": "true"}) is True`.
  - `test_hook_no_marker_warning_on_eal_step`. Write a minimal scenario with one `channel: eal` step whose command is `true` through the hook. Assert no `marker-only step` warning.

- [ ] **Step 2: Run them and see them fail.** Run `PYTHONPATH=core python -m pytest tests/engine/test_coverage_report_signal_quality.py tests/engine/test_lint_scenario_hook.py -q -k "eal"`. Expected: FAIL (True / warning present).

- [ ] **Step 3: Implement** the two one-line checks.

- [ ] **Step 4: Run the suites and the coverage gate.** Run the same two test files in full, then `make coverage-strict`. Expected: pass, and the marker-only count is unchanged because no corpus step is `channel: eal` yet.

- [ ] **Step 5: Commit**
```bash
git add detection_scanner/scripts/coverage_report.py .claude/hooks/lint-scenario.py tests/engine/test_coverage_report_signal_quality.py tests/engine/test_lint_scenario_hook.py
git commit -m "fix(scenarios): a channel: eal step is never marker-only"
```

---

### Task 5: E-0 + E-1, IdP emitter, source IP, body offset, passkey pattern

**Branch:** `feature/api-emitters-pink-shapes` from `origin/dev`.

**Files:**
- Modify: `core/eal_simulator/analytics_emitter.py` (add `body_time`)
- Modify: `core/eal_simulator/plugins/idp_signin_emulator.py`
- Test: `tests/eal_simulator/test_plugin_idp_signin_emulator.py`

**Interfaces:**
- Produces: `analytics_emitter.body_time(offset_seconds: int = 0, *, plus_seconds: float = 0.0) -> str`, which returns `(datetime.now(timezone.utc) + timedelta(seconds=offset_seconds + plus_seconds)).isoformat()`.
- Produces: on `IdpSigninEmulatorParams`:
  - `event_time_offset_seconds: int = Field(0, ge=-86400, le=0)`;
  - `source_ip: Optional[str] = None`, validated with `ipaddress.ip_address`.

  **Refinement of spec §9.4:** the offset field lives on the two params models that honour it, not on `AnalyticsEmitterParams`. That way none of the other 19 emitters advertises a field it would ignore; the helper is shared.
- Produces: `_okta_event`, `_microsoft_event` and `_google_event` each gain `when: Optional[str] = None`. When it is None they keep calling `datetime.now(...)`, so today's records are unchanged.
- Behaviour: `source_ip` replaces the IP of every record whose location is not `us-west`, the home baseline that `impossible_travel`, `token_replay` and `azure_risky_login` use. That is every attacker-side record, through `dataclasses.replace(loc, ip=source_ip)`.
- Produces: pattern `vishing_passkey_enrollment`, with provider `microsoft` only. Under `okta` or `google` the params model raises `vishing_passkey_enrollment needs provider microsoft`. It emits two records:
  1. a successful interactive sign-in from the attacker location (default `anon-vpn`), at `body_time(offset)`, with `authenticationDetails` showing password plus an approved number-matched push, `isInteractive: true` and the marker `vishing_passkey_marker: true`;
  2. a directory-audit "User registered security info" record, method Passkey, from the same IP, at `body_time(offset, plus_seconds=90)`, carrying the same marker.

  Field names come from the Microsoft Graph `signIn` and `directoryAudit` resource docs (Step 1).

- [ ] **Step 1: Read the record shapes.** Use the `microsoft_docs_fetch` / `microsoft_docs_search` tools for the Graph `signIn` resource (`authenticationDetails`, `isInteractive`) and the `directoryAudit` resource (`activityDisplayName`, `activityDateTime`, `category`, `loggedByService`, `initiatedBy.user.ipAddress`, `targetResources`, `additionalDetails`). Also find what the "User registered security info" audit records as the method. Note the exact names in the test file's docstring.

- [ ] **Step 2: Write the failing tests.**
  - `test_body_time_offset_moves_timestamp`. Freeze `datetime` in `analytics_emitter` with monkeypatch; `body_time(-1500)` is frozen − 1500 s and `body_time(-1500, plus_seconds=90)` is frozen − 1410 s.
  - `test_positive_offset_refused`. `IdpSigninEmulatorParams(collector_url=..., event_time_offset_seconds=10)` raises.
  - `test_source_ip_replaces_attacker_side_ips[pattern]`. Parametrised over all 9 patterns with `source_ip="172.93.100.252"`. Every record whose IP was not `203.0.113.10` (us-west) now carries `172.93.100.252`, and us-west records are unchanged.
  - `test_default_source_ip_keeps_todays_ips[pattern]`. Without `source_ip`, the IP set per pattern equals today's (derive it from `_LOCATIONS`).
  - `test_vishing_passkey_enrollment_shapes`. Provider `microsoft`, offset −1500. Exactly 2 records. Record 2 is the audit record with the Passkey method and the same IP. Its body time is record 1's + 90 s.
  - `test_vishing_passkey_needs_microsoft`. Provider `okta` raises.
  - Update `test_eight_event_patterns_registered` to 9 patterns (rename it `test_nine_event_patterns_registered`).

- [ ] **Step 3: Run them and see them fail.** Run `PYTHONPATH=core python -m pytest tests/eal_simulator/test_plugin_idp_signin_emulator.py -q`. Expected: the new tests fail (unknown field, unknown pattern).

- [ ] **Step 4: Implement** per the Interfaces block.

- [ ] **Step 5: Run the EAL suites and the streamer gate.** Run `PYTHONPATH=core python -m pytest tests/eal_simulator -q`, then `make check-streamer`. Expected: all pass.

- [ ] **Step 6: Update `docs/reference/eal-plugin-catalog.md`.** In the idp row, add the new params and the pattern. Commit:
```bash
git add core/eal_simulator/analytics_emitter.py core/eal_simulator/plugins/idp_signin_emulator.py tests/eal_simulator/test_plugin_idp_signin_emulator.py docs/reference/eal-plugin-catalog.md
git commit -m "feat(api): idp emitter takes a source IP, a body offset and a passkey pattern"
```

---

### Task 6: E-2, M365 emitter, source IP, record user agents, storage targets, extortion pattern

**Branch:** `feature/api-emitters-pink-shapes` (after Task 5).

**Files:**
- Modify: `core/eal_simulator/plugins/m365_activity_emitter.py`
- Test: `tests/eal_simulator/test_plugin_m365_activity_emitter.py`

**Interfaces:**
- Consumes: `analytics_emitter.body_time` (Task 5).
- Produces: on `M365ActivityEmitterParams`:
  - `source_ip: Optional[str] = None`, validated with `ipaddress`; None means `_ANOMALOUS_IP`;
  - `user_agents: list[str] = []`;
  - `storage_targets: list[Literal["onedrive", "sharepoint"]] = Field(default_factory=lambda: ["onedrive"], min_length=1)`;
  - `event_time_offset_seconds: int = Field(0, ge=-86400, le=0)`.
- Produces: `_m365_audit_event(..., when: Optional[str] = None, user_agent: Optional[str] = None)`. A `UserAgent` key is written only when `user_agent` is set. `user_agents` never touches the base `user_agent` (the collector POST header).
- Behaviour: every pattern passes:
  - `source_ip=params.source_ip or _ANOMALOUS_IP`;
  - `when=body_time(params.event_time_offset_seconds)`;
  - `user_agent=params.user_agents[idx % len] if params.user_agents else None`.
- Behaviour: `storage_exfiltration` cycles records over `storage_targets`:
  - `onedrive`: today's record (Workload OneDrive, `_ONEDRIVE_SITE`, `Documents/`);
  - `sharepoint`: `dataclasses.replace(activity, workload="SharePoint")`, `_SHAREPOINT_SITE`, `Shared Documents/`.
- Produces: pattern `internal_extortion_notice` (marker `internal_extortion_notice_marker`):
  - one Exchange `Send` mailbox-audit record from `target_user` to `burst_count` internal recipients `colleague-NN@<target domain>`;
  - `burst_count` Teams `MessageSent` records by `target_user`, one per recipient.

  Operation names, RecordType values (expected `ExchangeItem` = 2, `MicrosoftTeams` = 25) and the recipient field name come from the Office 365 Management Activity API schema (Step 1). If the schema has no recipient list on `Send`, write the recipients to `cortexsim_recipients`, a name that cannot be mistaken for a real field, and say so in the catalog.

- [ ] **Step 1: Read the schema.** Use `microsoft_docs_fetch` on the Office 365 Management Activity API schema: the common schema `RecordType` enum, the Exchange mailbox audit `Send` operation, and the Teams `MessageSent` operation's fields. Record the names in the test docstring.

- [ ] **Step 2: Write the failing tests.**
  - `test_default_records_unchanged[pattern]`. For all 6 existing patterns, with time and `secrets.token_hex` frozen, records built with defaults equal records built with `source_ip=None, user_agents=[], storage_targets=["onedrive"], event_time_offset_seconds=0`. Also assert no record has a `UserAgent` key.
  - `test_source_ip_stamps_every_record[pattern]`. All 7 patterns; `ClientIP == "172.93.100.252"` on every record.
  - `test_user_agents_rotate_in_records_not_header`. `user_agents` = the 3 published strings, `burst_count=6`:
    - record `UserAgent` values cycle through the three in order;
    - the recording client's POST `User-Agent` header contains none of them.
  - `test_storage_targets_both_workloads`. `["sharepoint", "onedrive"]`; both `Workload` values present, each with its own site URL.
  - `test_internal_extortion_notice_shapes`. One `Send` with 4 recipients and 4 Teams records (`burst_count=4`), all from `source_ip`, all carrying the marker.
  - Update `test_six_event_patterns_registered` to 7 patterns (rename it `test_seven_event_patterns_registered`).

- [ ] **Step 3: Run them and see them fail.** Run `PYTHONPATH=core python -m pytest tests/eal_simulator/test_plugin_m365_activity_emitter.py -q`. Expected: the new tests fail.

- [ ] **Step 4: Implement** per the Interfaces block.

- [ ] **Step 5: Run the EAL suites and the streamer gate.** Run `PYTHONPATH=core python -m pytest tests/eal_simulator -q` and `make check-streamer`. Expected: pass; `_DATASET` is still `msft_o365_audit`.

- [ ] **Step 6: Update the catalog row and commit.**
```bash
git add core/eal_simulator/plugins/m365_activity_emitter.py tests/eal_simulator/test_plugin_m365_activity_emitter.py docs/reference/eal-plugin-catalog.md
git commit -m "feat(api): m365 emitter takes a source IP, record user agents, storage targets and an extortion pattern"
```

---

### Task 7: Card TTP-2026-0204 and dataset `cortexsim_saas_raw`

**Branch:** `feature/scenarios-pink-cl-cri-1147`, from `origin/dev` once Tasks 3–6 are merged.

**Files:**
- Create: `detection_scanner/ttps/TTP-2026-0204-pink-cl-cri-1147-vishing-passkey-m365-extortion.json`
- Modify: `detection_scanner/scripts/validate.py` (`KNOWN_DATASETS` ~126)
- Modify: `spec/dataset_requirements.yml`
- Regenerate: `detection_scanner/exports/`

**Interfaces:**
- Produces: card `TTP-2026-0204`. First confirm the id is free with `for b in $(git branch -a --format='%(refname:short)' | grep -E 'unit42|cloud-logging'); do git ls-tree --name-only $b detection_scanner/ttps/; done | grep -o 'TTP-2026-0[0-9]*' | sort -u | tail -3`. As of 2026-11-11 the highest taken is 0203. Use the next free id if 0204 is gone, and rename everywhere below.
- Produces these card objects, named exactly as below. The scenario's `detection_id` for each is the slug the corpus resolver derives (the same convention as SIM-MP-034's `xql-mp-034-…`). Read it back with `make validate-detection`.

| Spec row | Kind | `purpose` | Name |
|---|---|---|---|
| D1 | `xql_queries` | validation | `MP-038 DNS Security Logged the PANW Phishing Test Domain` |
| D2 | `xql_queries` | hunt | `MP-038 DNS Security Logged a Pink Lure Domain` |
| D3 | `xql_queries` | validation | `MP-038 Endpoint Resolved a Pink Lure Domain` |
| D4 | `xql_queries` | hunt | `MP-038 Linux Agent Recorded the Pink DNS Query` |
| D5 | `xql_queries` | validation | `MP-038 Entra Sign-in for the Canary from the Pink Access IP` |
| D6 | `xql_queries` | validation | `MP-038 Passkey Registered from the Sign-in IP Within Ten Minutes` |
| D7 | `xql_queries` | validation | `MP-038 SharePoint and OneDrive Download Burst from the Pink IP` |
| D8 | `xql_queries` | validation | `MP-038 File Access Through Graph SDK and python-requests User Agents` |
| D9 | `xql_queries` | validation | `MP-038 Internal Fan-out Send from the Extortion IP` |
| D10 | `xql_queries` | validation | `MP-038 Teams Messages by the Canary from the Extortion IP` |
| CR-1 | `correlation_rules` | — | `Pink — Sign-in, Passkey and Download Burst on One Principal` (`rule_id` per the corpus `CR-MP-…` pattern) |
| CR-2 | `correlation_rules` | — | `Pink — Download Burst then Internal Extortion on One Principal` |
| CR-3 | `xql_queries` | hunt | `MP-038 Lure Resolution Within Thirty Minutes of the Sign-in` |

- Query rules:
  - Collector rows read `dataset = cortexsim_saas_raw` and scope by the `cortexsim_canary` field plus the planted canary principal. Once `canary_token = "cortexsim-mp-038-pink"` is set, `plant_canary` rewrites the principal fields to the canary principal (`userPrincipalName`, `initiatedBy.user.userPrincipalName`, m365 `UserId`/`UserKey`). So **do not scope on `target_user`**: `MailboxOwnerUPN`, Teams `Members[]` and `targetResources[]` keep it (amended 2026-11-12 after the final review, finding I5).
  - Never join on a list-valued field (`Members[]`, `targetResources[]`, `recipientList`). Join records on `UserId` / `userPrincipalName`.
  - Agent and NGFW rows scope by the exact domain.
  - No host name appears in any query, and no query uses `current_time(`.
  - Ordering and gaps (D6, CR-1, CR-2) key on body time fields (`createdDateTime`, `activityDateTime`, `CreationTime`), never `_time`.
  - Field names are taken from one captured collector row where one exists. Until L-1 is done, take them from the emitter's records and add a note to `metadata` that they are unconfirmed against a stored row.
- Produces: the card prose (`threat_context`). It carries Okta's kit pages and the attacker-enrolled passkey as context. `assignpasskey.com` and `setpasskey.com` appear in prose only.

- [ ] **Step 1: Copy the structure** of `detection_scanner/ttps/TTP-2026-0193-jingle-thief-m365-entra-gift-card-fraud.json` (`id`, `schema_version`, `metadata`, `identity`, `threat_context`, `mitre_attack`, `execution`, `detections`, `panw_mapping`, `remediation_guidance`, `references`). Set `references` to the TTI file URL. MITRE: T1566.004, T1078.004, T1098.005, T1530, T1213.002, T1657.

- [ ] **Step 2: Register the dataset.**
  - Add `"cortexsim_saas_raw"` to `KNOWN_DATASETS`.
  - Add a `spec/dataset_requirements.yml` row: `produced_by: cortexsim`, `doors: [http_collector]`, `emulated_by: []` (no plugin's Meta names this dataset), all `cortex_side` names `null`, all `verified` blocks empty. The source-side detail names the vendor `cortexsim` / product `saas` HTTP collector from §10 L-1.

- [ ] **Step 3: Run the corpus validator.** Run `python3 detection_scanner/scripts/validate.py --quiet`. Expected: 0 fail. Then run `python3 detection_scanner/scripts/export_artifacts.py --clean` and `make validate-detection`; expected green with the regenerated exports staged.

- [ ] **Step 4: Commit**
```bash
git add detection_scanner/ttps/TTP-2026-0204-pink-cl-cri-1147-vishing-passkey-m365-extortion.json detection_scanner/scripts/validate.py spec/dataset_requirements.yml detection_scanner/exports/
git commit -m "feat(ttp): Pink CL-CRI-1147 card and the cortexsim_saas_raw dataset"
```

---

### Task 8: Scenario SIM-MP-038

**Branch:** `feature/scenarios-pink-cl-cri-1147` (after Task 7).

**Files:**
- Create: `scenarios/multi_plane/mp-038-pink-cl-cri-1147-vishing-passkey-m365-extortion.yml`
- Create: `tests/engine/test_sim_mp_038_shape.py` (model: `tests/engine/test_sim_koi_009_shape.py`)
- Modify: `scripts/uctc_crosswalk_v2.2.py` (SIM-MP-038 row)
- Regenerate: `docs/reference/ground-truth.{json,md}`, the UC/TC sheet

**Interfaces:**
- Consumes: Task 7's card object names (via their resolved slugs); Tasks 5–6's params and patterns; Task 3's S-25 rule (the scenario must pass it); Task 4's marker-only rule.
- Produces: scenario `SIM-MP-038`, `plane: ANALYTICS`, `push_supported: false`, `pull_supported: true`. Its four steps are exactly §5:
  - **step-01** (agent, `identity: direct`): four lines `dig @${LAB_RESOLVER} <domain> A +short`, one per `passkeyadd.com`, `passkeydeploy.com`, `deploypasskey.com` and `test-phishing.testpanw.com`. Each is a bare lookup line, with nothing chained.
  - **step-02** (eal, `idp_signin_emulator`): `provider: microsoft`, `event_pattern: vishing_passkey_enrollment`, `source_ip: 172.93.100.252`, `event_time_offset_seconds: -1500`.
  - **step-03** (eal, `m365_activity_emitter`): `event_pattern: storage_exfiltration`, `source_ip: 172.93.100.252`, `user_agents` = ind-07..09, `storage_targets: [sharepoint, onedrive]`, `event_time_offset_seconds: -1200`.
  - **step-04** (eal, `m365_activity_emitter`): `event_pattern: internal_extortion_notice`, `source_ip: 96.232.20.66`, `event_time_offset_seconds: -300`.
- Every eal step also carries:
  - `target_user: cortexsim.pink.canary@cortexsim-canary.invalid`;
  - `canary_token: cortexsim-mp-038-pink`;
  - a descriptive no-op `command` (required by `StepSchema`);
  - no `collector_url`, because the instance target resolves it.
- Other fields:
  - `indicators[]`: exactly the 9 rows of §6, with `source_url` = the TTI file in `PaloAltoNetworks/Unit42-Threat-Intelligence-Article-Information`.
  - Tags: `unit42`, `pink`, `cl-cri-1147`, `real-ioc-dns-lookup`.
  - `threat_report_url` = the GitHub TTI file.
  - `expected_detections`: D1, D3, D5–D10, CR-1, CR-2, each carrying its card query as `verification_xql`. No `primary_kpi`, no `threshold`.
  - `uc_ref` / `tc_ref`: `UCS-ITDR-02` / `TC-ITDR-03`, with `TC-ITDR-02` in `tc_refs`, as on SIM-MP-034. `pov_scenario_id: POV-SC-001`.
  - `methodology_family: F1`, `stitching_key: user_principal`, `required_planes_in_incident: [ITDR, CLOUD_APP]`, `correlation_window_seconds: 3600`.
- The header comment states that `LAB_RESOLVER` is a public resolver reached through the PA-440, and that `pov-target-01` (Ubuntu) stands in for "a host that resolved the lure".

- [ ] **Step 1: Write the failing shape test** `tests/engine/test_sim_mp_038_shape.py`:
  - `test_loads_under_strict_refs`. The real loader returns SIM-MP-038.
  - `test_four_steps_one_agent_three_eal`. Check channels and plugins per the Interfaces block.
  - `test_eal_params_validate_against_their_plugins`. For each eal step, `registry.get(plugin).Meta.params_model(**params, collector_url="https://collector.lab.invalid/x")` validates. The loader does not validate `eal.params`, so this is where a bad pattern name gets caught. Then render each step with the plugin's `records_for(params, sim_run_id=..., iteration=0)` and assert it returns at least one record. A dry run never builds records (amended 2026-11-12, finding M3), so validation alone proves nothing about rendering.
  - `test_nine_indicators_seven_in_band`. ind-04 is out of band and the rest are in band.
  - `test_every_declared_detection_has_verification_xql`. Ten rows, each with a non-empty query.
  - `test_no_verification_query_reads_relative_to_now`. No `verification_xql` contains `current_time(` (Review Focus 2).
  - `test_no_kpi_no_threshold_push_off`.
  - `test_no_marker_only_step`. Uses `coverage_report.is_marker_only_step` on every step; the list is empty.
  - `test_published_user_agents_only_in_step_03_params`. ind-07..09 appear only under step-03's `eal.params.user_agents`.

- [ ] **Step 2: Run it and see it fail.** Run `PYTHONPATH=core python -m pytest tests/engine/test_sim_mp_038_shape.py -q`. Expected: FAIL (scenario not found).

- [ ] **Step 3: Write the scenario YAML.** Run `python3 .claude/hooks/lint-scenario.py scenarios/multi_plane/mp-038-pink-cl-cri-1147-vishing-passkey-m365-extortion.yml`; expected 0 errors and 0 marker-only warnings. Add the crosswalk row; `python3 scripts/uctc_crosswalk_v2.2.py --report` shows 0 unresolved.

- [ ] **Step 4: Run the shape test** (Step 2's command). Expected: PASS.

- [ ] **Step 5: Run the gates.**
  - Run `make validate-detection`, `make check-refs` and `make coverage-strict`. The correlation share rises: 2 of SIM-MP-038's 10 declared rows are correlation.
  - Run `make uctc-sheet` then `make check-uctc-sheet`, and `make build` then `make ground-truth`. Commit the regenerated files.
  - Run the host backend suite `PYTHONPATH=core python -m pytest tests/ --ignore=tests/smoke --ignore=tests/e2e_isolated -q`, then `make test-backend-non-utc` and `PYTHONPATH=core python -m pytest tests/e2e_isolated -q`.

  Expected: every gate green. Record the pass counts for the commit message.

- [ ] **Step 6: Commit**
```bash
git add scenarios/multi_plane/mp-038-pink-cl-cri-1147-vishing-passkey-m365-extortion.yml tests/engine/test_sim_mp_038_shape.py scripts/uctc_crosswalk_v2.2.py docs/reference/ground-truth.json docs/reference/ground-truth.md docs/uc_tc_mapping/_v2.2-source/engine_coverage_v2.3.csv docs/uc_tc_mapping/_v2.2-source/CortexUCTCIndex_v2.3_engine-coverage.xlsx
git commit -m "feat(scenarios): SIM-MP-038 Pink CL-CRI-1147 on today's lab doors"
```

---

### Task 9: Lab run and evidence

**Branch:** `feature/scenarios-pink-cl-cri-1147`. Needs Tasks 1–2 on `dev` and the branch rebased.

**Prerequisites (Henry, §10):** L-1 (HTTP collector + collector target, preflight `accepted` with `write_canary`), L-2 (DNS Security categories log), L-4 (`LAB_RESOLVER`), L-5 (XDR collectors running on `pov-target-01`) and L-6 (`xsiam_tenant` credential with `capabilities: ["xql"]`). D-2 has a default: accept the ms-01 attribution for this run.

**Files:**
- Create: `docs/lab/<date>-pink-cl-cri-1147-lab-run.md` (shape: `docs/lab/2026-11-11-unit42-deep-real-verification.md`)
- Possibly modify: the Task 7 card and the scenario, only to correct field names against the captured row

- [ ] **Step 1: Capture one real collector row.** Use `POST /api/collector-targets/{name}/preflight` with `write_canary: true`, then a read-only XQL `dataset = cortexsim_saas_raw | limit 1`. Compare its field names and nesting against the Task 7 queries. Record whether the row's `_time` is the ingest time or the record's body time: verify widens the window for back-dated bodies either way (finding I4), but the evidence doc must say which. Fix any query that disagrees, re-run `make validate-detection`, and commit as `fix(ttp): MP-038 queries match the stored collector row`.

- [ ] **Step 2: Launch the run.** `POST /api/runs/launch` with `scenario_id: SIM-MP-038`, `mode: pull`, `target_agent_id: <pov-target-01 beacon id>`, `consent: {simulation_authorized: true}` and `authorized_by: <Henry>`. Expected:
  - `channel_dispatch` shows step-02..04 `dispatched` with code `null`;
  - step-01 completes with rc 0.

  Any `EAL_DRY_RUN_*` code means a prerequisite is missing; fix it and relaunch.

- [ ] **Step 3: Verify the run.** Call `POST /api/runs/{id}/verify` at least 70 minutes after the launch, which exercises P-5. Expected:
  - D1, D3, D5–D10, CR-1 and CR-2 return rows;
  - `tc_verdict` is `not_applicable`;
  - `verification.window` is the run's own window.

  If D1 is empty, stop: the NGFW path is broken (policy, profile, forwarding or NAT), and D2 is not evidence.

- [ ] **Step 4: Run the hunts.** Run D2, D4 and CR-3 once each through the read-only XQL route and record the row counts. A hunt that returns rows becomes a candidate for promotion to declared, in a later change.

- [ ] **Step 5: Write the evidence doc.** It covers:
  - what landed, what fired and what did not, with run id, window and row counts;
  - that `tenant-verified` stays 0;
  - the overlapping-runs limit (§7);
  - the ms-01 attribution caveat (L-3).

  Commit:
```bash
git add docs/lab/<date>-pink-cl-cri-1147-lab-run.md
git commit -m "docs(lab): Pink CL-CRI-1147 first lab run against deathstar"
```
