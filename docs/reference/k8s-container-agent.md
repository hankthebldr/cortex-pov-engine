# Kubernetes container agent — operator reference

> **Status:** Phase 1 built, on branch `feature/agent-k8s-container-agent` (PR #171,
> into `dev`). **Authored is not proven.** No Cortex detection has been observed
> through this agent and `tenant-verified` is **0**. See §14 for exactly what a
> real pod has and has not shown.
>
> This is the single operator-facing reference. The API table lives in
> `docs/reference/api-and-agent-surface.md` §1.6, the push-manifest contract in
> `docs/reference/k8s-delivery.md`, and the design and its decisions in
> `docs/superpowers/specs/2026-11-10-k8s-container-agent-design.md`.

## 1 · What it is, and when to use it

The Linux beacon, running **inside a pod**, enrolled with SimCore like any other
agent. Cluster scenarios are then dispatched from the console with the normal
pull lifecycle: run record, live output, abort, seeded results, reconcile, MTTD.

| | Push manifest (`k8s-delivery.md`) | Container agent (this doc) |
|---|---|---|
| Delivery | `kubectl apply` of a per-scenario manifest | `kubectl apply` of one agent manifest, once |
| Lifetime | one scenario, then the pod exits | a standing pod until its expiry |
| Posture | each scenario plants its own | one fixed profile per agent |
| Console | no run record, no abort, no live output | full pull lifecycle |
| Use when | one-off, air-gapped, or the scenario needs its own posture | repeated runs, abort, live output, MTTD on a cluster |

## 2 · The two commands

Order matters. **Apply the manifest first, then create the Secret.**

```bash
kubectl apply -f 'http://<simcore>:8888/api/agents/manifest'
```

```bash
kubectl -n cortexsim-agent create secret generic cortexsim-enroll \
  --from-literal=server=http://<simcore>:8888 \
  --from-literal=token=cxs_…
```

The manifest creates the `cortexsim-agent` Namespace, so creating the Secret
**before** the apply fails with `NotFound`. The Secret is mounted as a volume
(`optional: false`), so a pod applied before the Secret exists waits (stuck in
`Init:0/2` / `ContainerCreating` with a `FailedMount` event) and starts when the
Secret appears. That symptom is expected from the pod spec; it has **not been
observed in a cluster**. The manifest carries **no token and no Secret object**, so it is static per SimCore build and
safe to paste, diff and keep in a GitOps repo. The console's Kubernetes tab
(Targets, deploy-agent dialog) builds both commands and mints the token
(label `kubernetes`, 12 h TTL, 50 uses).

Mint a token yourself with `POST /api/agents/enroll/tokens`; for cluster use make
it multi-use with a TTL, because a pod that is rescheduled enrolls afresh.

### Removal — read this before you apply

Deleting the namespace **alone leaves cluster-scoped objects behind**:
`clusterrole` and `clusterrolebinding` `cortexsim-agent` and
`cortexsim-reaper-agent` survive (observed in the final kind run), because the
reaper that would remove them is deleted with the namespace. If the agent ran as
`cluster-privilege` or `lab-privileged`, the leftover binding grants cluster-wide
`*` to any ServiceAccount later created as `cortexsim-agent/cortexsim-agent`.

Full removal is two commands, **in this order**:

```bash
kubectl delete clusterrole,clusterrolebinding -l cortexsim.io/agent=true
kubectl delete namespace cortexsim-agent
```

**Let runs finish, or abort them, first.** After the label delete the pod is
still running but has lost its RBAC, so a step that calls `kubectl` would fail
`Forbidden` and score as a miss. The label delete was exercised once, in the
proof script's teardown. The manifest header carries the same two lines.

Letting the pod expire (`ttl_seconds`) lets the reaper run. Observed in the
re-run against 2762d22 (`ttl_seconds` 60): within 150 s it deleted the
namespace, `clusterrole`/`clusterrolebinding` `cortexsim-agent` and
`clusterrole/cortexsim-reaper-agent`, and left
`clusterrolebinding/cortexsim-reaper-agent` behind, because that build deleted
its own ClusterRole first and so lost the permission to delete its own binding.
PR #176 reversed the order in the reaper script, which push manifests share: the
binding first, the ClusterRole last. Each of the two deletes revokes the
permission the other needs, so expiry is now expected to leave **one inert
residue: the unbound ClusterRole `cortexsim-reaper-agent`**. It grants nothing, and the label
delete above removes it. That residue is read from the script and the unit tests
that pin the order; expiry has **not been observed in a cluster since the fix**.

