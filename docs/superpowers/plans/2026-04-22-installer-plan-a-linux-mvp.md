# Installer — Plan A: Linux MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a working Linux download-cradle installer — `curl -fsSL .../install.sh | sudo bash` — that lands a running SimCore Docker container on a clean Ubuntu 22.04 or Rocky 9 box and passes `/api/health`.

**Architecture:** Two-stage install. Stage-1 is a small hand-auditable bash bootstrap that fetches and SHA-verifies stage-2. Stage-2 is a tar.gz payload with the real install logic (docker-ce bootstrap, image load/pull, compose render, systemd service, health check). A separate `annotate.sh` library emits structured NDJSON events at each stage, wired behind a `CORTEXSIM_DEMO_MODE` flag but quiet by default. A GitHub Actions PR-CI workflow builds a multi-arch SimCore image and pushes it to GHCR on every PR.

**Tech Stack:** bash, shellcheck, BATS (bats-core) for shell unit tests, Docker Buildx, GitHub Actions, GHCR, systemd, docker-ce (distro repos).

---

### Task 1: Initialize git repository and create installer directory structure

**Files:**
- Create: `.gitignore` (if missing)
- Create: `installer/README.md`
- Create: `installer/bootstrap/` (directory)
- Create: `installer/stage2/common/` (directory)
- Create: `installer/stage2/linux/` (directory)

- [ ] **Step 1: Initialize git repo and make initial commit of existing files**

```bash
cd /Users/henry/Github/Github_desktop/cortex-pov-engine
git init
git add .
git commit -m "chore: initial commit of existing CortexSim codebase"
```

Expected: Git repo initialized, one commit on `main`/`master`.

- [ ] **Step 2: Create installer directory structure**

```bash
mkdir -p installer/bootstrap
mkdir -p installer/stage2/common
mkdir -p installer/stage2/linux
mkdir -p tests/installer/bats
mkdir -p .github/workflows
```

- [ ] **Step 3: Create installer/README.md**

Write to `installer/README.md`:

```markdown
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
```

- [ ] **Step 4: Append installer artifacts to .gitignore**

Append to `.gitignore` (create if missing):

```
# installer build artifacts
installer/dist/
installer/**/*.tar.gz
installer/**/*.zip
manifest.json
SHA256SUMS
```

- [ ] **Step 5: Commit**

```bash
git add installer/ tests/installer/ .github/workflows/ .gitignore
git commit -m "chore: scaffold installer directory tree"
```

---

### Task 2: Verify or create SimCore Dockerfile

**Files:**
- Check: `Dockerfile` or `core/Dockerfile` (exists?)
- Create (if missing): `core/Dockerfile`
- Modify (if needed): `docker-compose.yml` to reference the Dockerfile

- [ ] **Step 1: Check for existing Dockerfile**

```bash
ls Dockerfile core/Dockerfile 2>/dev/null; grep -E "^\s*(image|build):" docker-compose.yml
```

Expected: at least one Dockerfile exists, referenced by `docker-compose.yml`. If neither exists, proceed to Step 2. If one exists, skip to Step 5.

- [ ] **Step 2: Create core/Dockerfile** (only if missing)

Write to `core/Dockerfile`:

```dockerfile
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY core/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY core/ /app/core/
COPY scenarios/ /app/scenarios/
COPY infra/ /app/infra/

ENV CORTEXSIM_BASE_DIR=/app
ENV CORTEXSIM_ENV=production
ENV CORTEXSIM_PORT=8888

EXPOSE 8888

CMD ["uvicorn", "core.main:app", "--host", "0.0.0.0", "--port", "8888"]
```

- [ ] **Step 3: Verify Dockerfile builds**

```bash
docker build -f core/Dockerfile -t cortexsim:dev .
```

Expected: `Successfully tagged cortexsim:dev`. If it fails due to missing dependencies in `requirements.txt`, fix the Dockerfile path or requirements before proceeding.

- [ ] **Step 4: Smoke-test the image**

```bash
docker run --rm -d --name cortexsim-smoke -p 18888:8888 cortexsim:dev
sleep 8
curl -fsS http://127.0.0.1:18888/api/health
docker rm -f cortexsim-smoke
```

Expected: `curl` returns 200 with a JSON health response. If 404, make sure the `/api/health` endpoint exists in `core/main.py` or add a trivial one.

- [ ] **Step 5: Commit**

```bash
git add core/Dockerfile docker-compose.yml 2>/dev/null
git commit -m "build: production Dockerfile for SimCore image" --allow-empty-message --allow-empty || true
```

(Use `--allow-empty` only if no changes were needed because Dockerfile already existed.)

---

### Task 3: CI workflow — lint shell scripts with shellcheck

**Files:**
- Create: `.github/workflows/ci.yml`

- [ ] **Step 1: Write the CI lint workflow**

Write to `.github/workflows/ci.yml`:

```yaml
name: CI

on:
  pull_request:
  push:
    branches: [main, master]

jobs:
  lint-shell:
    runs-on: ubuntu-22.04
    steps:
      - uses: actions/checkout@v4
      - name: Install shellcheck
        run: sudo apt-get update && sudo apt-get install -y shellcheck
      - name: Shellcheck installer scripts
        run: |
          shopt -s globstar nullglob
          shellcheck installer/bootstrap/*.sh installer/stage2/common/*.sh installer/stage2/linux/* tests/installer/bats/*.bats || true
          # Fail only when files exist; skip during scaffolding commits
          if compgen -G "installer/bootstrap/*.sh" > /dev/null; then
            shellcheck installer/bootstrap/*.sh
          fi
          if compgen -G "installer/stage2/common/*.sh" > /dev/null; then
            shellcheck installer/stage2/common/*.sh
          fi
          if compgen -G "installer/stage2/linux/*.sh" > /dev/null; then
            shellcheck installer/stage2/linux/*.sh
          fi
```

