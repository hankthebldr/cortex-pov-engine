# Lab asset lifecycle: spinning test assets up and down

**Scope:** every asset used to test CortexSim against the lab tenant: VMs on
ms-01, CortexSim beacons, vendor components installed on a lab host (XDR agent,
XDR Collector, later the Cortex Engine), the Broker VM, objects created in the
tenant console, and objects created in SimCore.
**Pattern:** this generalises the pov-target-01 cycle of 2026-11-02 to
2026-11-11 (XDR agent install through the beacon, side-by-side beta beacon,
cleanup verification). That cycle's records are in `docs/lab/2026-11-11-*.md`.
**Status:** standard procedure. The first use is
`docs/lab/2026-11-12-ingestion-sources-bringup.md`.

## Executive summary

Every test asset in the lab now goes through the same seven steps: stage it,
start it, prove it works, use it, take it down, prove it is gone, and write
down what happened. The steps exist because the last cycle lost hours to
assets that looked healthy and were not: a test agent that went offline when
the laptop's address changed, an endpoint agent whose data collectors were
still starting when the first test ran, and test accounts left behind on a
host. A test run on a half-working asset produces silence, and in a customer
report silence reads as "Cortex missed it". Each step below names who does it,
what proves it worked, and what evidence is kept. Recommended action: use this
procedure for every lab asset, starting with this week's collector, Broker VM
and HTTP collector bring-up, and keep one record per bring-up.

## Technical detail

### 1. Who can do what

| Actor | Reaches | Does |
|---|---|---|
| **Henry** | ms-01 (KVM host), every guest (`ssh -J henry@192.168.1.110 …`), the deathstar console | Creates and destroys VMs, copies installer files onto guests, creates and removes tenant-side objects, approves every run on a lab host |
| **Claude, through SimCore** | the SimCore API on :8888, the beacon on each enrolled guest, read-only tenant calls through the SimCore vault credential | Mints enrollment tokens, runs one-step root drafts on a beacon *after Henry's OK for that run*, reads tenant state read-only, writes the record |
| **Nobody** | — | Writes to the tenant through its API. `CORTEXSIM_XSIAM_ALLOW_WRITE` and `CORTEXSIM_XSIAM_ALLOW_DESTRUCTIVE` stay off. |

The Mac cannot reach ms-01 or its guests directly from the Claude app (no
route on 192.168.1.110; see the 2026-11-10 notes). On a guest, the beacon is
the only path for Claude. Everything that touches the hypervisor or copies a
file onto a guest is Henry's.

### 2. The lab today

| Asset | Where | State (2026-11-12) |
|---|---|---|
| SimCore | Mac, Docker, :8888, compose project `cortex-pov-engine` | 1.1.1 from `origin/dev` 5f113dda. Mounts its corpus from worktree `funny-bartik-cd5ed4`; rollback image `cortex-pov-engine-simcore:1.1.1-dev-prev-20261112` |
| Mac LAN address | en0 | 192.168.1.105. It has moved between .105 and .106 under DHCP (Pi-hole on 192.168.1.101). A move takes every beacon offline, because beacons dial an IP |
| ms-01 | 192.168.1.110, KVM, `virbr0` 192.168.122.1 (NAT) | K3s control plane, Gitea, Traefik. Not a throwaway host |
| pov-target-01 | libvirt guest on ms-01, 192.168.122.77, Ubuntu 24.04.5 x86_64 | Beacon `pov-target-01-4dcdce` (online), XDR agent 9.4.0.470 (CONNECTED, endpoint `0ad53c90…`) |
| deathstar | XSIAM tenant | Lab tenant. `xsiam_tenant` credential in the SimCore vault, capability `xql` |

Staged on the Mac in `~/Downloads` (sha256 measured 2026-11-12):

