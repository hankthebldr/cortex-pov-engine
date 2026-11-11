# Linux-endpoint test campaign — pov-target-01 (deathstar)

Working ledger. Generated 2026-11-11 from the corpus classifier. **tenant-verified stays 0** until a run is read back from the tenant; this doc records what is authored vs what a lab run proved.

Target: standalone Cortex XDR agent on `pov-target-01` (beacon `pov-target-01-4dcdce`), report-mode policy `SEBC-FY25-LAB-LinuxServerPolicy`. Endpoint detections come from the XDR agent recording local process/file/network causality. Non-endpoint planes (network/identity/cloud/AI/browser/email) POST to a collector or real SaaS and will NOT raise agent detections on this VM — they need the HTTP collector or a real integration, tracked separately.

## Scope decisions (Henry, 2026-11-11)
- Real tools, real scripts — these are emulation test environments.
- **Skip service-account provisioning** on the VM. Bucket B below is therefore run as root-identity draft *forks* of the library scenario (same commands/tools), with the lost service-account causality logged as a library-update finding — the originals are unchanged.
- Reuse and test the existing library; note what hardening each scenario needs.

## Bucket A — endpoint, runnable now (20): root / direct / container-runtime, no k8s

| Scenario | Plane | Steps | Real | Tools | Run | Result (issues / case) |
|---|---|---|---|---|---|---|
| SIM-CDR-003 | CDR | 5 | 5 | TOOL-DEEPCE | | |
| SIM-CDR-006 | CDR | 3 | 3 | — | | |
| SIM-CDR-008 | CDR | 3 | 2 | — | | |
| SIM-CDR-010 | CDR | 4 | 3 | — | | |
| SIM-CDR-012 | CDR | 4 | 3 | — | | |
| SIM-CDR-013 | CDR | 3 | 1 | — | | |
| SIM-CDR-014 | CDR | 4 | 3 | — | | |
| SIM-CDR-016 | CDR | 3 | 2 | — | | |
| SIM-CDR-018 | CDR | 4 | 2 | — | | |
| SIM-CDR-019 | CDR | 3 | 2 | — | | |
| SIM-CDR-020 | CDR | 3 | 3 | — | | |
| SIM-CDR-028 | CDR | 1 | 1 | — | | |
| SIM-EDR-003 | EDR | 5 | 5 | TOOL-ATOMIC-RED-TEAM | | |
| SIM-EDR-004 | EDR | 5 | 5 | TOOL-ATOMIC-RED-TEAM | | |
| SIM-EDR-007 | EDR | 3 | 3 | — | | |
| SIM-EDR-008 | EDR | 3 | 3 | — | | |
| SIM-EDR-009 | EDR | 4 | 3 | — | | |
| SIM-EDR-019 | EDR | 7 | 0 | — | | |
| SIM-EDR-023 | EDR | 4 | 4 | — | | |
| SIM-EDR-024 | EDR | 4 | 4 | — | | |

## Bucket B — endpoint, needs a service account (20) — run as root-fork, log the identity gap

| Scenario | Plane | Accts | Tools | Run | Result |
|---|---|---|---|---|---|
| SIM-CDR-002 | CDR | nobody | — | | |
| SIM-CDR-005 | CDR | www-data | — | | |
| SIM-CDR-017 | CDR | www-data | — | | |
| SIM-CDR-021 | CDR | svc-backup | — | | |
| SIM-CDR-022 | CDR | svc-backup | — | | |
| SIM-CDR-023 | CDR | svc-backup | — | | |
| SIM-CDR-024 | CDR | svc-backup | — | | |
| SIM-CDR-025 | CDR | svc-backup | — | | |
| SIM-CDR-026 | CDR | svc-backup | — | | |
| SIM-CDR-027 | CDR | www-data | — | | |
| SIM-EDR-001 | EDR | www-data | TOOL-ATOMIC-RED-TEAM | | |
| SIM-EDR-002 | EDR | www-data | TOOL-ATOMIC-RED-TEAM | | |
| SIM-EDR-005 | EDR | www-data | TOOL-ATOMIC-RED-TEAM,TOOL-NMAP | | |
| SIM-EDR-010 | EDR | svc-backup | — | | |
| SIM-EDR-011 | EDR | svc-backup | — | | |
| SIM-EDR-012 | EDR | www-data | — | | |
| SIM-EDR-015 | EDR | svc-backup | — | | |
| SIM-EDR-018 | EDR | svc-backup | — | | |
| SIM-EDR-020 | EDR | www-data | — | | |
| SIM-EDR-022 | EDR | www-data | TOOL-LINPEAS,TOOL-PSPY,TOOL-SUID3NUM | | |

## Bucket C — endpoint, needs k8s/container runtime (6) — BLOCKED on the container beacon (feature/agent-k8s-container-agent)

