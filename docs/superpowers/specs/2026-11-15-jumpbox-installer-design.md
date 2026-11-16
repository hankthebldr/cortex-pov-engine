# Jumpbox installer — design

**Status:** Approved by the owner, 2026-11-15
**Date:** 2026-11-15
**Target release:** 1.3.0 (milestone `1.3.0`, tracking issue #376)
**Branch:** `feature/docker-jumpbox-installer`, cut from `dev`. Topic branches
land here; it merges into `dev` only after 1.2.x closes.
**Supersedes:** `2026-04-22-installer-release-pipeline-design.md`, which was never
built. This design keeps its WSL2 + docker-ce runtime and its refusal of Docker
Desktop, and drops its demo mode, ATT&CK-annotated install events and
`curl | bash` cradles.

## 1. Intent

A Domain Consultant on a customer jumpbox should be able to open this repo's
GitHub Releases page, download one package for Windows or Linux, run it, and be
in the POVengine console with SimCore healthy, without cloning the repo,
installing Docker by hand or writing a `.env`. Every permission and prerequisite
is established by a wizard, which then hands off to the console's existing Get
started (tenant credential, collector, first agent).

The owner's measure is time to value: a public package and an install of one to
four clicks.

Today a DC clones the repo and runs `scripts/dev-up.sh`, which builds the image
on the jumpbox (`docker compose up -d --build`) and needs Docker already
installed. The released GHCR image cannot be pulled anonymously (the release
workflow's own probe records 401), so the clone-and-build path is the only path.

## 2. Goals, non-goals, anti-goals

**Goals**

- G1. One downloadable package per platform: `windows-amd64`, `linux-amd64`,
  `linux-arm64`, attached to every GitHub Release with `SHA256SUMS` and
  `manifest.json` entries.
- G2. A wizard in the browser, on Windows and on an Ubuntu desktop, plus a
  command-line mode on both that runs the same steps.
- G3. The wizard obtains every permission and prerequisite: elevation, the
  container engine, WSL on Windows (including a reboot and resume), the firewall
  rule, the service, the master secret, the address beacons use.
- G4. One to four clicks from "the setup program is running" to "the console is
  open on Get started" (section 6.2 counts them).
- G5. Works identically with or without internet: the package carries the
  SimCore image and the container engine.
- G6. Upgrade by running a newer package; uninstall that keeps the database
  unless the operator asks to purge it.
- G7. Proof on a clean Linux VM and a clean Windows machine, or CI runners
  standing in for them, recorded in the PR.

**Non-goals**

- Windows hosts that cannot run WSL2 (Server 2019, cloud VMs without nested
  virtualization, VMware guests with hardware virtualization hidden). They are
  refused at the first step with the reason and a pointer to the Linux package.
- A native Windows SimCore (no container). Owner decision, 2026-11-15.
- Docker Desktop, Rancher Desktop, Podman Desktop. Docker Desktop needs a paid
  subscription at companies over 250 employees or $10 M revenue; the others are
  per-user desktop products.
- Package-manager distribution (APT/YUM repos, winget, Chocolatey), .deb, .rpm,
  .msi. A tarball and an `.exe` are the whole distribution.
- An auto-updater.
- Code signing in v1. The release gains a signing step that runs only when a
  certificate secret is configured (section 11).
- Changing the developer path. `docker-compose.yml`, `scripts/dev-up.sh` and
  `install.sh` stay as they are for contributors.
- Carrying state between POVs. One SimCore per POV; "keeps the database" means
  across an upgrade or reinstall on the same jumpbox.

**Anti-goals** (would make this a failure even if it technically works)

- A wizard that reports success while SimCore is unhealthy, or while beacons
  cannot reach it from another machine.
- Any secret echoed to the screen, written to a log, or put in a URL.
- An install that depends on download.docker.com, apt/dnf mirrors, the
  Microsoft Store or GHCR at install time.
- A wizard longer than the click budget because it asks what it could detect.
- An install that silently changes a Docker engine the customer already runs.

## 3. Constraints

- SimCore is a Linux container (`core/Dockerfile`, `python:3.11-slim`, runs as
  root, `HEALTHCHECK` curls `127.0.0.1:8888/api/health`, `CMD` is uvicorn with
  `--workers 1`). The image already bakes scenarios, cards, adapter packs, UC/TC
  index, UI, beacon matrix and Rust tools. Two things compose supplies are **not**
  in the image (builder check, 2026-11-15): `sources/` (compose bind-mounts it;
  SimCore reads `sources/cortex-malicious-agentic-pack` in-process for the KOI
  `agentic_egress` plugin and starts `mocktaxii` / `gocortexbrokenbank` from
  `sources/`), and the payload shelf (`release.yml` never runs
  `scripts/build-payloads.sh`, so a released image reads
  `payload_shelf: SHELF_ARTIFACTS_MISSING`). Section 8.5 closes the first and
  section 11 the second (D2). The released image contains no private submodule
  content: only `ackbarx`, `xdrtop` and `signalbench` (public) are compiled into
  it.
- Database: SQLite in WAL mode at `/app/data/cortexsim.db` plus `-wal`/`-shm`
  side files. A backup copies all three, or uses SQLite's online backup.
- `CORTEXSIM_SECRET` encrypts the credential vault. A new secret over a surviving
  database reads `MASTER_KEY_MISMATCH` in `/api/health`. Production mode refuses
  a weak or placeholder secret at boot (`core/config.py::validate_master_key`).
- The Host guard answers `400 HOST_NOT_ALLOWED` to any DNS name not in
  `CORTEXSIM_ALLOWED_HOSTS` (localhost, IP literals and the container's own
  hostname always pass). `CORTEXSIM_PUBLIC_URLS` lists the origins the agent
  installer may name as `?server=`.
- The operator and agent APIs are unauthenticated (SEC-01). `/api/health`
  therefore always reads `degraded` with `api_auth: API_UNAUTHENTICATED`; any
  other degraded component is a real fault.
- The NGFW preflight infers each agent's firewall-side source from the agent's
  `last_ip` (`core/integrations/panos/sources.py`). Anything that rewrites the
  source address of beacon traffic before SimCore sees it makes every agent read
  as one address (`AGENT_ADDRESS_SHARED`, and a false `PN_SOURCE_COLLISION`).
- WSL2 is available on Windows 10/11, Server 2022 (with the 2022 WSL update) and
  Server 2025; not on Server 2019. It needs hardware virtualization inside the
  VM: on AWS, bare metal or (since Feb 2026) C8i/M8i/R8i; on Azure, most
  Dv3-and-later sizes; on VMware, "expose hardware-assisted virtualization".
  Mirrored networking (which keeps source addresses) is documented for Windows
  11 22H2 and later, not for Server 2022.
- WSL distributions are registered per Windows account, and running a custom
  distribution from a LocalSystem service is not documented by Microsoft
  (section 12, R1).
- The repo is public. No lab host names, addresses, tenant names or keys in code,
  docs, tests or issues. Examples use RFC 5737 addresses (`192.0.2.0/24`).
- Console UI rules: terse copy, one short line per state, detail behind a
  disclosure; kit components only (`ui/src/components/console/kit`: Button, Icon,
  StatusIcon).
- No writes to the Cortex tenant. The installer never contacts the tenant.

## 4. Approaches

Three ways to deliver the wizard were weighed. The runtime under all three is
the same (section 7).

1. **One setup program, wizard in the browser** — chosen. A Go program
   (`povengine-setup`, stdlib only, cross-compiled by the release like the
   beacon) elevates itself, serves the wizard on `127.0.0.1` behind a one-time
   token and opens the default browser; with flags it runs the same steps with
   no browser. One step engine behind GUI and CLI, so CI proves the code a DC
   clicks through; one codebase for both OSes; reuses the beacon's Go toolchain
   and release matrix; works headless. Costs: an unsigned `.exe` meets
   SmartScreen until signing exists, and an elevated local web server must be
   locked down (section 5.4).
2. **Native dialogs per OS, scripts underneath** (PowerShell WinForms, zenity,
   bash/PowerShell CLI). Readable by a customer's SOC with nothing compiled, but
   two GUIs that differ, a third front end in the scripts, zenity is a chain of
   popups and absent on minimal or RHEL desktops, and downloaded scripts meet
   Mark-of-the-Web and Group Policy execution-policy blocks.
3. **Desktop app (Tauri or Electron).** The most polished window, but it needs
   WebView2 / WebKitGTK or ships Chromium, adds Rust or Node to the release
   build, still needs a separate CLI, and its window is the hardest to drive in
   CI.

Owner picked 1 on 2026-11-15: "lets not overcomplicate it".

## 5. The setup program

### 5.1 What it is

`povengine-setup` — Go, standard library only (same rule as `agent/`), built
with `CGO_ENABLED=0 -trimpath`. Source under `installer/setup/` as its own Go
module. One binary per platform: `linux/amd64`, `linux/arm64`, `windows/amd64`.

It has one **step engine**: an ordered list of steps, each with `check` (read
only, safe to repeat), `apply` (does the work), and a stable result code. The
wizard and the CLI both call the engine; neither does host work of its own. The
engine writes one install log (`/var/log/povengine/setup.log`,
`C:\ProgramData\POVengine\logs\setup.log`) with every step's code; secrets are
never passed to the log writer.

### 5.2 Modes

```
povengine-setup                      # wizard: elevate, serve, open the browser
povengine-setup install [flags]      # CLI, same steps; --yes takes every default
povengine-setup upgrade [flags]      # same as install when an install exists
povengine-setup uninstall [--purge]  # keeps data unless --purge (and confirms)
povengine-setup status               # read-only: what is installed, health, codes
povengine-setup check                # read-only: every step's check, nothing applied
```

`check` is the dry run (the CLAUDE.md "bare command previews" rule): it reports
what `install` would do and what would refuse, and changes nothing.

CLI flags mirror the wizard's choices: `--address`, `--port` (default 8888),
`--listen` (`all` default, or the address only), `--firewall/--no-firewall`,
`--proxy <url>|none`, `--purge`. A flag the engine does not know exits 2, as
`dev-up.sh` does. There is no restore-from-another-machine flag: one SimCore per
POV, and the secret never leaves the box (section 8.3).

With no display (SSH session, Server Core) the bare command prints:
the CLI form, and a `ssh -L` line for tunnelling to the wizard if the operator
prefers it.

### 5.3 Elevation

- **Windows:** the `.exe` carries an application manifest requesting
  `requireAdministrator`, so double-clicking raises the UAC prompt. The manifest
  is linked as a `.syso` resource generated at build time by a small in-repo
  generator (no third-party build dependency).
- **Linux:** started unprivileged with a display, it re-executes itself through
  `pkexec` (the desktop's graphical password prompt). Without polkit or a
  display it asks for `sudo` in the terminal. Started as root, it proceeds. The
  browser is always opened as the desktop user, never as root
  (`SUDO_USER`/`PKEXEC_UID` → `xdg-open` run as that user).

### 5.4 The local wizard server

The wizard is served by the elevated process, so it is locked down:

- Listens on `127.0.0.1` on a random free port; never on another interface.
- A 256-bit one-time token is in the URL fragment the browser opens; the page
  sends it as a header on every API call. Requests without it, or with an
  `Origin`/`Host` that is not that loopback origin, get 403.
- The wizard API takes only the choices the CLI flags take. It never accepts a
  command, a path to execute, or a script.
- The server exits when the wizard finishes, when the window has been idle for
  30 minutes, or on Ctrl-C.
- No secret travels through it.

### 5.5 The wizard page

A second Vite entry in `ui/` (`ui/setup.html`, `ui/src/setup/`) built with the
console's tokens and kit components (Button, Icon, StatusIcon), embedded into
the Go binary with `embed`. Same look as the console, same copy rules. It talks
only to the setup program's API.

## 6. The wizard

### 6.1 Screens

The wizard detects everything it can and asks only what it cannot. Three
screens; the second is skipped when nothing needs the operator.

**Screen 1 — Review and install** (the only screen most installs see)

```
POVengine 1.3.0 — install on this jumpbox

  ✓ Host           Ubuntu 24.04 · x86_64 · 4 CPU · 8 GB · 38 GB free
  ✓ Engine         Bundled engine · installs privately (customer Docker untouched)
  ✓ Address        Beacons will reach this host at 192.0.2.10  ▾
  ✓ Firewall       Allow TCP 8888 inbound from any address      ▾
  ✓ Proxy          None found                                   ▾
  ✓ Secret         Generated and stored, never shown
  ! Endpoint prot. Cortex XDR found · exclusions needed          ▾

                                                   [ Install ]
```

Each row is one line; `▾` opens the detail (choose another address, change the
port, set or clear the proxy, the exact exclusion paths). The Firewall row's
detail says the API has no sign-in yet, so anyone who reaches the port can use it.
A row that refuses (unsupported OS, no virtualization, not enough disk) is
coral with its code and the Install button is replaced by the remedy.

**Screen 2 — Restart required** (Windows only, when WSL had to be enabled)

```
Windows needs to restart to finish enabling WSL.
Setup continues on its own after you sign in again.      [ Restart now ]
```

**Screen 3 — Done**

```
✓ SimCore is running at http://192.0.2.10:8888
  Health: ok (API sign-in not yet available — expected)

                                              [ Open console ]
```

`Open console` opens `http://<chosen address>:<port>/#/get-started`, never
`localhost`, because the console builds beacon install commands from its own
origin and a beacon on another machine cannot reach `localhost`.

### 6.2 Click budget

Counted from "the setup program is running":

| Path | Clicks |
|---|---|
| Linux, fresh | approve password (1) · Install (2) · Open console (3) |
| Windows, WSL already on | UAC Yes (1) · Install (2) · Open console (3) |
| Windows, WSL needs enabling | UAC Yes (1) · Install (2) · Restart now (3) · Open console (4) |
| Upgrade (either OS) | approve (1) · Upgrade (2) · Open console (3) |

Getting the program running costs, on top of that:

- **Windows:** double-click the downloaded `.exe`. Unsigned, it also meets
  SmartScreen ("More info" → "Run anyway") and possibly the browser's "not
  commonly downloaded" prompt. Signing (section 11) is what removes those.
- **Linux desktop:** extract the `.tar.gz` (Files does this on double-click) and
  run `povengine-setup`, either with right-click → "Run as a Program" or from a
  terminal. A browser download does not keep the executable bit on a bare
  binary, which is why the Linux package is a tarball.

The tenant credential, collector and first agent are not counted: they are Get
started, which the wizard opens.

### 6.3 Hand-off to Get started

The console's Get started already covers the tenant credential (encrypted vault
+ connector preflight), collector targets, Broker VM and the first agent
(enrollment token + one-liner, `ui/src/components/console/GetStartedView.jsx`,
`setupProgress.js`). The installer does not duplicate any of it and never asks
for a tenant key. Two changes make the hand-off clean:

- The installer writes `CORTEXSIM_PUBLIC_URLS` with the chosen address, so the
  agent installer's `?server=` accepts it.
- Get started reads a new read-only `install` block in `/api/health` (section
  8.4) and shows one line at the top: "Installed by setup 1.3.0 · listening at
  192.0.2.10:8888". No new step, no new checklist item.

## 7. Runtime

One runtime model on both OSes: a **private Docker engine** that the installer
owns, running the pinned SimCore image. It never uses or changes a Docker engine
the customer already has.

### 7.1 Linux

- Docker's static engine binaries (`dockerd`, `containerd`, `runc`,
  `containerd-shim-runc-v2`, `docker-init`, `docker` CLI; Apache-2.0) are
  installed to `/opt/povengine/engine/`.
- `povengine-engine.service` runs `dockerd` with its own socket
  (`/run/povengine/docker.sock`), data root (`/var/lib/povengine/engine`),
  exec root and pidfile, plus `--iptables=false --ip-masq=false --bridge=none`.
  With no iptables or bridge of its own it cannot collide with a customer's
  Docker engine on the same host.
- `povengine.service` (after the engine) runs SimCore with `docker run` against
  that socket: `--network host`, `--restart unless-stopped`, the data bind mount
  and the env file (section 8). No compose: one command line generated by the
  engine, so no compose plugin to ship.
- **Why host networking:** no NAT, so SimCore sees each beacon's real source
  address (constraint on `last_ip`), and the container's hostname is the
  jumpbox's, so the Host guard admits the jumpbox's own name with no extra entry.
- Firewall: `ufw` when active, else `firewalld` when running, else nothing to do
  (reported, not a failure). One rule: TCP `<port>` inbound from any source
  (D4).
- Supported: Ubuntu 22.04 and 24.04 (proven in CI). RHEL 9 family: best effort
  until a run proves it, because SELinux with a static engine is unproven here
  (section 12, R4). Requires systemd and cgroup v2; refused otherwise.

### 7.2 Windows

- `check` refuses with a named code when the host cannot run WSL2: OS build
  below the WSL2 floor (`WIN_BUILD_UNSUPPORTED`), Server 2019
  (`WSL2_NOT_AVAILABLE`), no virtualization exposed to the VM
  (`VIRTUALIZATION_UNAVAILABLE`, with the cloud and VMware remedies in its
  detail), or WSL disabled by policy (`WSL_BLOCKED_BY_POLICY`).
- `apply` enables the Virtual Machine Platform and WSL optional features if they
  are off (offline, inbox features; this is the step that may need a reboot),
  installs the bundled WSL package (the `wsl.<version>.x64.msi` from Microsoft's
  WSL GitHub releases, MIT since WSL was open-sourced) when the inbox WSL is too
  old, and imports the **host distribution** with
  `wsl --import povengine-host C:\ProgramData\POVengine\wsl <rootfs.tar> --version 2`.
- **Docker stays the runtime on Windows, through WSL** (owner, D1): Docker
  Engine (the static `dockerd`) inside WSL2, never Docker Desktop and never a
  second runtime such as a Hyper-V VM.
- The **host distribution** is built in CI from `installer/host-distro/`: a
  minimal Debian slim root filesystem plus the same static engine as Linux and a
  tiny init that starts `dockerd` and then SimCore with the same `docker run`
  line as Linux. It is exported to a tarball (`docker export`) and shipped in
  the package. It is not a general-purpose distribution and is not offered to
  the user.
- The `POVengine` Windows service keeps the distribution running (a WSL VM with
  nothing attached shuts down when idle), starts it at boot, and, on every start,
  re-applies the port forward from the host's port to the distribution's
  address, because that address changes each time WSL starts. Which account the
  service runs WSL under is the open spike R1.
- Networking: mirrored mode where the host supports it (source addresses
  preserved). Otherwise NAT plus a `netsh interface portproxy` rule; in that mode
  every beacon reads as one source address, so the wizard's Address row says
  "Beacon addresses will read as this host" and the Firewall check in the
  console must be given agent addresses by hand. The Windows Firewall gets one
  inbound rule for TCP `<port>` from any source (D4).
- Reboot and resume: when a reboot is needed, the engine records its state in
  `C:\ProgramData\POVengine\setup-state.json` and registers a one-shot
  `RunOnce` entry that reopens the wizard at the step it stopped on after the
  operator signs in.

## 8. SimCore configuration

### 8.1 Where things live

| | Linux | Windows |
|---|---|---|
| Program | `/opt/povengine/` | `C:\Program Files\POVengine\` |
| Config + secret | `/etc/povengine/` (root, 0700; files 0600) | `C:\ProgramData\POVengine\config\` (SYSTEM + Administrators only) |
| Database | `/var/lib/povengine/data/` → `/app/data` | inside the host distribution at `/var/lib/povengine/data/` |
| Engine data | `/var/lib/povengine/engine/` | inside the host distribution |
| Backups | `/var/lib/povengine/backups/<UTC>/` | `C:\ProgramData\POVengine\backups\<UTC>\` |
| Logs | `/var/log/povengine/` | `C:\ProgramData\POVengine\logs\` |

On Windows the database stays on the distribution's own disk: SQLite in WAL mode
needs shared memory that the `/mnt/c` bridge does not reliably provide.
Backups are copied out to the Windows folder.

### 8.2 Environment

The engine writes one env file and passes it with `--env-file`:

- `CORTEXSIM_ENV=production`
- `CORTEXSIM_SECRET` (section 8.3)
- `CORTEXSIM_ALLOWED_HOSTS` — the jumpbox's short and fully qualified names, plus
  the chosen address when it is a name
- `CORTEXSIM_PUBLIC_URLS` — `http://<chosen address>:<port>`
- `CORTEXSIM_BIND` — set only when "listen on this address only" is chosen
  (uvicorn's `--host` is overridden to match, and so is the health command)

Everything else keeps its image default. The env file is the installer's, not a
`.env` the DC edits; `status` prints its non-secret lines.

### 8.3 Master secret

Generated by the engine (`crypto/rand`, 48 bytes, URL-safe), written only to the
secret file, never displayed, logged, exported or sent through the wizard API.
Upgrade and reinstall on the same jumpbox keep it. The operator never supplies
one. Backups therefore serve same-box rollback only; moving a database to another
jumpbox is out of scope (one SimCore per POV).

### 8.4 `install` block in `/api/health`

A read-only block, populated only when the container was started by the setup
program (it sets `CORTEXSIM_INSTALLED_BY=povengine-setup/<version>` and
`CORTEXSIM_INSTALL_ADDRESS`): `{installed_by, address, listen, platform}`. No
secret, no path. Absent on a compose deployment, which keeps reading as today.

### 8.5 Image parity: `sources/` in the image

`core/Dockerfile` gains a `COPY` of the `sources/` trees SimCore itself reads at
runtime: the in-tree packs (`cortex-malicious-agentic-pack`,
`cortex-prompt-attacker`, `cortex-vulnerable-llm`, `cortex-browser-attacker`)
and the public submodules SimCore starts (`mocktaxii`, `gocortexbrokenbank`).
Never the private-origin submodules (`CDR`, `xsiam-prisma-cdr-lab`). A test
derives the list from the code paths that read `sources/` (tool registry
`source_path`, the agentic pack lookup) and fails when the image lacks one, so
the installed path cannot drift from compose again. Compose keeps its bind-mount
for live editing.

### 8.6 Egress proxy

The engine detects a static proxy (Linux: `HTTPS_PROXY`/`HTTP_PROXY` from the
environment and `/etc/environment`; Windows: the WinHTTP proxy) and shows it on
the Proxy row. When one is set it writes `HTTPS_PROXY`, `HTTP_PROXY` and
`NO_PROXY` (loopback, the jumpbox's own names and addresses, and the chosen
address) into the env file; on Windows the same values go into the host
distribution's engine so the container sees them. PAC files and WPAD are not
supported in v1; the row says so when it finds one, and `--proxy <url>` sets one
by hand. A proxy that needs credentials is not supported in v1.

## 9. Health gate

`install` and `upgrade` finish only when all of these hold:

1. The container is running and its health check is healthy.
2. `GET /api/health` answers, and every degraded component other than
   `api_auth` is listed on Screen 3 as a fault (the install still completes, but
   says what is wrong instead of reading "ok").
3. `master_key` is not `MASTER_KEY_MISMATCH`.
4. SimCore answers on the chosen address as well as on loopback, from the host.
   This catches a missing firewall rule or a wrong port forward. It cannot prove
   reachability from another machine; Get started's first agent does that.

## 10. Upgrade and uninstall

**An existing compose deployment** (a `cortex-pov-engine-simcore-*` container or
anything else on the chosen port) is detected by `check` and refused with
`COMPOSE_DEPLOYMENT_PRESENT` or `PORT_IN_USE`, naming the container and the
command that stops it. v1 does not import a compose database; one SimCore per POV
means a fresh install is the expected case.

**Upgrade** — run the newer package. The engine sees an existing install and
offers Upgrade instead of Install:

1. Refuse if a run is `running` or `queued` (`RUNS_IN_FLIGHT`, with the run ids);
   an upgrade mid-run loses the run.
2. Back up the database with SQLite's online backup into the backups folder,
   next to a copy of the env file's non-secret lines and the image id it ran.
3. Load the new image, replace the engine binaries if they changed, restart
   with the same env file and secret.
4. Health gate (section 9). On failure: stop, restore the previous image, put the
   backed-up database files back (all of `cortexsim.db*`, side files removed
   first), start the previous version, and report `UPGRADE_ROLLED_BACK` with
   the failing component. Older code is never pointed at a database newer code
   created (OPS-17).

A downgrade (older package over a newer install) is refused
(`DOWNGRADE_REFUSED`); the backup folder is the way back.

**Uninstall** — `povengine-setup uninstall`, or Remove in the wizard when an
install exists. Takes a final database backup, then removes the service, the
container, the image, the engine and the firewall rule. It keeps the database,
the secret and the backups unless `--purge` (or the wizard's "Also delete data",
confirmed by typing the host name). On Windows, a non-purge uninstall keeps the
host distribution registered and stopped so the data survives a reinstall; purge
unregisters it.

## 11. Release integration

**The payload shelf ships in the release (D2).** `build-image` runs
`scripts/build-payloads.sh` with `PAYLOAD_ALLOW_UNPINNED=0` before the image
build, so the released image, and therefore every package, carries the staged
shelf tools (8 today: the Linux privilege-escalation enumerator family) and
`/api/health` reads `payload_shelf` ok on an installed SimCore. A pin mismatch
fails the release. `manifest.json` and the release notes list each shelf
artifact with its pin and licence, taken from its pack's `install.artifact`.

A new `build-installer` job in `.github/workflows/release.yml`, after
`build-image` and `build-agent`, then feeding `manifest`:

1. Pull the just-pushed image by digest for each platform and `docker save` it
   (gzip). Record the image config id; the package's own manifest carries it and
   `install` verifies the loaded image against it.
2. Fetch the pinned Docker static engine archives and the pinned WSL `.msi`,
   verified against SHA-256 values committed in `installer/vendor.json` (the
   release never trusts an unpinned download).
3. Build the host distribution root filesystem (Windows package only).
4. Build the wizard page (`ui` setup entry) and cross-compile `povengine-setup`.
5. Assemble:
   - `povengine-<version>-linux-amd64.tar.gz` and `-linux-arm64.tar.gz`:
     `povengine-setup`, `payload/` (image, engine), `package.json`, `README.txt`.
   - `povengine-setup-<version>-windows-amd64.exe`: the setup program with its
     payload (image, engine-in-host-distribution, WSL `.msi`) appended as a zip
     after the executable; the program opens its own file to read it. One file to
     download and double-click.
6. Optional signing: when `WINDOWS_SIGNING_*` secrets are configured, sign the
   `.exe` (Authenticode) before checksumming. Absent, the job notes "unsigned"
   and the release notes say what SmartScreen will show.

`manifest` gains the three packages in `SHA256SUMS` and an `installer` block in
`manifest.json` (packages, image id, engine version, WSL version, signed
true/false). The release notes' install section leads with the packages; the
clone-and-`dev-up.sh` paragraph stays for contributors. `installer/README.md` is
rewritten to describe what now exists.

Package sizes are not yet measured. Estimates: Linux about 250 MB, Windows
about 400–700 MB. GitHub's per-asset limit is 2 GiB.

## 12. Proof and risks

### 12.1 Proof recorded in the PR

- **Unit:** the step engine's checks and code mapping in Go tests (fake host
  adapters), the wizard API's token/Origin refusal, the env-file writer (no
  secret in logs, permissions), upgrade rollback ordering.
