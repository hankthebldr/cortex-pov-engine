# SIM-KOI-009 GlassWorm Agentic-Endpoint (AES/XDR) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land `SIM-KOI-009` + `TTP-2026-0197`, the first KOI scenario that installs and runs an agentic component on a macOS lab endpoint so Cortex AES (inventory, risk) and Cortex XDR (`xdr_data` lineage) both observe it, with every repo gate green at 193 scenarios / 191 cards.

**Architecture:** A new benign, decoy-only pack component (`vscode/prettier-pro-canary/`) is installed by the IDE's own CLI, activated once, then driven step by step through the component's VS Code URI handler so each beacon-run step's real lineage is extension host → child. A rewritten card carries the detections; the scenario binds them by slug; crosswalk, counts, exports, golden digests and ground truth move together.

**Tech Stack:** YAML scenario + JSON card (`detection_scanner/schema/ttp-entry.schema.json`), Python 3.11 tests (pytest), stdlib lint hook, `scripts/uctc_crosswalk_v2.2.py`, Makefile gates, Docker for `make ground-truth`.

**Spec:** `docs/superpowers/specs/2026-10-28-koi-aes-glassworm-sim-design.md` — read it first; this plan argues from it and never restates its §5 shape table or §6 artifact rules.

## Global Constraints

- Card id is `TTP-2026-0197`; scenario id is `SIM-KOI-009`; correlation id is `CR-KOI-0009`. Never mint others.
- Branch rebased onto local `dev` at `e047aaa` (192 scenarios · 190 cards) before any content change.
- Zero marker-only steps (spec §9.2; `dev` ceiling `0.1266`, corpus at 100/790).
- At most ten step-detections per Correlation row (spec §9.2; corpus correlation share 10.07 % against a 10 % floor). This scenario declares **nine** step-detections and **one** Correlation row.
- Every step: `platforms: ["macos"]`, `identity: direct`, `execution_identity: {default: direct, options: [direct]}`; no exit masking (`|| true`, `|| echo`, `; true`, `2>/dev/null ||`).
- No `indicators[]` block on the scenario (spec §9.1). Card IOCs carry no `source_url`.
- No `outcome: prevent` anywhere (spec §4).
- The pack component ships no encryption, no obfuscation, no timers, no reads outside the decoy tree, no network unless `CORTEXSIM_C2_URL` is set, and every file carries `CORTEXSIM-CANARY` (spec §6). **The attack-shaped content — the component's activation behaviours and the six step commands — is authored by the executor from spec §5/§6 in the SAFE-MODE style of `scenarios/koi/sim-koi-008-*.yml`; this plan deliberately carries none of that text.**
- `panw_mapping` leg-A row: `module: cortex-xdr`, `submodule: agentic-endpoint-security`, `coverage_tier: exposure-mgmt` (spec §4). IOC types from the schema enum only (`ipv4`, `url`, `filename`, `filepath`).
- Commit staging by path name only. No PR unless asked. `tenant-verified` is reported as 0 everywhere.

## Review Focus

Failure modes the spec implies that no runtime test can exercise; each is pinned by a static assertion in the task named.

1. **`code` CLI absent on the lab Mac** — step 01 must exit non-zero and stop the run, not echo and continue. Pinned in Task 1 (no exit masking on any step) and Task 5 (runbook prerequisite).
2. **`CORTEXSIM_C2_URL` pointed at a real host** — the component would send the decoy archive there. Pinned in Task 2 (the only literal host in the component is `cortexsim-canary.invalid`; the scenario never sets the variable) and Task 5 (runbook: leave unset).
3. **Cleanup interrupted after step 04** — a loaded LaunchAgent would survive. Pinned in Task 1 (cleanup must carry the launchd unload, the plist removal, the decoy-keychain deletion, the extension uninstall and the decoy-root removal).
4. **Beacon enrolled as root** — a LaunchDaemon cannot reach the session user's IDE, Keychain or LaunchAgents domain. Pinned in Task 1 (`options: [direct]` only) and Task 5 (runbook: enroll as the session user).
5. **Beacon working directory is not the repo checkout** — the relative pack path in step 01 is unresolvable. Pinned in Task 1 (step 01 references `sources/cortex-malicious-agentic-pack/vscode/prettier-pro-canary`) and Task 5 (runbook states the dependency).