## 3 · Query parameters

`GET /api/agents/manifest` returns `text/yaml` (`Content-Disposition:
cortexsim-agent.yaml`).

| Parameter | Default | Bounds / values |
|---|---|---|
| `profile` | `baseline` | `baseline`, `cluster-privilege`, `lab-privileged` |
| `image` | `debian:12-slim` | 1-255 chars of letters, digits and `. _ / : @ -`, starting with a letter or digit. Any public image |
| `prepare` | `packages` | `packages`, `none` |
| `arch` | `amd64` | `amd64`, `arm64` (`x86_64` and `aarch64` are accepted spellings). Selects which beacon digest is pinned; must match the node |
| `ttl_seconds` | `43200` | integer 60..43200 (12 h), absolute expiry stamped at generation |
| `cluster_privilege_authorized` | absent | only the literal string `true` counts |
| `node_access_authorized` | absent | only the literal string `true` counts |
| `authorized_by` | absent | 1-120 chars of letters, digits, space and `. _ @ + ' ( ) -` (an allowlist: it is written into a YAML comment header). A blank or whitespace-only value is treated as absent |

Validation order, with the first failure winning: `profile`, `prepare`, `arch`,
`ttl_seconds`, `image`, `authorized_by`, then the consent rules in §5.

## 4 · Profiles — exactly what each grants

The profile is fixed at apply time. A standing pod has **one posture for its
life**; the push path's per-scenario posture does not apply here.

| Profile | Pod | RBAC | Consent needed |
|---|---|---|---|
| `baseline` | Not privileged, no host access, the runtime's default capability set (no `drop: ALL`: `apt-get` and `useradd`/`runuser` need `CHOWN`, `DAC_OVERRIDE`, `SETUID`, `SETGID`). ServiceAccount token mounted | none granted | none |
| `cluster-privilege` | As `baseline` | wildcard `ClusterRole` (`*/*/*`) bound to the ServiceAccount through a `ClusterRoleBinding` | `cluster_privilege_authorized` |
| `lab-privileged` | `privileged: true`, `allowPrivilegeEscalation`, capabilities `SYS_ADMIN` + `NET_ADMIN`, `hostPID`, host `/` mounted **read-only** at `/host`. Namespace gets privileged Pod Security labels | same wildcard ClusterRole as `cluster-privilege` | both keys **and** the deployment kill switch |

Resources are identical on every profile (limits 500m / 512Mi, requests 100m /
128Mi) so findings nest by privilege only. The host mount is read-only because
the posture model permanently refuses a writable `/`; escape steps use `hostPID`
plus `nsenter`.

**`baseline` is the default so a bare `kubectl apply` of the URL never creates a
privileged pod.** Every profile emits a `ClusterRole` named `cortexsim-agent`;
on `baseline` it has `rules: []` and the annotation `cortexsim.io/neutralised`.
Strict cluster policy that objects to one empty cluster-scoped object will see it
on every profile.

## 5 · Consent, `authorized_by`, the kill switch, refusals

Gated profiles are served on the **GET**, with consent in the query string, so
every profile is one `kubectl apply -f <url>`. This is a deliberate, scoped
departure from the push path: `GET /api/scenarios/{id}/download?format=k8s` still
refuses a gated posture with `409 GATED_FORMAT_REQUIRES_POST`, unchanged. Consent
keys are not secrets and may sit in shell history; the enrollment token never
appears in the URL.

Rules:

* A consent key must be the literal `true`. `1`, `yes`, `True` do not count.
* `authorized_by` is **required whenever either consent key is present in the
  query**, even with the value `false`. It names the operator who obtained the
  customer's approval. It is written into the manifest header and into a
  `cortexsim.io/authorized-by` annotation on every object, and a served gated
  manifest is logged server-side with the profile, the keys granted, the name and
  the caller's address.
* `lab-privileged` (node access) also needs `CORTEXSIM_ALLOW_PRIVILEGED_K8S=true`
  on the SimCore deployment, default `false`. It is the owner of that SimCore's
  decision, set once; no query string overrides it.
* A refusal **never** downgrades to a weaker profile.