- **Linux end to end (CI, `ubuntu-22.04` and `ubuntu-24.04` runners):** build
  the package from the tree, then `povengine-setup install --yes`, health gate,
  a beacon enrolled from a second network namespace (or container) against the
  chosen address, `upgrade` from the previous release's package over a database
  with a stored credential (no `MASTER_KEY_MISMATCH` after), `uninstall` keeping
  data, reinstall reading the same database, `uninstall --purge`.
- **Wizard UI:** Playwright against the setup program's local server driving
  Install to Done on the Linux runner (the GUI and the CLI share the engine, so
  this covers the page, not a second implementation).
- **Windows end to end (D3):** the same sequence run by hand on the owner's
  homelab Windows machine, recorded in the PR with its Windows build, edition,
  WSL version and the setup log, with host names and addresses removed (the repo
  is public). A hosted `windows-2022`/`windows-2025` runner runs it too if spike
  R2 shows WSL2 works there. No self-hosted runner.

### 12.2 Risks and spikes

- **R1 — WSL from a service (Windows, high).** Microsoft does not document
  running a custom WSL distribution from a LocalSystem service; distributions
  are per account. Spike first: (a) distribution imported and run as SYSTEM by
  the `POVengine` service; (b) fallback, a dedicated local account
  `povengine-svc` created by the installer with "Log on as a service", random
  password held by the service manager, never shown; (c) if both fail, a task
  that starts at sign-in of the installing account, so SimCore runs while that
  account is signed in (the wizard says so). Docker in WSL is the runtime in all
  three. The spike also measures reboot survival and the WSL idle shutdown, and
  runs on the owner's homelab Windows machine.
