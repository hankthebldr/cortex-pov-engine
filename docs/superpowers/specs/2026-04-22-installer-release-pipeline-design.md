# Installer Release Pipeline — Design

**Status:** Draft for implementation
**Owner:** Henry Reed
**Date:** 2026-04-22

## Problem

CortexSim today has two install paths and both assume a pre-built Linux jumpbox. The `install.sh` script is bespoke, assumes Ubuntu with sudo, and requires the repo to already be cloned. There is no supported path to install a full SimCore node on:

- A Windows workstation inside an ITDR sandbox
- A customer-supplied Linux VM that is not the reference jumpbox image
- An air-gapped demo environment with no access to GHCR or PyPI

DCs running POVs need to stand up a SimCore node on whatever workstation the customer hands them, repeatedly, in front of the customer, without fighting dependencies. The install experience is also an opportunity: a one-line download cradle is simultaneously the seamless UX the DC wants *and* a textbook ATT&CK T1059 + T1105 chain that Cortex should detect. Making the install itself a demonstrable scenario turns a logistics step into the opening act of the POV.

## Goal

A GitHub-Actions-driven release pipeline that, on every `v*.*.*` tag, produces:

1. A multi-arch Docker image of SimCore published to GHCR (`ghcr.io/hankthebldr/cortexsim:vX.Y.Z`).
2. Two hand-auditable bootstrap scripts — `install.sh` (Linux) and `install.ps1` (Windows) — that execute as one-line download cradles and stage the real install.
3. Offline installer bundles per platform (`cortexsim-{linux-amd64,linux-arm64,windows-amd64}.tar.gz`) containing the saved Docker image plus stage-2 assets, for air-gapped sandboxes.
4. An `install-scenario.yml` that matches SimCore's existing scenario schema, so the installer's own ATT&CK event stream can be validated against Cortex detections end-to-end.
5. A `SHA256SUMS` manifest and a `manifest.json` pinning every artifact's digest, used by the stage-1 bootstrap for integrity verification.

The default install is quiet and production-grade. A `--demo-mode` flag turns on loud, ATT&CK-annotated telemetry so the DC can demo the install itself as scenario zero.

## Non-Goals

- **Not an auto-updater.** Upgrades are re-runs of the installer with a newer tag. No background update channel.
- **Not code signing (yet).** CI produces sign-ready artifacts and exposes an optional signing step gated on secret presence, but no Authenticode or GPG certs are required for MVP. The whole value proposition leans on *unsigned* download-cradle execution anyway.
- **Not a Windows-native SimCore.** The runtime is always the Linux container. On Windows we install WSL2 + docker-ce *inside* WSL2 and run the same image. No Windows container image is produced.
- **Not a package-manager distribution.** No Homebrew tap, no APT repo, no Chocolatey package. GitHub Releases is the only distribution surface. Those can come later without restructuring.
- **Not Docker Desktop.** To avoid PANW's enterprise licensing footprint, Windows installs use WSL2 + docker-ce. Docker Desktop and Rancher Desktop are explicitly out of scope.

## High-Level Architecture

