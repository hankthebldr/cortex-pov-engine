# Kubernetes container agent — design

**Date:** 2026-11-10
**Status:** Phase 1 built (branch `feature/agent-k8s-container-agent`, PR #171). Not tenant-verified. See "As built" at the end.
**Branch:** `docs/agent-k8s-container-agent` (from `dev`)

Decisions are marked **[Henry]** where Henry made them in the design
conversation and **[call]** where the author chose a default that Henry has not
confirmed. A builder may change a **[call]** with a reason; a **[Henry]** item
needs Henry.

---

## 1 · Intent

A DC validating Cortex container detections needs an attacker's foothold
*inside* a pod, on a node where the XSIAM agent runs as a DaemonSet. Today
CortexSim offers two ways to get there, and neither is that:

| Path | What it does | What is missing |
|---|---|---|
| Jumpbox beacon driving `kubectl` | Full run lifecycle | The process chain Cortex sees starts on the jumpbox, not in a container |
| K8s push manifest (`core/engine/k8s_manifest.py`) | One pod per scenario, runs once, exits | No enrolled agent: no live output, no abort, no on-demand re-run, and each scenario is a new `kubectl apply` |

This design adds the third: **the Linux beacon running inside a pod**, enrolled
with SimCore like any other agent, so cluster scenarios are dispatched from the
console and get the full pull lifecycle (run record, SSE, abort, seeded results,
reconcile, MTTD).

The pattern is not new. Henry's `CDR` and `xsiam-prisma-cdr-lab` repos already
detonate container detections with one manifest: a privileged `alpine` pod whose
`command:` fetches a script and runs it. This design keeps that shape and swaps
what the command starts.

```
SimCore  ◄── enroll / poll / output / complete ──┐
   │                                             │
   │ GET /api/agents/manifest                    │
   ▼                                             │
kubectl apply ──► Pod                            │
                   ├─ init: fetch + verify beacon (from SimCore) → emptyDir
                   └─ main: <any image>  ── exec beacon ──────────┘
                              └─ sh -c <step>   (as root / www-data / svc-backup …)
Node: XSIAM agent DaemonSet observes the container's process tree
```

### The bar

**[Henry]** At least as good as CrowdStrike's `detection-container`
(`quay.io/crowdstrike/detection-container`). Measured against it:

| Theirs | This design |
|---|---|
| One `kubectl create -f <url>` | Two commands: create a Secret, `kubectl apply -f <simcore-url>` (§3) |
| Purpose-built public image | Any public image; nothing to publish (§4) |
| 12 scripted detections, random timer, stdout only | The CDR corpus dispatched on demand, with a run record and read-back |
| Real Apache/PHP/MySQL so `httpd → sh` is genuine | Phase 1 uses the existing anchor rename; a real service is phase 2 (§9) |
| Runs with no control plane | Phase 3 (§9) |
| Nothing Kubernetes-specific | Escape, service-account token, `kubectl`, host mounts |

Phase 1 is ahead on measurement and Kubernetes coverage and behind on two rows.
Those two rows are sequenced, not dropped.

---

## 2 · Goals, non-goals, anti-goals

**Goals**

1. A DC can put an enrolled beacon into a cluster with two commands and no image
   build, registry, or Go toolchain.
2. The beacon detonates scenarios inside its own container, dispatched from the
   console, with the same lifecycle a host beacon has.
3. The pod can take the shape Henry's lab proved (privileged, host root
   mounted), behind explicit consent.
4. The upstream tools the lab fetched from GitHub at run time arrive from the
   payload shelf instead, digest-pinned.
5. Proven by a pod that actually ran, on a throwaway cluster.

**Non-goals (phase 1)**

- Replacing the push manifests. They stay; they are the no-SimCore-at-runtime
  path.
- A CortexSim-published container image.
- A manifest hosted on a public URL.
- Self-running mode with no SimCore.
- Launching steps in *other* pods (agent as launcher).
- Installing the XSIAM agent DaemonSet. It is a prerequisite, as in the lab.
- Windows containers.

**Anti-goals — failure even if it works**

- A pod that starts, enrolls, and silently cannot run steps (no `python3`, no
  `runuser`), so missing detections read as Cortex misses.
- A privileged, host-mounted, remotely-commanded pod left running in a customer
  cluster with no lifetime bound and no clear owner.
