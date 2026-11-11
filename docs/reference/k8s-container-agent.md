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

```bash
kubectl apply -f 'http://<simcore>:8888/api/agents/manifest'
```

```bash
kubectl -n cortexsim-agent create secret generic cortexsim-enroll \
  --from-literal=server=http://<simcore>:8888 \
  --from-literal=token=cxs_…
```

Either order works. A pod applied before the Secret exists waits in
`CreateContainerConfigError` and starts when the Secret appears. The manifest
carries **no token and no Secret object**, so it is static per SimCore build and
safe to paste, diff and keep in a GitOps repo. The console's Kubernetes tab
(Targets, deploy-agent dialog) builds both commands and mints the token
(label `kubernetes`, 12 h TTL, 50 uses).

Mint a token yourself with `POST /api/agents/enroll/tokens`; for cluster use make
it multi-use with a TTL, because a pod that is rescheduled enrolls afresh.

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
| `authorized_by` | absent | 1-120 chars of letters, digits, space and `. _ @ + ' ( ) -` (an allowlist: it is written into a YAML comment header) |

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
| `artifact-token` | no | payload-shelf bearer token for `CORTEXSIM_K8S_PAYLOAD_AUTH=token`. **Reserved: the phase-1 pod does not read it.** `start.sh` passes no `--artifact-token` and sets no `CORTEXSIM_ARTIFACT_TOKEN`, so a SimCore in shelf token mode cannot serve shelf payloads to this agent yet |

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
  Ready has not enrolled yet.

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
with no `bash` also loses the renamed anchor (§11).

## 9 · Expiry, the reaper, re-apply, downgrade

**Absolute expiry.** `ttl_seconds` is turned into an absolute timestamp at
generation and stamped on every object and into the beacon's `--expires-at`, so a
rescheduled pod does not get a fresh clock. At expiry the beacon takes no more
tasks and exits 0; `start.sh` logs `LIFETIME_EXPIRED` and idles (a clean exit 0
caused by a SIGTERM is a shutdown, not an expiry). A task already delivered by an
in-flight long-poll can still start up to about 30 s late: do not quote expiry as
exact. A pod rescheduled after expiry still enrolls a fresh agent row.

**The reaper** is a `replicas: 1`, `Recreate` **Deployment** named
`cortexsim-reaper-agent`, built from the push path's reaper Job (same pinned RBAC,
image and script). It waits to the **absolute** expiry (`EXPIRES_AT_EPOCH` against
`date +%s`), then deletes namespace `cortexsim-agent` and the cluster-scoped
`ClusterRole` and `ClusterRoleBinding` named `cortexsim-agent` — the whole set
any profile can emit, whatever the current one. If the node clock gives no epoch
it exits 70 (`REAPER_CLOCK_UNAVAILABLE`) and crash-loops visibly rather than
restarting its clock. It is a safety net; delete the manifest yourself when done.

**Why a Deployment and not a Job.** A Job's pod template is immutable and the
expiry changes it on every generation, so re-applying the agent manifest was
rejected (found in the first kind run). A Deployment's template is mutable.

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
  cortexsim-agent`.

## 10 · The launch gate

A pod has one fixed posture and a fixed tool roster. A scenario needing more is
refused **before dispatch** by `core/engine/agent_launch_gate.py`; otherwise a
step that failed for lack of privilege would be scored as a detection Cortex
missed, a false negative we manufactured. Host and VM beacons are untouched.

| Code | Kind | Status | Meaning |
|---|---|---|---|
| `AGENT_POSTURE_INSUFFICIENT` | refusal | 409 | the scenario's cluster posture (findings **and** RBAC rules, namespaced and cluster-scoped) exceeds the agent's profile. `detail` has `missing_findings`, `missing_rbac_rules`, `agent_profile`, `satisfying_profile`. An agent reporting a profile this engine does not know is refused too |
| `AGENT_TOOL_ABSENT` | refusal | 409 | a step calls a tool the pod's roster lacks. `detail.missing` lists step and tools; `detail.override` is `allow_missing_tools`. Never overrides a posture refusal |
| `AGENT_NO_RBAC_FOR_KUBECTL` | warning | 200 | steps call `kubectl` on a `baseline` agent. A `Forbidden` is a legitimate step result, so this warns rather than refuses. Returned under `warnings` |
| `AGENT_TOOL_ROSTER_UNKNOWN` | warning | 200 | the agent reported no `tools` key. Unknown is not empty: the tool check is skipped and this warns |

`allow_missing_tools: true` on `POST /api/runs` runs anyway; the beacon still
reports that step `TOOL_ABSENT`. A missing `posture_profile` is read as
`baseline` unless the scenario needs more. Comparison is on posture, not tier:
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
  process. That needs a shell that survives `argv[0]` rewriting, which is `bash`.
  With no `bash` on `PATH` (a multi-call `sh`, e.g. busybox) the chain runs under
  `sh`, the first step's output says so and the completion summary carries
  `ANCHOR NOT HONOURED`. Detections keyed on the anchor process name will not
  match. It appears on images without bash, such as `alpine` with
  `prepare=none`; with `prepare=packages` bash is installed and the anchor is
  honoured.

## 12 · The beacon

Flags added for containers (all optional; the VM installer is unchanged):

| Flag | Meaning |
|---|---|
| `--enroll-token-file <path>` | redeem the token in this file; SimCore assigns the id. Mutually exclusive with `--id`. The file is read only when an enrollment is needed, so a restart that reuses a stored id works after the token is rotated or removed. The token is never logged; only its last 6 characters are |
| `--state-dir <dir>` | where the assigned id is kept across container restarts (`agent-id`). Checked **before** the token is redeemed, so an unwritable dir cannot burn a use per restart |
| `--posture-profile <name>` | reported as `runtime_facts.posture_profile` |
| `--prepare-status-file <path>` | first line reported as `runtime_facts.prepare_status` |
| `--expires-at <RFC3339>` | stop taking tasks and exit 0 after this time |
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
kubectl delete namespace cortexsim-agent
kubectl delete clusterrole,clusterrolebinding -l cortexsim.io/agent=true
```

