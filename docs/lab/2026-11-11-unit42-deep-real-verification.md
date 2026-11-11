# Unit 42 deep-real trio — lab verification (2026-11-11)

Branch: `feature/scenarios-unit42-deep-real` (SIM-EDR-025, SIM-KOI-010, SIM-KOI-011).
Target: `pov-target-01` (bare Ubuntu 24.04, XDR agent 9.4.0.470, policy
`SEBC-FY25-LAB-LinuxServerPolicy`, **report mode**). SimCore rebuilt from this
branch on :8888; beacon online; payload shelf 8/8 staged; agentic pack present.

**tenant-verified remains 0.** "Detected in a tenant" below means an alert was
read back by the Cortex MCP oracle (read-only); it does not make any authored
claim proven for POV purposes.

---

## SIM-EDR-025 — Akira (Howling Scorpius) Linux host ransomware

**Executed for real.** Run `46f4394b-a6ad-490b-b56f-607747f50786`, 6/6 steps
rc 0. Step-05 performed genuine `openssl` AES-256 encryption of the staged
victim tree under `/var/tmp/.cortexsim-akira` (~40 files → `*.akira`, RSA-wrapped
key, `akira_readme.txt` dropped), confined and cleaned.

**Endpoint telemetry recorded** (`xdr_data`, 19:30:05Z): `PROCESS_START openssl`
×28, `FILE_WRITE` ×59, `FILE_CREATE_NEW` ×63, `FILE_REMOVE` ×5. The agent saw
every byte of the encryption.

**What the tenant detected** (oracle, `observation_time` 19:30:06Z — one second
after the openssl burst):

| field | value |
|---|---|
| name | **Suspicious data encryption** |
| description | *The process openssl used to encrypt data on the local file system* |
| detection.method | `ANALYTICS_BIOC` (rule_id `30df8779-1e1e-4c5a-a9de-40cb94d837e7`) |
| MITRE | T1486 Data Encrypted for Impact · T1027.013 |
| category | Impact |
| **severity** | **LOW** |
| **case_ids** | **[] — not grouped** |
| action_status | DETECTED |

### Honest read

1. **The TTP is real and is seen.** openssl AES encryption fired a named,
   correct, T1486 analytics detection. This is not a SAFE-MODE no-op.
2. **It fired LOW and did not group.** Severity is set by the analytics rule,
   not the lab policy, so this is Low in block mode too. A Low analytics BIOC
   never creates or joins a case → SIM-EDR-025 produces one signal, not an
   incident. The scenario's stated purpose (group into one case) is **not met**
   by the encryption step alone.
3. **The declared `prevention-anti-ransomware-mass-encryption` did NOT fire** —
   no Medium/High anti-ransomware behavioral verdict at all. ~40 files under
   `/var/tmp` is below the behavioral mass-encryption floor (and canary-based
   anti-ransomware watches user paths, not `/var/tmp`). Declaring it as a
   firable expectation scores a working tenant a **MISS** — the exact rule this
   corpus holds to ("a declared-but-unfirable detection scores a working tenant
   a miss — put it `purpose: hunt`").
4. **The step-02 `/etc/shadow` read raised no new shadow-file ABIOC** in the
   run window (the two `Suspicious access to shadow file` alerts in the tenant
   are ~4 h older, from a different run).
5. **Why SimCore reconcile matched 0:** the tenant *did* detect (T1486 Low), but
   the scenario's expected_detections for the encryption step key on the
   anti-ransomware Prevention, not the built-in `Suspicious data encryption`
   ANALYTICS_BIOC. Raw truth and the matcher disagree because the authored
   expectation is wrong, not because nothing fired.

### Hardening required before this is POV-honest

- Demote `prevention-anti-ransomware-mass-encryption` to `purpose: hunt`
  (did not fire; over-declared).
- Realign the firable expectation to the built-in **Suspicious data encryption**
  ANALYTICS_BIOC (T1486), documented as Low → does not anchor a case on its own.
- To make Akira *group* (the scenario's reason to exist), pair the encryption
  with a Medium+ anchor the way SIM-EDR-023 did (its WildFire + Local-Analysis
  malware drops built case 51896), or raise encryption realism/volume into a
  monitored path to cross the anti-ransomware behavioral floor. As authored,
  it will not group.

---

## SIM-KOI-010 — npm supply-chain dev-endpoint credential theft

**Could not run on this lab.** `node`/`npm` are ABSENT on `pov-target-01`
(verified). The scenario declares `requires_interpreters: [node]` and its
`preinstall` lifecycle hook needs a Node runtime. **Not verifiable here** without
installing Node on the target.

## SIM-KOI-011 — OpenClaw malicious marketplace skill

**Partial only.** The base64 `curl|bash` dropper and the `openssl` infostealer
legs run without Node, but the skill-runner path that exercises
`cortex-malicious-agentic-pack` needs `node`. **Not fully verifiable here.**

---

## Bottom line

- SIM-EDR-025 executes real ransomware behaviour and **is detected** (T1486
  analytics, Low) — but as authored it **over-declares** an anti-ransomware
  Prevention that does not fire and **does not group** into a case. Needs the
  hardening above before PR to stay Gate-A5-honest.
- SIM-KOI-010/011 are **unverifiable on this lab** (no Node runtime). Either
  provision Node on `pov-target-01` or carry them as authored-not-lab-proven
  with the prerequisite documented.
- **tenant-verified: 0** throughout.