| File | What | sha256 |
|---|---|---|
| `hr-linux-collector-nov_sh.tar.gz` | XDR Collector 1.5.3.2371 shell installer + `collector.conf` | `2fc62451452fe37e81861b6671970e0aa77c3f8e9a4fa25e0552742adbc29a5a` |
| ↳ `collector-1.5.3.2371.sh` (inside) | the installer | `96f94fc6cc2c62ea2a92b012f65430647cb740faf0685a3487b50b6f8018f5a0` |
| `deathstar-bvm-image.qcow2` | Broker VM KVM image (9.9 GB file, 480 GiB virtual, thin) | `9d87bf8af037c7a195b42ffcac273ab048981b76a06386a1ea09b88a828cbd71` |
| `d1-hr-deathstar-engine-nov-8.16-14196846.sh` | Cortex XSOAR Engine 8.16 installer. Available, not planned | `6b809a5e7c2cf4ff9632e508cce118c9c43acef8ad8ada8ae6254e6fa326034e` |
| `hr-k8s-installer-nov.yaml` | XDR agent DaemonSet for Kubernetes (`cortex-xdr` namespace). Available, not planned | `91ade82f32e4592d64e3adb8b7e864f7211a08e2f4dede7736a5c26dd7067a8d` |
| `~/hr-linux-installer-nov_sh/` | XDR agent 9.4.0.470 installer (already installed on pov-target-01) | see `docs/lab/2026-11-11-*` |

`collector.conf` and `cortex.conf` carry the tenant's distribution ID and
upload address. They are not secrets in the API-key sense, but they register
hosts to deathstar: keep them out of the repo, out of chat, and out of run
output (print key names, never values).

### 3. The seven steps

Every asset goes through these in order. A step is done only when its proof is
recorded.

| # | Step | Proof that it worked |
|---|---|---|
| 1 | **Stage**: get the bytes and their sha256 onto the machine that will use them | sha256 on the Mac and on the target agree |
| 2 | **Spin up**: create, install or register | the component's own exit status, plus its log |
| 3 | **Verify**: prove it works from both ends | host side (service, process, file) **and** tenant side (read-only), each recorded |
| 4 | **Use**: run the test it exists for | the run id, the window, and the review in §5 |
| 5 | **Spin down**: uninstall, deregister, destroy | the component's own exit status, plus its log |
| 6 | **Verify clean**: prove nothing is left behind | a read-only check on the host, and the tenant listing |
| 7 | **Record**: write the bring-up record (§6) | the record is committed with the run ids in it |

Two rules that come from the last cycle:

- **Wait for the asset to settle before step 4.** The XDR agent's collectors
  read STOPPED for about a minute after the endpoint first shows CONNECTED,
  and run 1 of SIM-EDR-003 recorded nothing because it started 15 s after
  connect. Step 3 is not done until the component reports fully running, not
  merely registered.
- **"It ran" is not step 3.** A SAFE-MODE run or a run that exits 0 without
  the tool it needed proves nothing about the asset.

### 4. Procedures by asset type

#### 4.1 A VM on ms-01 (Henry)

Spin up, on ms-01:

```bash
df -h /var/lib/libvirt/images          # the image is thin; check headroom first
sudo install -m 0644 /tmp/<image>.qcow2 /var/lib/libvirt/images/<vm>.qcow2
sha256sum /var/lib/libvirt/images/<vm>.qcow2    # must equal the Mac's value
sudo virt-install --import --name <vm> --memory <MiB> --vcpus <n> \
  --disk path=/var/lib/libvirt/images/<vm>.qcow2,format=qcow2 \
  --osinfo generic --network network=default --graphics vnc --noautoconsole
sudo virsh domifaddr <vm>               # the DHCP address on 192.168.122.0/24
```

On a virt-install older than 4.0, write `--os-variant generic` instead of
`--osinfo generic`. Either matches the vendor's virt-manager steps ("leave OS type and
Version as Generic"). `network=default` puts the guest on `virbr0` next to
pov-target-01, so guests talk directly and reach the internet through ms-01's
NAT. Copy the image from the Mac first: `scp <image> henry@192.168.1.110:/tmp/`.

Reach a guest's web console from the Mac with a tunnel through ms-01:

```bash
ssh -N -L <localport>:<guest-ip>:<guest-port> henry@192.168.1.110
```

Spin down (keeps the disk; "never delete, move to an archive"):

```bash
sudo virsh shutdown <vm>                # or: sudo virsh destroy <vm> if it hangs
sudo virsh undefine <vm>
sudo install -d /var/lib/libvirt/images/archive/$(date +%Y%m%d)
sudo mv /var/lib/libvirt/images/<vm>.qcow2 /var/lib/libvirt/images/archive/$(date +%Y%m%d)/
```

Verify clean: `sudo virsh list --all` no longer shows it, and
`sudo virsh net-dhcp-leases default` no longer leases its address.

#### 4.2 A CortexSim beacon on a guest

Spin up (Claude mints, Henry runs the one-liner on the guest):