```
Developer
  |
  | git tag v1.2.3 && git push --tags
  v
GitHub Actions (.github/workflows/release.yml)
  |
  |  lint → build-image → build-stage2 → build-offline-bundles
  |       → optional-sign → checksum → release
  v
GitHub Release v1.2.3                                 GHCR
  ├── install.sh                                      └── ghcr.io/hankthebldr/cortexsim:v1.2.3
  ├── install.ps1                                          (linux/amd64, linux/arm64)
  ├── stage2-linux.tar.gz
  ├── stage2-windows.zip
  ├── cortexsim-linux-amd64.tar.gz    (offline bundle)
  ├── cortexsim-linux-arm64.tar.gz    (offline bundle)
  ├── cortexsim-windows-amd64.zip     (offline bundle)
  ├── install-scenario.yml
  ├── manifest.json
  └── SHA256SUMS
                     |
                     | DC runs one-liner on target workstation
                     v
         curl -fsSL .../install.sh | sudo bash          (Linux, T1059.004 + T1105)
         iex (iwr -useb .../install.ps1)                (Windows, T1059.001 + T1105)
                     |
                     v
          Stage-1 bootstrap (< 300 lines, hand-auditable)
                     |
                     |  detect OS/arch → fetch manifest.json → verify SHA
                     |  → fetch stage2-{linux,windows}.{tar.gz,zip} → verify SHA → extract
                     v
          Stage-2 installer (OS-aware real work)
                     |
                     |  Linux:   install docker-ce via distro repo
                     |  Windows: enable WSL2 feature, install Ubuntu WSL, docker-ce inside WSL
                     |
                     |  docker load <image.tar> OR docker pull ghcr.io/...
                     |  render docker-compose.yml with host-specific vars
                     |  register systemd unit (Linux) or Windows service (Windows)
                     |  docker compose up -d
                     |  curl http://localhost:8888/api/health (first-run verify)
                     v
          SimCore running on the workstation, reachable at http://<host>:8888
```

## Components

### 1. Stage-1 Bootstrap (`installer/bootstrap/`)

Two tiny, single-file, hand-auditable scripts. Both have one job: download and verify stage-2, then `exec` into it.

```
installer/
└── bootstrap/
    ├── install.sh                   # Linux stage-1 (bash, < 250 lines)
    └── install.ps1                  # Windows stage-1 (PowerShell 5.1+ compatible)
```

Stage-1 responsibilities (identical on both OSes):

1. Parse flags: `--version`, `--offline <path>`, `--demo-mode`, `--release-url`, `--help`.
2. Detect OS family and CPU arch (`uname -sm` / `$env:PROCESSOR_ARCHITECTURE`).
3. Pick the release tag — default `latest`, or the `--version` value.
4. Fetch `manifest.json` from the release.
5. Determine the stage-2 artifact for this OS/arch.
6. If `--offline`: read the bundle from the given path. Else: download the stage-2 tarball.
7. Verify SHA256 against `manifest.json`. Abort with clear error on mismatch.
8. Extract to a temp directory.
9. `exec` stage-2's entrypoint (`stage2/install`) with all remaining flags, preserving the parent environment plus a few injected variables (`CORTEXSIM_INSTALLER_RUN_ID`, `CORTEXSIM_DEMO_MODE`, `CORTEXSIM_OFFLINE_BUNDLE`).

Why this matters: the customer's SOC can inspect fewer than 500 lines of shell/PowerShell total to audit what the cradle does before it pulls the larger payload. That keeps the "unsigned download cradle" palatable for real sandboxes.

### 2. Stage-2 Installer (`installer/stage2/`)

Where the real work happens. Platform-specific because Docker bootstrap differs dramatically between Linux and Windows.

```
installer/
└── stage2/
    ├── common/
    │   ├── annotate.sh              # ATT&CK NDJSON emitter (sourced)
    │   ├── annotate.ps1             # ditto for PS
    │   ├── compose.yml.tmpl         # docker-compose template
    │   └── install-scenario.yml     # expected-detections for the installer itself
    ├── linux/
    │   ├── install                  # bash entrypoint (called by stage-1 exec)
    │   ├── docker-bootstrap.sh      # distro-aware docker-ce install
    │   ├── cortexsim.service        # systemd unit template
    │   └── uninstall.sh
    └── windows/
        ├── install.ps1              # PowerShell entrypoint
        ├── wsl-bootstrap.ps1        # WSL2 feature enable + Ubuntu install
        ├── docker-in-wsl.sh         # docker-ce install inside the Ubuntu WSL distro
        ├── cortexsim-service.ps1    # registers Windows service via sc.exe
        └── uninstall.ps1
```

**Linux stage-2 flow** (`installer/stage2/linux/install`):

