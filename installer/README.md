# CortexSim Installer

Two-stage Linux/Windows installer for SimCore. Distributed via GitHub Releases.

See design spec: `docs/superpowers/specs/2026-04-22-installer-release-pipeline-design.md`.

## Layout

- `bootstrap/` — hand-auditable stage-1 scripts (bash, PowerShell)
- `stage2/common/` — shared assets (annotate library, compose template, scenario YAML)
- `stage2/linux/` — Linux stage-2 (docker-ce bootstrap, systemd unit)
- `stage2/windows/` — Windows stage-2 (WSL2 bootstrap, Windows service) — see Plan B

## Running the installer locally (dev)

Linux:

    sudo installer/bootstrap/install.sh --local-stage2 installer/stage2

This skips the download and runs stage-2 from your checkout.