SIM-CDR-001, SIM-CDR-004, SIM-CDR-007, SIM-CDR-009, SIM-CDR-011, SIM-CDR-015

## Out of endpoint scope on this VM (137 non-EDR/CDR Linux scenarios)

These need the HTTP collector (EAL emitters) or a real SaaS/network target; they do not fire XDR-agent detections. Tracked under the collector-targets work, not this campaign.

Counts by plane: AIRS:5, AI_ACCESS:6, AI_SPM:7, ANALYTICS:35, ASM:6, BROWSER:6, CLOUD_APP:10, CSPM:5, DLP:5, EMAIL:5, ITDR:18, KOI:8, NDR:12, TIM:9

## Hardening findings (update the library)

- **F1 — internet-fetch at dispatch.** SIM-EDR-001 (mimipenguin), SIM-CDR-002 (xmrig), SIM-CDR-013 (malware ELFs) `curl`/`wget` their tool from GitHub at run time. On a default-deny customer host that is the first thing blocked, producing a manufactured "Cortex missed it". Move to shelf-staged, digest-pinned delivery like SIM-EDR-022. (8 privesc tools are already on the shelf.)
- **F2 — SIM-CDR-002 points at a live mining pool + installs cron.** `pool.minexmr.com:4444` and a `crontab` entry. Neuter for lab: shelf xmrig, `--dry-run` / no-pool, no cron. Do before any run.
- **F3 — service-account dependency (Bucket B, 20 scenarios).** Hard-depend on www-data/svc-backup/nobody, which do not exist on a bare endpoint, so a pull run fails at the nologin trap. Options: (a) scenario provisions the account in step-01; (b) ship a lab-provision helper; (c) document the precondition. Decide a convention.
- **F4 — custom BIOCs did not fire (SIM-EDR-023 run fac6e8d5).** The 3 EDR-023 BIOCs matched the run's events in XQL but raised no issue; the tenant API (OP-BIOC-GET) lists 0 BIOCs. Likely disabled, saved as XQL not BIOC, or API-invisible. Grouping came from the agent's own malware verdicts (Local Analysis HIGH + WildFire MEDIUM) → case 51896. Confirm the BIOC rule type/enablement.
- **F5 — grouping works off runtime malware verdicts, not custom BIOCs.** The reliable Medium+ trigger on this VM is the agent's WildFire/Local-Analysis verdict on a real malicious file. Design each chain so a real tool at each stage raises its own runtime verdict (the CDR SIM-CDR-013 DeimosC2 pattern; Oct case 49789 grouped 10+ such HIGH verdicts).
- **F6 — Detection Accuracy scoring.** XQL `verification_xql` passes do not count toward Detection Accuracy (only alert-backed observations do), so a run can be fully-observed in telemetry yet score `fail`. Expected, but note it when reading verdicts.


## Run log

| Run | Scenario | Steps rc | What reached the tenant |
|---|---|---|---|
| fac6e8d5 (16:24Z) | SIM-EDR-023 | 4/4 rc 0 | Local Analysis Malware HIGH + WildFire Malware MEDIUM on the cron re-run → **grouped into case 51896** across two causality chains. Real execution. MTTD 46 s. The 3 custom BIOCs did not fire (F4). |
| 0476f603 (18:36Z) | SIM-EDR-003 | 5/5 rc 0 | Largely SAFE-MODE: steps 1/2/4/5 stage to /tmp ("NOT installed/injected/written"); only step-03 `useradd -G sudo cortexsim_testuser` is real → a Low shadow-file ABIOC. No case (all Low). |

## SAFE-MODE audit (F7 — the central hardening finding)

The existing library EDR/CDR scenarios are predominantly **SAFE-MODE**: they stage an artifact to `/tmp` and print "NOT installed / NOT injected / Simulating", so they never perform the malicious action and the XDR agent's runtime detection has nothing to verdict. Of the 20 endpoint-runnable scenarios:

- **REAL (execute for real):** SIM-CDR-003, -008, -012, -016, -028, SIM-EDR-023, -024 (7)
- **MIXED:** SIM-CDR-006, -013, -014, SIM-EDR-007, -008 (5)
- **MOSTLY-SAFE (stage only):** SIM-CDR-010, -018, -019, -020, SIM-EDR-003, -004, -009, -019 (8)

This is why SIM-EDR-023 (built to actually run a real malicious ELF into `/etc/cron.d` with real re-execution) produced HIGH+MEDIUM verdicts and real grouping, while SIM-EDR-003 (stages to `/tmp`) produced one Low ABIOC. **To make the library's endpoint signatures real (Henry, 2026-11-11), the MOSTLY-SAFE and MIXED scenarios need a hardening pass:** execute the staged artifact for real (into real persistence paths), and use a real tool at each stage so the agent raises its own runtime verdict — the SIM-EDR-023 / SIM-CDR-013 pattern. The 8 privesc tools are already shelf-staged for this.