- [ ] **Step 2: Verify YAML parses**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"
```

Expected: no output (exit 0). If PyYAML isn't installed: `pip install pyyaml`.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: shellcheck workflow for installer scripts"
```

---

### Task 4: CI workflow — build and push SimCore image to GHCR on PR

**Files:**
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Add `build-image` job to the CI workflow**

Append to `.github/workflows/ci.yml` (indent as a sibling of `lint-shell`):

```yaml
  build-image:
    runs-on: ubuntu-22.04
    needs: lint-shell
    permissions:
      contents: read
      packages: write
    steps:
      - uses: actions/checkout@v4

      - name: Set up QEMU
        uses: docker/setup-qemu-action@v3

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Login to GHCR
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Compute image tag
        id: tag
        run: |
          if [ "${GITHUB_EVENT_NAME}" = "pull_request" ]; then
            echo "tag=pr-${{ github.event.number }}" >> "$GITHUB_OUTPUT"
          else
            echo "tag=sha-$(git rev-parse --short HEAD)" >> "$GITHUB_OUTPUT"
          fi

      - name: Build and push
        uses: docker/build-push-action@v5
        with:
          context: .
          file: core/Dockerfile
          platforms: linux/amd64,linux/arm64
          push: true
          tags: |
            ghcr.io/${{ github.repository_owner }}/cortexsim:${{ steps.tag.outputs.tag }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

- [ ] **Step 2: Verify YAML parses**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"
```

Expected: exit 0.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: build and push SimCore image to GHCR on PR/push"
```

---

### Task 5: Annotate library — write BATS test first

**Files:**
- Create: `tests/installer/bats/test_annotate.bats`

- [ ] **Step 1: Install BATS locally for running tests**

```bash
brew install bats-core 2>/dev/null || sudo apt-get install -y bats
bats --version
```

Expected: `Bats 1.x.x` printed.

- [ ] **Step 2: Write failing BATS test**

Write to `tests/installer/bats/test_annotate.bats`:

```bash
#!/usr/bin/env bats

setup() {
  export CORTEXSIM_DEMO_MODE=1
  export CORTEXSIM_INSTALLER_RUN_ID="test-run-$$"
  export ANNOTATE_LOG_PATH="$(mktemp)"
  # shellcheck disable=SC1091
  source "${BATS_TEST_DIRNAME}/../../../installer/stage2/common/annotate.sh"
}

teardown() {
  rm -f "$ANNOTATE_LOG_PATH"
}

@test "annotate emits NDJSON with technique when given T-id" {
  annotate "T1105" "fetched_stage2" '{"src":"ghcr.io/foo:bar"}'
  line="$(cat "$ANNOTATE_LOG_PATH")"
  echo "$line" | grep -q '"technique":"T1105"'
  echo "$line" | grep -q '"tactic":"command-and-control"'
  echo "$line" | grep -q '"action":"fetched_stage2"'
  echo "$line" | grep -q '"src":"ghcr.io/foo:bar"'
  echo "$line" | grep -q '"installer_run_id":"test-run-'
}

@test "annotate with dash marks event as infra-setup (technique null)" {
  annotate "-" "installed_docker_ce"
  line="$(cat "$ANNOTATE_LOG_PATH")"
  echo "$line" | grep -q '"technique":null'
  echo "$line" | grep -q '"tactic":null'
  echo "$line" | grep -q '"action":"installed_docker_ce"'
}

@test "annotate is a no-op when CORTEXSIM_DEMO_MODE=0" {
  export CORTEXSIM_DEMO_MODE=0
  annotate "T1105" "should_not_fire"
  [ ! -s "$ANNOTATE_LOG_PATH" ]
}

@test "annotate rejects unknown technique IDs" {
  run annotate "T9999" "bogus"
  [ "$status" -ne 0 ]
  echo "$output" | grep -q "unknown technique"
}
```

- [ ] **Step 3: Run the test and verify it fails with expected error**

```bash
bats tests/installer/bats/test_annotate.bats
```

Expected: all tests FAIL with `source: installer/stage2/common/annotate.sh: No such file or directory`.

- [ ] **Step 4: Commit the failing test**

```bash
git add tests/installer/bats/test_annotate.bats
git commit -m "test: BATS tests for annotate library (failing)"
```

---

### Task 6: Annotate library — implement

**Files:**
- Create: `installer/stage2/common/annotate.sh`

- [ ] **Step 1: Implement annotate.sh**

Write to `installer/stage2/common/annotate.sh`:

```bash
#!/usr/bin/env bash
# annotate.sh — structured ATT&CK-annotated install event emitter.
# Source this file; call `annotate <technique_or_dash> <action> [extra_json]`.

set -u

: "${CORTEXSIM_DEMO_MODE:=0}"
: "${CORTEXSIM_INSTALLER_RUN_ID:=$(cat /proc/sys/kernel/random/uuid 2>/dev/null || uuidgen 2>/dev/null || echo "run-$$-$(date +%s)")}"
: "${ANNOTATE_LOG_PATH:=/var/log/cortexsim-install.ndjson}"
: "${ANNOTATE_STAGE:=stage2-linux}"

# Technique → tactic lookup. Covers only techniques the installer emits.
__annotate_tactic() {
    case "$1" in
        T1059.001|T1059.004)  echo "execution" ;;
        T1105)                echo "command-and-control" ;;
        T1027)                echo "defense-evasion" ;;
        T1548.002|T1548.003)  echo "privilege-escalation" ;;
        T1543.002|T1543.003)  echo "persistence" ;;
        T1569.002)            echo "execution" ;;
        T1053.005)            echo "persistence" ;;
        *)                    return 1 ;;
    esac
}

__annotate_escape_json() {
    # Minimal JSON string escape (backslash, quote, newline).
    printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e ':a;N;$!ba;s/\n/\\n/g'
}

