# Jumpbox Installer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One downloadable package per platform (`linux-amd64`, `linux-arm64`, `windows-amd64`) that installs SimCore on a customer jumpbox through a browser wizard (or the same steps on the command line) in one to four clicks, built by the release and proven on clean Linux and Windows hosts.

**Architecture:** `povengine-setup` is a Go (stdlib) program with one step engine that the CLI and a loopback-only browser wizard both drive. Each package carries the SimCore image and a private Docker engine; Linux runs that engine under systemd, Windows runs it inside a purpose-built WSL2 distribution. SimCore runs with host networking from one generated `docker run` line, and upgrade/uninstall keep the database.

**Tech Stack:** Go 1.22 (stdlib only, `CGO_ENABLED=0 -trimpath`), Docker static engine binaries, WSL2 (`wsl --import`), systemd, Windows SCM, React + Vite (one more entry in `ui/`, kit components), GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-11-15-jumpbox-installer-design.md` (approved 2026-11-15). Section numbers below (§n) refer to it.

## Global Constraints

- Go module `installer/setup` (`module github.com/hankthebldr/cortexsim/installer/setup`, `go 1.22`), standard library only — no third-party modules, same rule as `agent/`.
- Built with `CGO_ENABLED=0 -trimpath -ldflags="-s -w -X main.version=<v>"` for `linux/amd64`, `linux/arm64`, `windows/amd64`.
- No secret is ever printed, logged, put in a URL, sent through the wizard API, or exported. The master secret is 48 bytes from `crypto/rand`, URL-safe base64 (64 characters).
- Names and paths are fixed (§8.1): `/opt/povengine`, `/etc/povengine` (0700, files 0600), `/var/lib/povengine/{data,engine,backups}`, `/var/log/povengine`; `C:\Program Files\POVengine`, `C:\ProgramData\POVengine\{config,backups,logs,wsl}`; units `povengine-engine.service`, `povengine.service`; Windows service `POVengine`; container `povengine-simcore`; WSL distribution `povengine-host`; engine socket `/run/povengine/docker.sock`.
- Result codes are the §13 list verbatim (plus any a task adds); never renamed.
- SimCore env written by the installer (§8.2): `CORTEXSIM_ENV=production`, `CORTEXSIM_SECRET`, `CORTEXSIM_ALLOWED_HOSTS`, `CORTEXSIM_PUBLIC_URLS=http://<address>:<port>`, `CORTEXSIM_INSTALLED_BY=povengine-setup/<version>`, `CORTEXSIM_INSTALL_ADDRESS`, `CORTEXSIM_BIND` only when listen-only, and `HTTPS_PROXY`/`HTTP_PROXY`/`NO_PROXY` only when a proxy is set.
- Default port 8888. Firewall rule: TCP `<port>` inbound from any source (D4).
- Docker Engine is the runtime on both OSes; never Docker Desktop (D1). The private engine runs with `--iptables=false --ip-masq=false --bridge=none`; SimCore runs `--network host`.
- `/api/health` reading `degraded` only because of `api_auth` (`API_UNAUTHENTICATED`) is a healthy install.
- Public repo: examples and fixtures use RFC 5737 addresses (`192.0.2.0/24`) and invented names; no lab host names, addresses, tenant names or keys anywhere, including PR bodies and the Windows proof log.
- UI: kit components only (`ui/src/components/console/kit`: Button, Icon, StatusIcon); one short line per state, detail behind a disclosure.
- The installer never contacts the Cortex tenant.
- Branching: every task is a topic branch `<type>/<area>-installer-<slug>` cut from `feature/docker-jumpbox-installer` and merged back into it, never into `dev`. Stage files by name; commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; no force-push, no rebase of a pushed branch; open PRs only when the owner asks. Bring `dev` in with a merge when needed.

## Review Focus

- **A jumpbox with two NICs (corporate + lab).** The Address row must default to the address of the interface holding the default route and list the others; a wrong default silently gives beacons an unreachable URL. Pinned in Task 7 (`TestAddressCandidates_DefaultRouteFirst`).
- **The package launched a second time on an installed box.** It must offer Upgrade (newer), "Already installed · Open console" (same version) or refuse a downgrade, never run a second install over the first. Pinned in Task 9 (`TestPlanFor_ExistingInstall`).
- **A host name that is uppercase, or `hostname -f` failing.** `CORTEXSIM_ALLOWED_HOSTS` must still carry the lower-cased short name and never an empty entry. Pinned in Task 6 (`TestRenderEnv_HostNames`).
- **The browser tab closed mid-install.** The install keeps running; launching again reattaches to its progress; the 30-minute idle timeout never fires while an apply is in flight. Pinned in Task 11 (`TestServer_IdleTimeoutWaitsForApply`, `TestLauncher_ReattachesToRunningServe`).
- **A truncated or tampered download** (a partial 600 MB `.exe`). Verification must refuse with `PAYLOAD_CORRUPT` before any step changes the host. Pinned in Task 10 (`TestOpen_TruncatedPayloadRefusesBeforeApply`).

---

## Phase 0 — Spikes (settle the Windows unknowns first)

### Task 1: Windows spikes R1 and R2

Throwaway code; the deliverable is the recorded result. Task 14 and Task 16 read it.

**Files:**
- Create: `installer/spikes/wsl-service-spike.ps1`
- Create: `.github/workflows/installer-spike.yml` (`workflow_dispatch` only, on `feature/docker-jumpbox-installer`; `actions/checkout` pinned to the SHA ci.yml uses; no third-party actions)
- Create: `docs/superpowers/specs/2026-11-15-jumpbox-installer-spikes.md`

- [ ] **Step 1: Write `wsl-service-spike.ps1`** taking `-Mode system|account|logon`. It imports a minimal rootfs (Alpine mini rootfs, downloaded and SHA-256-checked inside the script) as `povengine-spike`, starts `dockerd` inside it, runs `docker run --rm hello-world` and a long-lived `busybox httpd -p 18888`. It registers a Windows service (`system`: LocalSystem; `account`: a local account `povengine-spike-svc` with "Log on as a service", random password), or a sign-in scheduled task (`logon`), and writes every observation to `spike-result.json`.
- [ ] **Step 2: Run it on the owner's homelab Windows machine for each mode.** Record for each mode: Windows build and edition, `wsl --version`, whether the distribution starts from the service with nobody signed in, whether it survives a reboot, whether the VM idle-shuts down with only the service attached, whether `netsh interface portproxy` to the distribution reaches `busybox httpd` from another machine, and whether mirrored networking is available. Strip host names and addresses.
- [ ] **Step 3: Write `installer-spike.yml`** that runs on `windows-2022` and `windows-2025`: `wsl --update`, `wsl --import` the same rootfs, `wsl -d povengine-spike -- dockerd &` then `docker info`. Dispatch it and record pass/fail per image with the run id.
- [ ] **Step 4: Record the results** in `2026-11-15-jumpbox-installer-spikes.md`: one table per spike, the chosen service mode (first of `system` → `account` → `logon` that passed), and whether hosted runners can carry the Windows end-to-end test.
- [ ] **Step 5: Commit**

```bash
git add installer/spikes/wsl-service-spike.ps1 .github/workflows/installer-spike.yml docs/superpowers/specs/2026-11-15-jumpbox-installer-spikes.md
git commit -m "chore(ci): spike WSL-from-a-service and WSL2 on hosted Windows runners"
```

---

## Phase 1 — SimCore side

### Task 2: `sources/` runtime trees in the image (§8.5)