- Reporting anything here as tenant-verified. It is **0** until a run executes
  on a cluster with the XSIAM DaemonSet reporting to a live tenant.

---

## 3 · Operator experience

**[Henry]** The manifest is served by the DC's own SimCore, and the agent pulls
its setup when the container is created.

```bash
kubectl apply -f 'http://<simcore>:8888/api/agents/manifest'
```

```bash
kubectl -n cortexsim-agent create secret generic cortexsim-enroll --from-literal=server=http://<simcore>:8888 --from-literal=token=cxs_…
```

Apply the manifest first, then create the Secret. The manifest emits the
Namespace (with its Pod Security labels, as the push path does), so creating the
Secret first fails with `NotFound`; a pod applied before the Secret exists waits
and starts when the Secret appears. (Corrected in build: this section originally
said either order works; see §13 item 12.)

That is the `baseline` profile. **[Henry]** Gated profiles are the same single
URL apply, with the profile and its consent keys in the query string (§5):

```bash
kubectl apply -f 'http://<simcore>:8888/api/agents/manifest?profile=lab-privileged&cluster_privilege_authorized=true&node_access_authorized=true&authorized_by=<name>'
```

**[call]** The token lives in a Secret, never in the manifest URL or body. The
SimCore URL lives there too and the manifest carries neither, so it is static
per SimCore build and safe to paste, diff and keep in a GitOps repo. An optional
`artifact-token` key supplies the payload-shelf bearer token when SimCore runs
`CORTEXSIM_K8S_PAYLOAD_AUTH=token`. This matches how the host installer keeps the token in an env
var rather than the URL.

The console's existing agent-onboarding surface gains a Kubernetes tab that
mints the token and shows these commands. Removal is `kubectl delete namespace
cortexsim-agent`; cluster-scoped objects carry a label for
`kubectl delete clusterrole,clusterrolebinding -l cortexsim.io/agent=true`.

---

## 4 · The manifest

`GET /api/agents/manifest` returns one multi-document YAML, built as an object
graph and then serialised, the way `k8s_manifest.build_objects` does. Reuse that
module's `ClusterPosture` model, finding vocabulary, labels
(`cortexsim.io/…`), consent check and header builder rather than writing a
second manifest generator.

### Query parameters

| Param | Default | Meaning |
|---|---|---|
| `profile` | `baseline` | Pod posture, §5 |
| `image` | `debian:12-slim` | Main container image. **[Henry]** any public image that suits; alpine, busybox and others from Docker Hub. **[call]** on the default, see below |
| `prepare` | `packages` | Startup preparation, §6 |
| `arch` | `amd64` | Which beacon binary digest to pin (`amd64`/`arm64`) |
| `ttl_seconds` | `43200` | Absolute expiry, §7. Bounded 60..43200 by the existing `ClusterPosture` validator |
| `cluster_privilege_authorized`, `node_access_authorized` | absent | Consent keys for gated profiles, §5. Only the literal `true` counts |
| `authorized_by` | absent | Required with any consent key; who accepted the risk |

### Objects

Emitted: Namespace (Pod Security labels matching the profile), ServiceAccount,
optional RBAC, one ConfigMap holding the startup script, one Deployment
(`replicas: 1`), and the expiry reaper (§7).

**[call]** One pod on one node. A DaemonSet variant (an agent on every node) is
a later option, not phase 1.

### Pod

```
initContainers:
  - name: fetch            image: alpine:3.20   (k8s_manifest.DEFAULT_FETCH_IMAGE)
      wget  $SERVER/api/agents/binary?os=linux&arch=<arch>   → /cortexsim/agent.part      ($SERVER from the Secret)
      sha256sum check against the digest written in this manifest
      mv agent.part → /cortexsim/cortexsim-agent      (only after it verified)
      any failure → exit 78, reason on /dev/termination-log
containers:
  - name: agent            image: <image>
      command: ["/bin/sh", "/cortexsim-boot/start.sh"]
      start.sh:  prepare stage (§6)  →  exec /cortexsim/cortexsim-agent …
volumes: emptyDir /cortexsim, ConfigMap /cortexsim-boot, Secret cortexsim-enroll
env from downward API: POD_NAME, POD_NAMESPACE, NODE_NAME
```

