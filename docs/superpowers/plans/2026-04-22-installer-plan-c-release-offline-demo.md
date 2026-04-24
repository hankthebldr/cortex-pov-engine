# Installer — Plan C: Release + Offline + Demo-mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the work from Plans A and B into a tag-driven GitHub Release. A push of `v1.0.0` produces a complete Release containing online + offline installers for Linux (amd64, arm64) and Windows (amd64), a `manifest.json` pinning all artifact digests, `SHA256SUMS`, and an `install-scenario.yml` that validates the installer's own ATT&CK event chain end-to-end when run in `--demo-mode`.

**Architecture:** A new `.github/workflows/release.yml` runs only on `v*.*.*` tag push. Jobs: lint (reuse Plan A/B), build-image (tagged multi-arch push to GHCR), build-stage2 (tar.gz + zip), build-offline-bundles (matrix: linux/amd64, linux/arm64, windows/amd64 — each does `docker save` and bundles with stage-2), optional-sign (gated on secret presence), checksum (compose `manifest.json` + `SHA256SUMS`), release (`gh release create` with all assets). A scenario-self-validation test runs the installer in demo-mode locally, parses the emitted NDJSON, and asserts every event declared in `install-scenario.yml` is present.

**Tech Stack:** GitHub Actions tag triggers, `docker buildx` with `--output type=oci`, `docker save`, Python for scenario parsing (reuses SimCore's Pydantic schema from `core/engine/scenario_loader.py`), `gh` CLI in the release job.

**Prerequisites:** Plans A and B are complete and merged. Release URL and GHCR paths now reflect real artifact names.

---

### Task 1: Wire --offline into the Linux stage-1 (regression test)

**Files:**
- Modify: `tests/installer/bats/test_bootstrap_sh.bats`
- Verify: `installer/bootstrap/install.sh`

- [ ] **Step 1: Write a regression test for --offline path**

Append to `tests/installer/bats/test_bootstrap_sh.bats`:

```bash
@test "install.sh --offline extracts bundle and finds manifest" {
  local tmp workdir
  tmp="$(mktemp -d)"
  workdir="$(mktemp -d)"

  # Build a fake offline bundle: tar.gz containing manifest.json + stage2-linux.tar.gz
  mkdir -p "$workdir/stage2/common" "$workdir/stage2/linux"
  echo "#!/bin/bash" > "$workdir/stage2/linux/install"
  echo "echo FAKE_STAGE2_RAN" >> "$workdir/stage2/linux/install"
  chmod +x "$workdir/stage2/linux/install"
  tar -czf "$tmp/stage2-linux.tar.gz" -C "$workdir/stage2" common linux

  local expected_sha
  expected_sha="$(sha256sum "$tmp/stage2-linux.tar.gz" | awk '{print $1}')"
  cat > "$tmp/manifest.json" <<EOF
{"version":"vtest","artifacts":{"stage2-linux.tar.gz":{"sha256":"$expected_sha"}}}
EOF
  ( cd "$tmp" && tar -czf "$tmp/offline.tar.gz" manifest.json stage2-linux.tar.gz )

  run bash "$SCRIPT" --offline "$tmp/offline.tar.gz"
  echo "$output"
  [ "$status" -eq 0 ]
  echo "$output" | grep -q "FAKE_STAGE2_RAN"

  rm -rf "$tmp" "$workdir"
}
```

- [ ] **Step 2: Run test and confirm it passes**

```bash
bats tests/installer/bats/test_bootstrap_sh.bats
```

Expected: all tests including the new one pass. If the new one fails because the Plan A implementation had a bug in `--offline` parsing, fix `installer/bootstrap/install.sh` until it passes. (Most likely fix: the `exec "$entrypoint"` line needs to pass `--offline-bundle="$workdir"` unconditionally when the offline path was taken.)

- [ ] **Step 3: Commit**

```bash
git add tests/installer/bats/test_bootstrap_sh.bats installer/bootstrap/install.sh 2>/dev/null
git commit -m "test: regression test for --offline bundle extraction in stage-1" --allow-empty
```

---

### Task 2: Offline bundle builder script

**Files:**
- Create: `installer/scripts/build-offline-bundle.sh`

- [ ] **Step 1: Implement the builder**

Write to `installer/scripts/build-offline-bundle.sh`:

```bash
#!/usr/bin/env bash
# build-offline-bundle.sh — produce a self-contained offline installer bundle.
# Usage:
#   build-offline-bundle.sh --platform linux-amd64   --image ghcr.io/.../cortexsim:v1.0.0
#   build-offline-bundle.sh --platform linux-arm64   --image ghcr.io/.../cortexsim:v1.0.0
#   build-offline-bundle.sh --platform windows-amd64 --image ghcr.io/.../cortexsim:v1.0.0
# Output: installer/dist/cortexsim-<platform>.{tar.gz|zip}

set -euo pipefail

PLATFORM=""
IMAGE=""
while [ $# -gt 0 ]; do
    case "$1" in
        --platform) PLATFORM="$2"; shift 2 ;;
        --image)    IMAGE="$2";    shift 2 ;;
        *) echo "unknown flag: $1" >&2; exit 2 ;;
    esac
done
[ -n "$PLATFORM" ] || { echo "--platform required" >&2; exit 2; }
[ -n "$IMAGE" ]    || { echo "--image required"    >&2; exit 2; }

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DIST="$ROOT/installer/dist"
mkdir -p "$DIST"

# Build stage-2 archives first if not already present
[ -f "$DIST/stage2-linux.tar.gz" ] || "$ROOT/installer/scripts/build-stage2.sh"

# Figure out docker platform flag from our -platform label
case "$PLATFORM" in
    linux-amd64)   DOCKER_PLATFORM="linux/amd64";   STAGE2="stage2-linux.tar.gz";   OUT="$DIST/cortexsim-linux-amd64.tar.gz" ;;
    linux-arm64)   DOCKER_PLATFORM="linux/arm64";   STAGE2="stage2-linux.tar.gz";   OUT="$DIST/cortexsim-linux-arm64.tar.gz" ;;
    windows-amd64) DOCKER_PLATFORM="linux/amd64";   STAGE2="stage2-windows.zip";    OUT="$DIST/cortexsim-windows-amd64.zip"    ;;
    *) echo "unknown platform: $PLATFORM" >&2; exit 2 ;;
esac

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# Pull and save the platform-specific image
docker pull --platform "$DOCKER_PLATFORM" "$IMAGE"
IMAGE_TAR="$WORK/cortexsim-${PLATFORM%%-*}-${PLATFORM##*-}.tar"
docker save -o "$IMAGE_TAR" "$IMAGE"

# Copy stage-2 and manifest stub in
cp "$DIST/$STAGE2" "$WORK/"
# manifest.json is written here by gen-manifest.sh in the release workflow; for dev it's fine to skip.
[ -f "$DIST/manifest.json" ] && cp "$DIST/manifest.json" "$WORK/" || true

# Package
case "$PLATFORM" in
    linux-*)   tar -czf "$OUT" -C "$WORK" . ;;
    windows-*) ( cd "$WORK" && zip -r "$OUT" . >/dev/null ) ;;
esac

echo "built: $OUT"
```

- [ ] **Step 2: Mark executable and run a local dev-build (requires Docker)**

```bash
chmod +x installer/scripts/build-offline-bundle.sh
# Local dry-run using the image built by docker-compose/ci:
installer/scripts/build-offline-bundle.sh --platform linux-amd64 --image cortexsim:dev || true
ls -l installer/dist/
```

Expected: `installer/dist/cortexsim-linux-amd64.tar.gz` exists.

- [ ] **Step 3: Commit**

```bash
git add installer/scripts/build-offline-bundle.sh
git commit -m "build: per-platform offline installer bundle builder"
```

---

### Task 3: install-scenario.yml — the installer as a validatable scenario

**Files:**
- Create: `installer/stage2/common/install-scenario.yml`

- [ ] **Step 1: Inspect the scenario schema**

```bash
cat scenarios/_schema.yml | head -100
```

Read enough to know the required top-level fields (id, plane, title, mitre, expected_detections, etc). If the schema isn't present in the repo checkout, cross-reference `core/engine/scenario_loader.py` for the Pydantic model.

- [ ] **Step 2: Write install-scenario.yml matching the emitted events**

Write to `installer/stage2/common/install-scenario.yml`:

```yaml
id: SIM-INSTALL-001
plane: ANALYTICS
title: "CortexSim Installer Download Cradle + Persistence"
description: >
  Self-scenario: the CortexSim installer's own one-line download cradle mirrors
  a textbook adversarial initial-access-to-persistence chain. When run with
  --demo-mode, the installer emits NDJSON events tagged with ATT&CK technique
  IDs at each stage. This scenario enumerates the expected events so the DC
  can validate the Cortex installation by pointing it at the installer's own
  telemetry before running any real attack scenarios.

mitre:
  tactics: [execution, command-and-control, privilege-escalation, persistence]
  techniques:
    - T1059.004  # Linux bash
    - T1059.001  # PowerShell
    - T1105      # Ingress tool transfer (download cradle + image pull)
    - T1548.002  # UAC bypass / elevation (Windows)
    - T1548.003  # sudo (Linux)
    - T1543.002  # systemd service (Linux)
    - T1543.003  # Windows service
    - T1569.002  # Service execution
    - T1053.005  # Scheduled task (Windows post-reboot resume)

required_content: []
infra_modules_needed: []

execution:
  identity: root_or_administrator
  mode: push
  platforms: [linux, windows]

steps:
  - id: stage1-bootstrap
    description: "Stage-1 bootstrap executes via curl|bash or iex|iwr"
    expected_detections:
      - source: cortexsim-install.ndjson
        technique: T1059.004
        action: stage2_entered
        platform: linux
      - source: cortexsim-install.ndjson
        technique: T1059.001
        action: stage2_entered
        platform: windows

  - id: elevation
    description: "Stage-2 requires elevation"
    expected_detections:
      - source: cortexsim-install.ndjson
        technique: T1548.003
        action: elevated
        platform: linux
      - source: cortexsim-install.ndjson
        technique: T1548.002
        action: elevated
        platform: windows

  - id: image-transfer
    description: "SimCore image is pulled from GHCR or loaded from offline tarball"
    expected_detections:
      - source: cortexsim-install.ndjson
        technique: T1105
        action_any: [pulling_image_from_ghcr, fetched_stage2, fetched_manifest]

  - id: persistence
    description: "Installer registers a systemd service (Linux) or Windows service (Windows)"
    expected_detections:
      - source: cortexsim-install.ndjson
        technique: T1543.002
        action: installed_systemd_service
        platform: linux
      - source: cortexsim-install.ndjson
        technique: T1543.003
        action: installed_windows_service
        platform: windows

  - id: execution
    description: "Service starts the container"
    expected_detections:
      - source: cortexsim-install.ndjson
        technique: T1569.002
        action: started_service_managed_container

cleanup:
  linux:
    - sudo /opt/cortexsim/uninstall.sh
  windows:
    - powershell -Command "& $env:ProgramData\CortexSim\uninstall.ps1"
```

- [ ] **Step 3: Validate YAML**

```bash
python3 -c "import yaml; yaml.safe_load(open('installer/stage2/common/install-scenario.yml'))"
```

Expected: exit 0.

- [ ] **Step 4: Commit**

```bash
git add installer/stage2/common/install-scenario.yml
git commit -m "feat: install-scenario.yml — installer as a validatable Cortex scenario"
```

---

### Task 4: Scenario self-validation test — Python script

**Files:**
- Create: `tests/installer/test_scenario_self_validation.py`

- [ ] **Step 1: Write the validator (pytest)**

Write to `tests/installer/test_scenario_self_validation.py`:

```python
"""
Runs the Linux installer in demo-mode via --local-stage2, then checks that
every expected_detections entry in install-scenario.yml is present in the
emitted NDJSON log. This is our regression guard against annotation drift.

Skipped on non-Linux or when Docker is unavailable (e.g. restricted CI).
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCENARIO_PATH = ROOT / "installer" / "stage2" / "common" / "install-scenario.yml"


pytestmark = pytest.mark.skipif(
    platform.system() != "Linux" or not shutil.which("docker"),
    reason="scenario self-validation requires Linux + docker",
)


def _emitted_events(log_path: Path) -> list[dict]:
    events = []
    with log_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            events.append(json.loads(line))
    return events


def _expected_actions(scenario: dict, for_platform: str) -> set[str]:
    actions: set[str] = set()
    for step in scenario["steps"]:
        for det in step["expected_detections"]:
            if det.get("platform") and det["platform"] != for_platform:
                continue
            if "action" in det:
                actions.add(det["action"])
            if "action_any" in det:
                actions.update(det["action_any"])
    return actions


def test_demo_mode_events_cover_scenario(tmp_path):
    scenario = yaml.safe_load(SCENARIO_PATH.read_text())
    expected_actions = _expected_actions(scenario, "linux")

    # Build artifacts
    subprocess.run([str(ROOT / "installer/scripts/build-stage2.sh")], check=True)
    subprocess.run([str(ROOT / "installer/scripts/gen-manifest.sh"), "ci-demo"], check=True)

    # Prepare NDJSON log path for demo-mode
    log = tmp_path / "cortexsim-install.ndjson"
    env = os.environ | {
        "CORTEXSIM_INSTALLER_RUN_ID": "pytest-selfvalidate",
        "ANNOTATE_LOG_PATH": str(log),
    }

    # Note: this test requires passwordless sudo on the CI runner (GH Actions ubuntu-22.04 has it).
    subprocess.run(
        ["sudo", "-E", str(ROOT / "installer/bootstrap/install.sh"),
         "--local-stage2", str(ROOT / "installer/dist"),
         "--version", "ci-demo",
         "--demo-mode"],
        env=env, check=True,
    )

    # Uninstall afterwards, even on failure (test cleanup)
    try:
        events = _emitted_events(log)
        seen_actions = {e["action"] for e in events}
        missing = expected_actions - seen_actions
        assert not missing, f"install-scenario.yml expects actions that the installer did NOT emit: {missing}"
    finally:
        subprocess.run(["sudo", str(ROOT / "installer/stage2/linux/uninstall.sh")], check=False)
```

- [ ] **Step 2: Ensure pytest picks up the new test**

```bash
.venv/bin/pytest tests/installer/test_scenario_self_validation.py --collect-only
```

Expected: `test_demo_mode_events_cover_scenario` is collected (probably skipped locally unless you're on Linux with docker).

- [ ] **Step 3: Commit**

```bash
git add tests/installer/test_scenario_self_validation.py
git commit -m "test: scenario self-validation for installer demo-mode events"
```

---

### Task 5: Integrate scenario self-validation into Linux integration workflow

**Files:**
- Modify: `.github/workflows/installer-integration.yml`

- [ ] **Step 1: Add a `ubuntu-demo-mode` job**

Append to `.github/workflows/installer-integration.yml` as a sibling of `ubuntu-online`:

```yaml
  ubuntu-demo-mode:
    runs-on: ubuntu-22.04
    needs: ubuntu-online
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install test deps
        run: |
          pip install pytest pyyaml

      - name: Build SimCore image for demo-mode run
        run: docker build -f core/Dockerfile -t ghcr.io/${{ github.repository_owner }}/cortexsim:ci-demo .

      - name: Run scenario self-validation
        run: |
          pytest tests/installer/test_scenario_self_validation.py -v
```

- [ ] **Step 2: YAML parse check**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/installer-integration.yml'))"
```

Expected: exit 0.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/installer-integration.yml
git commit -m "ci: run installer scenario self-validation on ubuntu-22.04"
```

---

### Task 6: Offline-mode integration test on Ubuntu

**Files:**
- Modify: `.github/workflows/installer-integration.yml`

- [ ] **Step 1: Add `ubuntu-offline` job**

Append to the workflow as a sibling:

```yaml
  ubuntu-offline:
    runs-on: ubuntu-22.04
    needs: ubuntu-online
    steps:
      - uses: actions/checkout@v4

      - name: Build image and stage-2
        run: |
          docker build -f core/Dockerfile -t ghcr.io/${{ github.repository_owner }}/cortexsim:ci-offline .
          installer/scripts/build-stage2.sh

      - name: Build offline bundle
        run: |
          installer/scripts/build-offline-bundle.sh \
            --platform linux-amd64 \
            --image   ghcr.io/${{ github.repository_owner }}/cortexsim:ci-offline
          installer/scripts/gen-manifest.sh ci-offline

      - name: Install with egress blocked (simulate air-gap)
        run: |
          # Block outbound to ghcr.io and github.com for the duration of this step.
          sudo iptables -I OUTPUT -d 140.82.112.0/20 -j REJECT
          sudo iptables -I OUTPUT -d 185.199.108.0/22 -j REJECT
          sudo installer/bootstrap/install.sh \
            --offline "$PWD/installer/dist/cortexsim-linux-amd64.tar.gz" \
            --version ci-offline

      - name: Unblock and poll /api/health
        run: |
          sudo iptables -F OUTPUT
          for i in $(seq 1 24); do
            if curl -fsS http://127.0.0.1:8888/api/health; then
              echo "healthy offline after $i tries"
              exit 0
            fi
            sleep 5
          done
          echo "offline install failed" >&2
          sudo docker ps -a; sudo docker logs cortexsim --tail 100 || true
          exit 1

      - name: Uninstall
        run: sudo installer/stage2/linux/uninstall.sh
```

- [ ] **Step 2: YAML parse check**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/installer-integration.yml'))"
```

Expected: exit 0.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/installer-integration.yml
git commit -m "ci: offline-install integration test with egress blocked"
```

---

### Task 7: Release workflow scaffold (tag-driven)

**Files:**
- Create: `.github/workflows/release.yml`

- [ ] **Step 1: Write the base release workflow**

Write to `.github/workflows/release.yml`:

```yaml
name: Release

on:
  push:
    tags:
      - "v*.*.*"

permissions:
  contents: write
  packages: write

env:
  IMAGE: ghcr.io/${{ github.repository_owner }}/cortexsim

jobs:
  lint:
    runs-on: ubuntu-22.04
    steps:
      - uses: actions/checkout@v4
      - name: Install shellcheck
        run: sudo apt-get update && sudo apt-get install -y shellcheck
      - name: Shellcheck
        run: shellcheck installer/bootstrap/*.sh installer/stage2/common/*.sh installer/stage2/linux/*.sh installer/scripts/*.sh
      - name: PSScriptAnalyzer
        shell: pwsh
        run: |
          Install-Module -Name PSScriptAnalyzer -Force -Scope CurrentUser
          $issues = Invoke-ScriptAnalyzer -Path installer/ -Recurse -Severity Warning
          if ($issues) { $issues | Format-Table -AutoSize; exit 1 }

  build-image:
    runs-on: ubuntu-22.04
    needs: lint
    outputs:
      tag: ${{ steps.tag.outputs.tag }}
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-qemu-action@v3
      - uses: docker/setup-buildx-action@v3
      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - id: tag
        run: echo "tag=${GITHUB_REF_NAME}" >> "$GITHUB_OUTPUT"
      - uses: docker/build-push-action@v5
        with:
          context: .
          file: core/Dockerfile
          platforms: linux/amd64,linux/arm64
          push: true
          tags: |
            ${{ env.IMAGE }}:${{ steps.tag.outputs.tag }}
            ${{ env.IMAGE }}:latest

  build-stage2:
    runs-on: ubuntu-22.04
    needs: build-image
    steps:
      - uses: actions/checkout@v4
      - name: Build stage-2 archives
        run: |
          installer/scripts/build-stage2.sh
      - name: Upload stage-2 artifacts
        uses: actions/upload-artifact@v4
        with:
          name: stage2
          path: installer/dist/stage2-*

  build-offline-bundles:
    runs-on: ubuntu-22.04
    needs: [build-image, build-stage2]
    strategy:
      matrix:
        platform: [linux-amd64, linux-arm64, windows-amd64]
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-qemu-action@v3
      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Restore stage-2 archives
        uses: actions/download-artifact@v4
        with:
          name: stage2
          path: installer/dist/

      - name: Build offline bundle for ${{ matrix.platform }}
        run: |
          installer/scripts/build-offline-bundle.sh \
            --platform ${{ matrix.platform }} \
            --image    ${{ env.IMAGE }}:${{ needs.build-image.outputs.tag }}

      - name: Upload bundle
        uses: actions/upload-artifact@v4
        with:
          name: bundle-${{ matrix.platform }}
          path: installer/dist/cortexsim-${{ matrix.platform }}.*

  sign:
    runs-on: ubuntu-22.04
    needs: build-offline-bundles
    if: ${{ env.ACT != 'true' }}  # placeholder; real gate added in Task 9
    steps:
      - run: echo "signing step disabled (no secrets provisioned)"

  release:
    runs-on: ubuntu-22.04
    needs: [build-stage2, build-offline-bundles]
    steps:
      - uses: actions/checkout@v4

      - name: Restore all artifacts
        uses: actions/download-artifact@v4
        with:
          path: artifacts/

      - name: Flatten into installer/dist
        run: |
          mkdir -p installer/dist
          find artifacts -type f -exec cp {} installer/dist/ \;
          ls -la installer/dist/

      - name: Copy bootstrap scripts into dist
        run: |
          cp installer/bootstrap/install.sh installer/dist/
          cp installer/bootstrap/install.ps1 installer/dist/
          cp installer/stage2/common/install-scenario.yml installer/dist/

      - name: Generate manifest.json
        run: |
          installer/scripts/gen-manifest.sh ${GITHUB_REF_NAME}

      - name: Generate SHA256SUMS
        working-directory: installer/dist
        run: |
          sha256sum * > SHA256SUMS
          echo "---"
          cat SHA256SUMS

      - name: Create GitHub Release
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          gh release create "${GITHUB_REF_NAME}" \
            --title "CortexSim ${GITHUB_REF_NAME}" \
            --generate-notes \
            installer/dist/install.sh \
            installer/dist/install.ps1 \
            installer/dist/stage2-linux.tar.gz \
            installer/dist/stage2-windows.zip \
            installer/dist/cortexsim-linux-amd64.tar.gz \
            installer/dist/cortexsim-linux-arm64.tar.gz \
            installer/dist/cortexsim-windows-amd64.zip \
            installer/dist/install-scenario.yml \
            installer/dist/manifest.json \
            installer/dist/SHA256SUMS
```

- [ ] **Step 2: YAML parse check**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/release.yml'))"
```

Expected: exit 0.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "ci: tag-driven release workflow (lint→image→stage2→bundles→release)"
```

---

### Task 8: Move stage-1 bootstrap scripts into the release assets

**Files:**
- Verify: `.github/workflows/release.yml` already copies `install.sh` and `install.ps1` into `installer/dist` before the release step (yes, per Task 7)
- Verify: The bootstrap scripts' `RELEASE_URL_DEFAULT` matches the actual GitHub repository path

- [ ] **Step 1: Update install.sh default URL if owner differs**

Open `installer/bootstrap/install.sh` and confirm `RELEASE_URL_DEFAULT` points to the actual GitHub repo URL. If the repo is at `github.com/hankthebldr/cortexsim`, the default is already correct. If it's at a different owner, update:

```bash
# Change this line:
RELEASE_URL_DEFAULT="https://github.com/hankthebldr/cortexsim/releases"
# To match the real repo, for example:
RELEASE_URL_DEFAULT="https://github.com/your-org/cortexsim/releases"
```

- [ ] **Step 2: Same for install.ps1**

Open `installer/bootstrap/install.ps1` and update the `-ReleaseUrl` parameter's default if needed.

- [ ] **Step 3: Commit (only if a URL change was made)**

```bash
git add installer/bootstrap/install.sh installer/bootstrap/install.ps1
git commit -m "fix: release URL defaults match actual repository" --allow-empty
```

---

### Task 9: Optional-signing plumbing (gated on secrets)

**Files:**
- Modify: `.github/workflows/release.yml`

- [ ] **Step 1: Replace the placeholder `sign` job with real gated signing**

In `.github/workflows/release.yml`, replace the `sign:` job entirely with:

```yaml
  sign:
    runs-on: ubuntu-22.04
    needs: build-offline-bundles
    # Only run if the signing secrets are configured. If not, the job is a no-op.
    if: ${{ vars.ENABLE_SIGNING == 'true' }}
    steps:
      - uses: actions/checkout@v4
      - name: Restore artifacts
        uses: actions/download-artifact@v4
        with:
          path: artifacts/

      - name: Import GPG key
        if: ${{ secrets.GPG_PRIVATE_KEY != '' }}
        run: |
          echo "${{ secrets.GPG_PRIVATE_KEY }}" | gpg --batch --import
          for f in artifacts/**/*.tar.gz artifacts/**/*.sh; do
            [ -f "$f" ] && gpg --batch --yes --detach-sign --armor "$f"
          done

      - name: Authenticode sign PowerShell + zip (via osslsigncode)
        if: ${{ secrets.AUTHENTICODE_PFX != '' }}
        run: |
          sudo apt-get update && sudo apt-get install -y osslsigncode
          echo "${{ secrets.AUTHENTICODE_PFX }}" | base64 -d > /tmp/cert.pfx
          for f in artifacts/**/*.ps1 artifacts/**/*.zip; do
            [ -f "$f" ] && osslsigncode sign \
              -pkcs12 /tmp/cert.pfx -pass "${{ secrets.AUTHENTICODE_PFX_PASSWORD }}" \
              -in "$f" -out "$f.signed"
            [ -f "$f.signed" ] && mv "$f.signed" "$f"
          done
          rm -f /tmp/cert.pfx

      - name: Re-upload signed artifacts
        uses: actions/upload-artifact@v4
        with:
          name: signed
          path: artifacts/
```

Then update the `release` job to `needs: [build-stage2, build-offline-bundles, sign]` **only if** signing is enabled — since the conditional `if:` means `sign` is skipped when `ENABLE_SIGNING` isn't set. GitHub Actions treats skipped `needs` as success by default, so leaving `release: needs: [build-stage2, build-offline-bundles, sign]` is safe and both modes work.

Update the `release` job's `needs`:

```yaml
  release:
    runs-on: ubuntu-22.04
    needs: [build-stage2, build-offline-bundles, sign]
    if: ${{ always() && !failure() && !cancelled() }}
```

- [ ] **Step 2: YAML parse**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/release.yml'))"
```

Expected: exit 0.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "ci: optional GPG + Authenticode signing gated on repo var ENABLE_SIGNING"
```

---

### Task 10: Smoke-test the release workflow against a throwaway tag

**Files:**
- (No file changes; this is a CI validation task)

- [ ] **Step 1: Push a pre-release tag and observe the workflow**

```bash
git tag v0.0.0-rc1
git push origin v0.0.0-rc1
```

- [ ] **Step 2: Watch the Actions tab and wait for completion**

Open `https://github.com/<owner>/cortexsim/actions` in a browser. Observe:
- `lint` passes
- `build-image` pushes `ghcr.io/<owner>/cortexsim:v0.0.0-rc1` + `:latest`
- `build-stage2` uploads `stage2-linux.tar.gz` + `stage2-windows.zip`
- `build-offline-bundles` produces three bundles (linux/amd64, linux/arm64, windows/amd64)
- `sign` is skipped (ENABLE_SIGNING var not set)
- `release` creates a GitHub Release at the `v0.0.0-rc1` tag with all 10 expected artifacts

- [ ] **Step 3: Verify release artifacts via gh CLI**

```bash
gh release view v0.0.0-rc1 --json assets --jq '.assets[].name' | sort
```

Expected output (any order):

```
SHA256SUMS
cortexsim-linux-amd64.tar.gz
cortexsim-linux-arm64.tar.gz
cortexsim-windows-amd64.zip
install-scenario.yml
install.ps1
install.sh
manifest.json
stage2-linux.tar.gz
stage2-windows.zip
```

- [ ] **Step 4: Test the real one-liner against the pre-release tag**

On a clean Ubuntu 22.04 VM:

```bash
curl -fsSL https://github.com/<owner>/cortexsim/releases/download/v0.0.0-rc1/install.sh | sudo bash -s -- --version v0.0.0-rc1
curl -fsS http://127.0.0.1:8888/api/health
```

Expected: installer completes cleanly, `/api/health` returns 200.

- [ ] **Step 5: Clean up the test tag and release (if you want to)**

```bash
gh release delete v0.0.0-rc1 --yes
git push origin --delete v0.0.0-rc1
git tag -d v0.0.0-rc1
```

- [ ] **Step 6: Commit any workflow tweaks that were needed**

If the smoke test exposed bugs, fix them and commit:

```bash
git add .github/workflows/release.yml
git commit -m "fix: release workflow adjustments discovered via v0.0.0-rc1 smoke test"
```

---

### Task 11: Documentation — release process runbook

**Files:**
- Create: `docs/operations/release-process.md`

- [ ] **Step 1: Write the runbook**

```bash
mkdir -p docs/operations
```

Write to `docs/operations/release-process.md`:

```markdown
# Release Process

CortexSim releases are tag-driven. Pushing a `v*.*.*` tag triggers
`.github/workflows/release.yml`, which produces a complete GitHub Release.

## Cutting a release

1. Ensure `main` is green: `gh run list --branch main --limit 5`.
2. Update `CHANGELOG.md` with the new version's entry.
3. Tag and push:

       git tag v1.2.3
       git push origin v1.2.3

4. Watch the workflow: `gh run watch`.
5. When it completes, verify:

       gh release view v1.2.3 --json assets --jq '.assets[].name'

6. Smoke test on a clean VM:

       curl -fsSL https://github.com/<owner>/cortexsim/releases/download/v1.2.3/install.sh | sudo bash

7. Announce in #dc-gtm.

## Pre-release / rc builds

Same process with `v1.2.3-rc1` tags. GitHub marks tags containing a hyphen
as pre-releases automatically.

## Enabling signed artifacts

1. Set repository variable `ENABLE_SIGNING=true` in repo settings.
2. Add secrets:
   - `GPG_PRIVATE_KEY` (ASCII-armored, for `.sh` + `.tar.gz`)
   - `AUTHENTICODE_PFX` (base64-encoded PFX, for `.ps1` + `.zip`)
   - `AUTHENTICODE_PFX_PASSWORD`
3. Next release will produce signed artifacts.

## Rolling back a bad release

1. Delete the GitHub release (keeps tag): `gh release delete v1.2.3`
2. Delete the tag: `git push origin --delete v1.2.3 && git tag -d v1.2.3`
3. Fix and re-tag.

## Offline bundle size reference

Offline bundles include a saved Docker image. Approximate sizes:

| Platform | Size |
|----------|------|
| linux-amd64 | ~1.2 GB |
| linux-arm64 | ~1.2 GB |
| windows-amd64 | ~1.2 GB (Linux image; WSL2 runs it on Windows) |
```

- [ ] **Step 2: Commit**

```bash
git add docs/operations/release-process.md
git commit -m "docs: release process runbook"
```

---

### Task 12: README — cross-link all three plans and final quick-start

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add an installer section that links everything together**

Find the `## Quick Install (Linux)` and `## Quick Install (Windows)` sections. Immediately above `## Quick Install (Linux)`, insert:

```markdown
## Installer

CortexSim ships as a one-line download cradle. The cradle UX *is* an
adversarial TTP — it exercises ATT&CK T1059 + T1105 + T1543 + T1569 so
Cortex should detect its own installation. Run with `--demo-mode` to
emit explicit ATT&CK-tagged NDJSON telemetry at each stage, and point
Cortex at `installer/stage2/common/install-scenario.yml` to validate
detections before running real scenarios.

- **Design spec:** `docs/superpowers/specs/2026-04-22-installer-release-pipeline-design.md`
- **Plan A (Linux MVP):** `docs/superpowers/plans/2026-04-22-installer-plan-a-linux-mvp.md`
- **Plan B (Windows + WSL2):** `docs/superpowers/plans/2026-04-22-installer-plan-b-windows-wsl2.md`
- **Plan C (Release + Offline + Demo-mode):** `docs/superpowers/plans/2026-04-22-installer-plan-c-release-offline-demo.md`
- **Release runbook:** `docs/operations/release-process.md`
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: README — cross-link installer design, plans, and runbook"
```

---

**End of Plan C.** At this point: pushing a `v*.*.*` tag produces a complete GitHub Release with online + offline installers for Linux (amd64, arm64) and Windows (amd64), a `manifest.json` pinning every artifact digest, `SHA256SUMS`, and a self-validatable `install-scenario.yml`. CI exercises the installer end-to-end on Ubuntu (online, offline, demo-mode) and Windows Server (online) against every PR. Optional signing is plumbed but off until a repo var + secrets are provisioned. A DC running a POV now has a repeatable, auditable, Cortex-visible installation as the opening act of every engagement.