**Files:**
- Modify: `core/Dockerfile` (runtime stage, beside the other content `COPY` lines)
- Create: `tests/installer/test_image_sources_parity.py`
- Modify: `Makefile` (new target `check-image-sources`)
- Modify: `.github/workflows/ci.yml` (backend job: run `make IMAGE=cortexsim:ci check-image-sources` after `check-agent-shelf`)

**Interfaces:**
- Produces: `runtime_source_trees() -> set[str]` in the test module — the `sources/<name>` directories SimCore reads at runtime, derived from `core/tools/registry.py` `source_path` values and the agentic-pack lookup in `core/eal_simulator/plugins/agentic_egress.py`.

- [ ] **Step 1: Write the failing test** `test_dockerfile_copies_every_runtime_source_tree`: parse `core/Dockerfile` runtime-stage `COPY sources/<name>/ /app/sources/<name>/` lines; assert `runtime_source_trees() - copied == set()`. Also `test_dockerfile_never_copies_private_origin_trees`: `{"CDR", "xsiam-prisma-cdr-lab"} & copied == set()`. And `test_runtime_source_trees_are_known`: the derived set equals `{"cortex-malicious-agentic-pack", "cortex-prompt-attacker", "cortex-vulnerable-llm", "cortex-browser-attacker", "mocktaxii", "gocortexbrokenbank"}` plus any `source_path` tree the registry adds (rust tools already compiled into `/app/rust-dist` are excluded because the image never runs them from `sources/`).
- [ ] **Step 2: Run** `PYTHONPATH=core .venv/bin/python -m pytest tests/installer/test_image_sources_parity.py -v` — Expected: FAIL listing the missing trees.
- [ ] **Step 3: Add one directory-level `COPY` per tree** to the runtime stage (directory-level so an uninitialised submodule still builds, as the rust-builder comment explains).
- [ ] **Step 4: Run the test** — Expected: PASS.
- [ ] **Step 5: Add `check-image-sources`** to the Makefile: `docker run --rm --entrypoint sh $(IMAGE) -c 'test -d /app/sources/<name> && …'` over the same list, printing each tree and failing on the first missing one. Run `make build && make check-image-sources` — Expected: every tree printed, exit 0.
- [ ] **Step 6: Commit**

```bash
git add core/Dockerfile tests/installer/test_image_sources_parity.py Makefile .github/workflows/ci.yml
git commit -m "fix(docker): ship the sources/ trees SimCore reads at runtime"
```

### Task 3: Payload shelf staged by the release, manifest generator extracted (§11, D2)