1. Verify running as root; if not, re-exec under `sudo` (T1548.003 privilege-escalation marker when `--demo-mode` is set).
2. Detect distro (Ubuntu/Debian/RHEL/Rocky/Alma) from `/etc/os-release`.
3. Install docker-ce + docker-compose-plugin from the official Docker repo for that distro.
4. Enable and start `docker.service`.
5. `docker load` from the embedded image tarball (offline) or `docker pull` from GHCR (online).
6. Render `/opt/cortexsim/docker-compose.yml` from `compose.yml.tmpl` with host vars (hostname, data dir, port).
7. Install and enable `cortexsim.service` systemd unit (T1543.002 when demo-mode).
8. `systemctl start cortexsim`.
9. Poll `http://127.0.0.1:8888/api/health` for up to 120s.
10. Print the SimCore URL and the admin bootstrap token.

**Windows stage-2 flow** (`installer/stage2/windows/install.ps1`):

1. Verify elevation; if not, self-elevate via UAC prompt (T1548.002 marker when demo-mode).
2. Check Windows build ≥ 19041 (Win10 2004). Bail with a clear error on older builds.
3. If WSL2 feature not enabled: `wsl --install --no-distribution` (T1059.001 via PowerShell invoking system modification). Prompt for reboot; on reboot continuation, resume install via a one-shot scheduled task (T1053.005 self-persistence — *yes, this is also the install resuming, so the technique annotation is honest*).
4. Install Ubuntu-22.04 WSL distro if not present: `wsl --install -d Ubuntu-22.04`.
5. Inside WSL, run `docker-in-wsl.sh` to install docker-ce and configure auto-start.
6. Copy the image tarball or pull from GHCR into the WSL instance.
7. Render `docker-compose.yml` inside WSL at `/opt/cortexsim/`.
8. Register a Windows service (`cortexsim`) via `sc.exe create` with `binPath` pointing at a small PowerShell supervisor script (`C:\ProgramData\CortexSim\supervisor.ps1`). The supervisor runs `wsl.exe -d Ubuntu-22.04 -- docker compose -f /opt/cortexsim/docker-compose.yml up` in the foreground so the service stays "running" as long as the containers do, and forwards service-stop into `docker compose down`. This avoids the common pitfall of services that exit immediately after a detached command. (T1543.003 when demo-mode.)
9. `Start-Service cortexsim`.
10. Poll `http://127.0.0.1:8888/api/health` for up to 180s (WSL cold-start is slower).
11. Print the SimCore URL and admin token.

### 3. ATT&CK Annotation Library (`installer/stage2/common/annotate.{sh,ps1}`)

Tiny sourced/dot-loaded library exporting one function: `annotate <technique_id_or_dash> <action> [extra_json]`. Called at each meaningful stage of stage-2 when `CORTEXSIM_DEMO_MODE=1`. Passing `-` as the technique marks the event as infrastructure-setup (not a TTP); such events still appear in the NDJSON with `"technique": null` and `"tactic": null`, so the SOC can see full install telemetry, but they are not asserted in `install-scenario.yml` expected detections. Tactic is derived from the technique via a built-in lookup table so callers don't have to remember the mapping.

Output NDJSON line, one per event, written to:

- Linux: `/var/log/cortexsim-install.ndjson` **and** `logger -t cortexsim-install` (journald)
- Windows: `C:\ProgramData\CortexSim\install.ndjson` **and** `Write-EventLog -LogName Application -Source CortexSim-Installer`

Event schema:

```json
{
  "ts": "2026-04-22T10:15:03.482Z",
  "installer_run_id": "b4d1e7c2-...",
  "stage": "stage2-linux",
  "technique": "T1105",
  "tactic": "command-and-control",
  "action": "fetched_image_tarball",
  "src": "ghcr.io/hankthebldr/cortexsim:v1.2.3",
  "bytes": 481309447,
  "sha256": "ac1f...",
  "host": "sandbox-ws-04",
  "user": "root"
}
```

This is the log format the `install-scenario.yml` expected detections match against.

### 4. GitHub Actions Release Pipeline (`.github/workflows/release.yml`)

Triggered on tag push matching `v*.*.*`. Uses standard Actions — no custom reusable workflows — so the pipeline is self-contained and forkable.

Jobs, in order (later jobs `needs:` earlier ones):