**[call]** The fetch always happens in an init container, even when the main
image has `wget`. One code path, and the main image becomes truly arbitrary
(`debian:12-slim` ships neither `curl` nor `wget`). This is the same
init-container → `emptyDir` handoff, on the same image, that the served push
path uses.

**[call] Default main image is `debian:12-slim`, not `alpine`.** The causality
anchor works by starting a shell under another name. Alpine's and busybox's `sh`
is a multi-call binary that dispatches on its own name, so the rename fails with
"applet not found" (`k8s_manifest.py` documents this next to `DEFAULT_IMAGES`),
and 23 of the 28 CDR scenario files declare a `cgo_anchor`. Debian has a real
`bash`. Alpine stays selectable; §7 item 5 says what the beacon does there.

**Integrity model — do not fork it.** The beacon digest is computed from the
bytes this SimCore will serve, at manifest-generation time, and written into the
manifest. The pod verifies against that carried-in value, never against one it
fetches from the server it is trusting. Consequence, stated in the manifest
header: after a SimCore upgrade that changes the beacon, restarted pods exit 78
until the manifest is re-applied. That is the intended, loud behaviour.

`exit 78` (`EXIT_NEVER_RAN`) and `terminationMessagePolicy: File` are reused
from the push path so a pod that never ran looks the same in both.

---

## 5 · Posture profiles and consent