---

### Task 0: Rebase, fix the scaffold ids, commit

**Files:**
- Rename: `detection_scanner/ttps/TTP-2026-0194-sim-koi-009.json` → `detection_scanner/ttps/TTP-2026-0197-sim-koi-009.json`
- Modify: `scenarios/koi/sim-koi-009-glassworm-agentic-endpoint-takeover.yml` (every `TTP-2026-0194` → `TTP-2026-0197`)
- Modify: the renamed card (`"id": "TTP-2026-0197"`)

- [ ] **Step 1: Rebase the branch onto local `dev`**

Run: `git rebase dev`
Expected: the branch's commits (the design doc and this plan) replay cleanly; `git merge-base --is-ancestor dev HEAD` exits 0 and `git log --oneline -3` shows them directly on top of `e047aaa`. The two scaffold files are untracked and ride along.

- [ ] **Step 2: Confirm the id collision and the next free id**

Run: `ls detection_scanner/ttps | grep -oE 'TTP-2026-[0-9]{4}' | sort | tail -1`
Expected: `TTP-2026-0196`

- [ ] **Step 3: Rename the card file and rewrite both ids**

Use `git mv`-free plain `mv` (the file is untracked), then replace `TTP-2026-0194` with `TTP-2026-0197` in both scaffold files. Verify with `grep -rn 'TTP-2026-0194' scenarios/koi detection_scanner/ttps` → no output.

- [ ] **Step 4: Commit the scaffolds under their final ids**

```bash
git add detection_scanner/ttps/TTP-2026-0197-sim-koi-009.json scenarios/koi/sim-koi-009-glassworm-agentic-endpoint-takeover.yml
git commit -m "feat(scenarios): SIM-KOI-009 scaffold on TTP-2026-0197 (draft)"
```

---

### Task 1: Scenario-shape guard test (written RED against the scaffold)

**Files:**
- Create: `tests/engine/test_sim_koi_009_shape.py`

**Interfaces:**
- Consumes: `detection_scanner/scripts/coverage_report.py::is_marker_only_step(step) -> bool` (load via `importlib.util.spec_from_file_location`, as `tests/engine/test_coverage_report_signal_quality.py` does); `engine.scenario_loader.validate_causality_spine`; `engine.ttp_catalog.TtpCatalog.find(ttp_ref, detection_id) -> Optional[DetectionCard]` on the `catalog` fixture `tests/engine/test_ttp_catalog.py` builds (`TtpCatalog().load("detection_scanner/ttps")`); `.claude/hooks/lint-scenario.py` run as a subprocess with `[sys.executable, HOOK, path, REPO_ROOT]` (as `tests/engine/test_lint_scenario_hook.py` does).
- Produces: the executable definition of "done" for Task 4. Task 4 is finished when this module passes.

- [ ] **Step 1: Write the failing tests**

One module, one `scenario()` fixture loading the YAML with `yaml.safe_load`, one `card()` fixture loading `TTP-2026-0197-sim-koi-009.json`. Tests, with the spec's values:

```python
def test_status_active_and_ids(scenario, card):
    assert scenario["status"] == "active" and card["status"] == "active"
    assert scenario["scenario_id"] == "SIM-KOI-009" and card["id"] == "TTP-2026-0197"

def test_six_steps_all_macos_direct(scenario):
    steps = scenario["steps"]
    assert [s["id"] for s in steps] == [f"step-0{i}" for i in range(1, 7)]
    assert all(s["platforms"] == ["macos"] for s in steps)
    assert all(s["identity"] == "direct" for s in steps)
    assert scenario["execution_identity"] == {"default": "direct", "options": ["direct"]}

def test_zero_marker_only_steps(scenario, coverage_report):
    assert [s["id"] for s in scenario["steps"] if coverage_report.is_marker_only_step(s)] == []

def test_no_exit_masking(scenario):
    for s in scenario["steps"]:
        for bad in ("|| true", "|| echo", "; true", "2>/dev/null ||"):
            assert bad not in s["command"], (s["id"], bad)

def test_nine_step_detections_one_correlation(scenario):
    dets = [d for s in scenario["steps"] for d in s["expected_detections"]]
    assert len(dets) == 9
    assert [d["detection_id"] for d in dets if d["type"] == "Correlation"] == ["CR-KOI-0009"]
    assert scenario["steps"][-1]["expected_detections"][-1]["detection_id"] == "CR-KOI-0009"
    assert all(d["ttp_ref"] == "TTP-2026-0197" for d in dets)
    assert all(d.get("outcome", "detect") == "detect" for d in dets)

def test_every_detection_id_resolves_to_the_card(scenario, catalog):
    for s in scenario["steps"]:
        for d in s["expected_detections"]:
            assert catalog.find("TTP-2026-0197", d["detection_id"]) is not None, d["detection_id"]

def test_no_indicators_block(scenario):
    assert "indicators" not in scenario

def test_causality_spine_is_connected_and_matches_spec(scenario):
    validate_causality_spine(scenario)          # raises on forward/self refs or two roots
    by_id = {s["id"]: s for s in scenario["steps"]}
    assert "causality" not in by_id["step-01"]
    assert by_id["step-02"]["causality"] == {"parent_step": "step-01", "pivot": "shared_entity"}
    for sid in ("step-03", "step-04", "step-05"):
        assert by_id[sid]["causality"] == {"parent_step": "step-02", "pivot": "process_lineage"}
    assert by_id["step-06"]["causality"] == {"parent_step": "step-05", "pivot": "network_session"}
    assert scenario["cgo_anchor"]["primary_username"] == "developer"

def test_step01_references_the_pack_component(scenario):
    assert "sources/cortex-malicious-agentic-pack/vscode/prettier-pro-canary" in scenario["steps"][0]["command"]

def test_cleanup_removes_everything_it_created(scenario):
    joined = "\n".join(scenario["cleanup"]["commands"])
    for needle in ("launchctl bootout", "LaunchAgents", "delete-keychain",
                   "--uninstall-extension", "CORTEXSIM"):
        assert needle in joined, needle

def test_refs_and_methodology(scenario):
    assert scenario["uc_ref"] == "UCS-SCA-01" and scenario["tc_ref"] == "TC-SCA-01"
    assert scenario["tc_refs"] == ["TC-SCA-01", "TC-SSCAN-01"]
    assert scenario["pov_scenario_id"] == "POV-SC-012"
    assert scenario["methodology_family"] == "F1" and scenario["primary_kpi"] == "Detection Accuracy"
    assert scenario["threshold"] == {"kpi": "Detection Accuracy", "op": "≥", "value": 90, "unit": "%"}
    assert scenario["mitre_technique"] == "T1195"
    assert {t["technique"] for t in scenario["additional_techniques"]} == {
        "T1059.002", "T1543.001", "T1555.001", "T1552.001", "T1074.001", "T1560", "T1041"}

def test_lint_hook_says_ok(repo_root):
    out = subprocess.run([sys.executable, str(HOOK), str(SCENARIO), str(repo_root)],
                         capture_output=True, text=True)
    assert out.returncode == 0 and "OK" in out.stdout, out.stdout + out.stderr
```

- [ ] **Step 2: Run to verify it fails against the scaffold**