| Status | Code | Meaning |
|---|---|---|
| 400 | `BAD_PROFILE` | unknown profile |
| 400 | `BAD_PREPARE` | unknown prepare mode |
| 400 | `BAD_ARCH` | arch not `amd64` / `arm64` (or an alias) |
| 400 | `BAD_TTL` | not an integer in 60..43200 |
| 400 | `BAD_IMAGE` | image reference fails the allowlist |
| 400 | `BAD_AUTHORIZED_BY` | `authorized_by` fails the allowlist |
| 400 | `AUTHORIZED_BY_REQUIRED` | a consent key is present with no `authorized_by` |
| 403 | `CLUSTER_PRIVILEGE_DISABLED` | node access requested, kill switch off. `remedy` says to set the env flag and restart; no consent key changes it |
| 409 | `CLUSTER_CONSENT_REQUIRED` | a needed key is missing or not `true`. The body names each capability, the object it lands on and the literal key to add |
| 404 | `AGENT_BINARY_UNAVAILABLE` | no beacon for `linux/<arch>` on this SimCore's shelf. `make agent-dist` or rebuild the image |

All errors are the repo's structured `{error, code, detail}` shape. A 403 is
checked before a 409: the owner of the SimCore must act, not the caller.

## 6 · The Secret

`cortexsim-enroll` in namespace `cortexsim-agent`, mounted read-only at `/secret`.

| Key | Required | Meaning |
|---|---|---|
| `server` | yes | SimCore as the **pod** reaches it. Trailing slash and whitespace are trimmed. Must start `http://` or `https://` |
| `token` | yes | enrollment token (`cxs_…`) |
| `artifact-token` | no | payload-shelf bearer token for `CORTEXSIM_K8S_PAYLOAD_AUTH=token`. When the key is present and non-empty, `start.sh` exports it as `CORTEXSIM_ARTIFACT_TOKEN` before starting the beacon, which reads that variable. It goes through the environment only: never on the command line, never echoed. Absent or empty, nothing is set (unit-tested under real `sh`; not yet observed in a pod) |

## 7 · What the pod contains

```
Namespace cortexsim-agent
├─ ServiceAccount cortexsim-agent        (token mounted)
├─ ClusterRole    cortexsim-agent        (rules empty unless cluster-privilege / lab-privileged)
├─ ClusterRoleBinding cortexsim-agent    (only when the role has rules)
├─ ConfigMap      cortexsim-agent-boot   (fetch.sh, start.sh)
├─ Deployment     cortexsim-agent        (1 replica)
│    ├─ init  fetch    alpine:3.20       downloads the beacon, verifies its digest
│    ├─ init  kubectl  alpine/kubectl    copies kubectl to /cortexsim/bin
│    └─ main  agent    <image>           /bin/sh /cortexsim-boot/start.sh
└─ Deployment     cortexsim-reaper-agent (+ its ServiceAccount, Role/Binding, ClusterRole/Binding)
```

Every object carries `cortexsim.io/agent: "true"`, `cortexsim.io/expires-at` and
`cortexsim.io/agent-profile`.

* **`fetch` init.** Downloads `GET <server>/api/agents/binary?os=linux&arch=<arch>`
  and checks it against a SHA-256 **baked into the manifest by SimCore**, never
  one fetched from the server being trusted. Any failure exits **78** with a named
  cause on the termination log.
* **`kubectl` init.** Copies `kubectl` out of `alpine/kubectl:1.34.1` so an
  arbitrary image with no egress still has it. Failure: `KUBECTL_COPY_FAILED`.
* **`start.sh` is PID 1; the beacon is its child.** `start.sh` refuses to run on
  the wrong architecture (`ARCH_MISMATCH`), runs the prepare stage, then starts
  the beacon with `--subreaper` and supervises it. This is a change from the
  design, which had the beacon as PID 1: the supervisor is what allows the pod to
  idle after expiry instead of crash-looping. The beacon is therefore a *child*
  subreaper that reaps orphans left by finished steps.
* **Ready means enrolled.** The readiness probe passes only once the beacon has
  stored its assigned id at `/cortexsim/state/agent-id`. There is no liveness or
  startup probe: either would kill a slow package install. A pod that is not
  Ready has not enrolled yet. Observed in the final kind run: rollout Ready
  coincided with the beacon storing its id. **Known gap:** the id file lives in
  the pod's `emptyDir`, so after a *container* restart the pod still reads Ready
  before the restarted beacon has re-registered. And a pod rescheduled after expiry
  would enroll afresh, because enrollment happens before the expiry check.

## 8 · Prepare stage

`prepare=packages` (default) runs before the beacon and writes its outcome to
`/cortexsim/prepare.status`, which the beacon reports as `prepare_status`:

| Status | Meaning |
|---|---|
| `PREPARE_OK` | packages installed, scenario service accounts present |
| `PREPARE_FAILED <reason>` | one or more steps failed (`apt-get`/`apk` install, no supported package manager, could not create account `<name>`). The pod still runs; the launch gate sees the missing tools |
| `PREPARE_SKIPPED` | `prepare=none` |

It installs `bash curl wget python3 jq openssl tar ca-certificates util-linux`
(plus `cron` on Debian; `runuser shadow` on Alpine) and creates each service
account the identity harness names. Each package run is bounded to 120 s when
`timeout` exists. `apt-get` and `apk` are the only supported managers. **With
`prepare=none` the image must already carry what the scenarios call**; an image
whose `sh` is busybox/toybox and that has no `bash` also loses the renamed anchor
(§11).

## 9 · Expiry, the reaper, re-apply, downgrade

**Absolute expiry.** `ttl_seconds` is turned into an absolute timestamp at
generation and stamped on every object and into the beacon's `--expires-at`, so a
rescheduled pod does not get a fresh clock. At expiry the beacon takes no more
tasks and exits 0; `start.sh` logs `LIFETIME_EXPIRED` and idles (a clean exit 0
caused by a SIGTERM is a shutdown, not an expiry). A task already delivered by an
in-flight long-poll can still start up to about 30 s late: do not quote expiry as
exact. **Expiry is checked before enrollment:** a beacon that starts after its
`--expires-at` (a pod rescheduled or restarted late) logs `LIFETIME_EXPIRED` and
exits 0 without redeeming the token or registering, and `start.sh` idles. A
container restart before expiry still reads Ready from the stored id before it has
re-registered with SimCore (a known gap; Ready means "has an id", not "SimCore has
heard from it since the restart").

**The reaper** is a `replicas: 1`, `Recreate` **Deployment** named
`cortexsim-reaper-agent`, built from the push path's reaper Job (same pinned RBAC,
image and script). It waits to the **absolute** expiry (`EXPIRES_AT_EPOCH` against
`date +%s`), then deletes namespace `cortexsim-agent` and the cluster-scoped
`ClusterRole` and `ClusterRoleBinding` named `cortexsim-agent` — the whole set
any profile can emit, whatever the current one. Expiry-triggered cleanup was
observed in the re-run against 2762d22, which left the reaper's own
`ClusterRoleBinding` `cortexsim-reaper-agent` behind. Since PR #176 the reaper
deletes that binding first, so the one residue to expect is its own unbound
`ClusterRole` `cortexsim-reaper-agent`, inert and removed by the label delete;
not observed in a cluster since the fix (see "Removal" in §2).
If the node clock gives no epoch
it exits 70 (`REAPER_CLOCK_UNAVAILABLE`) and crash-loops visibly rather than
restarting its clock. It is a safety net; remove the agent yourself when done.
**The reaper dies with the namespace**, so deleting the namespace by hand leaves
the cluster-scoped objects behind; see "Removal" in §2 for the two-command
removal, in order. Observed in the final kind run: the reaper is a Deployment
and no Job exists.

**Why a Deployment and not a Job.** A Job's pod template is immutable and the
expiry changes it on every generation, so re-applying the agent manifest was
rejected (found in the first kind run). A Deployment's template is mutable.
Observed in the final kind run: re-applying with `profile=cluster-privilege`
over a running agent succeeded.

**Re-apply** is the supported way to upgrade after a SimCore change, to change
profile, or to extend a POV (a fresh manifest, a fresh expiry). After a SimCore
upgrade that changes the beacon, restarted pods exit 78 `BEACON_DIGEST_MISMATCH`
until the manifest is re-applied.

**Changing profile by re-applying is supported.**

* *Upgrade* (`baseline` to `cluster-privilege`): the new ClusterRole rules and the
  binding appear, the pod rolls.
* *Downgrade* (`cluster-privilege` to `baseline`): `kubectl apply` does not prune,
  so the manifest overwrites the wildcard ClusterRole **in place with an empty
  rule list** and the role is annotated `cortexsim.io/neutralised`. The grant is
  stripped **immediately**. The `ClusterRoleBinding` is **not** removed by the
  apply; it stays, pointing at a role that now grants nothing, and goes away at
  expiry (the reaper) or by hand: `kubectl delete clusterrolebinding
  cortexsim-agent`. Observed in the final kind run: after the `baseline`
  re-apply the ClusterRole's rules were empty, the pod's ServiceAccount answered
  `no` to `kubectl auth can-i list secrets --all-namespaces`, and the agent
  re-enrolled as `baseline`. Neither object disappears with a namespace delete
  (§2, Removal).