| Profile | Pod shape | Consent needed |
|---|---|---|
| `baseline` (default) | Non-privileged, no host access. ServiceAccount token mounted (as the lab's default-SA pods had), no RBAC granted | none |
| `cluster-privilege` | Baseline plus a wildcard ClusterRole (`*/*/*`) bound to the ServiceAccount | `cluster_privilege_authorized` |
| `lab-privileged` | **[Henry]** the `cdr.yml` shape: `privileged: true`, `SYS_ADMIN` + `NET_ADMIN`, host `/` mounted at `/host`, `hostPID`. **[call]** also includes the `cluster-privilege` RBAC, so one pod can run the whole corpus | both consent keys, and the deployment kill switch `CORTEXSIM_ALLOW_PRIVILEGED_K8S` |

**[call]** The host mount is **read-only**. The lab's was writable by default,
but `ClusterPosture` permanently refuses a writable `/` and no consent key
unlocks it. Escape steps go through `hostPID` + `nsenter`, which do not need a
writable mount.

**[call]** `baseline` is the default so that a bare `kubectl apply` of the URL
never creates a privileged pod. Henry's lab shape is one explicit parameter
away.

Profiles are expressed as `ClusterPosture` values and gated by
`check_cluster_consent`, so refusal codes (`CLUSTER_CONSENT_REQUIRED` 409,
`CLUSTER_PRIVILEGE_DISABLED` 403), per-object capability naming and posture
finding labels all come for free and stay consistent with the push path.

**[Henry] Gated profiles are served on the `GET`, with consent in the query
string**, so every profile is one `kubectl apply -f <url>`. Decided 2026-11-10.

This is a deliberate, scoped departure from the push path, which refuses gated
manifests on `GET` (`core/api/scenarios.py::_gated_format_requires_post`: "GET
carries no consent body, so the privileged bytes are never produced on this
path"). That rule stays exactly as it is for
`GET /api/scenarios/{id}/download?format=k8s`. It is relaxed for
`GET /api/agents/manifest` only, and the things that keep it a decision rather
than a click are:

- **Explicit keys, no default.** A gated profile with a consent key missing, or
  set to anything but `true`, returns 409 `CLUSTER_CONSENT_REQUIRED` whose body
  names each capability, the object it lands on and the literal key to add.
  Never a silent downgrade to `baseline`.
- **`authorized_by` is required** with any consent key (400 without it). It is
  written into the manifest header and an annotation on every object, and the
  generation is logged server-side with the profile, the keys and the caller's
  address. `kubectl apply` leaves no other trace on SimCore.
- **The kill switch still decides `node_access`.** With
  `CORTEXSIM_ALLOW_PRIVILEGED_K8S` at its default `false`, `lab-privileged`
  returns 403 `CLUSTER_PRIVILEGE_DISABLED` whatever the URL says. Henry's lab
  SimCore sets it once; a shared or demo SimCore cannot emit the bytes.
- **Consent keys are not secrets.** They may sit in shell history and proxy
  logs. The enrollment token still never appears in the URL.

What is given up, stated so nobody rediscovers it: on a SimCore with the kill
switch on, a pasted link is enough to obtain a privileged manifest. Applying it
still needs cluster-admin on the target cluster, and the manifest says in its
header who authorised it and what it grants.

The console's Kubernetes tab builds this URL from checkboxes and an
`authorized_by` field, and shows it only once the keys are ticked. No `POST`
variant is needed in phase 1.

**A standing pod has one posture for its life.** The push path gives each
scenario its own posture; here the agent's profile is fixed at apply time. So:

- The beacon reports its profile at enrollment and the console shows it on the
  agent row.
- "Needs more" is a **set comparison of posture findings**, not of tiers:
  `findings_for(scenario posture)` must be a subset of
  `findings_for(agent profile)`. A tier comparison would pass `SIM-CDR-001`,
  which is baseline-tier yet needs a namespaced Role for secrets, onto a
  baseline agent that has no such Role.
- A launch that fails the comparison is refused before dispatch with a code
  naming the missing findings and the profile that carries them. A `nsenter`
  step sent to a baseline pod would fail on permissions, and that failure would
  score as a detection miss.
- Scenarios with no `cluster_posture` (23 of the 28 files under
  `scenarios/cdr/`) are not refused, but two of them (`cdr-007`, `cdr-009`)
  contain `kubectl` steps. When a scenario has `kubectl` steps and the agent's
  profile grants no RBAC, the launch carries a warning code. It is a warning,
  not a refusal, because an RBAC `Forbidden` is a legitimate step result.

---

## 6 · Making an arbitrary image able to run steps

Measured on the 28 scenario files under `scenarios/cdr/` (98 steps), using
`k8s_manifest.required_binaries`:

- Tools: `python3` 22 steps, `curl` 11, `kubectl` 9, `aws` 6, `nsenter` 3,
  `wget`/`tar`/`crontab`/`openssl`/`jq` 2 each.
- Identity: 72 steps run direct (`container-runtime` 60, `root` 12); 26 need a
  service account through `runuser` (`svc-backup` 17, `www-data` 7, `nobody` 1,
  `node` 1).

A stock `alpine` has none of `python3`, `curl`, `kubectl`, `bash`, `runuser`,
and no `svc-backup` user. Three mechanisms cover this, and each one reports
rather than assumes:

1. **Prepare stage** (`prepare=packages`, default). `start.sh` detects the
   package manager (`apk`/`apt-get`), installs a fixed list, and creates the
   identity-harness accounts from `spec/identity_harness.json`. This is what the
   lab's scripts did with `apk add`. It needs the cluster to reach a distro
   mirror. `prepare=none` skips it for no-egress clusters or pre-baked images.
   **[call]** A prepare failure does not stop the beacon; it is recorded (see 3).
2. **Payload shelf** for attack tooling. Tools declared by a scenario's
   `external_tools[].adapter_ref` are staged by the existing artifact-fetch path
   at dispatch. LinPEAS, LinEnum, DEEPCE, pspy and amicontained are already on
   the shelf, which covers most of what the lab pulled from GitHub. No new code;
   the container beacon advertises `artifact-fetch` like any Linux beacon.
3. **Honest roster — new work.** Today's `interpreters` roster covers only
   python/perl/ruby/node, the preflight compares only a step's declared
   `requires_interpreters`, and no CDR scenario declares any. So nothing existing
   would catch a pod with no `curl`. Add:
   - a beacon **tool roster** (which of a fixed probe list resolve on `PATH`) and
     **account roster** (which harness accounts exist), sent at enrollment and
     refreshed on each poll cycle that follows a prepare stage;
   - a launch-time check that derives each step's tools with
     `k8s_manifest.required_binaries` and its identity from the shared spec.
     **[call]** A tool the roster says is absent **refuses** the launch with a
     code naming tool and step, overridable by an explicit flag, since
     `required_binaries` is conservative by design and can over-report;
   - at execution, a step whose identity account is absent records an explicit
     `IDENTITY NOT HONOURED` degradation in the run record, as the Windows beacon
     does, rather than failing inside `runuser` with a stderr line.

### Realism — the governing rule

**[Henry]** Everything should be as real as possible. Where a real tool, a real
binary or a real destination exists, the scenario uses it. An inert stand-in is
allowed only when the real thing cannot be delivered, and the scenario must then
say which detections the stand-in cannot reach.

**[Henry]** That includes what the lab did: a real `xmrig` mining against a
public pool, and real malware ELF samples (the lab used DeimosC2, Conti and
BPFDoor from `timb-machine/linux-malware`). Both carry over.

The **[call]**s below are how they carry over. Each is a default Henry has not
confirmed:

- **Bytes come from the payload shelf, pinned by sha256**, not from GitHub at
  dispatch. Same realism on the target, and the sample that ran is provably the
  one that was meant to. The shelf's `kind: file` covers a single ELF; `xmrig`
  ships as an archive, which the shelf rejects (`TA-08`), so the miner either
  needs a single-binary source or stays an image pull as in the lab. Builder to
  verify, not assume.
- **A separate consent key** (`live_sample_authorized`), distinct from
  `simulation_authorized`, required at launch and recorded on the run. Running
  a known ransomware binary is a different decision from running `linpeas`.
- **Destructive-class samples are refused on a pod that can reach the node.**
  A ransomware sample in a `privileged` + `hostPID` pod can encrypt the node
  through `/proc/1/root`, read-only mount or not. The lab ran its samples in
  non-root, drop-ALL, read-only-rootfs containers; this keeps that containment:
  ransomware-class samples launch only on `baseline`. Miners and implants are
  not restricted by profile.
- **No wallet or pool is baked in.** The operator supplies both at launch. The
  scenario refuses to run without them, so CortexSim never mines to an address
  nobody chose.
- **The scenario records what it cannot control.** A real implant calls out to
  whatever its build was configured for. That destination is not ours, may be
  dead, and may be live. The run record names it.
- **Staging has a side effect on the DC's own machine.** Real samples on the
  shelf sit on the SimCore host, where an XDR agent may quarantine them. A
  quarantined shelf file must surface as `PAYLOAD_NOT_STAGED`, never as a run
  that started without its sample.

These are scenario content plus shelf work, and they depend on the agent
existing. They are sequenced as phase 1b (§9), their own branch.

---

## 7 · Beacon changes

Small, and all in `agent/`:

1. **Native enrollment.** Today the installer script enrolls with `curl` and
   passes `--id`. A pod has no `curl` and should not parse JSON in shell. Add
   `--enroll-token-file <path>` (and `--state-dir`): the beacon POSTs
   `/api/agents/enroll` itself, stores the assigned id under the state dir
   (`emptyDir`), and reuses it when the container restarts. A rescheduled pod
   enrolls afresh; the old row goes offline through the existing sweep. Tokens
   for cluster use should therefore be multi-use with a TTL, and the console's
   Kubernetes tab should mint them that way.
2. **Runtime facts at enrollment.** Detect the container runtime, and send pod
   name, namespace, node name and posture profile. Advertise a `container`
   capability. `EnrollRequest` and the `Agent` row gain the fields; follow the
   nullable-JSON column pattern used for `cgo_anchor`.
3. **PID 1 behaviour.** The beacon is PID 1 in the container. It must reap
   orphaned children and exit cleanly on `SIGTERM`, killing any in-flight step
   tree, inside the pod's termination grace period.
4. **Absolute expiry.** **[call]** The manifest carries an expiry timestamp
   computed at generation (`ttl_seconds`, default and maximum 12h, the existing
   `ClusterPosture` bound), the way the push path stamps `expires`. It is
   absolute so a rescheduled pod does not get a fresh clock. The beacon stops
   accepting tasks at expiry, and the push path's reaper (`_reaper`, with its
   pinned RBAC) is reused to delete the objects, so an expired privileged pod
   does not sit idle on the node. A longer POV re-applies a fresh manifest.
   In the agent manifest the reaper runs as a `replicas: 1`, `Recreate`
   Deployment built from `_reaper`'s Job (same name, ServiceAccount, RBAC,
   image and script): a Job's pod template is immutable and the expiry changes
   it on every generation, so a re-apply was rejected (kind run, 2026-11-10).
   Because a Deployment restarts its pod, the reaper waits to the absolute
   expiry (`EXPIRES_AT_EPOCH`, `date +%s`), not a relative `TTL`. The agent
   container is Ready only once the beacon has stored its enrolled id
   (readiness probe on `/cortexsim/state/agent-id`; no liveness or startup
   probe, which would kill a slow package install).
   Henry's lab advice was "run about an hour, then tear down"; this enforces the
   upper bound.
