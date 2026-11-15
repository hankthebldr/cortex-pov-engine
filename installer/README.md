# installer/

This directory holds less than its name suggests. Read this before you look
for an installer here.

## What is here

| Path | What it is |
|---|---|
| `README.md` | This file. |
| `stage2/common/annotate.sh` | A bash library that writes ATT&CK-annotated install events as NDJSON (to `$ANNOTATE_LOG_PATH`, default `/var/log/cortexsim-install.ndjson`, plus `logger`). It prints nothing unless `CORTEXSIM_DEMO_MODE=1`. No installer sources it yet; only its test (`tests/installer/bats/test_annotate.bats`) does. The release workflow's `lint-shell` job shellchecks it. |

That is the whole directory. The two-stage design in
`docs/superpowers/specs/2026-04-22-installer-release-pipeline-design.md` (a
spec marked *Draft for implementation*) describes `bootstrap/`,
`stage2/linux/` and `stage2/windows/`, but **none of them exist**. In
particular, there is **no Windows stage-2 and no WSL2 bootstrap**.

## What the release attaches from here

`.github/workflows/release.yml` builds a `stage2-<os>` archive only when
`installer/stage2/<os>/` exists:

- `stage2-linux.tar.gz` needs `stage2/linux/`. If it exists, `stage2/common/`
  ships inside it, because `annotate.sh` is bash.
- `stage2-windows.zip` needs `stage2/windows/`, and holds only that directory.

`stage2/common/` alone never produces an asset, so today a release attaches no
stage-2 archive. Before this rule, the release attached a
`stage2-windows.zip` whose only content was `common/annotate.sh`.

With no `bootstrap/install.sh`, the release attaches the root `install.sh`.
That script is the full source bootstrap, which must run from the root of a
clone. It is not a one-line installer.

## How SimCore is installed today

From a clone, with Docker:

    git clone https://github.com/hankthebldr/cortex-pov-engine.git
    cd cortex-pov-engine
    ./scripts/dev-up.sh

`./install.sh` (run from the root of the same clone) is the heavier path for
Linux jumpboxes that need the toolchain built locally. SimCore itself is a
Linux container; nothing here installs it on Windows.

## How beacons are installed (this is where Windows lives)

Beacons are not installed from this directory. SimCore serves the installer
and the prebuilt beacon itself, so the endpoint needs no Go toolchain and no
public-internet egress. Mint an enrollment token in the console (or
`POST /api/agents/enroll/tokens`) and the console emits the right command.

| Endpoint | Command shape |
|---|---|
| Linux / macOS | `curl -fsSL '<server>/api/agents/install?os=linux' \| CORTEXSIM_TOKEN='cxs_…' bash` (`os=macos` for macOS) |
| Windows | `$env:CORTEXSIM_TOKEN='cxs_…'; iwr -useb '<server>/api/agents/install?os=windows' \| iex` |

`GET /api/agents/install?os=windows` returns a PowerShell installer that
downloads `cortexsim-agent-windows-amd64.exe` from the same SimCore
(`/api/agents/binary?os=windows&arch=amd64`), checks its sha256 and registers
it as a Windows service.

For offline Windows targets, the push bundle is
`GET /api/scenarios/{id}/download?format=powershell`, a self-contained `.ps1`
written for the Windows PowerShell 5.1 floor. It returns
`409 BUNDLE_TARGET_UNSATISFIABLE` when the scenario has no Windows content.

**Neither Windows path has been run on a Windows host.** The beacon
cross-compiles for `windows/amd64` and the image serves a real PE32+ file, but
nobody has yet watched the PowerShell installer create the service, or a
`.ps1` bundle run under Windows PowerShell 5.1, on Windows Server or a Windows
client. Treat both as unproven until that happens. They are separate from
*tenant-verified*, which is also 0.

Full contract, exit codes and install telemetry:
`docs/reference/api-and-agent-surface.md` §1.6.
