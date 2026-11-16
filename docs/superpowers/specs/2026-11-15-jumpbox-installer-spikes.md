# Jumpbox installer: Windows spikes R1 and R2

Date: 2026-11-15. Design: `2026-11-15-jumpbox-installer-design.md` sections 7.2 and 12.2.

Status: **tooling written, results pending.** The R1 run needs the owner's
Windows machine; the R2 run needs the hosted-runner workflow pushed. Every cell
below reads `pending` until it is filled from a real run. Nothing here is
inferred.

Tooling (throwaway, delete with this spike once the tables are filled):

- `installer/spikes/wsl-service-spike.ps1`, Windows PowerShell 5.1, ASCII only.
  `-Mode system|account|logon|probe|cleanup`. Every mode writes one JSON object
  of booleans, versions and short sanitised details (no host names, no
  addresses). Default result file is `spike-result.json` (`spike-cleanup.json`
  for cleanup, `spike-result-postreboot.json` with `-PostReboot`).
- `.github/workflows/installer-spike.yml`, runs `-Mode probe` on `windows-2022`
  and `windows-2025` and uploads each leg's JSON.

The rootfs is Alpine mini rootfs 3.24.4 x86_64 from `dl-cdn.alpinelinux.org`,
SHA-256 `cdaa38cb8de3537967ee5b6c9b6cd00cc962da2b018e6eea48696bb274923012`,
pinned in the script.

## R1: which account can run a WSL distribution from a service

`system` = a service running as LocalSystem. `account` = a service running as
the local account `povengine-spike-svc` (Log on as a service, random password).
`logon` = a scheduled task that starts at sign-in of the installing account.

| Observation | system | account | logon |
|---|---|---|---|
| Windows build and edition | pending (owner run) | pending (owner run) | pending (owner run) |
| `wsl --version` | pending (owner run) | pending (owner run) | pending (owner run) |
| Starts from the service with nobody signed in | pending (owner run) | pending (owner run) | pending (owner run) |
| Survives reboot | pending (owner run) | pending (owner run) | pending (owner run) |
| Idle shutdown with only the service attached | pending (owner run) | pending (owner run) | pending (owner run) |
| Portproxy reachable from another machine | pending (owner run) | pending (owner run) | pending (owner run) |
| Mirrored networking available | pending (owner run) | pending (owner run) | pending (owner run) |

How each row is read:

- *Starts with nobody signed in*: the service log records the count of
  `explorer` processes at start (0 means no interactive session) and whether
  `dockerd` answered inside the distribution. For `logon` this is expected to be
  no by design.
- *Survives reboot*: `-PostReboot` after a reboot during which nobody signed in
  (system, account). For `logon`, sign in first, then run `-PostReboot`.
- *Idle shutdown*: the service keeps `dockerd` as the only attached process;
  `docker info` is probed every 30 s for `-IdleWaitSeconds` (default 120).
- *Portproxy reachable from another machine*: the script applies the forward
  from the service (`account` may fail here, which is itself a finding) and
  checks it from the host. The other-machine fetch of
  `http://<host>:18888/` is manual: record it here.
- *Mirrored networking available*: WSL 2.0.0 or newer on Windows build 22621 or
  newer; the script does not change `.wslconfig`.

## R2: WSL2 plus dockerd on hosted Windows runners

Run id: 43896469374 (commit 627a614b; both legs `ok: true`). The first run,
43895310147, failed on a script bug: `apk` was not on the `wsl --exec` PATH.
The fix calls in-distro tools by absolute path.