5. **Anchor shell.** `newAnchorCmd` starts `sh` with `argv[0]` rewritten to the
   scenario's `cgo_anchor.image_name`. That breaks on a multi-call `sh`. The
   beacon must pick a shell that survives the rename (`bash` when present), and
   when only a multi-call shell exists, run the chain un-renamed and record an
   explicit `ANCHOR NOT HONOURED` degradation in the run record. It must not
   die, and it must not pretend.

Otherwise the chained executor is unchanged: steps descend from the anchor.

---

## 8 · Security posture of the thing itself

This is a pod that executes commands sent from outside the cluster, optionally
privileged with the host filesystem mounted. The design must say so plainly:

- The manifest header states the profile, what it grants, the SimCore it obeys,
  and the lifetime, the way `build_header` does for push manifests.
- Every object is labelled `cortexsim.io/agent=true` and carries the posture
  finding labels, so a customer's own KSPM flags it and can attribute it.
- Every gated manifest names who authorised it (`authorized_by`) in the header
  and in an annotation on each object.
- The enrollment token is the only credential in the cluster. It grants
  enrollment, not console access.
- SimCore-to-pod traffic is whatever the DC's SimCore serves (plain HTTP in a
  lab). The docs must say that anyone who can impersonate that SimCore to the
  pod can run commands in it, and on `lab-privileged`, on the node.