- **R2 — CI proof on Windows (medium).** The `setup-wsl` action documents WSL2
  on hosted Windows runners, while older GitHub statements say those VMs lack
  nested virtualization. Spike in week one; the fallback is the VM in 12.1.
- **R3 — NAT on Windows hosts without mirrored networking (medium).** All
  beacons read as one source address (section 7.2). Mitigation is the wizard
  line and hand-entered addresses in the Firewall check; not fixable inside WSL
  NAT.
- **R4 — SELinux with a static engine on RHEL 9 (medium).** Unproven; RHEL stays
  best effort until a run proves it.
- **R5 — Endpoint protection on the jumpbox (medium).** The payload carries
  scenario content, simulation tools and, since D2, the payload shelf's
  offensive tools; Cortex XDR or Defender in prevent mode
  may quarantine the package or block the engine. The wizard detects an XDR
  agent and Defender and shows the exact exclusion paths before Install; it
  never changes the protection settings itself.
- **R6 — Unsigned Windows executable (low, known).** SmartScreen and browser
  prompts add clicks before the wizard opens (section 6.2).

## 13. Stable result codes

Printed by the CLI, shown on the wizard row that raised it, written to the
setup log. Initial set (the builder may add, never rename):

`OK` · `NOT_ELEVATED` · `OS_UNSUPPORTED` · `ARCH_UNSUPPORTED` · `NO_SYSTEMD` ·
`NO_CGROUP_V2` · `DISK_LOW` · `MEMORY_LOW` · `PORT_IN_USE` ·
`WIN_BUILD_UNSUPPORTED` · `WSL2_NOT_AVAILABLE` · `VIRTUALIZATION_UNAVAILABLE` ·
`WSL_BLOCKED_BY_POLICY` · `REBOOT_REQUIRED` · `PAYLOAD_CORRUPT` ·
`IMAGE_ID_MISMATCH` · `ENGINE_START_FAILED` · `SIMCORE_UNHEALTHY` ·
`MASTER_KEY_MISMATCH` · `ADDRESS_UNREACHABLE` · `FIREWALL_RULE_FAILED` ·
`COMPOSE_DEPLOYMENT_PRESENT` · `PROXY_PAC_UNSUPPORTED` (warning) ·
`ENDPOINT_PROTECTION_EXCLUSION_NEEDED` (warning) · `RUNS_IN_FLIGHT` ·
`UPGRADE_ROLLED_BACK` · `DOWNGRADE_REFUSED` · `UNINSTALL_INCOMPLETE`.

