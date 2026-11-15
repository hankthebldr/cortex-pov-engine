# Extended pass — Windows & Linux endpoint ANALYTICS detections

**Goal.** Draft Cortex **Analytics / ABIOC** (behavioral) detections for the Windows
and Linux endpoint (**XDR Agent** data source) analytics alerts in the two target
lists, as draft TTP cards + scenarios. This is the ML/behavioral layer (rare /
uncommon / abnormal / first-seen), distinct from the deterministic BIOCs already in
the corpus.

**Output is a staged draft, never corpus content.** Every pair is written under
`drafts/detection-library/` with a provisional id and `status: draft`. Nothing in
this pipeline writes into `scenarios/` or `detection_scanner/ttps/`, bumps a corpus
pin, or regenerates an export. A draft reaches the corpus by promotion, one reviewed
pair at a time: `drafts/detection-library/README.md`.

Run from the repo root. **Review-gated: never push, never open a PR.** Leave
everything on disk for Henry to review.

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
privileged-container recon*. Do **2–4 per batch**, check each, then continue.

## The pipeline (per target)
Use the **gateway coder model**. Do NOT use the opencode agent / cpp-offsec for bulk
(reasoning model: slow, hangs, 2000-line Read cap). The gateway key comes from the
environment (`LITELLM_MASTER_KEY`).

1. **Generate the fragment** (cpp-coder via the LiteLLM gateway, `DETDEV_GATEWAY_URL`,
   default `http://localhost:4000`):
   ```
   python3 scripts/detdev/genfrag.py <FRAG.json> edr "<brief naming the attack and the alert it
       should raise>" <T-ids,comma or ""> cpp-coder --platform windows --alert "<exact alert name>"
   ```
   `--platform` (`linux` | `windows` | `macos`) sets the shell the prompt asks for and
   is recorded in the fragment. `--alert` must be a name from the target lists; the
   fragment then records that alert and its listed URL as its source. Do not type the
   reference by hand.
2. **Package it into a staged draft pair:**
   ```
   python3 scripts/detdev/package.py <FRAG.json>           # preview: paths, source, rows
   python3 scripts/detdev/package.py <FRAG.json> --apply   # writes the next free pair
   ```
   The packager builds both files from the fragment and its source only. It clones no
   exemplar, so there is nothing to retarget afterwards.
3. **Refresh the manifest** after a batch:
   ```
   python3 scripts/detdev/manifest.py --apply
   ```
   Read the batch's rows in `drafts/detection-library/INDEX.md`. A finding there
   (a step that declares nothing, a placeholder command, a routable host) is the
   fragment's fault: regenerate the fragment rather than hand-editing the pair.

## What the packager decides (do not work around it)
- **Source** is the listed alert, or "not recorded". A reference typed into
  `analytics_modules` counts only if the target list confirms the alert name.
- **A hash IOC needs a `source_url`.** One without is dropped. Never ask the model
  for a "realistic" hash; an invented hash is a false claim about a real file.
- **Identity and platform** come from `--platform`: Windows steps run as `direct`,
  Linux and macOS as `root`, and every step is labelled with the platform.
- **Cleanup** in the scenario is a placeholder that says it is not authored. The
  model's `cleanup_commands` are kept on the card as proposed and not run.
- **UC/TC refs, the measurement contract, threat context and remediation are not
  written.** They are authored at promotion.

## Conventions the model gets wrong
- **XQL/BIOC/ABIOC logic MUST start `dataset = xdr_data`.** The aggregation verb is
  **`comp`** (`| comp count() as n by ...`). The model often emits `stats` /
  `aggregate` / `summarize` / `groupby` / bare `count`.
- Use `actor_causality_id`, not bare `causality_id`, and
  `timestamp_diff(current_time(), _time, "SECOND") < N`, not
  `to_timestamp(current_time() - N)`. The tenant rejects the other forms.
- **detection_id is derived** as `<kind>-<slug(name)>`. A step's `detects` names must
  be exact detection names from the same fragment; a name that matches nothing is
  dropped by the packager.
- **Schema enums:** `expected_artifacts[].artifact_type` ∈ {process, file,
  network-connection, dns-query, auth-event, …}, no `log`. `ioc_type` has no
  `command` / `username`.
- **Commands** are real and run as written, inside the scratch directory the prompt
  names, with network destinations in TEST-NET-1 (192.0.2.0/24) or `*.invalid`. No
  real malware download, no placeholder words.

## Done means
Each pair of the batch is staged, the manifest is current
(`python3 scripts/detdev/manifest.py` prints `on disk: current`), and
`python -m pytest tests/scripts/test_detdev_library.py` passes. Then stop: Henry
reviews before anything is promoted.