annotate() {
    local technique="$1"
    local action="$2"
    local extra="${3:-}"

    [ "$CORTEXSIM_DEMO_MODE" = "1" ] || return 0

    local tactic technique_json tactic_json
    if [ "$technique" = "-" ]; then
        technique_json="null"
        tactic_json="null"
    else
        if ! tactic="$(__annotate_tactic "$technique")"; then
            echo "annotate: unknown technique '$technique'" >&2
            return 2
        fi
        technique_json="\"$technique\""
        tactic_json="\"$tactic\""
    fi

    local ts host user
    ts="$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ 2>/dev/null || date -u +%Y-%m-%dT%H:%M:%SZ)"
    host="$(hostname 2>/dev/null || echo unknown)"
    user="$(id -un 2>/dev/null || echo unknown)"

    local action_esc
    action_esc="$(__annotate_escape_json "$action")"

    local line
    line="{\"ts\":\"${ts}\",\"installer_run_id\":\"${CORTEXSIM_INSTALLER_RUN_ID}\",\"stage\":\"${ANNOTATE_STAGE}\",\"technique\":${technique_json},\"tactic\":${tactic_json},\"action\":\"${action_esc}\",\"host\":\"${host}\",\"user\":\"${user}\""

    if [ -n "$extra" ]; then
        # strip wrapping braces from extra and append
        local inner="${extra#\{}"
        inner="${inner%\}}"
        line="${line},${inner}"
    fi

    line="${line}}"

    mkdir -p "$(dirname "$ANNOTATE_LOG_PATH")" 2>/dev/null || true
    printf '%s\n' "$line" >> "$ANNOTATE_LOG_PATH"
    command -v logger >/dev/null 2>&1 && logger -t cortexsim-install "$line" || true
}
```

- [ ] **Step 2: Run the BATS tests and verify they pass**

```bash
bats tests/installer/bats/test_annotate.bats
```

Expected: all 4 tests pass.

- [ ] **Step 3: Shellcheck the library**

```bash
shellcheck installer/stage2/common/annotate.sh
```

Expected: no output (exit 0).

- [ ] **Step 4: Commit**

```bash
git add installer/stage2/common/annotate.sh
git commit -m "feat: annotate library for ATT&CK-tagged install events"
```

---

### Task 7: Compose template + render function — test first

**Files:**
- Create: `tests/installer/bats/test_render_compose.bats`
- Create (later): `installer/stage2/common/compose.yml.tmpl`
- Create (later): `installer/stage2/common/render_compose.sh`

- [ ] **Step 1: Write failing BATS test**

Write to `tests/installer/bats/test_render_compose.bats`:

```bash
#!/usr/bin/env bats

setup() {
  export OUT="$(mktemp)"
  # shellcheck disable=SC1091
  source "${BATS_TEST_DIRNAME}/../../../installer/stage2/common/render_compose.sh"
}

teardown() {
  rm -f "$OUT"
}

@test "render_compose substitutes image tag" {
  render_compose \
    --image "ghcr.io/hankthebldr/cortexsim:v1.2.3" \
    --data-dir "/opt/cortexsim/data" \
    --port 8888 \
    --out "$OUT"
  grep -q "image: ghcr.io/hankthebldr/cortexsim:v1.2.3" "$OUT"
  grep -q "/opt/cortexsim/data:/app/data" "$OUT"
  grep -q '"8888:8888"' "$OUT"
}

@test "render_compose fails if --image missing" {
  run render_compose --data-dir /x --port 8888 --out "$OUT"
  [ "$status" -ne 0 ]
  echo "$output" | grep -q "image.*required"
}
```

- [ ] **Step 2: Run and verify failure**

```bash
bats tests/installer/bats/test_render_compose.bats
```

Expected: FAIL — files don't exist.

- [ ] **Step 3: Create the compose template**

Write to `installer/stage2/common/compose.yml.tmpl`:

```yaml
# CortexSim compose file — rendered by installer stage-2.
services:
  simcore:
    image: __IMAGE__
    container_name: cortexsim
    restart: unless-stopped
    ports:
      - "__PORT__:8888"
    volumes:
      - __DATA_DIR__:/app/data
    environment:
      CORTEXSIM_ENV: production
      CORTEXSIM_BASE_DIR: /app
      CORTEXSIM_PORT: "8888"
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://127.0.0.1:8888/api/health"]
      interval: 10s
      timeout: 3s
      retries: 12
      start_period: 30s
```

- [ ] **Step 4: Implement render_compose.sh**

Write to `installer/stage2/common/render_compose.sh`:

```bash
#!/usr/bin/env bash
# render_compose.sh — render compose.yml.tmpl with host-specific values.
# Sourced by stage-2 installers.

render_compose() {
    local image="" data_dir="" port="" out=""
    while [ $# -gt 0 ]; do
        case "$1" in
            --image)    image="$2"; shift 2 ;;
            --data-dir) data_dir="$2"; shift 2 ;;
            --port)     port="$2"; shift 2 ;;
            --out)      out="$2"; shift 2 ;;
            *)          echo "render_compose: unknown arg: $1" >&2; return 2 ;;
        esac
    done

    [ -n "$image" ]    || { echo "render_compose: --image required" >&2; return 2; }
    [ -n "$data_dir" ] || { echo "render_compose: --data-dir required" >&2; return 2; }
    [ -n "$port" ]     || { echo "render_compose: --port required" >&2; return 2; }
    [ -n "$out" ]      || { echo "render_compose: --out required" >&2; return 2; }

    local tmpl
    tmpl="$(dirname "${BASH_SOURCE[0]}")/compose.yml.tmpl"
    [ -r "$tmpl" ] || { echo "render_compose: template missing: $tmpl" >&2; return 3; }

    sed \
        -e "s|__IMAGE__|${image}|g" \
        -e "s|__DATA_DIR__|${data_dir}|g" \
        -e "s|__PORT__|${port}|g" \
        "$tmpl" > "$out"
}
```

- [ ] **Step 5: Run tests and verify pass**

```bash
bats tests/installer/bats/test_render_compose.bats
shellcheck installer/stage2/common/render_compose.sh
```

Expected: tests pass, shellcheck clean.

- [ ] **Step 6: Commit**

```bash
git add installer/stage2/common/compose.yml.tmpl installer/stage2/common/render_compose.sh tests/installer/bats/test_render_compose.bats
git commit -m "feat: compose.yml.tmpl + render_compose shell function"
```

---

### Task 8: Stage-1 bash bootstrap — test first

**Files:**
- Create: `tests/installer/bats/test_bootstrap_sh.bats`
- Create (later): `installer/bootstrap/install.sh`

- [ ] **Step 1: Write failing BATS test**

Write to `tests/installer/bats/test_bootstrap_sh.bats`:

```bash
#!/usr/bin/env bats