- No NetworkPolicy is emitted in phase 1: scenarios need egress to produce their
  network legs. **[call]**

---

## 9 · Phases

| Phase | Contents | Closes |
|---|---|---|
| **1 — this design** | Manifest endpoint, profiles + consent, init fetch, prepare stage, native enrollment, runtime facts, PID 1, lifetime, console tab, `kind` proof | An enrolled beacon in a pod that detonates the CDR corpus |
| 1b | Live-sample scenarios (real miner, real malware ELFs) under the realism rule in §6: shelf entries, consent key, profile restriction, scenarios and TTP cards | The lab's `cdr.yml` and `attk-k8.yml` content, dispatched and measured |
| 2 | Third-party victim images (vulhub, juice-shop): same init handoff, with `start.sh` launching the image's own entrypoint beside the beacon, so the vulnerable service is the real parent process. Per-image wrappers, each with its own proof | The `httpd → sh` row of the CrowdStrike comparison |
| 3 | Self-running mode: scenario payloads carried in the manifest, fired on a timer with no SimCore | The no-control-plane row |
| later | Prebuilt CortexSim image for clusters that cannot reach a distro mirror; DaemonSet variant; a published public manifest | — |

**[Henry]** said both "build our own detection image" and "pull an existing
image such as vulhub" are on the table, then that the manifest's startup
commands should do the assembly. Phase 1 is the second statement; phases 2 and
"later" are where the first two land.

---

## 10 · Proof

**Unit / contract (CI, no cluster)**

- Manifest object-graph tests per profile: what is and is not present, that
  `baseline` contains no privileged field, that the beacon digest in the
  manifest equals the served binary's.
- Consent and kill-switch refusals, with the codes: a gated profile with a key
  missing or not `true` (409), without `authorized_by` (400), with the kill
  switch off (403). And a guard that the push path's
  `GATED_FORMAT_REQUIRES_POST` refusal on `download?format=k8s` is unchanged.
- Launch refusal when scenario posture exceeds agent profile.
- Go: enrollment, id reuse across restart, `SIGTERM` tree-kill, lifetime expiry,
  identity-absent degradation.
- Each new guard needs evidence it fails without the fix (Gate A).

**Real pod (opt-in job, like `tier-c-e2e`)**

`kind` cluster, SimCore reachable from it. On `baseline`, with a scenario that
has no `cluster_posture` and no `kubectl` steps (builder picks one):
apply → pod Ready → agent row `online` with pod facts → dispatch
→ per-step output arrives → run completes → abort a second run mid-step →
delete namespace → agent row goes offline. Then on `cluster-privilege`,
`SIM-CDR-001` (needs the secrets Role), and its refusal on `baseline`. Then two
negatives: corrupt the digest and expect exit 78 with the reason in
`kubectl describe pod`; run on `alpine` and expect `ANCHOR NOT HONOURED`
rather than a dead chain.