| Check | windows-2022 | windows-2025 |
|---|---|---|
| Windows build and edition | build 10.0.20348, Windows Server 2022 Datacenter | build 10.0.26100, Windows Server 2025 Datacenter |
| Hypervisor present | yes | yes |
| `wsl --version` | WSL 3.0.5.0, kernel 6.18.58.1-1 | WSL 3.0.5.0, kernel 6.18.58.1-1 |
| `wsl --update` | exit 0 | exit 0 |
| `wsl --set-default-version 2` | exit 0 | exit 0 |
| Optional features (VM Platform, WSL) | already enabled | already enabled |
| Rootfs SHA-256 matches pin | yes | yes |
| `wsl --import --version 2` | ok (exit 0) | ok (exit 0) |
| Mirrored networking available | no (build below 22621) | yes |
| `apk add docker` | exit 0 (run 43896469374); run 43895310147 failed here, `apk` not on PATH under `wsl --exec` (script bug, fixed) | exit 0 (run 43896469374) |
| `docker info` | yes, server version 29.9.3, after 1 s | yes, server version 29.9.3, after 1 s |
| `docker run --rm hello-world` | exit 0 | exit 0 |

So far: hosted runners expose a hypervisor and import a WSL2 distribution; the
open question is dockerd inside it. Every in-distro program is now called by
absolute path (`wsl --exec` is not a login shell, so `/sbin` is off PATH).

## Decisions

Chosen service mode: pending (first of `system`, `account`, `logon` that passed).

Hosted runners can carry the Windows e2e: yes — run 43896469374: WSL2, dockerd and hello-world worked on both windows-2022 and windows-2025.

## How to run

R1, on the owner's Windows machine, elevated Windows PowerShell 5.1, from the
repo root. Do not change the order: each mode starts from a clean machine.

The script stages itself, the rootfs and the service executable in
`C:\ProgramData\povengine-spike`, which SYSTEM tasks and the service run files
from. That directory is created with a protected ACL in the same call (SYSTEM
and Administrators full control, inheritance off, owner Administrators). If it
already exists, the script refuses to run unless its owner, its ACL and every
file in it check out and nothing in it is a reparse point; inspect and delete
such a directory by hand. In `account` mode the account gets read/execute on the
directory (this folder only), the staged script, the rootfs and the service
executable, and write on exactly two result files; nothing else. `-Mode cleanup`
exits non-zero if any post-condition (service, tasks, portproxy, firewall rule,
per-account distributions, account, profile, work directory) is not met.

```powershell
# 1. system
.\installer\spikes\wsl-service-spike.ps1 -Mode system -ResultPath .\spike-result-system.json
# 2. reboot. Do NOT sign in at the console. Wait 3 minutes, check from another
#    machine: http://<host>:18888/ . Then sign in and run:
.\installer\spikes\wsl-service-spike.ps1 -Mode system -PostReboot -ResultPath .\spike-result-system-postreboot.json
# 3. clean up before the next mode
.\installer\spikes\wsl-service-spike.ps1 -Mode cleanup

# 4. account
.\installer\spikes\wsl-service-spike.ps1 -Mode account -ResultPath .\spike-result-account.json
# 5. reboot, same procedure as step 2
.\installer\spikes\wsl-service-spike.ps1 -Mode account -PostReboot -ResultPath .\spike-result-account-postreboot.json
# 6. clean up
.\installer\spikes\wsl-service-spike.ps1 -Mode cleanup

# 7. logon
.\installer\spikes\wsl-service-spike.ps1 -Mode logon -ResultPath .\spike-result-logon.json
# 8. reboot, sign in, then:
.\installer\spikes\wsl-service-spike.ps1 -Mode logon -PostReboot -ResultPath .\spike-result-logon-postreboot.json
# 9. final clean up
.\installer\spikes\wsl-service-spike.ps1 -Mode cleanup
```

Steps 1, 4 and 7 stop for `-IdleWaitSeconds` (default 120) after the service is
up; the script prints a `MANUAL:` line then. Fetch `http://<host>:18888/` from
another machine during that window and record it in the R1 table. Attach the
six JSON files (they hold no host names or addresses) and fill the table.

R2, on CI: push `feature/docker-jumpbox-installer` (the workflow triggers on a
push that touches `installer/spikes/**` or the workflow file) or dispatch it once
it is on the default branch. Download `spike-result-windows-2022` and
`spike-result-windows-2025`, fill the R2 table and the run id. A red matrix leg
is an answer, not a defect: the probe exits non-zero when any step fails.