Run: `PYTHONPATH=core .venv/bin/python -m pytest tests/engine/test_sim_koi_009_shape.py -q`
Expected: FAIL — at least `test_status_active_and_ids`, `test_six_steps_all_macos_direct`, `test_zero_marker_only_steps` (the scaffold's two steps are echo-only) and `test_cleanup_removes_everything_it_created`.

- [ ] **Step 3: Commit the red test**

```bash
git add tests/engine/test_sim_koi_009_shape.py
git commit -m "test(scenarios): SIM-KOI-009 shape guard (red against the intake scaffold)"
```

---

### Task 2: Pack component `prettier-pro-canary` with its rules test

**Files:**
- Create: `tests/eal_simulator/test_agentic_pack_prettier_pro_canary.py`
- Create: `sources/cortex-malicious-agentic-pack/vscode/prettier-pro-canary/extension/package.json`
- Create: `sources/cortex-malicious-agentic-pack/vscode/prettier-pro-canary/extension/dist/extension.js`
- Modify: `sources/cortex-malicious-agentic-pack/README.md` (layout table row, after the `helpful-ai-assistant` row)

**Interfaces:**
- Produces (contract Task 4 relies on): extension identifier `cortexsim-canary.prettier-pro-canary`; `displayName` `"Prettier Pro"`; `activationEvents: ["*"]`; a `vscode.window.registerUriHandler` whose accepted URI paths are exactly `/step-03`, `/step-04`, `/step-05`, `/step-06`; environment variables read by the component: `CORTEXSIM_KOI009_DECOY_ROOT` (the decoy tree every path is derived from) and `CORTEXSIM_C2_URL` (the only outbound gate, default unset → no network); every file contains the literal `CORTEXSIM-CANARY`.

- [ ] **Step 1: Write the failing rules test**

```python
COMPONENT = REPO_ROOT / "sources" / "cortex-malicious-agentic-pack" / "vscode" / "prettier-pro-canary" / "extension"
FORBIDDEN = ("require(\"crypto\")", "require('crypto')", "setTimeout", "setInterval", "atob(",
             "Buffer.from(", ".aws/credentials", ".ssh/id_rsa", "login.keychain", "/Users/")

def check_source_rules(src: str) -> None:
    """The rule set, as one function, so the negative control exercises the real check."""
    assert "CORTEXSIM-CANARY" in src
    assert "registerUriHandler" in src
    for path in ("/step-03", "/step-04", "/step-05", "/step-06"):
        assert path in src
    assert "CORTEXSIM_KOI009_DECOY_ROOT" in src and "CORTEXSIM_C2_URL" in src
    for tok in FORBIDDEN:
        assert tok not in src, tok
    hosts = set(re.findall(r"https?://([A-Za-z0-9.-]+)", src))
    assert hosts <= {"cortexsim-canary.invalid"}, hosts

def test_manifest_shape():
    m = json.loads((COMPONENT / "package.json").read_text())
    assert m["publisher"] == "cortexsim-canary" and m["name"] == "prettier-pro-canary"
    assert m["displayName"] == "Prettier Pro" and m["activationEvents"] == ["*"]
    assert "CORTEXSIM-CANARY" in m["description"]

def test_source_rules():
    check_source_rules((COMPONENT / "dist" / "extension.js").read_text())

@pytest.mark.parametrize("tok", FORBIDDEN + ("https://example.invalid/x",))
def test_rules_fail_on_a_broken_copy(tok):
    """Gate A evidence: the real check is observed failing on a copy that breaks one rule."""
    broken = (COMPONENT / "dist" / "extension.js").read_text() + f"\n// {tok}\n"
    with pytest.raises(AssertionError):
        check_source_rules(broken)
```

- [ ] **Step 2: Run to verify it fails (component absent)**

Run: `.venv/bin/python -m pytest tests/eal_simulator/test_agentic_pack_prettier_pro_canary.py -q`
Expected: FAIL with `FileNotFoundError` on `package.json`.

- [ ] **Step 3: Author the component per spec §6**

Two files, same layout as `vscode/helpful-ai-assistant/`. The executor writes the activation and the four URI-handled actions from spec §5's "safe form" column and §6's five rules; each action derives every path from `CORTEXSIM_KOI009_DECOY_ROOT`, writes the canary marker and returns when that variable is unset, and performs no outbound call unless `CORTEXSIM_C2_URL` is set. This plan carries no further detail by design.

- [ ] **Step 4: Run the rules test to verify it passes**

Run: `.venv/bin/python -m pytest tests/eal_simulator/test_agentic_pack_prettier_pro_canary.py -q`
Expected: PASS (all, including the parametrized broken-copy cases).

- [ ] **Step 5: Confirm the existing egress tests still pass**

Run: `.venv/bin/python -m pytest tests/eal_simulator/test_plugin_agentic_egress.py tests/eal_simulator/test_plugins.py -q`
Expected: PASS, unchanged counts.

- [ ] **Step 6: Add the README row and commit**

```bash
git add sources/cortex-malicious-agentic-pack/vscode/prettier-pro-canary sources/cortex-malicious-agentic-pack/README.md tests/eal_simulator/test_agentic_pack_prettier_pro_canary.py
git commit -m "feat(koi): prettier-pro-canary pack component (decoy-only, URI-triggered)"
```

---

### Task 3: Card `TTP-2026-0197` — detections, IOCs, mapping

**Files:**
- Modify: `detection_scanner/ttps/TTP-2026-0197-sim-koi-009.json` (rewrite the scaffold; mirror `TTP-2026-0138-sim-koi-008.json` field for field)

**Interfaces:**
- Produces: the detection **names** below, verbatim. Task 4 binds by the slug `engine.ttp_catalog._slug(name, prefix)` derives (lowercase, non-alphanumerics → `-`, runs collapsed, prefix-tagged). Rename a detection and Task 1's resolver test goes red.

| detections list | name (verbatim) | slug Task 4 uses | dataset | MITRE |
|---|---|---|---|---|
| `xql_queries` | `KOI-009 VS Code Extension Install Creates Unknown Publisher Extension Directory` | `xql-koi-009-vs-code-extension-install-creates-unknown-publisher-extension-directory` | `xdr_data` | T1195 |
| `abiocs` | `KOI-009 IDE Extension Host Spawns Scripting Host For First Seen Publisher` | `abioc-koi-009-ide-extension-host-spawns-scripting-host-for-first-seen-publisher` | `xdr_data` | T1059.002, T1195 |
| `biocs` | `KOI-009 Keychain Access By IDE Descendant` | `bioc-koi-009-keychain-access-by-ide-descendant` | `xdr_data` | T1555.001 |
| `biocs` | `KOI-009 LaunchAgent Written And Loaded From IDE Lineage` | `bioc-koi-009-launchagent-written-and-loaded-from-ide-lineage` | `xdr_data` | T1543.001 |
| `biocs` | `KOI-009 Multi Credential File Read Under IDE CGO` | `bioc-koi-009-multi-credential-file-read-under-ide-cgo` | `xdr_data` | T1552.001 |
| `xql_queries` | `KOI-009 Staging Directory Created Under IDE Lineage` | `xql-koi-009-staging-directory-created-under-ide-lineage` | `xdr_data` | T1074.001 |
| `xql_queries` | `KOI-009 Archive Sent From IDE Lineage To Non Marketplace Host` | `xql-koi-009-archive-sent-from-ide-lineage-to-non-marketplace-host` | `xdr_data` | T1560, T1041 |
| `xql_queries` | `KOI-009 NGFW Session From IDE Host To Non Marketplace Destination` | `xql-koi-009-ngfw-session-from-ide-host-to-non-marketplace-destination` | `panw_ngfw_traffic_raw` | T1041 |
| `correlation_rules` | rule_id `CR-KOI-0009` | `CR-KOI-0009` | — | — |

- [ ] **Step 1: Rewrite the card**

Values the spec pins: `status: active`, `entry_version: 1.0.0`, `metadata.pov_engine.auto_load: true`, `platforms: ["macos"]`, `execution.target_platform: "macos"`, `simulation_class: "supply-chain"`, `safety_class: "safe-by-design"`, `requires_network_egress: false`. `mitre_attack.techniques` = T1195 (TA0001) plus the seven additional techniques from spec §8. Every `xql_queries[]`, `biocs[]` and `abiocs[]` body opens with `dataset = xdr_data` (the NGFW row with `dataset = panw_ngfw_traffic_raw`), keys on `causality_actor_process_image_name` as a **set** of the VS Code main-process and extension-host image names plus `causality_id`, and never on a username. ABIOC rows carry `behavioral_profile: "machine-learning"` and a `causality_anchor`. `CR-KOI-0009.logic` names the five detections 02→06 in `FOLLOWED_BY` order stitched by `agent_hostname` + `causality_id`, as `CR-KOI-0008` does. `detections.iocs[]`: the two `ipv4` servers, the `url` exfil endpoint, the three extension identifiers as `filename`, the staging directory as `filepath`; `context` on each, no `source_url`. `analytics_modules[]`: one Cortex Cloud Code line for the unknown-publisher `activationEvents: ["*"]` manifest, one line stating the encrypted-loader/timer code-scan detection is **described, not exercised** (spec §2 non-goal). `panw_mapping.products[]`: the leg-A row from Global Constraints; `cortex-xdr` / `behavioral-analytics` / `detection`; `cortex-xsiam` / `analytics` / `detection` with `XSIAM-CR-KOI-0009`; `cortex-cloud` / `code-security` / `prevention`. `references[]`: the PANW SecOps post as primary (keep the scaffold's URL), MITRE, TechDocs.

- [ ] **Step 2: Validate the corpus**

Run: `python3 detection_scanner/scripts/validate.py --quiet`
Expected: last line `--- N pass, 0 warn, 0 fail ---` with N = 386 + the new card's checks; zero WARN (a WARN on an unknown dataset or a placeholder token is a defect, not noise).

- [ ] **Step 3: Regenerate exports and confirm determinism**

Run: `python3 detection_scanner/scripts/export_artifacts.py --clean && git status --porcelain -- detection_scanner/exports/`
Expected: only `A`/`??` lines for `TTP-2026-0197.*` and the exports README; nothing `M` for other cards.

- [ ] **Step 4: Commit**

```bash
git add detection_scanner/ttps/TTP-2026-0197-sim-koi-009.json detection_scanner/exports
git commit -m "feat(ttp): TTP-2026-0197 GlassWorm agentic-endpoint card (macOS, AES/XDR)"
```

---

### Task 4: Scenario `SIM-KOI-009` — metadata, bindings, steps

**Files:**
- Modify: `scenarios/koi/sim-koi-009-glassworm-agentic-endpoint-takeover.yml` (rewrite the scaffold)

**Interfaces:**
- Consumes: Task 2's contract (identifier, URI paths, env variables); Task 3's slugs.

- [ ] **Step 1: Write the metadata**

`name: "KOI — GlassWorm Poisoned IDE Extension on a macOS Developer Endpoint (install → activation → Keychain → LaunchAgent → credential staging → send)"`, `version: "1.0"`, `status: active`, `plane: KOI`, `detection_types: [ABIOC, BIOC, XQL, Correlation]`, the refs/methodology/threshold/MITRE values asserted in Task 1, `moat_tier: MOAT`, `threat_report` + `threat_report_url` from the scaffold, `required_content: [{repo: "hankthebldr/cortex-malicious-agentic-pack"}]`, `infra_modules_needed: [base]`, `external_tools` = the `TOOL-CORTEX-AGENTIC-PACK` entry as in SIM-KOI-004, `cgo_anchor: {image_name: <VS Code main-process image, verified on the lab Mac>, primary_username: developer}`, `success_criteria` stating leg A ("observe the extension and its risk score in AES; not machine-scored") separately from leg B, and the `tenant-verified: 0` sentence. `author`, `created: "2026-10-28"`, `last_updated`, `tags: [koi, vscode, extension, supply-chain, macos, glassworm, causality, aes]`.

- [ ] **Step 2: Write the six steps and the cleanup**

Per spec §5: six steps, each `platforms: ["macos"]`, `identity: direct`, the `mitre_technique` of its row, the `causality` block Task 1 asserts, and `expected_detections` bound to Task 3's slugs in this distribution — 01: the install XQL; 02: the ABIOC; 03: the Keychain BIOC; 04: the LaunchAgent BIOC; 05: the multi-credential BIOC + the staging XQL; 06: the NDR XQL (`plane: NDR`), the archive-sent XQL, then `CR-KOI-0009` last. Each `description` names what the sensor records (spec §5 column 3) and, on step 01, adds "observe in AES: inventory + risk score". **Commands:** the executor authors them from spec §5's "safe form" column in the SAFE-MODE style of `sim-koi-008-*.yml`; step 02 launches the IDE with `CORTEXSIM_KOI009_DECOY_ROOT` exported; steps 03–06 each open `vscode://cortexsim-canary.prettier-pro-canary/<step-id>` after planting that step's decoys; no step masks its exit. `cleanup.commands` remove, in order: the LaunchAgent (unload, then delete), the decoy keychain, the extension (`--uninstall-extension`), the decoy root, and the installed `.vsix`.

- [ ] **Step 3: Run the lint hook**

Run: `python3 .claude/hooks/lint-scenario.py scenarios/koi/sim-koi-009-glassworm-agentic-endpoint-takeover.yml .`
Expected: `OK: …` with no `marker-only step` warning.

- [ ] **Step 4: Run the shape guard**

Run: `PYTHONPATH=core .venv/bin/python -m pytest tests/engine/test_sim_koi_009_shape.py -q`
Expected: PASS, all tests.

- [ ] **Step 5: Confirm the coverage floors hold**

Run: `make coverage-strict`
Expected: exit 0; the JSON's `signal_quality.marker_only_steps` is still 100 and `steps` is 796.

- [ ] **Step 6: Commit**

```bash
git add scenarios/koi/sim-koi-009-glassworm-agentic-endpoint-takeover.yml
git commit -m "feat(scenarios): SIM-KOI-009 GlassWorm agentic-endpoint takeover on macOS (active)"
```

---

### Task 5: Crosswalk, count pins, docs, plane, adapter, runbook

**Files:**
- Modify: `scripts/uctc_crosswalk_v2.2.py` — add after the `"SIM-KOI-008"` entry: `"SIM-KOI-009": (["TC-SCA-01", "TC-SSCAN-01"], NETNEW, "GlassWorm poisoned IDE extension executing, persisting and collecting developer credentials on a macOS endpoint.")`; add a NETNEW row after `TC-AEPS-08`: `tc_id TC-AEPS-09`, `uc_id UC-AEPS`, `ucs_id UCS-AEPS-02`, `use_case "Agentic Endpoint Security"`, `scenario "Agentic Supply-Chain Compromise"`, `title "Validate detection of a poisoned IDE extension update that executes, persists and collects developer credentials on a macOS endpoint"`, `primary_kpi "Detection Accuracy"`, `threshold ">90%"`, `detection_source "XSIAM (XQL: dataset=xdr_data)"`, `scenario_id "SIM-KOI-009"`, `rationale "The first KOI scenario that lands and runs on an endpoint; TC-AEPS-04..08 cover fetch and scan paths only."`
- Modify: `tests/api/test_uctc_index.py:65` and `tests/engine/test_corpus_refs_strict.py:65` — `192` → `193`, docstring line `192 -> 193 on 2026-10-28: SIM-KOI-009 (GlassWorm agentic endpoint, binds already-evidenced TC-SCA-01/TC-SSCAN-01; evidenced pins unchanged).`
- Modify: `README.md` lines 8, 26, 196, 197, 646; `docs/reference/README.md:118`; `docs/reference/scenario-catalog.md:5` and the KOI section at line 231 (heading count → 9, add the SIM-KOI-009 row in the table's column order); `CLAUDE.md:269` (KOI row: 9 scenarios, add `SIM-KOI-009` clause) and the canonical-count paragraph at line 279 (prepend a dated 193/191 baseline sentence; keep prior baselines).
- Modify: `scenarios/koi/README.md` (new paragraph: SIM-KOI-009 is the first endpoint-resident scenario; AES leg is observed, not scored); `core/planes/koi.py` `key_techniques` + `"T1543.001"`, `"T1555.001"` with comments; `tools/packs/cortex-agentic-pack.yml` `ttp_refs` + `TTP-2026-0197`.
- Modify: `docs/reference/lab-runbook.md` — insert `## Agentic endpoint (macOS)` after "Enrolling a beacon" (before "Launching a run", line 279) with spec §7's six numbered prerequisites verbatim in intent, plus one line each for Review Focus 1, 2, 4 and 5.

- [ ] **Step 1: Make the crosswalk edits and regenerate its outputs**

Run: `python3 scripts/uctc_crosswalk_v2.2.py --report && python3 scripts/uctc_crosswalk_v2.2.py --emit && python3 scripts/uctc_crosswalk_v2.2.py --emit-xlsx`
Expected: report shows 193 rows / 0 unresolved; `git status --porcelain` lists `docs/uc_tc_mapping/crosswalk-v2.2.csv`, `docs/uc_tc_mapping/_v2.2-source/engine_coverage_v2.3.csv`, the xlsx and `scoreboard.md` as modified.

- [ ] **Step 2: Run the two count tests and the sheet gate**

Run: `PYTHONPATH=core .venv/bin/python -m pytest tests/api/test_uctc_index.py tests/engine/test_corpus_refs_strict.py -q && make check-uctc-sheet`
Expected: PASS; sheet gate exit 0.

- [ ] **Step 3: Make the doc, plane, adapter and runbook edits**

Then: `grep -rn '\b192 scenarios\|\b192 loadable\|190 TTP' README.md docs/reference/README.md docs/reference/scenario-catalog.md CLAUDE.md` → no output.

- [ ] **Step 4: Commit in two boundaries**

```bash
git add scripts/uctc_crosswalk_v2.2.py docs/uc_tc_mapping tests/api/test_uctc_index.py tests/engine/test_corpus_refs_strict.py
git commit -m "feat(uctc): SIM-KOI-009 crosswalk row + net-new TC-AEPS-09; count pins 192 -> 193"
git add README.md docs/reference/README.md docs/reference/scenario-catalog.md CLAUDE.md scenarios/koi/README.md core/planes/koi.py tools/packs/cortex-agentic-pack.yml docs/reference/lab-runbook.md
git commit -m "docs: SIM-KOI-009 in catalogs and counts; macOS agentic-endpoint lab rung"
```

---

### Task 6: Golden digests, ground truth, full gates

**Files:**
- Modify: `tests/engine/_golden/push_bundle_digests.json` (one new key `SIM-KOI-009`)
- Modify: `docs/reference/ground-truth.json`, `docs/reference/ground-truth.md` (regenerated)

- [ ] **Step 1: Add the golden entry exactly as the invariant test computes it**

With the adapter catalog loaded (`tools.adapter_catalog.catalog.load("tools/packs")`, as the test's autouse fixture does), insert one key `"SIM-KOI-009": {"bash": <sha256 of generate_bash(data)>, "powershell": null}` — `null` because `resolve_target(data, "windows").emittable` is false for a macOS-only scenario, which is exactly what `test_bash_and_powershell_bundles_are_byte_identical` checks per format. Diff must touch one key.

Run: `PYTHONPATH=core .venv/bin/python -m pytest tests/engine/test_push_generator_invariant.py -q`
Expected: PASS, including `test_golden_file_covers_the_corpus` and `test_bundles_never_reference_simcore`.

- [ ] **Step 2: Regenerate ground truth in the image**

Run: `make build && make ground-truth && git diff --stat docs/reference/ground-truth.json docs/reference/ground-truth.md`
Expected: `scenarios_loadable: 193`, TTP cards 191, KOI 9; `make check-ground-truth` then exits 0.

- [ ] **Step 3: Run every gate the spec names**

Run, in order: `make validate`; `make coverage-strict`; `PYTHONPATH=core .venv/bin/python -m pytest tests/ --ignore=tests/smoke --ignore=tests/e2e_isolated -q`; `PYTHONPATH=core .venv/bin/python -m pytest tests/e2e_isolated -q`.
Expected: each exit 0; host suite previous pass count + the new tests; e2e_isolated unchanged.

- [ ] **Step 4: Commit**

```bash
git add tests/engine/_golden/push_bundle_digests.json docs/reference/ground-truth.json docs/reference/ground-truth.md
git commit -m "chore: golden digests and ground truth for SIM-KOI-009 (193 scenarios / 191 cards)"
```

---

### Task 7: Lab execution record (only with a macOS lab endpoint available)

**Files:**
- Modify: `docs/reference/lab-runbook.md` (the macOS rung's "Last executed" line)

- [ ] **Step 1: Enroll and launch in pull mode per the rung**

Expected: six `Result` rows with `executed_at`; `GET /api/runs/{id}` terminal `completed`; cleanup leaves no plist, no decoy keychain, no extension directory.

- [ ] **Step 2: Record the outcome honestly**

Write the date, beacon version, macOS version, which of legs A/B/C were observed, and `tenant-verified: 0` unless `/reconcile` against a tenant produced matched observations. Commit with `docs(runbook): SIM-KOI-009 first lab execution record`.

If no lab Mac exists, write `Last executed: never` and leave the task open; the scenario is authored, not proven.
