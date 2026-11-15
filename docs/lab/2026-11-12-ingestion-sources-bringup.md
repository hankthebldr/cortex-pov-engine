# Lab bring-up: HTTP log collector, XDR Collector, Broker VM syslog

**Tenant:** deathstar. **Hosts:** pov-target-01 (192.168.122.77, beacon
`pov-target-01-4dcdce`, XDR agent 9.4.0.470) and a new Broker VM `bvm-01` on
ms-01. **SimCore:** 1.1.1 from `origin/dev` 5f113dda on the Mac, :8888.
**Procedure:** `docs/lab/lab-asset-lifecycle.md` (the seven steps, the install
step shape, the four-layer review).
**Design this follows:** `docs/superpowers/specs/2026-11-11-ingestion-test-path-design.md`
(approved 2026-11-11), done by hand, before any of its 28 build tasks (#227).
**Status:** plan. Nothing below has run. Every run on a lab host waits for
Henry's OK for that run.

## Executive summary

Before the next release, each route that carries logs into the lab tenant gets
tested end to end, by hand, so that what we learn shapes the automated version
instead of the other way round. Three routes are in scope this round: the
cloud HTTP log collector, the XDR Collector installed next to the XDR agent on
the existing test machine, and a new on-premises Broker VM receiving syslog.
Each test sends a uniquely tagged record, confirms it arrived with a read-only
search, and sends one record that is deliberately wrong and must not arrive.
Only that pair counts as proof the route works. The main risk is the XDR
Collector: the test machine runs an operating system version the vendor does
not list, and running the collector next to the agent is undocumented, so a
failure there is reported as a host problem, not as a broken route. The Engine
is available but out of scope. Recommended action: run the HTTP collector test
first (no new equipment), then the Broker VM, then the XDR Collector, and
pair the Broker VM and configure its syslog applet at the two points marked
**Henry, now** below.

## Technical detail

### 0. Before anything runs

| # | Check | Owner | Why |
|---|---|---|---|
| 0.1 | Reserve the Mac's address in Pi-hole DHCP (192.168.1.105) | Henry | The beacon dials an IP; the Mac moved .106 → .105 on 2026-11-11 and took the beacon offline mid-cycle |
| 0.2 | `GET /api/health`: only `api_auth` degraded, agent `pov-target-01-4dcdce` online | Claude | Baseline for the review |
| 0.3 | `POST /api/connectors/xsiam_tenant/preflight` on deathstar, record the clock skew | Claude | Read-back grace windows depend on it |
| 0.4 | Confirm no other session is running tests against :8888 (`GET /api/runs` for the last hour) | Claude | 2026-11-12 20:54 UTC: a Tier-D run hit the live SimCore (#215) |
| 0.5 | Note the window start `T0` (UTC) | Claude | Every review reads from `T0` |

### Phase A: HTTP log collector (no new equipment)

**A1. Create the collector (Henry, console).** Settings › Data Sources &
Integrations › + Add New › HTTP › Add.

| Field | Value |
|---|---|
| Name | `CortexSim door http` |
| Compression | uncompressed |
| Log Format | JSON |
| Vendor / Product | `cortexsim` / `door_http`, so the dataset is `cortexsim_door_http_raw` |

Save & Generate Token. Copy the endpoint URL (Custom Collectors page,
`https://api-<tenant>/logs/v1/event`) and the key. The key is shown once.

**A2. Register it in SimCore (Henry, console, so the key never enters chat).**
Tenant › Collector targets › Add: name `door-http`, kind *XSIAM HTTP
collector*, the URL, dataset `cortexsim_door_http_raw`, key in the write-only
field, auth header `Authorization` with an empty scheme (the bare key; a
`Bearer` scheme gets 401).

**A3. Check, then test record (Claude).** `POST /api/collector-targets/door-http/preflight`
without, then with, `write_canary`. Expect `reachable`, then `accepted`.
Record the send time.

**A4. Tagged records through an emitter (Claude).** One campaign, one step:
plugin `idp_signin_emulator`, one `event_pattern`, `iterations: 1`,
`canary_token: door-http-<yyyymmdd>-<4 hex>`, `collector_target: door-http`.
The token lands in every record as `cortexsim_canary`. Launch it and record
the delivery accounting: `delivered`, with attempted equal to delivered
(call that number N).

**A5. Read back (Claude, read-only XQL through SimCore).**

```
dataset = cortexsim_door_http_raw
| filter cortexsim_canary = "door-http-<yyyymmdd>-<4 hex>"
| fields _time, _insert_time, cortexsim_canary, cortexsim_run_id
```

Pass: exactly N rows. Ingest latency is `_insert_time` minus the send time.
The A3 test record carries no nonce yet (spec §7.8), so it is read back by
window only: `filter cortexsim_preflight = true and _insert_time >= <send time>`.

**A6. Negative control (Claude).** A second target `door-http-badkey` with a
deliberately wrong key; send a test record. Pass: 401 at send (delivery
code `collector_unauthorized`) and no row for its window. Delete the target
afterwards.

**Phase A passes** when A4 reads N of N delivered, A5 returns exactly those N
rows, and A6 is refused and absent. It proves SimCore's JSON POST reaches
`cortexsim_door_http_raw`. It does not prove a detection fires: the cards
query vendor datasets, which this collector's `cortexsim`/`door_http` naming
deliberately avoids. Mapping vendor datasets is the wizard's job
(`docs/superpowers/specs/2026-11-12-http-collector-wizard-design.md`).

### Phase B: Broker VM `bvm-01` on ms-01

**B1. Stage (Henry).**

```bash
scp ~/Downloads/deathstar-bvm-image.qcow2 henry@192.168.1.110:/tmp/
# on ms-01:
df -h /var/lib/libvirt/images        # thin disk, 480 GiB virtual; check headroom
echo "9d87bf8af037c7a195b42ffcac273ab048981b76a06386a1ea09b88a828cbd71  /tmp/deathstar-bvm-image.qcow2" | sha256sum -c -
sudo install -m 0644 /tmp/deathstar-bvm-image.qcow2 /var/lib/libvirt/images/bvm-01.qcow2
```

**B2. Create the VM (Henry, ms-01).** Vendor sizing: 4 vCPU, 8 GB RAM, the
image's thin disk. Same libvirt network as pov-target-01, so syslog arrives
from pov-target-01's real address and nothing is exposed on the LAN.

```bash
sudo virt-install --import --name bvm-01 --memory 8192 --vcpus 4 \
  --disk path=/var/lib/libvirt/images/bvm-01.qcow2,format=qcow2 \
  --osinfo generic --network network=default --graphics vnc --noautoconsole
sudo virsh domifaddr bvm-01
```

Pin the address so the syslog target never moves (Henry, ms-01; one
reservation on the libvirt network):

```bash
MAC=$(sudo virsh domiflist bvm-01 | awk '/network/ {print $5}')
sudo virsh net-update default add ip-dhcp-host \
  "<host mac='$MAC' name='bvm-01' ip='192.168.122.<n>'/>" --live --config
```

**B3. Path check before pairing (Claude, read-only draft on pov-target-01, after
Henry's OK).** pov-target-01 sits behind the same NAT, so it is a proxy for
bvm-01's uplink: resolve `br-<tenant>.xdr.<region>.paloaltonetworks.com` and
`distributions.traps.paloaltonetworks.com`, open TLS to each on 443, and open
TCP to bvm-01 on 4443. The tenant host names come from the console's Broker VM
page; they are not guessed. This step proves the network, not the broker.

**B4. First login (Henry).** Tunnel and open the console:

```bash
ssh -N -L 4443:192.168.122.<n>:4443 henry@192.168.1.110
# then https://localhost:4443  (Broker VM 19.x+; 18.x serves on 443)
```

Log in with the vendor's documented default password, set a new one (12+
characters, upper, digit, special), store it in 1Password as
`Lab / bvm-01 console`. Set NTP if the defaults (`time.google.com`,
`pool.ntp.org`) are not reachable. Leave the internal network at
`172.17.0.1/16` (no clash on the libvirt side).

> **Henry, now: pair the Broker VM.** Do this only after B3 passed and B4's
> console is up, because the token expires after 24 hours. Generate the token
> (Settings › Configurations › Data Broker › Broker VMs › Add Broker ›
> Generate Token), enter it in the bvm-01 console, register. Tell Claude
> when the Broker VMs page lists `bvm-01` as connected.

**B5. Verify the pairing (Claude, read-only).** `collection_auditing` for the
last hour shows no `could not reach the receptor` entries, and the broker's
heartbeat appears in the tenant metrics. Record what each read returned; a
read that returns nothing is `not checked`, not `ok`.

> **Henry, now: configure the syslog applet.** Do this after B5. On bvm-01 in
> the console: activate the Syslog Collector and add one row, which cannot be
> edited later except for its source network:
>
> | Field | Value |
> |---|---|
> | Protocol / Port | TCP / 5514 (allowed range; TCP gives a connect result, UDP does not) |
> | Format | CEF |
> | Vendor / Product | `cortexsim` / `door_syslog` → dataset `cortexsim_door_syslog_raw` |
> | Source Network | `192.0.2.1/32` for now (a documentation address; nothing sends from it) |
>
> Tell Claude when it is saved.

**B6. Calibrate the source address (Claude, draft on pov-target-01).** Send one
line that must be rejected, then read the rejection to learn which address the
Broker VM sees:

```bash
logger -n 192.168.122.<n> -P 5514 -T --octet-count --rfc3164 -t cortexsim \
  'CEF:0|cortexsim|door_syslog|1|door-calibration|CortexSim calibration <nonce>|1|cs1Label=cortexsim_door_nonce cs1=<nonce> msg=calibration - safe to discard'
```

```
dataset = collection_auditing | sort desc _time | limit 20
```

Expect `Connection rejected from <ip>`; it should be 192.168.122.77. Henry
then sets the row's Source Network to `<ip>/32`.

**B7. Probe (Claude, draft on pov-target-01).** The same `logger` line with a
fresh nonce and `door-probe`. Read back:

```
dataset = cortexsim_door_syslog_raw | filter _raw_log contains "<nonce>"
```

If the column holding the nonce differs, record which one it was; the spec
lists this as unverified.

**B8. Negative controls.**
- *Wrong source (Henry, on ms-01):* the same line with its own nonce, sent from
  ms-01 (192.168.122.1, outside the `/32`). Expect no row and a rejection in
  `collection_auditing`. The spec planned to send this from SimCore on the Mac,
  but the Mac has no route to 192.168.122.0/24, so ms-01 stands in.
- *Wrong format (Claude, draft on pov-target-01):* a plain-text line with a
  nonce and no CEF header. Expect no row and a format complaint.

**Phase B passes** when B7's nonce lands, both B8 controls are absent, and
each control's complaint is recorded. Syslog over 514/UDP, other vendors'
rows, parsers and detections are not covered.

### Phase C: XDR Collector on pov-target-01 (co-installed with the agent)

Known risk, Henry's call (2026-11-11): Ubuntu Server 24 is not on the
collector's support list (the list ends at 22, re-checked 2026-11-12), and
installing it next to the XDR agent is not documented. A failure in this phase
reads **host unsupported**, never "route closed", and the agent is re-checked
after every step.

**C1. Tenant precondition (Henry).** The installation package used for
`hr-linux-collector-nov_sh.tar.gz` must still exist in the console, or the
collector cannot connect (vendor doc).

**C2. Stage (Henry).**

```bash
scp -J henry@192.168.1.110 ~/Downloads/hr-linux-collector-nov_sh.tar.gz henry@192.168.122.77:/tmp/
```

Expected sha256: tarball `2fc62451452fe37e81861b6671970e0aa77c3f8e9a4fa25e0552742adbc29a5a`,
installer inside `96f94fc6cc2c62ea2a92b012f65430647cb740faf0685a3487b50b6f8018f5a0`.

**C3. Pre-checks (Claude, read-only draft).** `/etc/os-release`, `uname -m`,
`findmnt -no OPTIONS /tmp` (a `noexec` here breaks the installer),
`df -h /`, the tarball's sha256, `find /etc/panw -type f -exec sha256sum {} +`
(the snapshot the post-check diffs against), `systemctl is-active traps_pmd`,
and `/opt/traps/bin/cytool runtime query` (component state). Plus the tenant
side: `OP-ENDPOINTS-GET-ENDPOINT` for pov-target-01 reads CONNECTED.

**C4. Install (Claude, one draft, install step shape).** Vendor steps for the
shell installer, verbatim:

```bash
echo "2fc62451452fe37e81861b6671970e0aa77c3f8e9a4fa25e0552742adbc29a5a  /tmp/hr-linux-collector-nov_sh.tar.gz" | sha256sum -c -
install -d -m 0700 /tmp/xdrc && tar -xzf /tmp/hr-linux-collector-nov_sh.tar.gz -C /tmp/xdrc
echo "96f94fc6cc2c62ea2a92b012f65430647cb740faf0685a3487b50b6f8018f5a0  /tmp/xdrc/collector-1.5.3.2371.sh" | sha256sum -c -
mkdir -p /etc/panw
test ! -e /etc/panw/collector.conf || { echo "collector.conf already present"; exit 3; }
cp /tmp/xdrc/collector.conf /etc/panw/
chmod +x /tmp/xdrc/collector-1.5.3.2371.sh
/tmp/xdrc/collector-1.5.3.2371.sh
```

**C5. Post-checks (Claude, read-only draft).** The new units
(`systemctl list-units --all | grep -iE 'collector|xdrc|filebeat'`), their
state and log tail, the `/etc/panw` diff against C3 (any change to an agent
file is `XDRC_AGENT_FILES_TOUCHED`), `systemctl is-active traps_pmd` and
`cytool runtime query` again, and `OP-ENDPOINTS-GET-ENDPOINT` still
CONNECTED. Record where the collector installed itself and its uninstall
path: the vendor's Linux install page does not document either.

**C6. Profile and policy (Henry, console).** XDR Collectors › Profiles › Linux
› Filebeat, named `CortexSim door probe`, one `filestream` input
`cortexsim-door-probe` on `/var/log/cortexsim/door-xdrc/probe.log`, with
`processors: [add_fields: {fields: {vendor: cortexsim, product: door_xdrc}}]`.
Map it to a policy that covers pov-target-01 only.

**C7. Probe and control (Claude, draft).** Append one JSON line with a fresh
nonce to `probe.log`, and one control line with its own nonce to
`/var/log/cortexsim/door-xdrc/excluded.log`, which the profile does not cover.

**C8. Read back (Claude, read-only XQL).**

```
dataset = cortexsim_door_xdrc_raw | filter _raw_log contains "<nonce>"
preset = metrics_view | filter _collector_ip = "192.168.122.77" | sort desc last_seen | limit 5
```

**Phase C passes** when the probe nonce lands, the control nonce does not, and
the agent reads CONNECTED with its components running after the install. It
proves this collector, on this unsupported host, shipped a line from a file to
`cortexsim_door_xdrc_raw`; nothing about Windows collection or supportability.

### Spin-down (when Henry calls it)

| Asset | Steps |
|---|---|
| HTTP collector | Delete targets `door-http`, `door-http-badkey` in SimCore; disable or delete `CortexSim door http` in the console |
| Broker VM | Remove the syslog row and deregister in the console; on ms-01 shut down, undefine and archive the disk (lifecycle §4.1); remove the DHCP reservation |
| XDR Collector | The vendor uninstaller (path recorded at C5) through the install step shape; remove `/etc/panw/collector.conf` only, never `cortex.conf`; re-check the agent; remove the profile and policy in the console |
| Files on pov-target-01 | Read-only verify-clean draft: no collector units or processes, `/etc/panw` equals the C3 snapshot, `/tmp/xdrc` and the staged tarball moved to `/var/backups/cortexsim/<date>/` |

### Review after each phase

Run the four-layer review (`lab-asset-lifecycle.md` §5) for the phase window
and paste its result into the bring-up record,
`docs/lab/2026-11-12-ingestion-sources-lab-run.md`. Until
`scripts/lab/lab-review.py` exists, Claude runs the same reads by hand and
records each call.

### What this round feeds back into the #227 build

| Finding before the run | Effect on the plan |
|---|---|
| The XDR Collector package is a shell installer (`collector-1.5.3.2371.sh`, Makeself), not the `.deb` the spec's §7.7 assumed | Task 20–22 install, validity check and uninstall follow the `.sh` path; add `/tmp noexec` to the validity facts |
| The Mac cannot route to the libvirt network, so SimCore cannot send the spec's `wrong_source` syslog control | Task 18: the control's sender is a second lab host (ms-01 here), not SimCore |
| The emitters already carry an operator canary (`cortexsim_canary`) | Task 1's nonce can reuse that field's shape for EAL records |
| The vendor's Linux install page documents neither the service name nor an uninstall | C5 records both; Task 21 pins them |
