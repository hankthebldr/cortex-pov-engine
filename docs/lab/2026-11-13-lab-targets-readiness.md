# Lab targets readiness for the v1.2.0 testing pass

**Read on:** 2026-11-13, 22:08–22:25 UTC. **Tenant:** deathstar. **SimCore:** `:8888`, 1.1.1 (container
`cortex-pov-engine-simcore-v1.1.1`), Mac at 192.168.1.105. **Hosts:** `pov-target-01` (Ubuntu 24.04.5
libvirt guest, 192.168.122.77) on `ms-01` (192.168.1.110).
**Builds on:** `docs/superpowers/specs/2026-11-11-ingestion-test-path-design.md` (#227) and the bring-up plan
`docs/lab/2026-11-12-ingestion-sources-bringup.md` (branch `docs/docs-lab-ingestion-bringup`, not pushed). Section 6
lists where this record corrects that plan.

Every check below is read-only. Nothing on the VM, ms-01, SimCore or the tenant was changed. The steps marked
**Henry** need a click in the console or an OK before Claude runs them.

## Executive summary

The test machine and its endpoint protection are working and reporting. Four of the six data paths are not
ready yet. Two problems sit on the test machine itself. First, a leftover from an earlier simulation still fires every
minute: about a fifth of the machine's process activity in the last day was this one leftover, and it could be
matched to a new test by mistake. Second, none of the twelve service accounts the scenarios impersonate is usable
(ten are missing, two are locked), so about 270 Linux steps in 78 scenarios would fail before they do anything, and
that failure looks like a missed detection in a report. Both have a short fix waiting on approval. The log collector on the test
machine has not been installed: the vendor does not list this operating system version, so a failure there is a host
problem, not a product gap. The on-premises log broker has not been deployed. The host has ample spare capacity and
fast spare disks, but only one of the four planned collection types has a data source in the lab today. The cloud HTTP
collector is receiving data, but the local simulator is not pointed at it yet, and the simulator's built-in
connection test would wrongly report this collector as refusing data. **Recommended action:** approve
the cleanup and the account setup now, deploy the broker this week, and run the testing pass only after the
local simulator rebuild adds the HTTP collector target. Treat Windows event, file share and database collection
as closed paths in this pass unless a source is added.

## Technical detail

### 1. Readiness table

| # | Target | State | Read-only evidence | Remaining steps |
|---|---|---|---|---|
| 1 | VM beacon `pov-target-01-4dcdce` | **ready** | SimCore `GET /api/agents`: `status online`, `last_seen_age_seconds 3.3`. On the VM (run `1ac4cc89`): `cortexsim-agent active`, unit carries `--server http://192.168.1.105:8888 --id pov-target-01-4dcdce`. Mac `en0` = 192.168.1.105 | Reserve 192.168.1.105 for the Mac in Pi-hole DHCP (not verified; the Mac already moved .106 → .105 once) |
| 2 | XDR agent 9.4.0.470 | **ready** | `get_filtered_endpoints hostname=pov-target-01`: `CONNECTED`, `operational_status PROTECTED`, details `[]`, content `UP_TO_DATE`, policy `SEBC-FY25-LAB-LinuxServerPolicy`, last_seen 22:05:29Z. `xdr_data` last 24 h: PROCESS_START 7,249, FILE_OPEN 16,734, NETWORK, LOAD_IMAGE, NAMESPACE and STORY events, newest seconds old. `cned`/`piud` STOPPED (see §2.2) | None |
| 3 | Service-account identities | **blocked on Henry** (OK to run §3.2) | Run `1ac4cc89`: `www-data` and `nobody` have `/usr/sbin/nologin`, `runuser -l` → "This account is currently not available". `svc-backup`, `node`, `postgres`, `mysql`, `python3`, `app`, `developer`, `runner`, `vertex-agent`, `user`: **ABSENT**. Corpus on dev: ~273 POSIX steps in 78 scenarios run as one of these (text heuristic: `www-data` 116, `svc-backup` 112, `runner` 14, `node` 11, `developer` 9, `app` 6, `vertex-agent` 4, `nobody` 1) | OK the provisioning script in §3.2 |
| 4 | VM free of CortexSim leftovers | **blocked on Henry** (OK to run §3.1) | Runs `1ac4cc89` + `4ac6543f`: live root cron `/etc/cron.d/cortexsim-sim` (SIM-CDR-003, written 2026-11-11 18:40Z) appends to `/tmp/cortexsim_escape_proof.txt` every minute, 3,090 lines so far. In `xdr_data` that is 1,436 `sh` process events in 24 h. Also user `cortexsim_testuser` (uid 1001, in group `sudo`), SIM-EDR-003 files in `/tmp` incl. `/tmp/.cortexsim_ssh_test/id_ed25519`, `/var/tmp/.cortexsim-akira` (SIM-EDR-025, 52 files), `/var/tmp/.cortexsim-ice` (SIM-EDR-023), `/etc/systemd/system/cortexsim-decoy.service` (disabled, inactive) | OK the cleanup script in §3.1 |
| 5 | XDR Collector on `pov-target-01` | **blocked on Henry** (support decision + console) | No collector units on the VM, no `/etc/panw/collector.conf`. `metrics_view` over 7 d: no row with an XDR Collector or `_collector_hostname`. Vendor support list, read today: Ubuntu Server up to 22, the VM is 24.04.5. Tarball sha256 `2fc62451…29a5a` matches the plan | §4 |
| 6 | Broker VM `bvm-01` on ms-01 | **not started** (needs Henry's OK to create the VM) | ms-01: 16 threads, 62 GiB RAM (55 GiB available), libvirt runs only `pov-target-01`. Image `deathstar-bvm-image.qcow2` sha256 `9d87bf8a…cbd71`, qcow2 v3, virtual size 480 GiB | §5 |
| 6a | Applet: Syslog Collector | **not started** | No broker of ours in the tenant. The only broker in `metrics_view` is `5X7S0G08` at 10.60.0.3 (§7) | §5.4 |
| 6b | Applet: Windows Event Collector | **not started; no source** | The lab has no Windows Server 2012 R2+ forwarder | §5.5 |
| 6c | Applet: Files and Folders Collector | **not started; no source** | No SMB or NFS share exists in the lab | §5.5 |
| 6d | Applet: Database Collector | **not started; no source** | No MySQL/PostgreSQL/MSSQL/Oracle instance exists in the lab | §5.5 |
| 7 | HTTP event collector `pov-engine-test` (tenant side) | **receiving** (created by Henry ~22:00Z for the canary session; not touched here) | `metrics_view` 7 d: `_collector_type HTTP`, `CUSTOM/custom`, 19 events, last_seen 22:00Z. `dataset = custom_custom_raw` over 5 d: 19 rows, inserted 22:01:14–22:02:52Z (a 24 h window shows 18: canary T4 carried a body `_time` of 2026-11-10 22:01:12Z, which the collector honours). Canary session: JSON, **gzip required** (uncompressed body → 400), every 200 landed and no refused body did | None for the collector itself. The key was pasted into a chat: rotate it |
| 8 | SimCore collector target → that collector | **not pointed** | `GET /api/collector-targets`: `count 0`, `code NO_COLLECTOR_TARGETS`, 15 unbound emitter plugins. `:8888` predates #263: `openapi.json` has no `/arrival` or `/data-streams/{key}/detections` route. The key is in no env var or vault | §6 |

### 2. Evidence

#### 2.1 How the VM was read

Two one-step `identity: root` drafts through the existing beacon, both read-only, both rc 0:

| Run | Draft | Reads |
|---|---|---|
| `1ac4cc89-b9ea-4e29-b676-9ee080687225` (22:08:45Z) | `SIM-DRAFT-lab-readiness-probe-pov-target-01-targets-20261113` | host, beacon unit, `cytool runtime query`, `getent passwd` + `runuser -l <u> -c 'echo RUNUSER_OK'` for 12 accounts, `find` for `*cortexsim*`/`*atomic*`, `/tmp` `/var/tmp` `/dev/shm`, users uid ≥ 1000, cron, custom units, timers, sudoers.d, profile.d, `ld.so.preload`, root `authorized_keys`, XDR Collector pre-checks |
| `4ac6543f-6620-481d-bf48-6ec0dfb05298` (22:10Z) | `SIM-DRAFT-lab-readiness-probe-2-pov-target-01-leftovers-detail-20261113` | cron file + journal, decoy unit state, `/opt/cortexsim`, test user, EDR-003 files, `/var/tmp` trees, `/var/log/traps` |

Each probe run raises the known low-severity "Uncommon Linux shell command execution" analytics alert
(`cortexsim-agent` → `sh`). It is expected and is not a scenario result.

Not leftovers, left in place: `/opt/cortexsim/cache` (the beacon's digest-checked artifact cache, one 41 KB file),
`/home/henry/.cortexsim` (empty, from the 2026-11-02 install), `/tmp/host-scanner` (empty log, created 00:10Z the day
the agent installed). Henry's agent installer files `/tmp/cortex-9.4.0.470.sh`, `/tmp/cortex.conf` and
`/tmp/cortex-install.log` are not CortexSim's. Archive them when convenient.

#### 2.2 `cned` and `piud` STOPPED does not matter for this pass

The vendor docs do not describe either component (the docs Q&A says so). The test is what the tenant receives: the
agent reports `PROTECTED` with no operational-status details, and `xdr_data` carries process, file, network, image-load
and namespace events from this host with the newest seconds old. Every telemetry type the Linux scenarios read is
arriving. If a scenario later depends on a component that is not in that list, re-check here first.

#### 2.3 Tenant reads (all read-only)

| Read | Tool | Result |
|---|---|---|
| Endpoint | cortex-mcp `get_filtered_endpoints` (hostname) | row 2 above |
| Event types, 24 h | SimCore `POST /api/xsiam/tenants/deathstar/xql`: `dataset = xdr_data \| filter agent_hostname = "pov-target-01" \| comp count() by event_type, event_sub_type` | row 2 above |
| Cron noise, 24 h | same route, `… and action_process_image_command_line contains "CORTEXSIM-PERSISTENCE"` | 1,436 `sh` events |
| Collectors, 7 and 30 d | same route, `preset = metrics_view \| comp … by _collector_type, _collector_name, _vendor, _product, _broker_device_*` | rows 5–7 above, §7 |
| Agent installation packages | SimCore op `OP-DISTRIBUTIONS-GET-DISTRIBUTIONS` (`access_class: read`) | 40 agent packages incl. `hr-linux-installer-nov`. Collector installers are not in this API, so the collector package's existence is a console check (§4 step 1) |

### 3. Fixes on `pov-target-01` (each waits for Henry's OK)

Both run as a one-step `identity: root` draft through the beacon, the same way as the probes. Nothing is
deleted outright: files move to `/var/backups/cortexsim/20261113-lab-targets/`.

#### 3.1 Cleanup

```bash
set -u
A=/var/backups/cortexsim/20261113-lab-targets
install -d -m 0700 "$A"
# 1. stop the live SIM-CDR-003 cron first, so the proof file stops growing
[ -e /etc/cron.d/cortexsim-sim ] && mv /etc/cron.d/cortexsim-sim "$A/"
# 2. SIM-EDR-025 decoy unit (already disabled and inactive)
[ -e /etc/systemd/system/cortexsim-decoy.service ] && mv /etc/systemd/system/cortexsim-decoy.service "$A/" && systemctl daemon-reload
# 3. SIM-EDR-003 / -023 / -025 / CDR-003 files
for p in /tmp/.cortexsim_ssh_test /tmp/cortexsim-backdoor.service /tmp/cortexsim_cron_backup \
         /tmp/cortexsim_cron_test /tmp/cortexsim_profile_test /tmp/cortexsim_escape_proof.txt \
         /var/tmp/.cortexsim-akira /var/tmp/.cortexsim-ice; do
  [ -e "$p" ] && mv "$p" "$A/"
done
# 4. SIM-EDR-003 test user: record it, then remove it. Locking is not enough:
#    the scenario's `useradd cortexsim_testuser` collides with an existing account.
if id cortexsim_testuser >/dev/null 2>&1; then
  getent passwd cortexsim_testuser > "$A/cortexsim_testuser.passwd"
  id cortexsim_testuser > "$A/cortexsim_testuser.id"
  userdel cortexsim_testuser
fi
ls -la "$A"
```

Verify by re-running probe 1 (`1ac4cc89`'s draft). Expected: the leftovers section lists nothing, no uid 1001,
`/etc/cron.d` holds only `e2scrub_all` and `sysstat`, and the cron query in §2.3 stops growing.

The beacon does not run a scenario's `cleanup.commands` (SIM-CDR-003 declares exactly this cleanup at
`scenarios/cdr/cdr-003-container-escape.yml:244`). Every scenario in the testing pass that persists something will
leave it behind again. Re-run probe 1 between scenarios that share artefact names.

#### 3.2 Identity provisioning

The same accounts and shape as `deploy/tier-d/Dockerfile.target` (lines 62–110), i.e. every name in
`spec/identity_harness.json` `service_accounts` plus `user`, each with a login shell and a home directory.

```bash
set -eu
usermod --shell /bin/bash --home /var/www www-data
install -d -o www-data -g www-data -m 0755 /var/www
usermod --shell /bin/bash nobody
for u in postgres mysql node python3 svc-backup app developer runner vertex-agent user; do
  if ! getent passwd "$u" >/dev/null; then
    useradd --create-home --shell /bin/bash "$u"
  else
    usermod --shell /bin/bash "$u"
    install -d -o "$u" -g "$u" -m 0755 "/home/$u"
  fi
done
# Inert credential-shaped bait, as Tier-D ships it, so credential-sweep steps find something
for u in svc-backup user developer; do
  h="/home/$u"
  printf 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI%s cortexsim-fixture\n' "$u" > "$h/.ssh_fixture_pub"
  printf '# cortexsim tier-d fixture - inert\nhost=localhost\nuser=%s\n' "$u" > "$h/.pgpass"
  printf 'cd /tmp\nls -la\n' > "$h/.bash_history"
  chown -R "$u:$u" "$h"; chmod 600 "$h/.pgpass" "$h/.bash_history"
done
for u in www-data nobody svc-backup node postgres mysql python3 app developer runner vertex-agent user; do
  printf '%s: ' "$u"; timeout 5 runuser -l "$u" -c 'echo RUNUSER_OK' 2>&1 | tail -n 1
done
```

Expected: twelve `RUNUSER_OK` lines. `nobody` keeps home `/nonexistent`, as Tier-D does, so its `runuser` prints a
cd warning first. That is one step in the corpus.

Two side effects to accept. `nobody` and `www-data` get a login shell (lab host only). And a `postgres` or `mysql`
account created here is a regular user, which can make a later `apt install postgresql` fail in postinst. If §5.5's
database source goes on this VM, install the database before running this script.

### 4. XDR Collector on `pov-target-01`

**Support caveat (re-read 2026-11-13):** the XSIAM machine-requirements page lists Ubuntu Server 12–22, not 24.
It does not mention co-installing the collector next to the XDR agent. The vendor recommends the DEB or RPM package
over the SH that was downloaded. Henry's call on 2026-11-11 stands (this host). A failure here is
**host unsupported**, not "door closed". Alternative if it fails: a small Ubuntu 22.04 guest on ms-01, which has the
capacity (§5.1).

1. **Henry, console:** confirm the collector installation package behind `hr-linux-collector-nov_sh.tar.gz`
   still exists (XDR Collectors › installers). If it was removed, the collector cannot connect.
2. **Claude, after OK:** stage the tarball: `scp -J henry@192.168.1.110 ~/Downloads/hr-linux-collector-nov_sh.tar.gz
   henry@192.168.122.77:/tmp/`. Key-based SSH from the Mac reaches the VM, checked today.
3. **Claude, after OK:** install through a root draft. Verify the tarball and installer hashes first, then follow the
   vendor's SH steps: `/etc/panw/collector.conf`, `chmod +x`, run as root. Bring-up plan §C4 has the exact script.
   `/tmp` is not `noexec` (pre-check passed: `/tmp` sits on `/`, mounted `rw,relatime`), and `/` has 34 GB free.
   `/etc/panw` today holds only the agent's `cortex.conf`. The script refuses to run if `collector.conf` is already present.
4. **Claude:** post-checks (plan §C5): the new units, `/etc/panw` diff, `traps_pmd` active, agent still CONNECTED.
5. **Henry, console:** XDR Collectors › Profiles › Linux › Filebeat profile `CortexSim door probe` with one `filestream`
   input on `/var/log/cortexsim/door-xdrc/probe.log` (plan §C6). Map it to a policy that covers `pov-target-01` only.
6. **Verify (Claude, read-only):**

   ```
   preset = metrics_view | filter _collector_hostname = "pov-target-01" or _collector_ip = "192.168.122.77"
   | comp sum(total_event_count) as events, max(last_seen) as last_seen by _collector_type, _collector_name, _vendor, _product
   ```

   Ready means a row appears and the probe nonce from plan §C7 lands in `cortexsim_door_xdrc_raw` while the control
   file's nonce does not.

### 5. Broker VM `bvm-01` on ms-01

#### 5.1 Placement and sizing

| | Vendor minimum | ms-01 today |
|---|---|---|
| CPU | 4 cores | 16 threads (i5-12600H), `pov-target-01` uses 4 vCPU |
| RAM | 8 GB | 62 GiB, 55 GiB available, `pov-target-01` uses 8 GiB |
| Disk | 512 GB, thin | Image is a thin qcow2 with a 480 GiB virtual size. Root LV (holds `/var/lib/libvirt/images` and the K3s control plane): 56 GB free. `/mnt/data0` and `/mnt/data1`: 870 GB free each, owned by `henry` |

Put the disk on `/mnt/data0`, not `/var/lib/libvirt/images`. A thin disk that grows past 56 GB would fill the
root volume under K3s. `/mnt/data0` also needs no sudo: `henry` owns it and is in the `libvirt` group (sudo on
ms-01 asks for a password).

Network: libvirt `default` NAT (192.168.122.0/24, DHCP .2–.254), the same as `pov-target-01`. Syslog then arrives
from the VM's real address and nothing listens on the LAN. Two consequences: the Mac cannot reach the broker
except through an SSH tunnel, and a LAN or Windows source cannot reach it either. Change this only if §5.5 adds a
LAN source.

#### 5.2 Deploy (Claude can run all of this from the Mac after Henry's OK; no sudo)

```bash
# Stage and verify the image
ssh henry@192.168.1.110 'install -d -m 0755 /mnt/data0/libvirt'
scp ~/Downloads/deathstar-bvm-image.qcow2 henry@192.168.1.110:/mnt/data0/libvirt/bvm-01.qcow2
ssh henry@192.168.1.110 'echo "9d87bf8af037c7a195b42ffcac273ab048981b76a06386a1ea09b88a828cbd71  /mnt/data0/libvirt/bvm-01.qcow2" | sha256sum -c -'

# Pin addresses BEFORE first boot (the broker takes DHCP by default). Also pins pov-target-01, which today
# holds .77 on a lease only (no reservation), and the syslog allowlist depends on it.
ssh henry@192.168.1.110 'virsh -c qemu:///system net-update default add ip-dhcp-host "<host mac=\"52:54:00:cb:01:01\" name=\"bvm-01\" ip=\"192.168.122.10\"/>" --live --config &&
  virsh -c qemu:///system net-update default add ip-dhcp-host "<host mac=\"52:54:00:f9:1b:32\" name=\"pov-target-01\" ip=\"192.168.122.77\"/>" --live --config'

# Create the VM with the vendor's KVM settings: import, generic OS, 8192 MB, 4 vCPU
ssh henry@192.168.1.110 'virt-install --connect qemu:///system --import --name bvm-01 --memory 8192 --vcpus 4 \
  --disk path=/mnt/data0/libvirt/bvm-01.qcow2,format=qcow2 --osinfo generic \
  --network network=default,mac=52:54:00:cb:01:01 --graphics vnc,listen=127.0.0.1 --noautoconsole &&
  virsh -c qemu:///system autostart bvm-01 && sleep 60 && virsh -c qemu:///system domifaddr bvm-01'
```

#### 5.3 First login and pairing (Henry)

1. Tunnel from the Mac: `ssh -N -L 4443:192.168.122.10:4443 henry@192.168.1.110`. Open `https://localhost:4443`
   (Broker VM 19.x and later serve the console on 4443).
2. Log in with the vendor's documented default and set a new password. Set NTP only if the defaults are unreachable.
3. **Henry, now:** Settings › Configurations › Data Broker › Broker VMs › Add Broker › Generate Token. Paste it in the
   bvm-01 console and register. The token expires after 24 hours, so do steps 1–3 in one sitting.
4. In the tenant, rename the broker to `bvm-01`. The existing broker reports as `5X7S0G08`, so names are not
   set automatically.

Verify (Claude, read-only), after the first applet is active:

```
preset = metrics_view | filter _broker_device_ip = "192.168.122.10"
| comp sum(total_event_count) as events, max(last_seen) as last_seen by _broker_device_name, _collector_type, _vendor, _product
dataset = collection_auditing | sort desc _time | limit 20
```

SimCore has no Broker VM operation in its catalogue. The tenant-side Broker API (`GET /public_api/v1/brokers/`, and
`GET …/brokers/{device_id}/applets/{applet_name}/` for `syslog`, `wec`, `file`, `db`) needs the `broker_action`
permission. Until an op exists, the console's Broker VMs page is the pairing check.

#### 5.4 Syslog Collector applet (the one applet with a lab source)

**Henry, console**, after pairing: activate Syslog Collector and add one row:

| Field | Value |
|---|---|
| Protocol / Port | TCP / 5514 (a TCP connect result is observable; UDP 514 is not) |
| Format | CEF |
| Vendor / Product | `cortexsim` / `door_syslog` → dataset `cortexsim_door_syslog_raw` |
| Source Network | `192.168.122.77/32` (same bridge, no NAT between guests; calibrate per plan §B6 only if it is rejected) |

Verify (Claude): plan §B7's `logger -n 192.168.122.10 -P 5514 -T --octet-count …` line with a fresh nonce from a
root draft on `pov-target-01`. Then `dataset = cortexsim_door_syslog_raw | filter _raw_log contains "<nonce>"`.
Then the §B8 wrong-format control. The wrong-source control is sent from ms-01 (192.168.122.1).

#### 5.5 The other three applets need a source the lab does not have

| Applet | What must exist first (vendor docs) | Lab option | State for the pass |
|---|---|---|---|
| Windows Event Collector | Windows Server 2012 R2+ forwarders; the broker FQDN in DNS, resolvable by forwarders; the WEF client certificate on each forwarder with `NETWORK SERVICE` access to its key; TCP 5986 outbound from the forwarders | A Windows VM on ms-01 plus a Pi-hole record for `bvm-01` | Closed: the 31 Windows-event detections read as door closed |
| Files and Folders Collector | A Windows share (host/share + credentials) or an NFS path; the account must be able to rename and delete; UTF-8 files over 256 bytes; writes `<vendor>_<product>_raw` | An NFS export from `/mnt/data1` on ms-01 (sudo, Henry) | Closed until a share exists |
| Database Collector | MySQL, PostgreSQL, MSSQL or Oracle reachable by host/port, a database name, a read-only user; SSL on by default; writes `<vendor>_<product>_raw` | PostgreSQL on `pov-target-01`, installed before §3.2 | Closed until a database exists |

Activating an applet without its source proves nothing, and a "Connected" applet status says nothing about data.
Each of these is ready only when a nonce row lands in its dataset.

### 6. HTTP collector and corrections to the bring-up plan

**HTTP collector.** The canary session owns the tenant side, and nothing there was created, edited or posted to from
here. The tenant shows it receiving (row 7). SimCore does not point at it: `:8888` has zero collector targets and
runs a build without #263's `declaration`/`arrival` routes. Henry's steps, after the `:8888` rebuild that follows
#235:

1. Rotate the `pov-engine-test` key (it was pasted into a chat), then Tenant › Collector targets › Add with the
   collector's URL, dataset `custom_custom_raw` and **compress on**. Type the new key into the write-only field
   yourself. The auth header is `Authorization` with no scheme, because a `Bearer` scheme gets 401.
2. Claude, read-only: `GET /api/collector-targets` showing `count 1` and the target as default.

**Do not read this collector's preflight as a closed door.** Both SimCore preflights (campaign preflight and the
target's "Send test record") post an uncompressed `application/json` body whatever the target's `compress`
setting (`core/eal_simulator/collector.py:271-273`). This collector refuses uncompressed bodies with 400, so the
preflight reads `collector_rejected` even when a `compress=true` campaign lands. Prove the route with a compressed
canary and a `custom_custom_raw` read-back, as the canary session did. Making the preflight honour `compress` is a
SimCore fix for the canary session's follow-up list.

**Count read-backs by arrival.** This collector honours a top-level `_time` in the body, so a record can land with a
`_time` days in the past. A relative timeframe measured on `_time` then misses it. Filter on `_insert_time`, or use a
window that reaches back past every `_time` you sent. `metrics_view` already counts by arrival.

**Corrections to `docs/lab/2026-11-12-ingestion-sources-bringup.md`:**
- B1/B2 put the Broker VM disk in `/var/lib/libvirt/images`. That is on the 56 GB-free root LV under K3s, so use
  `/mnt/data0` (§5.1), which also removes every `sudo` from B1–B2.
- B2 reserves the broker's address after first boot. Reserve it before (§5.2), and reserve `pov-target-01` too.
- B4 says to store the console password in 1Password. There is no 1Password on this Mac.
- B5–B7 assumed a source-network calibration. On the shared libvirt bridge the source is `192.168.122.77` directly.

### 7. Other findings

- **Unknown broker in the tenant.** `5X7S0G08` at 10.60.0.3 has shipped Syslog from 10.60.0.5 (`unknown/unknown`)
  for 30 days, 3,021 events, last seen 2026-11-13 21:15Z. Neither address is on 192.168.1.0/24 or 192.168.122.0/24.
  It is not the lab broker. Henry should confirm whose it is, so nobody reads its rows as `bvm-01`'s.
- **No libvirt DHCP reservation for `pov-target-01`.** Its .77 is a lease, expiring 23:13:27 local time on ms-01
  when read. dnsmasq normally renews the same address, but the syslog allowlist and the collector's
  `_collector_ip` read assume it. §5.2 pins it.