## 14. Left to the builder

- Go package layout inside `installer/setup/`, the host-adapter interfaces and
  fakes.
- The exact minimal base for the host distribution (Debian slim is the default;
  anything smaller that runs the static engine is fine).
- Pinned versions of the Docker static engine and the WSL `.msi`, as long as
  both are pinned by SHA-256 in `installer/vendor.json`.
- The wizard page's internal structure, within the kit and copy rules.
- Detection of XDR / Defender and the exact exclusion list.
- How the Linux end-to-end test stands in a second machine (network namespace or
  a second container).

## 15. Calls made in this design (owner to check)

These were not asked; they are my calls, open to correction:

1. The installer never asks for the tenant credential; Get started owns it.
2. A private engine always, even when the host has Docker: one path to test, and
   the customer's engine is never touched.
3. Host networking for SimCore, for real beacon source addresses and the Host
   guard's own-name rule.
4. No compose in the installed path; one generated `docker run` line.
5. Port fixed at 8888 by default; changeable in the Address row's detail and by
   `--port`.
6. Linux package is a tarball; Windows package is one `.exe` with its payload
   appended.
7. Ubuntu 22.04/24.04 supported and proven; RHEL 9 best effort.
8. Uninstall keeps data by default; purge needs typing the host name.
9. Signing is a release step that runs only when a certificate is configured.
10. Upgrade refuses while a run is in flight and rolls back on a failed health
    gate.
11. The image gains the `sources/` trees SimCore reads at runtime (section 8.5),
    pinned by a test, so the installed path matches compose.
12. A static proxy is detected and passed through; PAC/WPAD and authenticated
    proxies are out of v1 (section 8.6).
13. No restore onto another jumpbox and no secret export; backups are for
    same-box rollback.
14. An existing compose deployment is refused by name, not imported.
15. Linux and Windows ship together in 1.3.0; there is no Linux-only release
    (1.3.0 has no date).

## 16. Decisions (owner, 2026-11-15)

Raised by the builder check; answered by the owner the same day.

- **D1 — Runtime on Windows.** Docker stays the engine and runs through WSL
  (section 7.2). If the service spike fails both ways, the fallback is the
  sign-in task in R1 (c), still Docker in WSL.
- **D2 — Payload shelf.** Public, in the release: "we already leverage public
  repos for adapters and malware samples". Staged and pinned by the release
  (section 11).
- **D3 — Windows proof.** The owner's homelab Windows machine (section 12.1).
- **D4 — Firewall.** Open TCP `<port>` to all traffic after install.