SCRIPT="${BATS_TEST_DIRNAME}/../../../installer/bootstrap/install.sh"

@test "install.sh --help prints usage and exits 0" {
  run bash "$SCRIPT" --help
  [ "$status" -eq 0 ]
  echo "$output" | grep -q "Usage:"
  echo "$output" | grep -q -- "--offline"
  echo "$output" | grep -q -- "--demo-mode"
  echo "$output" | grep -q -- "--version"
}

@test "install.sh rejects unknown flags" {
  run bash "$SCRIPT" --not-a-real-flag
  [ "$status" -ne 0 ]
  echo "$output" | grep -q "unknown"
}

@test "install.sh --detect-os prints linux-amd64 or linux-arm64" {
  run bash "$SCRIPT" --detect-os
  [ "$status" -eq 0 ]
  echo "$output" | grep -qE "^linux-(amd64|arm64)$"
}

@test "install.sh fails with clear message on SHA mismatch" {
  local tmp="$(mktemp -d)"
  echo "fake stage2 payload" > "$tmp/stage2-linux.tar.gz"
  echo '{"version":"vtest","artifacts":{"stage2-linux.tar.gz":{"sha256":"0000000000000000000000000000000000000000000000000000000000000000"}}}' > "$tmp/manifest.json"
  run bash "$SCRIPT" --local-stage2 "$tmp"
  [ "$status" -ne 0 ]
  echo "$output" | grep -qi "sha256"
  rm -rf "$tmp"
}
```

- [ ] **Step 2: Run and verify failure**

```bash
bats tests/installer/bats/test_bootstrap_sh.bats
```

Expected: FAIL — script missing.

- [ ] **Step 3: Implement install.sh**

Write to `installer/bootstrap/install.sh`:

```bash
#!/usr/bin/env bash
# CortexSim stage-1 bootstrap (Linux).
# Fetches, verifies, and executes stage-2. Runs via:
#   curl -fsSL https://.../install.sh | sudo bash -s -- [flags]
set -euo pipefail

RELEASE_URL_DEFAULT="https://github.com/hankthebldr/cortexsim/releases"
VERSION="latest"
OFFLINE_BUNDLE=""
LOCAL_STAGE2=""
DEMO_MODE=0
RELEASE_URL=""

usage() {
    cat <<EOF
Usage: install.sh [OPTIONS]

Options:
  --version VERSION        Release tag to install (default: latest)
  --offline PATH           Path to offline bundle tar.gz (skips network fetch)
  --local-stage2 DIR       Use a local stage-2 directory (dev only); expects manifest.json
  --demo-mode              Emit ATT&CK-annotated NDJSON telemetry during install
  --release-url URL        Override release URL (default: $RELEASE_URL_DEFAULT)
  --detect-os              Print detected os-arch (linux-amd64 / linux-arm64) and exit
  --help                   Show this help

Examples:
  curl -fsSL .../install.sh | sudo bash
  curl -fsSL .../install.sh | sudo bash -s -- --demo-mode
  sudo ./install.sh --offline ./cortexsim-linux-amd64.tar.gz
EOF
}

detect_os_arch() {
    local kernel arch
    kernel="$(uname -s | tr '[:upper:]' '[:lower:]')"
    arch="$(uname -m)"
    case "$arch" in
        x86_64|amd64)  arch="amd64" ;;
        aarch64|arm64) arch="arm64" ;;
        *) echo "unsupported arch: $arch" >&2; return 1 ;;
    esac
    [ "$kernel" = "linux" ] || { echo "unsupported kernel: $kernel" >&2; return 1; }
    echo "${kernel}-${arch}"
}

sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    else
        shasum -a 256 "$1" | awk '{print $1}'
    fi
}

json_get() {
    # json_get <file> <jq-like-path> — uses python3 since not every host has jq.
    python3 -c "
import json, sys
with open('$1') as f: d = json.load(f)
path = '$2'.split('.')
for p in path: d = d[p]
print(d)
"
}

