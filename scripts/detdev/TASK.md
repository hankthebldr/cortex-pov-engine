# Extended pass — Windows & Linux endpoint ANALYTICS detections

**Goal.** Author Cortex **Analytics / ABIOC** (behavioral) detections for the Windows
and Linux endpoint (**XDR Agent** data source) analytics alerts in the two target
lists, as new TTP cards + scenarios that pass the repo gates. This is the ML/behavioral
layer (rare / uncommon / abnormal / first-seen) — distinct from the deterministic BIOCs
already in the corpus.

Run from the repo root: `~/Github/cortex-pov-engine`. **Review-gated: never push, never
open a PR.** Leave everything on disk for Henry to review.

## Targets
- `scripts/detdev/targets-windows-analytics.txt` — 291 Windows XDR-Agent analytics alerts (`name<TAB>url`)
- `scripts/detdev/targets-linux-analytics.txt`   — 22 Linux XDR-Agent analytics alerts

Prioritise high-value, attack-mappable behavioral detectors first, e.g. Windows:
*masquerading as a Microsoft product · rare DLL hijacked into a Microsoft process ·
abnormal RDP to multiple hosts · unsigned process created scheduled task + injection ·
command execution via wmiexec / PsExec · copy a process memory file (LSASS) ·
PowerShell base64 · suspicious ntds.dit dump*. Linux: *possible brute force on sudo user ·
masquerading as crond · setuid/setgid bit manipulation · reading bash history ·
binary padding using dd · compressed file exfil over SSH · nsenter container escape ·
privileged-container recon*. Do **2–4 per batch**, verify, then continue.

## The reliable pipeline (per target)
Use the **gateway coder model** — fast and reliable. Do NOT use the opencode agent /
cpp-offsec for bulk (reasoning model: slow, hangs, 2000-line Read cap).

1. **Generate content** (cpp-coder via LiteLLM gateway :4000):
   ```
   python3 scripts/detdev/genfrag.py <FRAG.json> edr "<brief naming the attack + that it should
       trigger the '<Cortex alert name>' analytics/ABIOC>" <T-ids,comma> cpp-coder
   ```
   The fragment carries: name, description, mitre_technique_ids, execution_payload,
   expected_artifacts, biocs, **abiocs** (behavioral: behavioral_profile + causality_anchor),
   iocs, xql_queries, steps.
2. **Set platform + the analytics-module reference** (deterministic — you know the exact alert):
   ```python
   import json; f=json.load(open("<FRAG.json>"))
   f["target_platform"]="windows"   # or "linux"
   f["analytics_modules"]=["Cortex XSIAM Analytics (XDR Agent) — <exact alert name>; Required Data: XDR Agent; ATT&CK <T-id>. Ref: <url>"]
   json.dump(f,open("<FRAG.json>","w"),indent=2)
   ```
3. **Package into a valid card + scenario** (clones a platform-matched exemplar; fills schema):
   ```
   python3 scripts/detdev/package.py <FRAG.json> <EX_CARD> <EX_SCN> <CARD_PATH> <SCN_PATH> <NEXT_TTP> <SIM_ID> edr
   ```
   - **Windows exemplar:** `detection_scanner/ttps/TTP-2026-0002-*.json` + `scenarios/edr/edr-002-*.yml`
   - **Linux exemplar:**   `detection_scanner/ttps/TTP-2026-0032-*.json` + `scenarios/edr/edr-001-*.yml`
   - `NEXT_TTP` = max `TTP-2026-NNNN` + 1; `SIM_ID`/paths = next free `edr-NNN` (use `10#` in bash arithmetic — leading-zero = octal bug).
4. **Regenerate + validate:**
   ```
   python3 detection_scanner/scripts/export_artifacts.py
   python3 detection_scanner/scripts/validate.py --quiet     # must end "0 fail"
   ```
5. After a batch: `make check-refs` (docker; validates scenario refs + detection_id resolution),
   and **bump the size guard** in `tests/engine/test_corpus_refs_strict.py`
   (`assert len(parsed) == N` → N + scenarios added, add a dated comment line).

## Conventions & gotchas (learned the hard way — honor these)
- **XQL/BIOC/ABIOC logic MUST start `dataset = xdr_data`.** Aggregation verb is **`comp`**
  (`| comp count() as n by ...`). The model often emits `stats`/`aggregate`/`summarize`/
  `groupby`/bare `count` — normalize them to `comp` or validate WARNs.
- **detection_id is auto-derived** as `<kind>-<slug(name)>` (lowercased, non-alnum→`-`).
  Do NOT put an explicit `detection_id` on a card XQL object (schema forbids it). In the
  scenario, set each `expected_detections[].detection_id` to the auto-slug of the card
  detection's `name` (type `Analytics` points at an `xql-...` or `abioc-...` id).
- **Schema enums:** `execution.execution_framework` ∈ {atomic-red-team, custom-cortex, …}
  (use `custom-cortex`). `expected_artifacts[].artifact_type` ∈ {process, file, network-connection,
  dns-query, auth-event, …} — **no `log`**. `ioc_type` has no `command`/`username` — drop or map.
  `references[]` need `publisher`, and one entry needs `primary: true`. `detections` is strict —
  no extra keys (`_detection_notes` is rejected).
- **analytics_modules** entries may be plain strings (NDR/EDR cards) or `{module,note,validated}` dicts.
- **Safe-detonation doctrine:** reproduce telemetry SHAPE only; no live malware exec; TEST-NET-1
  (192.0.2.0/24) for C2; real malware SHA-256 tracked as `file-sha256` IOCs, never executed.
- The cloned exemplar's `threat_context`/`panw_mapping`/top-level scenario fields carry the
  exemplar's subject — **retarget them to the alert** (or at minimum flag for review).

## Done means
validate `0 fail`, `make check-refs` green, size guard bumped, detection_ids resolve, and each
card has: the behavioral **ABIOC** keyed to the attack, the **analytics_modules** reference to the
named Cortex alert, supporting XQL on `dataset=xdr_data`, and `execution.payload` that produces
the telemetry. Then stop — Henry reviews the XQL dialect + detonation payloads before landing.