| Job | Runs on | Purpose |
|-----|---------|---------|
| `lint` | ubuntu-latest | `shellcheck installer/**/*.sh`, `Invoke-ScriptAnalyzer installer/**/*.ps1`, `pytest tests/installer/`, `go vet ./...` |
| `build-image` | ubuntu-latest | `docker buildx build --platform linux/amd64,linux/arm64 --push -t ghcr.io/hankthebldr/cortexsim:${TAG}` |
| `build-stage2` | ubuntu-latest | Package `installer/stage2/{linux,common}` → `stage2-linux.tar.gz`; `installer/stage2/{windows,common}` → `stage2-windows.zip` |
| `build-offline-bundles` | matrix: linux/amd64, linux/arm64, windows/amd64 | `docker save` the image, bundle with stage-2, compress per-platform |
| `sign` (optional) | ubuntu-latest + windows-latest | Gated on `secrets.AUTHENTICODE_PFX` / `secrets.GPG_KEY` being set. Skipped when absent, preserving unsigned-by-default release |
| `checksum` | ubuntu-latest | `sha256sum *` → `SHA256SUMS`; emit `manifest.json` with per-artifact digests + release metadata |
| `release` | ubuntu-latest | `gh release create` with all artifacts, auto-generated notes from commit log since previous tag |

A separate workflow `.github/workflows/ci.yml` runs on PRs: `lint` + `build-image` (tag `:pr-<num>`) + installer integration smoke test (next section). No release artifacts produced on PRs.

### 5. Installer Integration Tests (`.github/workflows/installer-test.yml`)

Matrix workflow, triggered on PR + post-release, that actually runs the installer end-to-end on ephemeral runners:

| Target | Runner | Verification |
|--------|--------|--------------|
| Ubuntu 22.04 | `ubuntu-22.04` | `bash install.sh` → poll `/api/health` |
| Rocky 9 | `ubuntu-22.04` w/ Rocky container-in-container | same |
| Windows Server 2022 | `windows-2022` | `pwsh install.ps1` → poll `/api/health` (uses the runner's own WSL2 support) |
| Offline Linux | `ubuntu-22.04` | `install.sh --offline ./cortexsim-linux-amd64.tar.gz` with egress blocked via iptables |
| Demo-mode Linux | `ubuntu-22.04` | `install.sh --demo-mode` and assert NDJSON matches `install-scenario.yml` expected events |

### 6. Install Scenario (`installer/stage2/common/install-scenario.yml`)

A standard CortexSim scenario YAML with `id: SIM-INSTALL-001`, `plane: ANALYTICS`, `required_content: []`, and `expected_detections` that correspond one-to-one with the NDJSON events the installer emits in demo mode. The DC can load this into SimCore (after SimCore is installed) and validate that Cortex observed the install event chain, proving the lab is wired correctly before any real scenarios run.

## Data Flow — The Install, End-to-End

```
1. DC opens a browser on the sandbox workstation.
2. DC copies the one-liner from the GitHub Release page.
3. DC pastes into terminal / PowerShell and hits enter.

    Linux:   curl -fsSL https://github.com/hankthebldr/cortexsim/releases/download/v1.2.3/install.sh | sudo bash -s -- --demo-mode
    Windows: iex (iwr -useb https://github.com/hankthebldr/cortexsim/releases/download/v1.2.3/install.ps1); Install-CortexSim -DemoMode

4. Stage-1 runs in memory (curl|bash) or is downloaded to a temp file (iwr|iex).
   Emits: annotate T1059.{004,001} "bootstrap_executed"
          annotate T1105 "fetched_manifest"
          annotate T1105 "fetched_stage2"
          annotate T1027 "verified_stage2_sha256"         (if demo-mode)

5. Stage-2 extracted to tempdir, exec'd.
   Emits: annotate T1548.00{2,3} "elevated"
          annotate T1059.{004,001} "stage2_entered"

6. Docker bootstrap runs.
   Linux:   annotate - "installed_docker_ce"             (infra-setup, no TTP mapping)
   Windows: annotate T1059.001 "enabled_wsl_feature"     (PowerShell invokes dism to modify system)
            annotate T1059.001 "installed_wsl_distro"
            annotate - "installed_docker_ce_in_wsl"      (infra-setup)

7. Image load/pull.
   Online:  annotate T1105 "pulled_image_from_ghcr"
   Offline: annotate - "loaded_image_from_local"         (infra-setup; no network ingress)

8. Persistence.
   Linux:   annotate T1543.002 "installed_systemd_service"
   Windows: annotate T1543.003 "installed_windows_service"

9. Execution.
   annotate T1569.002 "started_service_managed_container"  (service execution)
   annotate - "container_listening_on_8888"              (infra-setup)

10. Health check.
    annotate - "verified_local_http_endpoint"            (infra-setup; local loopback is not a TTP)

11. Print access info. Installer exits 0.
```

Every `annotate` line becomes an NDJSON row on disk and a matching journald/EventLog entry. The SOC watching Cortex sees a textbook initial-access-to-persistence chain light up, all from a single one-liner, in under four minutes on Linux and under eight on Windows (first-run WSL cold start dominates).

## Error Handling

Stage-1 fails loudly and unambiguously, because it is the last point where recovery is trivial:

- **Network unreachable** → print the exact `--offline` command with a link to the offline-bundle asset.
- **SHA256 mismatch** → abort, print both expected and actual hashes, and the URL that was fetched. Do not exec stage-2.
- **OS unsupported** (e.g., Windows < 2004, arm32 Linux) → abort with a table of what is supported.
- **Not root / not elevated** → re-exec with `sudo` / UAC prompt once; if that also fails, abort.

Stage-2 failures roll back in reverse order of install (service → compose file → systemd unit → image → docker), using a trap in bash and `try/finally` in PowerShell. Offline bundles deliberately *skip* the docker-ce install if docker is already present and functional, to avoid clobbering an existing customer install.

Every failure path also emits an ATT&CK-annotated event with `"status": "failed"`, so the install-scenario validation catches "installer crashed halfway" as cleanly as success.

## Testing

Three layers:

1. **Static** — shellcheck, PSScriptAnalyzer, pytest over any Python helpers, go vet. Runs in `lint` job on every PR.
2. **Integration** — full installer run on clean runners (Ubuntu 22.04, Rocky 9, Windows Server 2022), both online and offline, both default and demo mode. Asserts `/api/health` responds and the expected NDJSON events were written.
3. **Scenario self-validation** — in demo mode, the installer's own NDJSON is fed into SimCore after install completes, and the installer exits non-zero if `install-scenario.yml`'s expected detections are not all satisfied. This is also how we catch annotation drift: if we rename or remove a technique event, the scenario validation fails and CI blocks the merge.

## Migration & Compatibility

The existing `install.sh` at the repo root stays put for now — it's what builds the jumpbox from a checked-out clone and has a different target audience (developers, IaC jumpbox cloud-init). The new installer lives under `installer/` and is only distributed via GitHub Releases. They can coexist indefinitely. When the new installer is proven on real POVs, we can sunset the root-level script by pointing its `--help` at the new one.

## Open Questions

None blocking. Things to revisit once MVP ships:

- Whether to publish a Homebrew tap for `brew install cortexsim` on Mac (developer ergonomics, not sandbox-critical).
- Whether the Windows service should run as a dedicated local account rather than `LocalSystem`, to reduce blast radius if the container escapes.
- Whether to publish the `install-scenario.yml` as its own release asset separately, so DCs can pre-load it before running the installer.

## Phase Scope

- **Phase 1 (this spec):** Online + offline installers for Linux (amd64, arm64) and Windows (amd64). Tag-driven release. Demo-mode. Install-scenario self-validation.
- **Phase 2 (future):** Optional signing turned on. Homebrew tap. macOS target (dev-only, since POV sandboxes are never Mac).
- **Phase 3 (future):** Auto-update channel gated by a config flag, probably pull-based (the installer's own service polls GitHub Releases weekly and re-runs stage-1 when a newer tag exists — which, charmingly, is T1071.001).