**Files:**
- Create: `scripts/release/build_manifest.py` (the inline Python in `release.yml`'s "Generate manifest.json" step, moved here unchanged, then extended)
- Create: `tests/release/test_build_manifest.py`
- Modify: `.github/workflows/release.yml` (build-image: stage the shelf before Build and push; manifest: call the script; release: notes paragraph)

**Interfaces:**
- Produces: `build_manifest(dist: Path, *, tag: str, image: str, digest: str, repo: str, agent_go_version: str, shelf_sources: Path, installer: dict | None = None) -> dict` and a `__main__` reading the same env vars the step used. Task 15 passes `installer`.

- [ ] **Step 1: Write the failing tests**: `test_manifest_matches_the_inline_generator` (a fixture `dist/` with five fake beacons, `SHA256SUMS`, buildinfo: output keys and `agent` block equal what today's inline code produces); `test_manifest_lists_shelf_artifacts` (`manifest["shelf"]` is a list of `{name, sha256, license, pin_type, pin_ref, adapter_id}` taken from `payloads/sources.json`'s `payloads`, 8 entries on the real file); `test_manifest_refuses_incomplete_beacons` (four beacons → `SystemExit`).
- [ ] **Step 2: Run** `PYTHONPATH=core .venv/bin/python -m pytest tests/release/test_build_manifest.py -v` — Expected: FAIL (module missing).
- [ ] **Step 3: Implement `build_manifest`** by moving the inline code and adding the `shelf` block.
- [ ] **Step 4: Run the tests** — Expected: PASS.
- [ ] **Step 5: Wire the workflow.** In `build-image`, before "Build and push": `run: bash scripts/build-payloads.sh` with `env: PAYLOAD_ALLOW_UNPINNED: "0"`. In `manifest`, replace the heredoc with `python3 scripts/release/build_manifest.py`. In the notes step, add one paragraph: the image and every package carry the payload shelf, each artifact pinned, licences in `manifest.json` → `shelf`.
- [ ] **Step 6: Verify the shelf stages** locally: `PAYLOAD_ALLOW_UNPINNED=0 bash scripts/build-payloads.sh && ls payloads/` — Expected: the 8 artifacts plus `sources.json` and `README.md`, exit 0.
- [ ] **Step 7: Commit**

```bash
git add scripts/release/build_manifest.py tests/release/test_build_manifest.py .github/workflows/release.yml
git commit -m "chore(ci): stage the payload shelf in the release and list it in the manifest"
```

### Task 4: `install` block in `/api/health` and the Get started line (§8.4, §6.3)

**Files:**
- Modify: `core/config.py` (Settings: `CORTEXSIM_INSTALLED_BY: str = ""`, `CORTEXSIM_INSTALL_ADDRESS: str = ""`)
- Modify: `.env.example` (document both; `tests/test_env_example_coverage.py` requires it)
- Modify: `core/api/health.py` (`health_payload` gains a top-level `install` key)
- Create: `tests/api/test_health_install_block.py`
- Modify: `ui/src/components/console/GetStartedView.jsx` (one line under the `h1`)
- Test: `ui/src/components/console/__tests__/GetStartedInstallLine.test.jsx`

**Interfaces:**
- Produces: `install_block() -> dict | None` in `core/api/health.py`: `None` when `CORTEXSIM_INSTALLED_BY` is empty, else `{"installed_by": str, "address": str, "listen": "all" | "address", "platform": "linux" | "windows"}`. `listen` is `"address"` when `CORTEXSIM_BIND` is set; `platform` comes from `CORTEXSIM_INSTALLED_BY`'s suffix `…/<version>+<platform>`.

- [ ] **Step 1: Write the failing tests**: `test_install_block_absent_on_compose` (`health_payload()["install"] is None` with the vars unset); `test_install_block_present_when_installed` (vars `povengine-setup/1.3.0+linux` and `192.0.2.10:8888` → `{"installed_by": "povengine-setup/1.3.0", "address": "192.0.2.10:8888", "listen": "all", "platform": "linux"}`); `test_install_block_never_carries_secret` (no value in the block equals `settings.CORTEXSIM_SECRET`). UI: `renders install line when health.install is set` (text `Installed by setup 1.3.0 · listening at 192.0.2.10:8888`), `renders nothing when health.install is null`.
- [ ] **Step 2: Run** `PYTHONPATH=core .venv/bin/python -m pytest tests/api/test_health_install_block.py tests/test_env_example_coverage.py -v` and `cd ui && npx vitest run src/components/console/__tests__/GetStartedInstallLine.test.jsx` — Expected: FAIL.
- [ ] **Step 3: Implement** `install_block()`, add `"install": install_block()` to `health_payload`, document the two vars in `.env.example`, and render the line from `useEnvironment().health?.install` (plain text, no new component).
- [ ] **Step 4: Run the same commands** — Expected: PASS. Also run `PYTHONPATH=core .venv/bin/python -m pytest tests/test_health.py -v`; if a test pins the top-level key set, add `install` to it.
- [ ] **Step 5: Commit**

```bash
git add core/config.py .env.example core/api/health.py tests/api/test_health_install_block.py ui/src/components/console/GetStartedView.jsx ui/src/components/console/__tests__/GetStartedInstallLine.test.jsx
git commit -m "feat(api): report an installer-managed SimCore in /api/health"
```

---

## Phase 2 — The setup program

### Task 5: Module, step engine, result codes, log writer, CLI

**Files:**
- Create: `installer/setup/go.mod`, `installer/setup/main.go`
- Create: `installer/setup/internal/engine/{engine.go,codes.go,engine_test.go}`
- Create: `installer/setup/internal/host/{host.go,fake.go}`
- Create: `installer/setup/internal/logx/{logx.go,logx_test.go}`
- Create: `installer/setup/internal/cli/{cli.go,cli_test.go}`
- Modify: `.github/workflows/ci.yml` (new job `installer`: `actions/setup-go` pinned as in `agent`, `go-version: '1.22'`; `go vet ./...` and `go test -race -count=1 ./...` in `installer/setup`; cross-compile the three targets)
- Modify: `Makefile` (`test-installer`, `installer-setup` targets)

**Interfaces:**
- Produces (engine):
  - `type Code string` with one constant per §13 code (`CodeOK = "OK"`, `CodeNotElevated = "NOT_ELEVATED"`, …).
  - `type Result struct { Code Code; Detail string; Warning bool }`; `func (r Result) Blocks() bool` (true when `Code != CodeOK && !Warning`).
  - `type Choices struct { Address string; Port int; ListenOnly bool; Firewall bool; Proxy string; Purge bool }` (`Proxy`: "" = use detected, "none" = no proxy, else a URL).
  - `type State struct { Version, Platform string; Choices Choices; Facts map[string]string }`.
  - `type Step interface { ID() string; Title() string; Check(ctx context.Context, st *State) Result; Apply(ctx context.Context, st *State) Result }`.
  - `type Mode int` (`ModeCheck`, `ModeApply`); `type Event struct { StepID, Phase string; Result Result }`; `type Report struct { Events []Event; Final Code }`.
  - `func Run(ctx context.Context, steps []Step, st *State, mode Mode, emit func(Event)) Report`.
- Produces (host): `type Host interface { Run(ctx context.Context, name string, args ...string) (stdout, stderr []byte, err error); ReadFile(string) ([]byte, error); WriteFile(string, []byte, fs.FileMode) error; MkdirAll(string, fs.FileMode) error; RemoveAll(string) error; Stat(string) (fs.FileInfo, error); LookPath(string) (string, error); HTTPGet(ctx context.Context, url string) (int, []byte, error) }`; `OS() Host`; `type Fake struct { Files map[string][]byte; Responses map[string]FakeResponse; Calls []Call }` implementing Host.
- Produces (logx): `func New(w io.Writer) *Logger`; `func (l *Logger) Redact(secret string)`; `func (l *Logger) Event(e engine.Event)`; `func (l *Logger) Line(format string, args ...any)`.
- Produces (cli): `func Parse(args []string) (Command, error)` where `Command{Name string /* "", serve, check, install, upgrade, uninstall, status */; Choices engine.Choices; Yes bool; URLFile string; ConfirmHost string}`; `--purge` and `--confirm-host <name>` are accepted only with `uninstall`; an unknown flag or command returns `ErrUsage` and `main` exits 2.

- [ ] **Step 1: Write the failing tests.** `TestRun_CheckModeRunsEveryCheckAndNoApply` (three fake steps, the second blocking: all three `Check` called, zero `Apply`, `Final` = second step's code). `TestRun_ApplyStopsAtFirstBlockingCheck` (first passes and applies, second blocks: second's `Apply` never called, third untouched). `TestRun_WarningDoesNotBlock`. `TestRun_ApplyFailureStops`. `TestLogger_RedactsRegisteredSecret` (after `Redact("s3cr3t-value")`, `Line("x=%s", "s3cr3t-value")` writes `x=[redacted]`). `TestParse_UnknownFlagIsUsageError` (`install --frobnicate` → `ErrUsage`). `TestParse_InstallFlags` (`install --address 192.0.2.10 --port 9000 --listen address --no-firewall --proxy none --yes` → the matching `Choices`). `TestParse_PurgeOnlyWithUninstall`.
- [ ] **Step 2: Run** `cd installer/setup && go test ./...` — Expected: FAIL (packages missing).
- [ ] **Step 3: Implement** the four packages and `main.go` (dispatch on `Command.Name`; unknown → exit 2 with usage on stderr; `--version` prints `main.version`).
- [ ] **Step 4: Run** `go vet ./... && go test -race -count=1 ./...` and `GOOS=windows GOARCH=amd64 go build ./... && GOOS=linux GOARCH=arm64 go build ./...` — Expected: PASS, builds succeed.
- [ ] **Step 5: Commit**

```bash
git add installer/setup/go.mod installer/setup/main.go installer/setup/internal/engine installer/setup/internal/host installer/setup/internal/logx installer/setup/internal/cli .github/workflows/ci.yml Makefile
git commit -m "feat(docker): add the povengine-setup step engine and CLI"
```

### Task 6: Paths, master secret and the env file (§8.1–§8.3, §8.6)

**Files:**
- Create: `installer/setup/internal/config/{paths.go,secret.go,envfile.go,config_test.go}`

**Interfaces:**
- Produces: `type Paths struct { Program, ConfigDir, SecretFile, EnvFile, DataDir, EngineData, Backups, Logs, StateFile string }`; `func DefaultPaths(goos string) Paths` (values from Global Constraints; `StateFile` = `<ConfigDir>/setup-state.json`).
- Produces: `func GenerateSecret() (string, error)`; `func EnsureSecret(h host.Host, p Paths) (secret string, created bool, err error)` (reads an existing secret file, else generates and writes it 0600 / admin-only ACL on Windows via the host adapter).
- Produces: `type EnvInput struct { Secret, Version, Platform, Address string; Port int; ListenOnly bool; ShortName, FQDN string; Proxy string; LocalAddrs []string }`; `func RenderEnv(in EnvInput) []byte` (stable key order); `func PublicLines(env []byte) []string` (every line except `CORTEXSIM_SECRET`, with any `user:pass@` in a proxy URL replaced by `***@`).

- [ ] **Step 1: Write the failing tests.** `TestGenerateSecret_LengthAndAlphabet` (64 characters, only `[A-Za-z0-9_-]`, two calls differ). `TestEnsureSecret_KeepsExisting` (fake host with a secret file → returned unchanged, `created == false`, no write). `TestEnsureSecret_WritesOwnerOnly` (fake records mode `0600` on Linux). `TestRenderEnv_HostNames` (ShortName `JUMPBOX01`, FQDN `""` → `CORTEXSIM_ALLOWED_HOSTS=jumpbox01`; ShortName `jumpbox01`, FQDN `jumpbox01.corp.example`, Address `pov.corp.example` → `jumpbox01,jumpbox01.corp.example,pov.corp.example`; never a leading, trailing or doubled comma). `TestRenderEnv_PublicURLAndInstallVars` (Address `192.0.2.10`, Port 8888 → `CORTEXSIM_PUBLIC_URLS=http://192.0.2.10:8888`, `CORTEXSIM_INSTALL_ADDRESS=192.0.2.10:8888`, `CORTEXSIM_INSTALLED_BY=povengine-setup/1.3.0+linux`, `CORTEXSIM_ENV=production`). `TestRenderEnv_BindOnlyWhenListenOnly`. `TestRenderEnv_ProxyAndNoProxy` (Proxy `http://198.51.100.7:3128` → `HTTPS_PROXY`, `HTTP_PROXY` and `NO_PROXY=localhost,127.0.0.1,::1,jumpbox01,192.0.2.10` + `LocalAddrs`; Proxy `none` → no proxy keys). `TestPublicLines_NeverContainsSecret`.
- [ ] **Step 2: Run** `go test ./internal/config/...` — Expected: FAIL.
- [ ] **Step 3: Implement** the three files.
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit**

```bash
git add installer/setup/internal/config
git commit -m "feat(docker): write the installer's env file and master secret"
```

### Task 7: Linux checks (§7.1, §6.1 rows)

**Files:**
- Create: `installer/setup/internal/steps/linux/{checks.go,address.go,proxy.go,protection.go,checks_test.go}`
- Create: `installer/setup/internal/steps/linux/testdata/` (`os-release` samples, `ip -j route`/`ip -j addr` JSON, `/proc/self/mountinfo` with and without cgroup2, `ss -Hltn` output)

**Interfaces:**
- Produces steps (each an `engine.Step`): `HostCheck` (OS from `/etc/os-release`: Ubuntu 22.04/24.04 supported, RHEL-family 9 warning `OS_UNSUPPORTED`+Warning "best effort", anything else blocking `OS_UNSUPPORTED`; arch amd64/arm64 else `ARCH_UNSUPPORTED`; systemd as PID 1 else `NO_SYSTEMD`; cgroup2 mounted else `NO_CGROUP_V2`; free disk ≥ 10 GiB under `/var/lib` else `DISK_LOW`; memory ≥ 4 GiB else `MEMORY_LOW`), `PortFree` (`PORT_IN_USE` unless the holder is our own `povengine-simcore`), `ComposeAbsent` (`docker ps` against the default socket, when present, lists a `cortex-pov-engine-simcore-*` container → `COMPOSE_DEPLOYMENT_PRESENT` with `docker compose -p cortex-pov-engine stop` in Detail), `AddressStep`, `ProxyStep`, `EndpointProtection` (`/opt/traps` or the `traps_pmd` process → Warning `ENDPOINT_PROTECTION_EXCLUSION_NEEDED` with the paths `/opt/povengine`, `/var/lib/povengine` in Detail).
- Produces: `func AddressCandidates(routeJSON, addrJSON []byte) ([]string, error)` (default-route interface's IPv4 first, then other global IPv4, then global IPv6; loopback and link-local never); `func DetectProxy(env []string, etcEnvironment []byte) (url string, pac bool)`.

- [ ] **Step 1: Write the failing tests** with the testdata: `TestHostCheck_Ubuntu2404Passes`, `TestHostCheck_Rhel9IsBestEffortWarning`, `TestHostCheck_Debian11Refused`, `TestHostCheck_NoCgroupV2Refused`, `TestPortFree_OwnContainerIsFine`, `TestComposeAbsent_NamesTheContainer`, `TestAddressCandidates_DefaultRouteFirst` (two NICs `192.0.2.10` on the default route and `198.51.100.20` → `["192.0.2.10", "198.51.100.20"]`), `TestAddressCandidates_SkipsLinkLocal`, `TestDetectProxy_EnvWinsOverEtcEnvironment`, `TestDetectProxy_PacIsFlagged` (`auto_proxy=` / a `.pac` URL → `pac == true`, which the step reports as Warning `PROXY_PAC_UNSUPPORTED`), `TestEndpointProtection_XdrWarns`.
- [ ] **Step 2: Run** `go test ./internal/steps/linux/...` — Expected: FAIL.
- [ ] **Step 3: Implement**; every host read goes through `host.Host` so the tests run on any OS (`//go:build` tags are not needed for pure parsing).
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit**

```bash
git add installer/setup/internal/steps/linux
git commit -m "feat(docker): add the installer's Linux host checks"
```

### Task 8: Linux runtime — private engine, SimCore service, firewall, health gate (§7.1, §9)

**Files:**
- Create: `installer/setup/internal/runtime/{dockerargs.go,health.go,runtime_test.go}`
- Create: `installer/setup/internal/steps/linux/{engine.go,service.go,firewall.go,units.go,runtime_test.go}`
- Create: `installer/setup/internal/steps/linux/testdata/{povengine-engine.service.golden,povengine.service.golden,health-*.json}`

**Interfaces:**
- Consumes: `config.Paths`, `config.RenderEnv` (Task 6); `payload.Package` (Task 10 — until it lands, steps take `ImagePath, EngineArchive, ImageID string`).
- Produces: `type RunInput struct { Image, Name, EnvFile, DataDir string; Address string; Port int; ListenOnly bool }`; `func RunArgs(in RunInput) []string` — the args after `docker -H unix:///run/povengine/docker.sock`; always `run -d --name povengine-simcore --network host --restart unless-stopped --env-file <EnvFile> -v <DataDir>:/app/data`; when `ListenOnly` or `Port != 8888`, also `--health-cmd "curl -fsS --max-time 5 -o /dev/null http://<host>:<port>/api/health"` and the image command `uvicorn main:app --host <host> --port <port> --workers 1 --timeout-graceful-shutdown 5` (`<host>` = Address when ListenOnly, else `0.0.0.0`; health host `127.0.0.1` unless ListenOnly).
- Produces: `type HealthVerdict struct { Code engine.Code; Faults []string }`; `func JudgeHealth(body []byte) (HealthVerdict, error)` — an unparseable body or a body without `components` is an error (`SIMCORE_UNHEALTHY`), never "ok"; `components.master_key.code == "MASTER_KEY_MISMATCH"` → that code; `degraded_components` minus `api_auth` → `Faults` with `Code = OK`.
- Produces steps: `Engine` (extract the static archive into `/opt/povengine/engine`, write and enable `povengine-engine.service`: `ExecStart=/opt/povengine/engine/dockerd --host unix:///run/povengine/docker.sock --data-root /var/lib/povengine/engine --exec-root /run/povengine/exec --pidfile /run/povengine/dockerd.pid --iptables=false --ip-masq=false --bridge=none`; `Check` returns `ENGINE_START_FAILED` when the socket does not answer `docker version` within 30 s after start), `Image` (`docker load -i`, then `docker image inspect --format {{.Id}}` must equal the package's image id, else `IMAGE_ID_MISMATCH`), `Service` (write and enable `povengine.service` running `RunArgs`), `Firewall` (`ufw allow <port>/tcp` when `ufw status` is active, else `firewall-cmd --permanent --add-port=<port>/tcp && firewall-cmd --reload` when firewalld runs, else Result OK with Detail "no host firewall active"; failure → `FIREWALL_RULE_FAILED`), `HealthGate` (container health `healthy` within 120 s, `JudgeHealth` on loopback, then `HTTPGet http://<address>:<port>/api/health` succeeds, else `ADDRESS_UNREACHABLE`).

- [ ] **Step 1: Write the failing tests.** `TestRunArgs_Default` (golden argv for `192.0.2.10`, 8888, not listen-only: exactly the fixed prefix + image, no `--health-cmd`). `TestRunArgs_ListenOnlyOverridesHostAndHealth`. `TestRunArgs_NonDefaultPort`. `TestJudgeHealth_ApiAuthOnlyIsOK` (fixture `health-api-auth-only.json` → `OK`, no faults). `TestJudgeHealth_OtherDegradedListed` (`api_auth` + `payload_shelf` → `OK`, `Faults == ["payload_shelf"]`). `TestJudgeHealth_MasterKeyMismatch`. `TestJudgeHealth_UnknownShapeIsError` (`{"status":"ok"}` with no `components` → error). `TestUnits_MatchGolden` (both unit files). `TestFirewall_UfwActive`, `TestFirewall_FirewalldRunning`, `TestFirewall_NoneActive`, `TestImage_IdMismatchRefuses`, `TestHealthGate_AddressUnreachable` (loopback ok, address fails → `ADDRESS_UNREACHABLE`).
- [ ] **Step 2: Run** `go test ./internal/runtime/... ./internal/steps/linux/...` — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit**

```bash
git add installer/setup/internal/runtime installer/setup/internal/steps/linux
git commit -m "feat(docker): run SimCore on the installer's private engine"
```

### Task 9: Install state, upgrade, rollback, uninstall (§10)

**Files:**
- Create: `installer/setup/internal/lifecycle/{state.go,plan.go,upgrade.go,uninstall.go,lifecycle_test.go}`

**Interfaces:**
- Consumes: `engine.Step`, `host.Host`, `config.Paths`, `runtime.JudgeHealth`.
- Produces: `type InstallState struct { Version, Platform, ImageID, PreviousImageID string; Choices engine.Choices; InstalledAt time.Time }` stored as JSON in `Paths.StateFile` (no secret); `func LoadState(h host.Host, p config.Paths) (*InstallState, error)` (`nil, nil` when absent).
- Produces: `func CompareVersions(a, b string) (int, error)` (semver `X.Y.Z[-pre]`; a pre-release sorts before its release).
- Produces: `type Action string` (`ActionInstall`, `ActionUpgrade`, `ActionAlreadyInstalled`, `ActionDowngradeRefused`); `func PlanFor(pkgVersion string, st *InstallState) Action`.
- Produces: `type StatusView struct { Installed bool; State *InstallState; Container, Health string; Faults []string; EnvLines []string }`; `func Status(ctx context.Context, h host.Host, p config.Paths) (StatusView, error)` — read only; `EnvLines` comes from `config.PublicLines`, so the secret never appears.
- Produces: `func RunsInFlight(body []byte) ([]string, error)` — parses `GET /api/runs?fields=summary&status=<s>` (`{"runs":[{"id":…}],"total":N}`) for `running` and `queued`; an unknown shape is an error, never "none".
- Produces steps: `UpgradeSteps(...) []engine.Step` in this order — ensure the current SimCore is running (start it if stopped, wait for health) → refuse on runs in flight (`RUNS_IN_FLIGHT`, ids in Detail) → online backup into `Backups/<UTC>/` (`docker exec povengine-simcore python -c` SQLite `backup()` to `/app/data/cortexsim.backup.db`, then copy out with the public env lines and the old image id) → load new image → swap the engine if its version changed → restart → `HealthGate`; on a blocking result after the swap, the rollback (stop, restore the previous image, remove every `cortexsim.db*` then copy the backup in, start, health) runs and the report's `Final` is `UPGRADE_ROLLED_BACK` with the failing code in Detail. `UninstallSteps(purge bool, confirmHost string)`: final backup → remove service, container, image, engine, firewall rule → with `purge`, remove data, secret and backups only when `confirmHost` equals the host's short name (else `UNINSTALL_INCOMPLETE` with Detail "purge not confirmed").

- [ ] **Step 1: Write the failing tests.** `TestCompareVersions` (table: `1.3.0` vs `1.2.9` → 1; `1.3.0-rc.1` vs `1.3.0` → -1; `1.3` → error). `TestPlanFor_ExistingInstall` (no state → Install; same version → AlreadyInstalled; older installed → Upgrade; newer installed → DowngradeRefused). `TestRunsInFlight_ParsesIds`, `TestRunsInFlight_UnknownShapeIsError`. `TestUpgrade_RefusesWithRunsInFlight` (fake `/api/runs` returns one queued run → `RUNS_IN_FLIGHT`, no image load call recorded). `TestUpgrade_StartsStoppedSimCoreBeforeCheckingRuns`. `TestUpgrade_RollbackOrder` (health gate fails after swap → fake call log shows stop, previous image tag, remove `cortexsim.db`, `cortexsim.db-wal`, `cortexsim.db-shm`, copy backup, start — in that order — and `Final == UPGRADE_ROLLED_BACK`). `TestUninstall_KeepsDataByDefault`, `TestUninstall_PurgeNeedsHostName`, `TestState_NeverContainsSecret`, `TestStatus_PrintsPublicLinesOnly` (a fake env file with a secret → `EnvLines` lacks `CORTEXSIM_SECRET`; a running container → `Health` from `JudgeHealth`).
- [ ] **Step 2: Run** `go test ./internal/lifecycle/...` — Expected: FAIL.
- [ ] **Step 3: Implement.** The Linux and Windows host adapters supply the platform commands; this package only orders steps.
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit**

```bash
git add installer/setup/internal/lifecycle
git commit -m "feat(docker): add installer upgrade with rollback and data-keeping uninstall"
```

### Task 10: Package format, verification and the assembler (§11)

**Files:**
- Create: `installer/setup/internal/payload/{payload.go,trailer.go,payload_test.go}`
- Create: `installer/setup/cmd/assemble/main.go` (release-time tool, same module)

**Interfaces:**
- Produces: `type File struct { Path, SHA256 string; Size int64 }` and `type Manifest struct { SchemaVersion int; Version, Platform, ImageRef, ImageID, EngineVersion, WSLVersion string; Files []File }`, serialised as `package.json` with snake_case JSON keys (`schema_version`, `image_id`, `engine_version`, `wsl_version`, `files[].sha256`, …).
- Produces: the appended-payload trailer — the last 16 bytes of a Windows package are the ASCII magic `POVPAYLD` followed by the zip's start offset as a little-endian `uint64`; `func Append(exe, zip io.Reader, out io.Writer) error`.
- Produces: `func Open(selfPath string) (*Package, error)` — trailer present → zip via `io.NewSectionReader`; else `package.json` and `payload/` beside the executable (Linux tarball); `func (p *Package) Verify() error` — every listed file's size and SHA-256, returning an error wrapping `engine.CodePayloadCorrupt` that names the first bad file; `func (p *Package) Extract(name, dest string) error`.
- Produces (assemble CLI): `assemble --platform linux-amd64|linux-arm64|windows-amd64 --version <v> --setup <binary> --payload-dir <dir> --out <file>` writing `povengine-<v>-linux-<arch>.tar.gz` (top dir `povengine-<v>/` with `povengine-setup`, `package.json`, `payload/`, `README.txt`; mode 0755 on the binary) or `povengine-setup-<v>-windows-amd64.exe`.

- [ ] **Step 1: Write the failing tests.** `TestAppendThenOpen_RoundTrip` (fake exe bytes + a zip with two files → `Open` lists both, `Verify` passes). `TestOpen_TruncatedPayloadRefusesBeforeApply` (drop the last 100 KiB of an assembled file → `Open` or `Verify` returns `PAYLOAD_CORRUPT`; a plan built over it runs zero `Apply` calls). `TestVerify_TamperedFileNamed` (one flipped byte in `payload/simcore-image.tar.gz` → error names that path). `TestOpen_TarballLayout` (directory layout beside a fake executable). `TestAssemble_TarballKeepsExecBit` (`assemble` output untarred → `povengine-setup` mode 0755).
- [ ] **Step 2: Run** `go test ./internal/payload/... ./cmd/assemble/...` — Expected: FAIL.
- [ ] **Step 3: Implement.** `main.go` calls `payload.Open(os.Executable())` then `Verify()` before building any plan; a failure exits with the code and touches nothing.
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit**

```bash
git add installer/setup/internal/payload installer/setup/cmd/assemble installer/setup/main.go
git commit -m "feat(docker): add the installer package format and assembler"
```

---

## Phase 3 — The wizard

### Task 11: Launcher, elevation and the local wizard server (§5.3, §5.4)

**Files:**
- Create: `installer/setup/internal/wizard/{server.go,api.go,server_test.go}`
- Create: `installer/setup/internal/launch/{launch.go,launch_linux.go,launch_windows.go,launch_test.go}`

**Interfaces:**
- Consumes: `engine.Run`, `lifecycle.PlanFor`, the platform plan builders.
- Produces: `type Driver interface { Plan(ctx context.Context) PlanView; Start(ctx context.Context, action lifecycle.Action, c engine.Choices) error; Progress() ProgressView; Restart(ctx context.Context) error }`; `type PlanView struct { Version, Platform string; Action lifecycle.Action; Rows []RowView; Choices engine.Choices; Candidates []string }`; `type RowView struct { ID, Title string; Code engine.Code; Detail string; Warning, Editable bool }`; `type ProgressView struct { Running, Done bool; Events []engine.Event; Final engine.Code; ConsoleURL string; RestartRequired bool }`.
- Produces: `type Options struct { Token string; IdleTimeout time.Duration; Now func() time.Time }` (`IdleTimeout` default 30 min; `Token` generated as 32 random bytes hex when empty); `func NewServer(d Driver, o Options) (*Server, error)` (listens on `127.0.0.1:0`); `func (s *Server) URL() string` (`http://127.0.0.1:<port>/#token=<token>`); `func (s *Server) Serve(ctx context.Context) error`.
- API (all JSON, all require header `X-Setup-Token: <token>` and, when present, `Origin == http://127.0.0.1:<port>`, and `Host == 127.0.0.1:<port>`; otherwise 403): `GET /api/plan` → PlanView; `POST /api/run` body `{"action": "install|upgrade|uninstall", "choices": {...}, "purge": bool, "confirm_host": "..."}` with `DisallowUnknownFields` → 202, 409 when already running; `GET /api/progress` → ProgressView; `POST /api/restart` → 202 (Windows only, else 404). `GET /` and static assets need no token (the page carries none until it reads the fragment).
- Produces: `func Launch(self string, args []string) error` — unprivileged: create a 0600 URL file in the user's temp dir, start `self serve --url-file <path>` elevated (Linux: `pkexec` when `DISPLAY` or `WAYLAND_DISPLAY` is set and `pkexec` is on PATH, else `sudo`; Windows: `ShellExecuteW` with `runas`), wait up to 120 s for the file, open the URL (`xdg-open` / `rundll32 url.dll,FileProtocolHandler`), exit. If a `serve` for this install is already running (its URL file in `Paths.ConfigDir` is live), open that URL instead (reattach). Already elevated: serve directly and print the URL. No display: print the CLI form and `ssh -L <port>:127.0.0.1:<port> <host>`.

- [ ] **Step 1: Write the failing tests.** `TestServer_RejectsMissingToken` (403), `TestServer_RejectsWrongOrigin` (`Origin: http://evil.example` → 403), `TestServer_RejectsWrongHost`, `TestServer_RejectsUnknownField` (`{"action":"install","script":"x"}` → 400), `TestServer_RunTwiceIs409`, `TestServer_IdleTimeoutShutsDown` (fake clock advanced 31 min with no request → `Serve` returns), `TestServer_IdleTimeoutWaitsForApply` (apply in flight + 31 min → still serving), `TestServer_ExitsAfterDone`, `TestLauncher_ReattachesToRunningServe` (live URL file → no elevation call recorded, URL opened), `TestLauncher_NoDisplayPrintsCLIForm`.
- [ ] **Step 2: Run** `go test ./internal/wizard/... ./internal/launch/...` — Expected: FAIL.
- [ ] **Step 3: Implement.** Static files come from `internal/web` (Task 12); until then serve a placeholder page from an `embed.FS` with one `index.html`.
- [ ] **Step 4: Run** — Expected: PASS; `GOOS=windows go build ./...` succeeds.
- [ ] **Step 5: Commit**

```bash
git add installer/setup/internal/wizard installer/setup/internal/launch
git commit -m "feat(docker): serve the installer wizard on loopback behind a one-time token"
```

### Task 12: The wizard page (§5.5, §6)

**Files:**
- Create: `ui/setup.html`, `ui/src/setup/{main.jsx,SetupApp.jsx,setupApi.js,rows.js,setup.css}`
- Test: `ui/src/setup/__tests__/SetupApp.test.jsx`, `ui/tests/e2e/20-setup-wizard.spec.ts`
- Modify: `ui/vite.config.js` (a second build: `vite build --config vite.setup.config.js` or `build.rollupOptions.input` with `setup.html` emitting to `../installer/setup/internal/web/dist`)
- Create: `installer/setup/internal/web/{embed.go,dist/.gitkeep}` (`//go:embed dist`)
- Modify: `ui/package.json` (`"build:setup"` script), `.gitignore` (`installer/setup/internal/web/dist/*` except `.gitkeep`)

**Interfaces:**
- Consumes: the Task 11 API and JSON shapes.
- Produces: `rowState(row) -> 'ok' | 'warn' | 'refuse'` in `rows.js` (refuse = `code !== 'OK' && !warning`); `consoleUrlFor(progress) -> string` (returns `progress.console_url`, which the server builds from the chosen address — the page never builds it from `window.location`).

States by `PlanView.Action`: `install` → Screen 1 with `Install`; `upgrade` → the same rows with `Upgrade`; `already_installed` → one line `POVengine <version> is installed` with `Open console` and a `Remove` disclosure; `downgrade_refused` → the refusal line only. `Remove` (shown whenever an install exists) posts `{"action": "uninstall"}`; its "Also delete data" option needs the host's short name typed into a field before the button enables, and posts `purge: true` with `confirm_host`.

Copy, verbatim from §6.1: title `POVengine <version> — install on this jumpbox`; buttons `Install`, `Upgrade`, `Remove`, `Restart now`, `Open console`; done line `SimCore is running at <url>`; health line `Health: ok (API sign-in not yet available — expected)`; restart line `Windows needs to restart to finish enabling WSL.` / `Setup continues on its own after you sign in again.`; Firewall detail `The API has no sign-in yet, so anyone who reaches the port can use it.` Each row is one line with a StatusIcon; detail behind a disclosure.

- [ ] **Step 1: Write the failing tests.** Vitest: `renders one line per row with its status`, `replaces Install with the remedy when a row refuses` (a `VIRTUALIZATION_UNAVAILABLE` row → no `Install` button, the row's Detail shown), `warn row keeps Install enabled`, `shows Upgrade when action is upgrade`, `already installed shows Open console and no Install`, `purge stays disabled until the host name is typed`, `Open console uses console_url from progress` (progress `console_url: "http://192.0.2.10:8888/#/get-started"` → link href equal, never `localhost`), `sends the token header from the URL fragment`. Playwright `20-setup-wizard.spec.ts`: against `povengine-setup serve` started with a fake driver (`--fake-driver` test-only flag guarded by build tag `setupfake`), drive Install → Done → Open console href.
- [ ] **Step 2: Run** `cd ui && npx vitest run src/setup` — Expected: FAIL.
- [ ] **Step 3: Implement** with kit Button, Icon, StatusIcon and the console tokens; no new shared components.
- [ ] **Step 4: Run** vitest — Expected: PASS. Run `npm run build:setup && cd ../installer/setup && go build ./...` — Expected: embed picks up `dist/index.html`. Run the Playwright spec per `project_playwright_on_this_mac` conventions locally, and in the `installer` CI job.
- [ ] **Step 5: Commit**

```bash
git add ui/setup.html ui/src/setup ui/tests/e2e/20-setup-wizard.spec.ts ui/vite.config.js ui/package.json .gitignore installer/setup/internal/web
git commit -m "feat(ui): add the installer wizard page"
```

---

## Phase 4 — Windows

### Task 13: The host distribution (§7.2)

**Files:**
- Create: `installer/host-distro/{Dockerfile,povengine-init.sh,README.md}`
- Create: `installer/vendor.json` with the `docker_static.{amd64,arm64}` entries `{version, url, sha256}` (Task 15 adds `wsl_msi`)
- Create: `tests/installer/test_host_distro.py`
- Modify: `Makefile` (`host-distro` target: `docker build` then `docker export` to `dist/installer/povengine-host-rootfs.tar`)

**Interfaces:**
- Produces: `installer/vendor.json`, read by this task's Dockerfile build and by Task 15's packaging script.
- Produces: a rootfs tar whose `/usr/local/sbin/povengine-init` starts `dockerd` with the same flags as Linux (`--iptables=false --ip-masq=false --bridge=none`, socket `/run/povengine/docker.sock`), loads `/opt/povengine/payload/simcore-image.tar.gz` when the image id differs, and runs SimCore with the `RunArgs` line written to `/etc/povengine/run-args` by the Windows steps (the init never computes it).

- [ ] **Step 1: Write the failing test** `test_host_distro_rootfs`: after `make host-distro`, the tar lists `usr/local/sbin/povengine-init` (mode 0755), `opt/povengine/engine/dockerd`, `etc/wsl.conf` with `[boot] command=/usr/local/sbin/povengine-init`, and no `/var/lib/povengine/data` content. Skip (with reason) when docker is unavailable.
- [ ] **Step 2: Run** `PYTHONPATH=core .venv/bin/python -m pytest tests/installer/test_host_distro.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement** (Debian 12 slim base; static engine from the pinned archive; `shellcheck` clean init).
- [ ] **Step 4: Run** — Expected: PASS. Also `docker run --rm --privileged <host-distro-image> /usr/local/sbin/povengine-init --self-test` starts `dockerd` and exits 0.
- [ ] **Step 5: Commit**

```bash
git add installer/host-distro tests/installer/test_host_distro.py Makefile
git commit -m "feat(docker): build the WSL host distribution for the Windows installer"
```

### Task 14: Windows steps (§7.2, §8.6, §10)

Uses the service mode recorded by Task 1.

**Files:**
- Create: `installer/setup/internal/steps/windows/{parse.go,checks.go,wsl.go,service.go,network.go,protection.go,parse_test.go}`
- Create: `installer/setup/internal/steps/windows/{wsl_windows.go,service_windows.go,steps_windows_test.go}`
- Create: `installer/setup/internal/steps/windows/testdata/` (UTF-16LE `wsl --status` / `wsl --version` captures, `systeminfo` Hyper-V sections, `netsh winhttp show proxy` outputs — all with invented names)

**Interfaces:**
- Produces (pure, cross-platform tests): `func DecodeWSLOutput(b []byte) string` (UTF-16LE with or without BOM → string); `func ParseWSLVersion(out string) (wslVersion string, err error)`; `func VirtualizationAvailable(systeminfo string) (bool, error)` ("A hypervisor has been detected" or every Hyper-V requirement "Yes" → true); `func ParseWinHTTPProxy(out string) (url string, err error)` ("Direct access (no proxy server)." → ""); `func PortProxyArgs(port int, connectAddr string) []string` (`interface portproxy add v4tov4 listenport=<port> listenaddress=0.0.0.0 connectport=<port> connectaddress=<connectAddr>`); `func MirroredSupported(build int) bool` (Windows 11 22H2 = build ≥ 22621, and not a Server SKU unless the spike recorded otherwise).
- Produces steps: `HostCheck` (`WIN_BUILD_UNSUPPORTED`, `WSL2_NOT_AVAILABLE` for Server 2019 / build < 19041, `VIRTUALIZATION_UNAVAILABLE` with the cloud/VMware remedies in Detail, `WSL_BLOCKED_BY_POLICY`), `EnableFeatures` (`VirtualMachinePlatform`, `Microsoft-Windows-Subsystem-Linux` via `dism /online /enable-feature /norestart`; exit code 3010 → `REBOOT_REQUIRED` and writes `setup-state.json` + a `RunOnce` value `POVengineSetup = "<program>\povengine-setup.exe"`), `InstallWSL` (bundled `.msi` via `msiexec /i … /qn` when `wsl --version` is absent or older than the pinned version), `ImportDistro` (`wsl --import povengine-host C:\ProgramData\POVengine\wsl <rootfs.tar> --version 2`, skipped when registered), `Service` (the Task 1 mode), `Network` (mirrored when supported; else `netsh interface portproxy` re-applied by the service on every start, and Warning text "Beacon addresses will read as this host"), `Firewall` (`netsh advfirewall firewall add rule name="POVengine <port>" dir=in action=allow protocol=TCP localport=<port>`), `EndpointProtection` (Cortex XDR service `cyserver` or Defender real-time on → Warning with the exclusion paths `C:\Program Files\POVengine`, `C:\ProgramData\POVengine`), and the Windows half of `lifecycle.UninstallSteps`: without purge, stop and keep `povengine-host` registered (its disk holds the database); with confirmed purge, `wsl --unregister povengine-host`.

- [ ] **Step 1: Write the failing tests** (pure, run everywhere): `TestDecodeWSLOutput_UTF16WithBOM`, `TestParseWSLVersion`, `TestVirtualizationAvailable_HypervisorDetected`, `TestVirtualizationAvailable_FirmwareDisabled` (false), `TestParseWinHTTPProxy_Direct`, `TestParseWinHTTPProxy_Set`, `TestPortProxyArgs`, `TestMirroredSupported_Server2022IsFalse`. Windows-only (`steps_windows_test.go`, run by the existing `windows` CI job): `TestEnableFeatures_RebootWritesStateAndRunOnce` against a fake host, `TestService_ReappliesPortProxyOnStart`, `TestUninstall_KeepsDistroWithoutPurge` (no `wsl --unregister` call recorded).
- [ ] **Step 2: Run** `go test ./internal/steps/windows/...` — Expected: FAIL.
- [ ] **Step 3: Implement.** Windows API calls (`ShellExecuteW`, registry `RunOnce`, SCM) go through the standard library's `syscall` only (no `golang.org/x/sys`), following `agent/service_windows.go`.
- [ ] **Step 4: Run** locally and with `GOOS=windows go vet ./...`; push and confirm the `windows` CI job runs the Windows-only tests — Expected: PASS.
- [ ] **Step 5: Commit**

```bash
git add installer/setup/internal/steps/windows
git commit -m "feat(docker): add the installer's Windows steps on WSL2"
```

---

## Phase 5 — Release and proof

### Task 15: `build-installer` release job (§11)

**Files:**
- Modify: `installer/vendor.json` (add `wsl_msi.x64`: `{version, url, sha256}`)
- Create: `scripts/release/build-installer-packages.sh` (`OUT_DIR`, `VERSION`, `IMAGE_REF` inputs; refuses an unpinned or mismatching vendor download)
- Create: `tests/release/test_build_installer_packages.py`
- Modify: `.github/workflows/release.yml` (new `build-installer` job after `build-image` and `build-agent`, `permissions: contents: read, packages: read`; `manifest` needs it and downloads its artifact; optional signing step gated on `WINDOWS_SIGNING_CERT` / `WINDOWS_SIGNING_PASSWORD` secrets)
- Modify: `scripts/release/build_manifest.py` (the `installer` block), `tests/release/test_build_manifest.py`
- Modify: `Makefile` (`installer-packages` target calling the script with a locally built image)

**Interfaces:**
- Consumes: `assemble` (Task 10), the host distribution (Task 13), the wizard page build (Task 12), `build_manifest(..., installer=...)` (Task 3).
- Produces: release assets `povengine-<v>-linux-amd64.tar.gz`, `povengine-<v>-linux-arm64.tar.gz`, `povengine-setup-<v>-windows-amd64.exe`; `manifest.json` → `installer: {packages: [{name, platform, sha256, size}], image_id: {amd64, arm64}, engine_version, wsl_version, signed: bool}`; all three in `SHA256SUMS`.

- [ ] **Step 1: Write the failing tests.** `test_vendor_downloads_are_pinned` (every `vendor.json` entry has a 64-hex `sha256` and an `https://` URL on `download.docker.com` or `github.com/microsoft/WSL/releases`). `test_script_refuses_sha_mismatch` (run the script with `VENDOR_JSON` pointing at a fixture whose sha is wrong and a local file URL → non-zero exit naming the file). `test_manifest_installer_block` (fixture packages → block fields and `signed: false` without signing).
- [ ] **Step 2: Run** `PYTHONPATH=core .venv/bin/python -m pytest tests/release -v` — Expected: FAIL.
- [ ] **Step 3: Implement** the script: per platform `docker pull --platform` the image by digest, `docker save | gzip`, record `docker image inspect --format {{.Id}}`; fetch and verify vendor archives; build the host distribution (Windows); `npm ci && npm run build:setup`; cross-compile `povengine-setup`; `assemble`. In the release notes step, the install section now leads with the three packages (download, run, the click budget, the SmartScreen note while unsigned); the clone-and-`dev-up.sh` paragraph follows for contributors.
- [ ] **Step 4: Run** the tests — Expected: PASS. Run `make installer-packages VERSION=1.3.0-dev` on a Linux host with Docker — Expected: three files in `dist/installer/` and their sizes printed. Record the measured sizes in §11 of the spec (replacing the estimate).
- [ ] **Step 5: Dispatch `release.yml`** on the feature branch with a throwaway tag input such as `v1.3.0-rc.0` only if the owner agrees; otherwise rely on the Task 16 CI job building the same packages.
- [ ] **Step 6: Commit**

```bash
git add installer/vendor.json scripts/release/build-installer-packages.sh scripts/release/build_manifest.py tests/release .github/workflows/release.yml Makefile docs/superpowers/specs/2026-11-15-jumpbox-installer-design.md
git commit -m "chore(ci): build and publish the jumpbox installer packages"
```

### Task 16: Linux end-to-end in CI (§12.1)

**Files:**
- Create: `tests/installer/e2e/run-linux-e2e.sh`
- Modify: `.github/workflows/ci.yml` (`installer` job gains a matrix `ubuntu-22.04`, `ubuntu-24.04` step running the script; uploads the setup log and `status` output as `installer-proof`)

**Interfaces:**
- Consumes: `make installer-packages` (Task 15) with a locally built image; a second package built with `--label povengine.test=upgrade` and version `<v>+1` for the upgrade leg.

- [ ] **Step 1: Write `run-linux-e2e.sh`** asserting, in order, with a `[ok]`/`[FAIL]` line each: `povengine-setup check` exits 0 and changes nothing (no `/opt/povengine`); with outbound traffic dropped by an `OUTPUT` rule (loopback and `192.0.2.0/24` allowed) for the whole install, `install --yes --address 192.0.2.10` exits 0 on a veth pair addressed `192.0.2.10/24` (host) and `192.0.2.20/24` (namespace `beacon`); `status` shows `povengine-simcore` healthy and `/api/health` `install.installed_by` set; from namespace `beacon`, the Linux beacon enrolled through the console's token one-liner against `http://192.0.2.10:8888` reaches `online`; a stored credential (`POST /api/credentials/integrations` with a dummy value) survives `upgrade` with the second package and `master_key` is not `MASTER_KEY_MISMATCH`; a queued run makes `upgrade` refuse with `RUNS_IN_FLIGHT`; `uninstall` leaves `/var/lib/povengine/data/cortexsim.db`; a reinstall reads the same credential; `uninstall --purge --confirm-host <short name>` removes `/var/lib/povengine`. The customer Docker engine on the runner is untouched throughout (`docker ps` against the default socket lists the same containers before and after).
- [ ] **Step 2: Run** it on an Ubuntu 24.04 VM or the CI job — Expected: every line `[ok]`, exit 0.
- [ ] **Step 3: Prove the gate fails without the fix**: re-run with the Firewall step forced to skip on a host with `ufw` enabled (or the health gate pointed at a closed port) and record the `[FAIL]` line it produces in the PR body.
- [ ] **Step 4: Commit**

```bash
git add tests/installer/e2e/run-linux-e2e.sh .github/workflows/ci.yml
git commit -m "chore(ci): prove the jumpbox installer end to end on Ubuntu"
```

### Task 17: Windows proof on the homelab machine, docs and changelog

**Files:**
- Create: `tests/installer/e2e/run-windows-e2e.ps1` (Windows PowerShell 5.1; same assertions as Task 16 adapted: WSL distribution registered, `POVengine` service running, port forward or mirrored mode, beacon from a second machine or VM)
- Create: `docs/reference/jumpbox-installer.md` (operator reference: download, clicks, CLI, codes table from §13, paths, upgrade, uninstall, what is and is not proven)
- Modify: `installer/README.md` (rewrite: what now exists), `README.md` (install section leads with the packages), `CHANGELOG.md` (`[Unreleased]` entry), `CLAUDE.md` (CI section: the `installer` job; Build & Run: the installed path), `CONTRIBUTING.md` (Gate A job count), `docs/release/mvp-launch-runbook.md` (§8.5 points installed hosts at `povengine-setup upgrade`)

- [ ] **Step 1: Run `run-windows-e2e.ps1`** on the owner's homelab Windows machine (with the owner present) from a fresh snapshot: wizard Install → Done with the click count observed, then the scripted assertions, a reboot, and the assertions again. Save the transcript with host names and addresses removed.
- [ ] **Step 2: Write the docs** listed above; `docs/reference/jumpbox-installer.md` states each proof by where it ran (CI runner, homelab Windows build/edition) and what it did not cover (RHEL 9, Server Core, hosts with Cortex XDR in prevent mode, signed binaries).
- [ ] **Step 3: Run** `make check-ground-truth` if any counted number moved, `cd ui && npx vitest run`, `PYTHONPATH=core .venv/bin/python -m pytest tests/installer tests/release tests/api/test_health_install_block.py -v`, and `cd installer/setup && go test ./...` — Expected: all green.
- [ ] **Step 4: Commit**

```bash
git add tests/installer/e2e/run-windows-e2e.ps1 docs/reference/jumpbox-installer.md installer/README.md README.md CHANGELOG.md CLAUDE.md CONTRIBUTING.md docs/release/mvp-launch-runbook.md
git commit -m "docs(docs): document the jumpbox installer and record its proofs"
```

- [ ] **Step 5: Hand off.** Ask the owner whether to open the PR from `feature/docker-jumpbox-installer` into `dev` (after 1.2.x closes); its body carries the Linux CI run ids, the Windows transcript summary, the fail-without-fix line from Task 16 Step 3, and the measured package sizes.