```bash
# Mac: one token, one use, one day. The response carries the token; it is never written to a file.
curl -s -X POST http://192.168.1.105:8888/api/agents/enroll/tokens \
  -H 'Content-Type: application/json' \
  -d '{"label":"<guest> <yyyy-mm-dd>","ttl_seconds":86400,"max_uses":1}'
# Guest (Henry):
curl -fsSL 'http://192.168.1.105:8888/api/agents/install?os=linux' | sudo CORTEXSIM_TOKEN='cxs_…' bash
```

Verify: `GET /api/agents` shows the new `agent_id` `online`, and
`GET /api/agents/install/attempts` shows the install's final stage code.

Spin down: `curl -fsSL 'http://192.168.1.105:8888/api/agents/install?os=linux&uninstall=1' | sudo bash`
on the guest, then revoke the token (`DELETE /api/agents/enroll/tokens/<id>`).
The agent row stays in SimCore. **Rows are never deleted from the SimCore DB**
without Henry's explicit go-ahead for those rows.

Testing a branch beside the live beacon (do not run a second installer: it
replaces the guest's only beacon): see the side-by-side procedure in
`docs/lab/2026-11-11-persistence-grouping-lab-run.md` and the 2026-11-10
notes, using `systemd-run --unit cortexsim-agent-beta`.

#### 4.3 A vendor component installed through the beacon

This is the XDR agent pattern of 2026-11-10, which the XDR Collector repeats.

1. **Stage (Henry).** From the Mac:
   `scp -J henry@192.168.1.110 ~/Downloads/<package> henry@192.168.122.77:/tmp/`.
   Claude records the Mac-side sha256.
2. **Pre-checks (Claude, read-only draft, after Henry's OK).** One step,
   `identity: root`. It reads the facts the install depends on and changes
   nothing: OS, arch, `/tmp` mount options (`findmnt -no OPTIONS /tmp`; a
   `noexec` there breaks every Makeself installer), free disk, the sha256 of
   the staged file, a listing with sha256 of `/etc/panw`, and the state of
   every vendor service already on the host.
3. **Install (Claude, one draft, after Henry's OK).** One step, `identity:
   root`, shaped as below. The sha256 is carried in by the draft, never read
   from the server being trusted.
4. **Verify.** Host: the service is active and its log shows a connect. Tenant
   (read-only): the host appears for that product, and the component's data
   reaches its dataset (§5.4).
5. **Spin down.** The vendor uninstaller, through the same draft shape, with
   the same evidence.
6. **Verify clean.** A read-only draft listing processes, units, `/etc/panw`
   and the install directory.

Install step shape (every install and uninstall step follows it). The beacon
runs each step with `sh -c`, which is dash on Ubuntu, so the body is handed to
bash explicitly (`pipefail` and the `tee` redirection are bash features):

```bash
bash -s <<'LAB'
set -euo pipefail
LOG=/var/log/cortexsim/lab/<asset>-$(date -u +%Y%m%dT%H%M%SZ).log
install -d -m 0750 /var/log/cortexsim/lab
exec > >(tee -a "$LOG") 2>&1
echo "LAB_STEP_BEGIN asset=<asset> step=<step> host=$(hostname) utc=$(date -u +%FT%TZ)"
trap 'rc=$?; echo "LAB_STEP_END asset=<asset> step=<step> rc=$rc log=$LOG"; exit $rc' EXIT
echo "<expected sha256>  /tmp/<package>" | sha256sum -c -
# … the vendor's own commands, verbatim from its doc …
LAB
```

`LAB_STEP_BEGIN` and `LAB_STEP_END` lines land in the run's step output, so
the review in §5 can find every step's start, end and exit code without
reading the whole log, and the log on the host keeps the full trace.

Drafts need one expected detection and a real `tc_ref` to launch (2026-11-05
finding). Diagnostic drafts bind TC-EDR-05 as before; drafts are excluded from
UC/TC evidence by the active-only default, so they never count as coverage.

#### 4.4 Objects in the tenant console (Henry)

Installation packages, XDR Collector profiles and policies, Broker VM
registrations and applets, HTTP collectors, BIOC and correlation rules.

- Name every lab object with a `cortexsim` prefix (`CortexSim door probe`,
  `cortexsim`/`door_http`), so the read-only listings and datasets can find
  them and nothing is confused with real content.
- Record each object's name, where it lives in the console, and when it was
  created, in the bring-up record.
- Spin down: disable or remove it in the console. Claude confirms it is gone
  with a read-only listing where one exists.

#### 4.5 Objects in SimCore

Collector targets (`/api/collector-targets/{name}`; the key goes to the vault),
enrollment tokens, drafts, credentials. Spin down: delete the collector target
(its vault secret goes with it), revoke tokens. Runs, results, agents and door
events stay in the DB.

### 5. Evidence and error review

A test is reviewed from four layers. A test that did not fail visibly can
still have failed in any of them, so each is read every time, for the test's
time window.

| Layer | What it records | How it is read |
|---|---|---|
| **SimCore** | run state, each step's exit code and output, install telemetry, warnings and errors in the app log | `GET /api/runs/{id}`, `GET /api/runs/{id}/events`, `GET /api/agents/install/attempts`, `docker logs <container> --since <t0>` filtered to WARNING/ERROR, `/api/health` before and after |
| **Beacon** | delivery, kill, identity degradation, its own errors | the run record (`IDENTITY NOT HONOURED` and step outcomes are written there) and, through a read-only draft, `journalctl -u cortexsim-agent --since <t0>` |
| **Host** | the vendor components' service state and logs | read-only draft: `systemctl is-active` and `systemctl status` for each vendor unit, the component's own log, the `LAB_STEP_*` log under `/var/log/cortexsim/lab/` |
| **Tenant** (read-only) | whether the data arrived, and the platform's own complaints | XQL through SimCore (`POST /api/xsiam/tenants/deathstar/xql`): the dataset's newest rows, `collection_auditing` for collector errors, `metrics_view`/`metrics_source` for collector heartbeats; `GET /api/xsiam/tenants/deathstar/metrics`; endpoint state through `OP-ENDPOINTS-GET-ENDPOINT` |

**The review script (to build after this plan is approved).**
`scripts/lab/lab-review.py`, stdlib only, run on the Mac:
`lab-review.py --since <utc> [--run <id>…] [--agent <id>] [--dataset <name>…]`.
It reads the SimCore and tenant layers itself, through the SimCore API only
(the tenant credential never leaves the vault), and prints the host-layer
draft for Henry's OK rather than launching it. It writes one markdown report:
a verdict line per layer (`ok`, `problem`, `not checked`), each problem with
its evidence and the signature it matched from the table below, and the exact
query or call that produced each line. A layer it could not read reads `not
checked`, never `ok`.

**Failure signatures seen in the lab** (the review checks each one):

| Signature | Seen | What it looks like | Check |
|---|---|---|---|
| Beacon offline after the Mac's address moved | 2026-11-11 | every beacon `offline`, no errors anywhere | Mac en0 vs the beacon's `--server` |
| Collectors still starting after connect | 2026-11-10 | run completes rc 0, nothing in `xdr_data` | first event time in `xdr_data` vs run start |
| Back-to-back steps lose telemetry | 2026-11-10/11 | steps 3–5 of a fast chain missing from `xdr_data` | per-step event presence; space steps ≥ 40 s |
| Service account cannot log in | 2026-11-02 | step 1 fails in ~120 ms: "account is currently not available" | step stderr |
| Identity collapsed | Windows design | `IDENTITY NOT HONOURED` in the run record | run record |
| Makeself installer blocked | vendor doc | installer exits non-zero right after extraction | `findmnt -no OPTIONS /tmp` |
| Collector refuses the key | 2026-11-10 research | 401 from `/logs/v1/event` | delivery taxonomy code in the step detail |
| Dataset name differs from what the cards query | 2026-11-10 research | data arrives, detections stay silent | `<vendor>_<product>_raw` vs the card's dataset |
| Clock skew | 2026-10 | MTTD negative or huge, matcher finds nothing | connector preflight clock stage |
| Another session's tests on the live SimCore | 2026-11-12 | unexpected `docker-desktop-*` runs and agents | `GET /api/runs` in the window |

### 6. The bring-up record

One file per bring-up: `docs/lab/<yyyy-mm-dd>-<topic>-lab-run.md`, in the
house report shape (executive summary, then technical detail). Technical
detail carries, per asset: what was staged (name, sha256), each step's run id
and `LAB_STEP_END` rc, the host-side and tenant-side proof, what was left
running, and what was taken down. Problems found go in a findings table with
the evidence. Never paste a key, token, distribution ID or password into it.