main() {
    # Parse flags
    while [ $# -gt 0 ]; do
        case "$1" in
            --version)      VERSION="$2"; shift 2 ;;
            --offline)      OFFLINE_BUNDLE="$2"; shift 2 ;;
            --local-stage2) LOCAL_STAGE2="$2"; shift 2 ;;
            --demo-mode)    DEMO_MODE=1; shift ;;
            --release-url)  RELEASE_URL="$2"; shift 2 ;;
            --detect-os)    detect_os_arch; exit $? ;;
            --help|-h)      usage; exit 0 ;;
            *)              echo "unknown flag: $1" >&2; usage >&2; exit 2 ;;
        esac
    done

    RELEASE_URL="${RELEASE_URL:-$RELEASE_URL_DEFAULT}"
    export CORTEXSIM_DEMO_MODE="$DEMO_MODE"
    export CORTEXSIM_INSTALLER_RUN_ID="${CORTEXSIM_INSTALLER_RUN_ID:-$(cat /proc/sys/kernel/random/uuid 2>/dev/null || echo "run-$$")}"

    local osarch stage2_name workdir manifest stage2_archive expected_sha actual_sha
    osarch="$(detect_os_arch)"
    stage2_name="stage2-linux.tar.gz"
    workdir="$(mktemp -d -t cortexsim-install-XXXXXX)"
    trap 'rm -rf "$workdir"' EXIT

    # Fetch or locate manifest + stage-2
    if [ -n "$LOCAL_STAGE2" ]; then
        manifest="$LOCAL_STAGE2/manifest.json"
        stage2_archive="$LOCAL_STAGE2/$stage2_name"
    elif [ -n "$OFFLINE_BUNDLE" ]; then
        # Offline bundle is itself a tar.gz containing manifest.json and the image + stage-2.
        tar -xzf "$OFFLINE_BUNDLE" -C "$workdir"
        manifest="$workdir/manifest.json"
        stage2_archive="$workdir/$stage2_name"
    else
        # Online: download manifest.json and stage2 from the release
        local base="${RELEASE_URL}/download/${VERSION}"
        [ "$VERSION" = "latest" ] && base="${RELEASE_URL}/latest/download"
        manifest="$workdir/manifest.json"
        stage2_archive="$workdir/$stage2_name"
        curl -fsSL "$base/manifest.json" -o "$manifest"
        curl -fsSL "$base/$stage2_name" -o "$stage2_archive"
    fi

    # SHA verify
    expected_sha="$(json_get "$manifest" "artifacts.$stage2_name.sha256")"
    actual_sha="$(sha256_of "$stage2_archive")"
    if [ "$expected_sha" != "$actual_sha" ]; then
        echo "ERROR: sha256 mismatch for $stage2_name" >&2
        echo "  expected: $expected_sha" >&2
        echo "  actual:   $actual_sha" >&2
        exit 3
    fi

    # Extract and exec
    local stage2_dir="$workdir/stage2"
    mkdir -p "$stage2_dir"
    tar -xzf "$stage2_archive" -C "$stage2_dir"

    local entrypoint="$stage2_dir/linux/install"
    [ -x "$entrypoint" ] || chmod +x "$entrypoint" 2>/dev/null || true
    [ -x "$entrypoint" ] || { echo "stage-2 entrypoint missing: $entrypoint" >&2; exit 4; }

    exec "$entrypoint" \
        --demo-mode="$DEMO_MODE" \
        --version="$VERSION" \
        ${OFFLINE_BUNDLE:+--offline-bundle="$workdir"}
}

main "$@"
```

- [ ] **Step 4: Run BATS tests and shellcheck**

```bash
chmod +x installer/bootstrap/install.sh
bats tests/installer/bats/test_bootstrap_sh.bats
shellcheck installer/bootstrap/install.sh
```

Expected: BATS tests pass (except the 4th one about SHA mismatch, which requires a mock stage-2 at `$tmp/stage2-linux.tar.gz` plus a real `--local-stage2` flow — test is already designed to assert the SHA mismatch path). Shellcheck clean.

- [ ] **Step 5: Commit**

```bash
git add installer/bootstrap/install.sh tests/installer/bats/test_bootstrap_sh.bats
git commit -m "feat: stage-1 bash bootstrap with SHA verification"
```

---

### Task 9: Stage-2 Linux — docker-bootstrap.sh

**Files:**
- Create: `installer/stage2/linux/docker-bootstrap.sh`
- Create: `tests/installer/bats/test_docker_bootstrap.bats`

- [ ] **Step 1: Write failing BATS test (unit tests only — real install happens in integration test)**

Write to `tests/installer/bats/test_docker_bootstrap.bats`:

```bash
#!/usr/bin/env bats

setup() {
  # shellcheck disable=SC1091
  source "${BATS_TEST_DIRNAME}/../../../installer/stage2/linux/docker-bootstrap.sh"
}

@test "detect_distro returns ubuntu for an Ubuntu-shaped os-release" {
  local osrel
  osrel="$(mktemp)"
  cat >"$osrel" <<'EOF'
ID=ubuntu
VERSION_ID="22.04"
EOF
  run detect_distro "$osrel"
  [ "$status" -eq 0 ]
  [ "$output" = "ubuntu" ]
  rm -f "$osrel"
}

@test "detect_distro returns rocky for a Rocky Linux os-release" {
  local osrel
  osrel="$(mktemp)"
  cat >"$osrel" <<'EOF'
ID="rocky"
VERSION_ID="9.3"
EOF
  run detect_distro "$osrel"
  [ "$status" -eq 0 ]
  [ "$output" = "rocky" ]
  rm -f "$osrel"
}

@test "detect_distro fails on unknown distro" {
  local osrel
  osrel="$(mktemp)"
  cat >"$osrel" <<'EOF'
ID=plan9
EOF
  run detect_distro "$osrel"
  [ "$status" -ne 0 ]
  rm -f "$osrel"
}

@test "docker_already_installed is false when docker binary missing" {
  PATH="/nonexistent" run docker_already_installed
  [ "$status" -ne 0 ]
}
```

- [ ] **Step 2: Run and verify failure**

```bash
bats tests/installer/bats/test_docker_bootstrap.bats
```

Expected: FAIL — script missing.

- [ ] **Step 3: Implement docker-bootstrap.sh**

Write to `installer/stage2/linux/docker-bootstrap.sh`:

```bash
#!/usr/bin/env bash
# docker-bootstrap.sh — install docker-ce + compose plugin on Linux.
# Sourced by stage-2 install script.

set -euo pipefail

detect_distro() {
    local osrel="${1:-/etc/os-release}"
    [ -r "$osrel" ] || { echo "no os-release at $osrel" >&2; return 1; }
    # shellcheck disable=SC1090
    . "$osrel"
    case "${ID:-unknown}" in
        ubuntu|debian|rocky|almalinux|centos|rhel|fedora) echo "${ID}" ;;
        *) echo "unsupported distro: ${ID:-unknown}" >&2; return 2 ;;
    esac
}

docker_already_installed() {
    command -v docker >/dev/null 2>&1 && docker version >/dev/null 2>&1
}