## 10 · The launch gate

A pod has one fixed posture and a fixed tool roster. A scenario needing more is
refused **before dispatch** by `core/engine/agent_launch_gate.py`; otherwise a
step that failed for lack of privilege would be scored as a detection Cortex
missed, a false negative we manufactured. Host and VM beacons are untouched.

**It applies only to an agent this manifest deployed**, recognised by a non-empty
`posture_profile` (the manifest's `start.sh` always passes `--posture-profile`).
A beacon started in Docker, Podman or a pod not from this manifest reports
`container: true` but no profile; for it (`""`, whitespace, `null` or no key) the
gate is a strict no-op: no refusal, no warning, no `required_binaries` stamping,
exactly as for a host beacon. A non-empty profile the engine does not know is
still refused. In a composed run every agent target is gated on its own facts and
only for the steps routed to it; a refusal's `detail.agent_id` and each warning's
`agent_id` name the agent.

| Code | Kind | Status | Meaning |
|---|---|---|---|
| `AGENT_POSTURE_INSUFFICIENT` | refusal | 409 | the scenario's cluster posture (findings **and** RBAC rules, namespaced and cluster-scoped) exceeds the agent's profile. `detail` has `missing_findings`, `missing_rbac_rules`, `agent_profile`, `satisfying_profile`. An agent reporting a profile this engine does not know is refused too |
| `AGENT_TOOL_ABSENT` | refusal | 409 | a step calls a tool the pod's roster lacks. `detail.missing` lists step and tools; `detail.override` is `allow_missing_tools`. Never overrides a posture refusal |
| `AGENT_NO_RBAC_FOR_KUBECTL` | warning | 200 | steps call `kubectl` on a `baseline` agent. A `Forbidden` is a legitimate step result, so this warns rather than refuses. Returned under `warnings` |
| `AGENT_TOOL_ROSTER_UNKNOWN` | warning | 200 | the agent reported no `tools` key. Unknown is not empty: the tool check is skipped and this warns |

`allow_missing_tools: true` on `POST /api/runs` runs anyway; the beacon still
reports that step `TOOL_ABSENT`. **In the console** (Launch), warnings from a
launch the gate let through are listed with their code and steps before the
console moves to the run ("View run"); an `AGENT_TOOL_ABSENT` refusal lists the
missing tools per step and offers **Launch anyway (steps needing a missing tool
will stop with TOOL_ABSENT)**, which re-sends the same launch with
`allow_missing_tools: true`; an `AGENT_POSTURE_INSUFFICIENT` refusal shows the
missing findings and RBAC rules and the `satisfying_profile`, with no override
(jsdom-tested; not checked in a real browser). Comparison is on posture, not tier:
`SIM-CDR-001` is baseline-tier yet needs a namespaced secrets Role, so a baseline
agent is refused.

## 11 · Honest degradations

Both appear in the run record. Neither is silent and neither is a pass.

* **`IDENTITY NOT HONOURED`.** A step declares a service account (`www-data`,
  `postgres`, …) and the beacon cannot run as it, so the step runs as the beacon's
  own account and the output says so (`!! IDENTITY NOT HONOURED:`; the completion
  summary adds `IDENTITY DEGRADED: N step(s)`). Actor-keyed detections are
  **unvalidated** for those steps. It appears when the account is absent, which is
  decided only when `user.Lookup` fails **and** `id -u -- <name>` exits 1
  (an inconclusive probe means present, so NSS-only accounts are not falsely
  degraded). **This now applies on every POSIX beacon, not only containers**
  (§15). A name that is not a portable account name (uppercase, `user@domain`,
  `DOMAIN\user`) is not probed and is read as present; a name that is empty,
  starts with `-` or `#`, or holds a control character is unusable and degrades.
  On Windows every non-direct identity degrades, as before.
* **`ANCHOR NOT HONOURED`.** A scenario's `cgo_anchor` renames the chain's root
  process by rewriting the anchor shell's `argv[0]`. The rule, on every POSIX
  beacon: the anchor is **`sh` renamed** (dash on Debian/Ubuntu: what host
  beacons have always run), **unless `sh` resolves** (`LookPath`, then symlinks)
  **to `busybox` or `toybox`**, where `argv[0]` selects the applet. Only then is
  it `bash` renamed if `bash` is on `PATH`, else `sh` un-renamed: the first
  step's output says so and the completion summary carries `ANCHOR NOT
  HONOURED`. Detections keyed on the anchor process name will not match. It
  appears on images whose `sh` is busybox and that have no bash, such as `alpine`
  with `prepare=none`. A hard-linked multi-call `sh` (no symlink to follow) is detected by comparing it with a `busybox`/`toybox` found on `PATH`: the same file means multi-call. In the final
  kind run processes named after the anchor (`apache2`) were observed mid-step on
  `debian:12-slim`, and on `alpine:3.20` with `prepare=none` `ANCHOR NOT
  HONOURED` was observed in both the step output and the completion summary.
  **That run predates the rule above** (the anchor was then `bash` wherever bash
  existed); the debian rename via `sh` (dash) and the alpine + `prepare=packages`
  bash path were observed in the re-run below.

### Re-run — 2026-11-10, kind on arm64, against 2762d22: RESULT PASS, zero defect lines

Observed in a real pod (`debian:12-slim` unless noted); this run's binary
predates commit `aa390d0`:

* debian: processes named `apache2` were present mid-step (the rename; their
  absence is the one `DEFECT` signature, and none was recorded), and their
  `/proc/<pid>/exe` read `/usr/bin/dash` (observed, not asserted);
* `alpine:3.20` with `prepare=packages`: a renamed `apache2` process was present
  mid-step and the run completed without `ANCHOR NOT HONOURED` (both asserted);
  its `/proc/<pid>/exe` read `/bin/bash` (observed, not asserted);
* `alpine:3.20` with `prepare=none`: `ANCHOR NOT HONOURED` (asserted);
* the manifest's `# REMOVAL:` header is present and does not change the `kubectl
  apply` output;
* **expiry** (`ttl_seconds` 60): within 150 s the reaper deleted namespace
  `cortexsim-agent`, and `clusterrole` and `clusterrolebinding`
  `cortexsim-agent` and `clusterrole/cortexsim-reaper-agent` were gone. Before
  expiry all four cluster-scoped objects existed.

**Defect found by this run, since changed by PR #176.** `clusterrolebinding/
cortexsim-reaper-agent` was left behind after expiry: the reaper deleted its own
ClusterRole before its own ClusterRoleBinding. The leftover bound to a deleted
role and granted nothing; `kubectl delete clusterrole,clusterrolebinding -l
cortexsim.io/agent=true` removes it. The cause was in the reaper script, which
the push manifests also use. PR #176 (`c0a6267`) deletes the binding first and
the ClusterRole last, so what it is expected to leave is one inert, unbound ClusterRole
`cortexsim-reaper-agent`, removed by the same label delete. That order is pinned
by unit tests, not observed in a cluster.

**Not covered by this run:** the hard-linked busybox `sh` detection (commit
`aa390d0`) postdates it. No pod with a hard-linked busybox `sh` has exercised it.
PR #176's binding-first reaper order also postdates it; no expiry has been
observed since.

## 12 · The beacon

Flags added for containers (all optional; the VM installer is unchanged):

| Flag | Meaning |
|---|---|
| `--enroll-token-file <path>` | redeem the token in this file; SimCore assigns the id. Mutually exclusive with `--id`. The file is read only when an enrollment is needed, so a restart that reuses a stored id works after the token is rotated or removed. The token is never logged; only its last 6 characters are |
| `--state-dir <dir>` | where the assigned id is kept across container restarts (`agent-id`). Checked **before** the token is redeemed, so an unwritable dir cannot burn a use per restart |
| `--posture-profile <name>` | reported as `runtime_facts.posture_profile` |
| `--prepare-status-file <path>` | first line reported as `runtime_facts.prepare_status` |
| `--expires-at <RFC3339>` | stop taking tasks and exit 0 after this time; checked before enrollment, so a beacon started after it exits 0 (`LIFETIME_EXPIRED`) without redeeming the token or registering |
| `--subreaper` | become a child subreaper (Linux) and reap orphans of finished steps. On darwin / windows it logs `SUBREAPER_UNAVAILABLE` |

`start.sh` also passes `--long-poll 30 --stream-output`. Enrollment retries
`SERVER_UNREACHABLE` five times, 3 s apart. A rescheduled pod enrolls afresh; the
old row goes `offline` through the existing sweep.

**Fatal exits.** The beacon writes one line, `FATAL <CODE>: <reason>`, to stderr
and to `/dev/termination-log`, so `kubectl describe pod` shows why it is
crash-looping. Tokens are reduced to a prefix. Fatal exit code is **1**; pod-setup
failures before the beacon exit **78**.

| Code | Cause |
|---|---|
| `ENROLL_DENIED` | the server refused the token (revoked, expired, used up, wrong) |
| `ENROLL_REDIRECTED` | SimCore answered with a 3xx; redirects are not followed |
| `ENROLL_TOKEN_EMPTY` / `ENROLL_TOKEN_UNREADABLE` | `/secret/token` empty or unreadable |
| `STATE_DIR_UNWRITABLE` | `--state-dir` cannot be written |
| `SERVER_UNREACHABLE` | SimCore did not answer after five attempts |
| `BAD_EXPIRES_AT` | `--expires-at` is not RFC3339 |
| `ENROLL_FAILED`, or the server's own code | anything else |

Other flag-validation exits print to stderr and exit 1 without a termination
message.

## 13 · Removal and troubleshooting

```bash
kubectl delete clusterrole,clusterrolebinding -l cortexsim.io/agent=true
kubectl delete namespace cortexsim-agent
```

In this order, after letting runs finish or aborting them: once the label delete
runs, the still-running pod has lost its RBAC and a `kubectl` step would fail
`Forbidden` and score as a miss. Namespace deletion cannot remove cluster-scoped
objects and takes the reaper with it; the label delete also catches the reaper's
own ClusterRole/Binding. The label delete was exercised once, in the proof
script's teardown. After the delete the agent row reads `offline` within
SimCore's 5-minute window (the final kind run asserted the agent was `online`
immediately before the delete, then `offline`).

What `kubectl get pods -n cortexsim-agent` and `kubectl describe pod` show:

| You see | Cause | Do |
|---|---|---|
| `Init:0/2` / `ContainerCreating`, event `FailedMount` for `cortexsim-enroll` | the Secret does not exist yet (expected from the pod spec; **not observed in a cluster**) | create it (§2); the pod starts by itself |
| `ErrImagePull` / `ImagePullBackOff` on an init or the main container | the registry is unreachable from the node or rate-limiting (`alpine:3.20`, `alpine/kubectl:1.34.1`, your `image`) | fix egress or mirror the image |
| `Init:Error`, message `BEACON_DIGEST_MISMATCH` | the served beacon differs from the digest baked into the manifest (SimCore rebuilt, or the manifest was edited) | re-apply the manifest from this SimCore |
| `Init:Error`, `BEACON_FETCH_FAILED` | the pod cannot download the beacon from `server` | check the Secret's `server`, DNS and egress from the node, and that `make agent-dist` or the image shipped `linux/<arch>` |
| `Init:Error`, `BAD_SERVER_URL` | `server` is empty or lacks `http://` / `https://` | fix the Secret |
| `Init:Error`, `KUBECTL_COPY_FAILED` | `mkdir` or `cp` failed inside the `kubectl` init container (the image pulled fine) | `kubectl logs <pod> -c kubectl`; check the init's mounts and the image's `kubectl` path |
| `Error` / `CrashLoopBackOff`, message `ARCH_MISMATCH` | `arch` does not match the node | re-apply with the right `arch` |
| `CrashLoopBackOff`, message `FATAL ENROLL_DENIED` | token revoked, expired or used up | mint a new token, update the Secret, `kubectl rollout restart` |
| `CrashLoopBackOff`, `FATAL SERVER_UNREACHABLE` | the pod cannot reach `server` | as `BEACON_FETCH_FAILED` |
| `Running`, `0/1` Ready | not enrolled yet: still in prepare, or retrying enrollment | `kubectl logs`; `prepare=packages` on a slow mirror takes minutes |
| `Running`, `1/1`, but a run is refused | the launch gate (§10) | read the code |

## 14 · What is proven and what is not

**First run, 2026-11-10** (kind on arm64, SimCore on the host) showed that the
pod schedules, pulls and goes Ready, verifies the beacon digest, enrolls
natively, prepares, completes a scenario and honours an abort. It also found
three product defects:

1. re-applying the manifest was rejected (the reaper was a Job with an immutable
   template);
2. a pod with no detectable interpreter could not enroll (the beacon sent
   `"interpreters": null`, a 422 that crash-looped with no message);
3. `alpine` could not prepare (`www-data` exists as a group, so `useradd` failed).

A review of the first fix then found a fourth, serious: after the reaper became
re-appliable, a profile **downgrade** left the wildcard ClusterRole and binding in
place. All four were fixed in code and re-run.

### Final run — 2026-11-10, kind on arm64, against commit `cd7601e`

`RESULT PASS`, exit 0, **zero `DEFECT` lines**. Observed in a real pod
(`debian:12-slim` unless noted):

* rollout Ready coincided with the beacon storing its id; the reaper is a
  Deployment, Available, and no Job exists;
* native enrollment with `container=true`; `PREPARE_OK`; `kubectl` delivered by
  the init container (v1.34.1);
* the beacon logged "child subreaper enabled", and no zombies were left after
  `SIM-CDR-010`;
* the anchor rename (then via `bash`; the anchor is `sh` since the final-review
  fix, §11; the `sh` rename was observed in the §11 re-run): processes with `cmd=apache2` were present
  mid-step (asserted);
* baseline refused `AGENT_POSTURE_INSUFFICIENT` (`SIM-CDR-015`) and
  `AGENT_TOOL_ABSENT`; `SIM-CDR-010` completed 4 steps;
* abort mid-step, and the step's process group was gone from the pod (asserted);
* re-apply to `cluster-privilege` worked (no Job immutability), and `SIM-CDR-015`
  was accepted and completed;
* **downgrade**: `baseline` re-applied over `cluster-privilege` left the
  ClusterRole with `rules=null`, the pod's ServiceAccount answered `no` to `can-i
  list secrets -A`, and the agent re-enrolled as `baseline`;
* a flipped digest made the new pod's `fetch` init exit 78 `BEACON_DIGEST_MISMATCH`;
* `alpine:3.20` with `prepare=packages`: `PREPARE_OK` and `id -u www-data` succeeds;
* `alpine:3.20` with `prepare=none` (no bash): the pod enrolls and `SIM-CDR-011`
  completes with `ANCHOR NOT HONOURED` in its output and its summary;
* a garbage token: `FATAL ENROLL_DENIED` visible in the termination message in
  `kubectl describe pod`;
* the agent was `online` immediately before the namespace delete and `offline`
  after it.

**Observed caveat:** deleting the namespace by hand left `clusterrole` and
`clusterrolebinding` `cortexsim-agent` and `cortexsim-reaper-agent` behind. See
"Removal" in §2.

**Still not observed, and stated plainly:**

* `lab-privileged` has **never been run anywhere**: not in kind, not on a node.
* An **amd64** node was not exercised (the runs were arm64).
* The opt-in CI step has never run on GitHub.
* `SIM-CDR-001` in a pod: its payload shelf was not staged, so the shelf gate
  fires before the agent gate. `SIM-CDR-015` stood in; the namespace-rule path is
  proven by unit and API tests only.
* **Mid-step output streaming in chained execution**: SimCore holds nothing for a
  step until it ends (`--stream-output` does not stream chained steps). This is
  pre-existing, not specific to containers.
* The missing-Secret symptom (`FailedMount`) and the Linux CI `agent` job running
  the subreaper unit test are likewise not seen.
* The console's **Kubernetes tab was not checked in a real browser**. Its tests
  run in jsdom, which cannot see layout.
* **No Cortex detection has been observed**, because that needs a cluster whose
  nodes run the Cortex agent DaemonSet reporting to a tenant.

**`tenant-verified` is 0.** Everything above shows that the pod runs and reports,
not that a detection fires.

## 15 · Cautions

* **Each Cortex agent pod watches only its own node.** Confirm the CortexSim
  agent pod landed on a node that runs the Cortex DaemonSet. The console's agent
  row shows the node name. A pod on a node without the Cortex agent produces
  signal nobody sees, which reads as a miss.
* **The pod obeys whichever SimCore the Secret names**, over whatever transport
  that SimCore serves (plain `http://` unless you front it with TLS). Anyone who
  can impersonate that SimCore to the pod can run commands in it. Pin the address,
  prefer `https://`, and treat the Secret and the network path as part of the
  trust boundary. The digest check protects the **binary**; it does not
  authenticate the task stream.
* **A DNS name for SimCore in `server` may need allow-listing** once the
  host-guard change (not on this branch) lands: a DNS `Host` header that is not in
  `CORTEXSIM_ALLOWED_HOSTS` is then refused 400 and the pod sits in `Init:Error`.
  IP literals pass.
* An agent with `cluster-privilege` or `lab-privileged` is deliberately
  over-permissioned. Delete it when the POV ends; the reaper is a net, not a plan.
* Behaviour changes that reach beyond containers are listed in `CHANGELOG.md`
  under `[Unreleased]`: absent-account degradation on every POSIX beacon, every
  beacon sending `runtime_facts`, null `capabilities` / `interpreters` accepted,
  and the `agents.runtime_facts` column.