This also closes several items `docs/reference/k8s-delivery.md` §10 lists as
never observed in a real pod.

**Not provable here.** `lab-privileged` on a real node, and any detection
actually firing, need a cluster with the XSIAM DaemonSet reporting to a tenant.
That run is Henry's, in his lab. Until it happens every claim in this document
is *authored, not proven*, and `tenant-verified` stays **0**.

---

## 11 · Left to the builder

- Module layout: extend `k8s_manifest.py` or add a sibling that imports its
  model. Do not duplicate the posture model or the consent check.
- The exact prepare package list per distro, and whether `kubectl` comes from a
  distro package or elsewhere. Verify availability; do not assume.
- Schema migration notes for the new `Agent` columns (CLAUDE.md lists the
  `ALTER TABLE` convention).
- Where the Kubernetes tab sits in the console's agent onboarding surface.
- Docs to update when built: `docs/reference/api-and-agent-surface.md` §1.6,
  `docs/reference/k8s-delivery.md` (it currently says there is no beacon to
  enroll for a cloud-native target, which becomes "two paths"), CLAUDE.md,
  CHANGELOG.

## 12 · Settled with Henry (2026-11-10)

1. Gated profiles are served by URL with query-string consent (§5).
2. Real miners and real malware samples carry over, and realism is the governing
   rule for all content (§6). The safeguards listed there are the author's calls
   and are Henry's to veto.

---

## 13 · As built (2026-11-10)

Operator reference: `docs/reference/k8s-container-agent.md`. This section records
only where the build differs from, or settled a **[call]** in, the text above.
**Authored is not proven:** `tenant-verified` is 0, and no Cortex detection has
been observed through this agent.

**Plan-level decisions that refine the design.**

1. **`kubectl` reaches the pod from a second init container**, not a package
   manager: `alpine/kubectl:1.34.1` copies the binary to `/cortexsim/bin`.
2. **`start.sh` is PID 1 and supervises the beacon as a child.** §7 item 3 said
   the beacon is PID 1. The supervisor is needed so a pod can idle after expiry
   (`LIFETIME_EXPIRED`) instead of crash-looping; the beacon is therefore a child
   subreaper (`--subreaper`) that reaps orphans of finished steps. `start.sh`
   idles only on a clean exit 0 with no TERM received, so a SIGTERM-driven exit 0
   is a shutdown, not an expiry.

**Settled during build and the first real-pod run.**

3. **The agent manifest's reaper is a Deployment, not a Job.** A Job's pod template
   is immutable and the expiry changes it per generation, so re-apply was rejected.
   It waits to the absolute expiry and exits 70 if the clock is unusable (see §7
   item 4).
4. **Profile downgrade neutralises immediately.** Every profile emits ClusterRole
   `cortexsim-agent`; one with no cluster rules is emitted with `rules: []` and
   `cortexsim.io/neutralised`, so re-applying a lower profile strips the wildcard
   grant at once. The ClusterRoleBinding is removed at expiry or by hand. The
   reaper always targets the union of agent cluster-scoped names.
5. **All profiles use the runtime's default capability set** (no `drop: ALL`),
   because `apt-get` and `useradd`/`runuser` need it. Measured: no change to
   findings or consent keys.
6. **The pod is Ready only once enrolled** (readiness probe on the stored id);
   there is no liveness or startup probe.
7. **Absent-account degradation applies to every POSIX beacon**, not only
   containers: one mechanism, visible as `IDENTITY NOT HONOURED`. An account is
   absent only if `user.Lookup` fails **and** `id -u -- <name>` exits 1; an
   inconclusive probe means present, and a non-portable name is not probed.
   **[Henry may veto]** to restrict it to container facts.
8. **Container agent with no `tools` key = roster unknown**, not empty: the tool
   check is skipped and `AGENT_TOOL_ROSTER_UNKNOWN` warns.
9. **The server coerces `null` `capabilities` / `interpreters` to `[]`** and the
   beacon no longer sends null (a pod with no interpreter could not enroll).