install_docker_debian_family() {
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -y
    apt-get install -y ca-certificates curl gnupg
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL "https://download.docker.com/linux/${1}/gpg" -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    local codename
    codename="$(. /etc/os-release && echo "${VERSION_CODENAME}")"
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/${1} ${codename} stable" \
        > /etc/apt/sources.list.d/docker.list
    apt-get update -y
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
}

install_docker_rhel_family() {
    local distro="$1"
    local repo_base="https://download.docker.com/linux/${distro}"
    # Rocky/Alma use the centos repo
    case "$distro" in
        rocky|almalinux) repo_base="https://download.docker.com/linux/centos" ;;
    esac
    dnf -y install dnf-plugins-core
    dnf config-manager --add-repo "${repo_base}/docker-ce.repo"
    dnf -y install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
}

bootstrap_docker() {
    if docker_already_installed; then
        echo "docker already installed; skipping"
        return 0
    fi

    local distro
    distro="$(detect_distro)"
    case "$distro" in
        ubuntu)                   install_docker_debian_family ubuntu ;;
        debian)                   install_docker_debian_family debian ;;
        rocky|almalinux|centos|rhel|fedora)
                                  install_docker_rhel_family "$distro" ;;
        *) echo "unreachable"; return 99 ;;
    esac

    systemctl enable --now docker
}
```

- [ ] **Step 4: Run tests and shellcheck**

```bash
bats tests/installer/bats/test_docker_bootstrap.bats
shellcheck installer/stage2/linux/docker-bootstrap.sh
```

Expected: 4 tests pass, shellcheck clean.

- [ ] **Step 5: Commit**

```bash
git add installer/stage2/linux/docker-bootstrap.sh tests/installer/bats/test_docker_bootstrap.bats
git commit -m "feat: distro-aware docker-ce bootstrap for Linux stage-2"
```

---

### Task 10: Stage-2 Linux — systemd unit + main install script

**Files:**
- Create: `installer/stage2/linux/cortexsim.service`
- Create: `installer/stage2/linux/install`

- [ ] **Step 1: Write the systemd unit**

Write to `installer/stage2/linux/cortexsim.service`:

```ini
[Unit]
Description=CortexSim detection simulation engine
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/cortexsim
ExecStart=/usr/bin/docker compose -f /opt/cortexsim/docker-compose.yml up
ExecStop=/usr/bin/docker compose -f /opt/cortexsim/docker-compose.yml down
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Write the main stage-2 install script**

Write to `installer/stage2/linux/install`:

```bash
#!/usr/bin/env bash
# Stage-2 Linux installer. Called by stage-1 bootstrap via exec.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMMON_DIR="$SCRIPT_DIR/../common"
# shellcheck disable=SC1091
source "$COMMON_DIR/annotate.sh"
# shellcheck disable=SC1091
source "$COMMON_DIR/render_compose.sh"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/docker-bootstrap.sh"

export ANNOTATE_STAGE="stage2-linux"
INSTALL_DIR="/opt/cortexsim"
DATA_DIR="${INSTALL_DIR}/data"
VERSION="latest"
DEMO_MODE=0
OFFLINE_BUNDLE_DIR=""

while [ $# -gt 0 ]; do
    case "$1" in
        --demo-mode=*)      DEMO_MODE="${1#*=}"; shift ;;
        --version=*)        VERSION="${1#*=}"; shift ;;
        --offline-bundle=*) OFFLINE_BUNDLE_DIR="${1#*=}"; shift ;;
        *)                  echo "stage2: unknown flag: $1" >&2; exit 2 ;;
    esac
done
export CORTEXSIM_DEMO_MODE="$DEMO_MODE"

require_root() {
    if [ "$(id -u)" -ne 0 ]; then
        annotate "T1548.003" "elevation_required_but_not_root"
        echo "stage2: must run as root; re-exec with sudo" >&2
        exit 13
    fi
    annotate "T1548.003" "elevated"
}

install_image() {
    local image_tar
    if [ -n "$OFFLINE_BUNDLE_DIR" ] && image_tar="$(ls "$OFFLINE_BUNDLE_DIR"/cortexsim-linux-*.tar 2>/dev/null | head -1)" && [ -n "$image_tar" ]; then
        annotate "-" "loading_image_from_local" "{\"path\":\"$image_tar\"}"
        docker load -i "$image_tar"
        # Image name is embedded in the tar; grab the most-recently-tagged cortexsim image.
        IMAGE_REF="$(docker images --format '{{.Repository}}:{{.Tag}}' | grep -E '^(ghcr.io/.+/)?cortexsim:' | head -1)"
    else
        IMAGE_REF="ghcr.io/hankthebldr/cortexsim:${VERSION}"
        annotate "T1105" "pulling_image_from_ghcr" "{\"ref\":\"$IMAGE_REF\"}"
        docker pull "$IMAGE_REF"
    fi
    export IMAGE_REF
}

write_compose() {
    mkdir -p "$INSTALL_DIR" "$DATA_DIR"
    render_compose \
        --image "$IMAGE_REF" \
        --data-dir "$DATA_DIR" \
        --port 8888 \
        --out "$INSTALL_DIR/docker-compose.yml"
    annotate "-" "rendered_compose_file"
}

install_service() {
    install -m 0644 "$SCRIPT_DIR/cortexsim.service" /etc/systemd/system/cortexsim.service
    systemctl daemon-reload
    systemctl enable cortexsim.service
    annotate "T1543.002" "installed_systemd_service"
}

start_service() {
    systemctl start cortexsim.service
    annotate "T1569.002" "started_service_managed_container"
}

wait_healthy() {
    local deadline=$(( $(date +%s) + 120 ))
    while [ "$(date +%s)" -lt "$deadline" ]; do
        if curl -fsS http://127.0.0.1:8888/api/health >/dev/null 2>&1; then
            annotate "-" "verified_local_http_endpoint"
            echo "CortexSim is healthy: http://$(hostname -f 2>/dev/null || hostname):8888"
            return 0
        fi
        sleep 3
    done
    annotate "-" "health_check_failed"
    echo "ERROR: /api/health did not respond within 120s" >&2
    return 1
}

main() {
    annotate "T1059.004" "stage2_entered" "{\"version\":\"$VERSION\"}"
    require_root
    bootstrap_docker
    install_image
    write_compose
    install_service
    start_service
    wait_healthy
    echo "==============================================="
    echo "CortexSim installed. http://127.0.0.1:8888"
    echo "Admin bootstrap token: $(cat $DATA_DIR/admin.token 2>/dev/null || echo '(created on first login)')"
    echo "==============================================="
}

main "$@"
```