Deleting the namespace takes the pod and the reaper with it; the second command
removes the cluster-scoped objects, which namespace deletion cannot. The agent
row then reads `offline` after SimCore's 5-minute window.

What `kubectl get pods -n cortexsim-agent` and `kubectl describe pod` show:

| You see | Cause | Do |
|---|---|---|
| `CreateContainerConfigError` | the `cortexsim-enroll` Secret does not exist yet | create it (§2); the pod starts by itself |
| `Init:Error`, message `BEACON_DIGEST_MISMATCH` | the served beacon differs from the digest baked into the manifest (SimCore rebuilt, or the manifest was edited) | re-apply the manifest from this SimCore |
| `Init:Error`, `BEACON_FETCH_FAILED` | the pod cannot download the beacon from `server` | check the Secret's `server`, DNS and egress from the node, and that `make agent-dist` or the image shipped `linux/<arch>` |
| `Init:Error`, `BAD_SERVER_URL` | `server` is empty or lacks `http://` / `https://` | fix the Secret |
| `Init:Error`, `KUBECTL_COPY_FAILED` | the `kubectl` init could not copy the binary | check the pull of `alpine/kubectl:1.34.1` |
| `Error` / `CrashLoopBackOff`, message `ARCH_MISMATCH` | `arch` does not match the node | re-apply with the right `arch` |
| `CrashLoopBackOff`, message `FATAL ENROLL_DENIED` | token revoked, expired or used up | mint a new token, update the Secret, `kubectl rollout restart` |
| `CrashLoopBackOff`, `FATAL SERVER_UNREACHABLE` | the pod cannot reach `server` | as `BEACON_FETCH_FAILED` |
| `Running`, `0/1` Ready | not enrolled yet: still in prepare, or retrying enrollment | `kubectl logs`; `prepare=packages` on a slow mirror takes minutes |
| `Running`, `1/1`, but a run is refused | the launch gate (§10) | read the code |

## 14 · What is proven and what is not

**Observed in a real pod, first run, 2026-11-10** (kind on arm64, SimCore on the
host): the pod schedules, pulls and goes Ready; the `fetch` init downloads and
verifies the digest; the `kubectl` init copies a working `kubectl` (v1.34.1); the
beacon enrolls natively and the agent row carries `runtime_facts.container ==
true`; the prepare stage reaches `PREPARE_OK` on `debian:12-slim` with default
capabilities; `id -u` of an unknown user exits 1 on both Debian and busybox;
`start.sh` is PID 1 with the beacon as its child; the baseline agent is refused
`AGENT_POSTURE_INSUFFICIENT` for `SIM-CDR-015` and `AGENT_TOOL_ABSENT` where a
tool is missing; `SIM-CDR-010` ran four steps to completion with output; a run
was aborted mid-step; `cluster-privilege` was accepted with consent; a flipped
digest made the `fetch` init exit 78 `BEACON_DIGEST_MISMATCH`; deleting the
namespace took the agent offline.

That run also found three product defects, **all fixed in code after it**:

1. re-applying the manifest was rejected (the reaper was a Job with an immutable
   template);
2. a pod with no detectable interpreter could not enroll (the beacon sent
   `"interpreters": null`, a 422 that crash-looped with no message);
3. `alpine` could not prepare (`www-data` exists as a group, so `useradd` failed).

A review of the first fix then found a fourth, serious: after the reaper became
re-appliable, a profile **downgrade** left the wildcard ClusterRole and binding in
place. It is fixed by the neutralised-ClusterRole behaviour in §9.

**Fixed in code, cluster confirmation pending.** The three defects above, the
downgrade neutralisation, the readiness probe, and the reaper's absolute wait and
exit 70 are covered by unit and API tests but have not yet been seen in a pod. A
re-run is in progress at the time of writing.

<!-- E2E-RERUN-RESULT: to be filled by the controller -->

**Not observed, and stated plainly:**

* `lab-privileged` has **never been run anywhere**: not in kind, not on a node.
* The Linux **subreaper effect** (`PR_SET_CHILD_SUBREAPER` reparenting and the
  reaper collecting an orphan) is not yet asserted in a pod; the macOS dev host
  cannot execute it. The Linux unit test that exercises it must run, not skip,
  in the Linux CI `agent` job.
* The **anchor rename under bash** (`/proc/<pid>/cmdline`) is not yet asserted in
  a pod, and `ANCHOR NOT HONOURED` was not reached in the first run.
* `SIM-CDR-001`'s baseline refusal was not seen in a pod: the payload-shelf gate
  fires before the agent gate. `SIM-CDR-015` stood in; the namespace-rule path is
  proven by unit and API tests only.
* The console's **Kubernetes tab was not checked in a real browser**. Its tests
  run in jsdom, which cannot see layout.
* The opt-in CI step has never run on GitHub.
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