10. **The Secret's optional `artifact-token` key is read** (superseded; it was
    reserved and unread in Tasks 1-11): `start.sh` exports a non-empty
    `artifact-token` as `CORTEXSIM_ARTIFACT_TOKEN` before starting the beacon,
    through the environment only, never argv. See item 16.
11. **`SIM-CDR-015` stood in for `SIM-CDR-001`** in the real-pod posture stage,
    because the payload-shelf gate fires before the agent gate for `SIM-CDR-001`.

**Not in phase 1 / not proven:** `lab-privileged` has never been run anywhere. The
final kind run observed, on debian, processes whose cmdline carried the anchor
name while a step ran, and observed `child subreaper enabled` in the beacon log
plus no zombies after a run; orphan **reparenting** itself was not asserted. That
run predates the anchor-rule change in item 15; a re-run on PR head `2762d22`
observed the debian rename via `sh` (dash) and the alpine + `prepare=packages`
bash path. The hard-link detection in item 15 postdates that re-run and has not
been exercised in a pod. The console tab was not checked in a real browser; the opt-in CI step has not
run on GitHub.

12. **Secret ordering corrected.** §3 said either order works and that a pod
    without the Secret waits in `CreateContainerConfigError`. The Namespace comes
    from the manifest, so the Secret must follow the apply; and because the Secret
    is a volume mount (`optional: false`), the waiting symptom is `Init:0/2` /
    `ContainerCreating` with a `FailedMount` event (expected from the pod spec; not
    observed in a cluster).
13. **Removal.** Deleting the namespace alone leaves the agent and reaper
    ClusterRole/ClusterRoleBinding behind (observed in the final kind run); remove
    them first by label, then the namespace.
14. **The launch gate applies only to agents the manifest deployed** (final review
    I1). Every beacon now sends runtime facts, and one started in Docker, Podman
    or a foreign pod reports `container: true` with no `--posture-profile`. The
    gate runs only when `posture_profile` is a non-empty string; `""`, whitespace,
    `None` or a missing key is a strict no-op (no refusal, no warning, no
    `required_binaries` stamping), exactly as for `container: false`. A non-empty
    profile outside the known three is still refused. Every agent target of a
    composed run is gated on its own facts, and a refusal or warning names the
    agent (`agent_id`).
15. **The chained anchor shell is `sh` again on host beacons** (final review I2).
    Tasks 1-11 made it `bash` wherever bash was on PATH, silently changing which
    shell evaluates commands on Debian/Ubuntu hosts. Rule: the anchor is `sh` with
    argv[0] rewritten, unless `sh` resolves (`LookPath` then `EvalSymlinks`) to
    `busybox` or `toybox`; only then bash renamed if bash is on PATH, else `sh`
    un-renamed and `ANCHOR NOT HONOURED`. `runtime_facts.anchor_rename` reports the
    same rule. A hard-linked busybox `sh` is detected by comparing it with `busybox`/`toybox` on PATH (`os.SameFile`); any stat error falls back to the name rule.
16. **`artifact-token` reaches the beacon** (final review S1): see item 10.
17. **Expiry is checked before enrollment** (final review S2). A beacon whose
    `--expires-at` has passed at startup logs `LIFETIME_EXPIRED` and exits 0
    without redeeming the token or registering; `start.sh` then idles. A container
    restart before expiry still reads Ready from the stored id before it has
    re-registered (documented gap, unchanged).
18. **Reaper own-RBAC residue: one inert, unbound ClusterRole.** The re-run on
    `2762d22` observed expiry cleanup (namespace, both `cortexsim-agent` objects
    and `clusterrole/cortexsim-reaper-agent` gone within 150 s of a 60 s TTL) but
    `clusterrolebinding/cortexsim-reaper-agent` remained: the reaper deleted its
    own ClusterRole before its own ClusterRoleBinding. PR #176 reversed that order
    in the shared reaper (`k8s_manifest._reaper`, whose `own` list renders into
    `_REAPER_SCRIPT`; push manifests and the agent reaper Deployment both use it;
    no golden digest moved, because the push-bundle goldens cover
    bash/PowerShell only). Each own delete revokes the permission the other
    needs, so what PR #176 is expected to leave is one inert, unbound ClusterRole
    `cortexsim-reaper-agent`: it grants nothing and the label delete removes it.
    The order is pinned by unit tests; expiry has not been observed in a cluster
    since the fix.