- [ ] **Step 3: Shellcheck the scripts**

```bash
chmod +x installer/stage2/linux/install
shellcheck installer/stage2/linux/install
```

Expected: clean (any warnings about sourcing are acceptable; `# shellcheck disable=` comments handle those).

- [ ] **Step 4: Commit**

```bash
git add installer/stage2/linux/install installer/stage2/linux/cortexsim.service
git commit -m "feat: stage-2 Linux install entrypoint + systemd unit"
```

---

### Task 11: Stage-2 Linux — uninstall script

**Files:**
- Create: `installer/stage2/linux/uninstall.sh`

- [ ] **Step 1: Write uninstall.sh**

Write to `installer/stage2/linux/uninstall.sh`:

```bash
#!/usr/bin/env bash
# uninstall.sh — remove CortexSim from a Linux host. Idempotent.
set -euo pipefail

INSTALL_DIR="/opt/cortexsim"
KEEP_DATA=0

while [ $# -gt 0 ]; do
    case "$1" in
        --keep-data) KEEP_DATA=1; shift ;;
        --help)      echo "Usage: uninstall.sh [--keep-data]"; exit 0 ;;
        *)           echo "uninstall: unknown flag: $1" >&2; exit 2 ;;
    esac
done

if [ "$(id -u)" -ne 0 ]; then
    echo "uninstall: must run as root" >&2
    exit 13
fi

echo "Stopping cortexsim service..."
systemctl stop cortexsim.service 2>/dev/null || true
systemctl disable cortexsim.service 2>/dev/null || true

echo "Bringing down docker compose project..."
if [ -f "$INSTALL_DIR/docker-compose.yml" ]; then
    docker compose -f "$INSTALL_DIR/docker-compose.yml" down --remove-orphans || true
fi

echo "Removing systemd unit..."
rm -f /etc/systemd/system/cortexsim.service
systemctl daemon-reload

if [ "$KEEP_DATA" -eq 1 ]; then
    echo "Keeping $INSTALL_DIR/data/ (per --keep-data)"
    find "$INSTALL_DIR" -mindepth 1 -maxdepth 1 ! -name data -exec rm -rf {} +
else
    echo "Removing $INSTALL_DIR ..."
    rm -rf "$INSTALL_DIR"
fi

echo "Uninstall complete."
```

- [ ] **Step 2: Shellcheck**

```bash
chmod +x installer/stage2/linux/uninstall.sh
shellcheck installer/stage2/linux/uninstall.sh
```

Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add installer/stage2/linux/uninstall.sh
git commit -m "feat: Linux uninstall script (idempotent, --keep-data option)"
```

---

### Task 12: Manifest generation script for local dev

**Files:**
- Create: `installer/scripts/build-stage2.sh`
- Create: `installer/scripts/gen-manifest.sh`

- [ ] **Step 1: Create build-stage2.sh**

```bash
mkdir -p installer/scripts
```

Write to `installer/scripts/build-stage2.sh`:

```bash
#!/usr/bin/env bash
# build-stage2.sh — produce stage2-linux.tar.gz from installer/stage2/.
# Output: installer/dist/stage2-linux.tar.gz
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT_DIR="$ROOT/installer/dist"
mkdir -p "$OUT_DIR"

STAGE2_ROOT="$ROOT/installer/stage2"

# Copy linux + common under a clean tree, preserving exec bits.
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

cp -r "$STAGE2_ROOT/common" "$TMP/"
cp -r "$STAGE2_ROOT/linux"  "$TMP/"
chmod +x "$TMP/linux/install" "$TMP/linux/"*.sh "$TMP/common/"*.sh 2>/dev/null || true

tar -czf "$OUT_DIR/stage2-linux.tar.gz" -C "$TMP" common linux
echo "built: $OUT_DIR/stage2-linux.tar.gz"
```

- [ ] **Step 2: Create gen-manifest.sh**

Write to `installer/scripts/gen-manifest.sh`:

```bash
#!/usr/bin/env bash
# gen-manifest.sh — emit manifest.json listing artifacts in installer/dist/ with sha256 digests.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DIST="$ROOT/installer/dist"
VERSION="${1:-dev}"

[ -d "$DIST" ] || { echo "no dist dir; run build-stage2.sh first" >&2; exit 1; }

sha() {
    if command -v sha256sum >/dev/null; then sha256sum "$1" | awk '{print $1}'
    else shasum -a 256 "$1" | awk '{print $1}'; fi
}

python3 - "$VERSION" "$DIST" <<'PY' > "$DIST/manifest.json"
import hashlib, json, os, sys, pathlib
version, dist = sys.argv[1], pathlib.Path(sys.argv[2])
artifacts = {}
for p in sorted(dist.iterdir()):
    if p.is_file() and p.name != "manifest.json":
        h = hashlib.sha256(p.read_bytes()).hexdigest()
        artifacts[p.name] = {"sha256": h, "bytes": p.stat().st_size}
print(json.dumps({"version": version, "artifacts": artifacts}, indent=2))
PY

echo "wrote $DIST/manifest.json"
```

- [ ] **Step 3: Run the build locally and verify output**

```bash
chmod +x installer/scripts/*.sh
installer/scripts/build-stage2.sh
installer/scripts/gen-manifest.sh v0.0.0-dev
cat installer/dist/manifest.json
```

Expected: JSON with `stage2-linux.tar.gz` entry and real sha256.

- [ ] **Step 4: Commit**

```bash
git add installer/scripts/
git commit -m "build: helper scripts to produce stage2-linux.tar.gz + manifest.json"
```

---

### Task 13: End-to-end integration test in CI (Ubuntu 22.04)

**Files:**
- Create: `.github/workflows/installer-integration.yml`

- [ ] **Step 1: Write the integration workflow**

Write to `.github/workflows/installer-integration.yml`:

```yaml
name: Installer Integration (Linux)

on:
  pull_request:
    paths:
      - "installer/**"
      - ".github/workflows/installer-integration.yml"
      - "core/Dockerfile"
  push:
    branches: [main, master]

jobs:
  ubuntu-online:
    runs-on: ubuntu-22.04
    permissions:
      contents: read
      packages: read
    steps:
      - uses: actions/checkout@v4

      - name: Build SimCore image locally and tag as the release ref
        run: |
          docker build -f core/Dockerfile -t ghcr.io/${{ github.repository_owner }}/cortexsim:ci-latest .

      - name: Build stage-2 + manifest
        run: |
          installer/scripts/build-stage2.sh
          installer/scripts/gen-manifest.sh ci-latest

      - name: Run installer via --local-stage2
        run: |
          sudo CORTEXSIM_INSTALLER_RUN_ID=ci-$(uuidgen) \
               installer/bootstrap/install.sh \
               --local-stage2 "$PWD/installer/dist" \
               --version ci-latest

      - name: Poll health endpoint
        run: |
          for i in $(seq 1 24); do
            if curl -fsS http://127.0.0.1:8888/api/health; then
              echo "healthy after ${i} tries"
              exit 0
            fi
            sleep 5
          done
          echo "health endpoint never responded" >&2
          sudo journalctl -u cortexsim --no-pager | tail -100
          sudo docker ps -a
          sudo docker logs cortexsim --tail 100 || true
          exit 1

      - name: Uninstall cleanly
        run: sudo installer/stage2/linux/uninstall.sh

      - name: Verify clean uninstall
        run: |
          ! systemctl is-active --quiet cortexsim.service
          [ ! -d /opt/cortexsim ]
```

- [ ] **Step 2: Note — stage-1 must understand --version with pre-built local image**

In `installer/bootstrap/install.sh`, the `--local-stage2` path passes `--version` through to stage-2 which uses it as the image tag. We need to verify stage-2 handles the case where the image is already present locally. Review `install_image()` in `installer/stage2/linux/install`:

If `docker image inspect "$IMAGE_REF"` succeeds, skip pull. Add to `install_image()`:

```bash
# Before the online pull branch:
if docker image inspect "$IMAGE_REF" >/dev/null 2>&1; then
    annotate "-" "image_already_present_locally" "{\"ref\":\"$IMAGE_REF\"}"
    export IMAGE_REF
    return 0
fi
```

- [ ] **Step 3: Apply the fix to install_image()**

Edit `installer/stage2/linux/install`, find the `install_image()` function, and insert the `image_already_present_locally` check at the very top of the else-branch (online path):

```bash
install_image() {
    local image_tar
    if [ -n "$OFFLINE_BUNDLE_DIR" ] && image_tar="$(ls "$OFFLINE_BUNDLE_DIR"/cortexsim-linux-*.tar 2>/dev/null | head -1)" && [ -n "$image_tar" ]; then
        annotate "-" "loading_image_from_local" "{\"path\":\"$image_tar\"}"
        docker load -i "$image_tar"
        IMAGE_REF="$(docker images --format '{{.Repository}}:{{.Tag}}' | grep -E '^(ghcr.io/.+/)?cortexsim:' | head -1)"
    else
        IMAGE_REF="ghcr.io/hankthebldr/cortexsim:${VERSION}"
        if docker image inspect "$IMAGE_REF" >/dev/null 2>&1; then
            annotate "-" "image_already_present_locally" "{\"ref\":\"$IMAGE_REF\"}"
        else
            annotate "T1105" "pulling_image_from_ghcr" "{\"ref\":\"$IMAGE_REF\"}"
            docker pull "$IMAGE_REF"
        fi
    fi
    export IMAGE_REF
}
```

- [ ] **Step 4: Verify shellcheck still clean**

```bash
shellcheck installer/stage2/linux/install
```

Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/installer-integration.yml installer/stage2/linux/install
git commit -m "ci: end-to-end installer integration test on ubuntu-22.04"
```

---

### Task 14: README updates and DC-facing quick-start

**Files:**
- Modify: `README.md` (repo root)

- [ ] **Step 1: Append a Quick Install section to the main README**

Find the existing `## Build & Run Commands` section in `README.md`. Immediately above it, insert:

```markdown
## Quick Install (Linux)

> Requires a clean Ubuntu 22.04 / Rocky 9 host with sudo.

    curl -fsSL https://github.com/hankthebldr/cortexsim/releases/latest/download/install.sh | sudo bash

Add `--demo-mode` to emit ATT&CK-tagged NDJSON telemetry at each install stage (useful for validating the Cortex install-scenario detection).

Offline install (air-gapped sandbox):

    sudo ./install.sh --offline ./cortexsim-linux-amd64.tar.gz

Windows install is covered in Plan B — see `docs/superpowers/plans/2026-04-22-installer-plan-b-windows-wsl2.md`.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: quick install section in root README"
```

---

**End of Plan A.** At this point: a push of this branch to GitHub triggers CI, which lints the scripts, builds+pushes the Docker image to GHCR, and runs an end-to-end installer test on Ubuntu 22.04 that ends with a successful `/api/health` and a clean uninstall. Plan B (Windows) builds on the annotate library and compose template established here.
