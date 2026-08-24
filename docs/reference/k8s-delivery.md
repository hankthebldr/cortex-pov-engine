# K8s delivery — the contract

> **Status:** shipped. `core/engine/k8s_manifest.py` (model, vocabulary, builder)
> + `core/engine/push_generator.py::generate_k8s` (the seam).
> **This file is the contract three other tracks author against.** If you need
> something here to change, change it here first.

For a cloud-native target **the deployment is the agent**. There is no beacon to
enroll and no binary to drop on a host: `kubectl apply` is the delivery, the
kubelet pulls the image, the entrypoint runs the payload.

That makes an intentionally over-permissioned workload **three artifacts at
once**:

1. a **posture finding** — KSPM/CSPM should flag the wildcard RBAC and the
   privileged securityContext;
2. the **execution vehicle** the runtime TTP needs to be non-vacuous;
3. a real **causality anchor** — one service-named process owning one payload
   shell owning every step.

---

## 0 · What changed, and what deliberately did not

| | Before | Now |
|---|---|---|
| Objects | Namespace + **one Job per step** | Namespace, ServiceAccount, optional RBAC, ConfigMaps, **one** workload, optional reaper |
| Image | `ubuntu:22.04` — **no curl, no wget, no python3, no kubectl** | `debian:12-slim` (glibc) or `alpine:3.20` (musl), overridable |
| Missing tool | shell error, step scrolls past, run looks green | **exit 78**, `MISSING_BIN=curl step=step-01` on `/dev/termination-log` |
| Step causality | N unrelated process trees rooted at N container shims | one CGO-named anchor → one payload shell → N subshell children |
| Command embedding | inlined `bash -c '<cmd>'` with naive `'`-escaping | a payload **file**, sha256-verified before it may run |
| Teardown | none — `{manifest_path}` was substituted **nowhere** | 4 independent guarantees (§6) |
| `ttlSecondsAfterFinished` | `300` — deleted the evidence, and the finding, before any scan cycle | **absent**; `activeDeadlineSeconds` on the Job path instead |
| Label prefix | `cortexsim/` | `cortexsim.io/` |

**`generate_bash` and `generate_powershell` are byte-for-byte unchanged.** They
carry the no-SimCore-at-runtime invariant that 169 scenarios depend on.
`tests/engine/test_push_generator_invariant.py` compares every bundle in the
corpus against a digest file captured from the pre-change tree, and separately
asserts neither generator ever names a SimCore endpoint.

**Why the legacy path changed at all.** A scenario with no `cluster_posture`
gets the new shape, not the old one. That is strictly better on four counts —
missing tools now fail loudly instead of silently, the quoting-bug class is
gone, step→step causality exists, and teardown is real — **and it keeps the
self-containment the old shape had**, because embedded delivery is the default
(§5).

---

## 1 · `cluster_posture` — the schema

Optional and additive. All 169 scenarios load unchanged without it.

```yaml
cgo_anchor:
  image_name: ci-runner            # the GRAPH label
  primary_username: root

cluster_posture:
  workload: auto                   # auto | job | deployment | daemonset
  libc: glibc                      # glibc | musl
  image: null                      # explicit override; null -> derived from libc
  replicas: 1                      # 1..10
  namespace: null                  # null -> cortexsim-{scenario_id_lower}
  ttl_seconds: 1800                # 60..43200
  app_identity:
    name: ci-runner                # the RUNTIME fact — must equal cgo_anchor
    component: build
  service_account:
    automount_token: true
    cluster_role_rules:            # [] -> no ClusterRole/Binding is emitted
      - api_groups: ["*"]
        resources: ["*"]
        verbs: ["*"]
    namespace_role_rules: []       # Role/RoleBinding inside our namespace
  security_context:
    privileged: false
    run_as_user: 0
    allow_privilege_escalation: false
    capabilities_add: []
    capabilities_drop: ["ALL"]
    read_only_root_filesystem: false
  host_access:
    host_pid: false
    host_network: false
    host_ipc: false
    host_port: null
    host_paths:
      - name: hostroot
        host_path: /
        mount_path: /host
        type: Directory            # ""|Directory|DirectoryOrCreate|File|FileOrCreate|Socket
        read_only: true
  resources:                       # null -> plants pod-no-resource-limits
    cpu_limit: "500m"
    memory_limit: "512Mi"
    cpu_request: "100m"
    memory_request: "128Mi"
  require_bins: []                 # extra binaries beyond those scanned from commands
  payloads: []                     # staged tool artifacts; non-empty forces served delivery
```

### Loader diagnostics

| Code | Condition | Severity |
|---|---|---|
| `S-17` | `app_identity.name` and `cgo_anchor.image_name` both set and **differ** | **ERROR**, not relaxable by `CORTEXSIM_STRICT_REFS` |
| `S-18` | `cluster_posture` declared but no step lists `k8s`/`container` in `platforms` | WARNING |

`cgo_anchor` is the **graph label**; `app_identity.name` is the **runtime fact**
(the process PID 1 is actually renamed to). If they disagree the Causality View
shows a process the cluster never ran — confidently wrong in front of a
customer. So drift is made structurally impossible: **when only one is set the
other is back-filled from it**, and when both are set and differ the scenario is
rejected. S-17 is structural — about one file's internal consistency, not about
the index snapshot — so it is never relaxed.

### Normalisation you should know about

`privileged: true` forces `allow_privilege_escalation: true`. Kubernetes rejects
the combination outright (`cannot set allowPrivilegeEscalation to false and
privileged to true` — caught by `kubectl apply --dry-run=server` against a live
API server). Normalising at the **model** rather than at emission keeps the
declaration and the emission in agreement, and the
`pod-allow-privilege-escalation` finding then correctly fires.

### Persistence

`_schema_to_orm_kwargs` includes `cluster_posture` **only when
`hasattr(Scenario, "cluster_posture")`**. The ORM column is owned by another
track; add it plus `ALTER TABLE scenarios ADD COLUMN cluster_posture JSON` and
the loader starts persisting with no further change. Until then the field
validates and generates but does not round-trip through the DB.

---

## 2 · The finding vocabulary — derived, never hand-listed

`PostureFinding.emitted_when` is the **only** source of truth for whether a
posture plants a finding. The builder cannot write a slug literal; it calls
`findings_for(posture)`. `tests/engine/test_k8s_manifest.py` fails if any slug
appears more than once in `k8s_manifest.py` or at all in `push_generator.py`,
and `test_emitted_labels_match_the_vocabulary_exactly` asserts the labels on the
built object graph equal `findings_for(posture)` exactly.

**Do not copy this table.** Cite `GET /api/k8s/posture-findings`
(→ `k8s_manifest.vocabulary()`), which is the serialisable form. A hand-copied
slug that drifts produces an assertion that can never go green for a reason
invisible to everyone — exactly what happened on the IaC side.

| slug | CIS | carrier | tier |
|---|---|---|---|
| `pod-privileged` | 5.2.2 | podtemplate | node_access |
| `hostpath-container-socket` | 5.2.12 | podtemplate | node_access |
| `hostpath-root-mount` | 5.2.12 | podtemplate | node_access |
| `pod-host-pid` | 5.2.3 | podtemplate | node_access |
| `pod-host-network` | 5.2.4 | podtemplate | node_access |
| `pod-host-ipc` | 5.2.3 | podtemplate | node_access |
| `clusterrole-wildcard-verbs` | 5.1.3 | clusterrole | cluster_privilege |
| `clusterrole-wildcard-resources` | 5.1.3 | clusterrole | cluster_privilege |
| `clusterrole-secrets-read-cluster-wide` | 5.1.2 | clusterrole | cluster_privilege |
| `clusterrole-pod-exec` | 5.1.3 | clusterrole | cluster_privilege |
| `clusterrole-token-mint` | 5.1.3 | clusterrole | cluster_privilege |
| `clusterrole-impersonate` | 5.1.3 | clusterrole | cluster_privilege |
| `clusterrolebinding-to-workload-sa` | 5.1.1 | clusterrolebinding | cluster_privilege |
| `pod-added-capabilities` | 5.2.9 | podtemplate | node_access |
| `pod-host-port` | 5.2.4 | podtemplate | node_access |
| `hostpath-mount` | 5.2.12 | podtemplate | node_access |
| `pod-allow-privilege-escalation` | 5.2.5 | podtemplate | node_access |
| `namespace-pod-security-privileged` | 5.2.1 | namespace | node_access |
| `serviceaccount-token-automounted` | 5.1.5 | serviceaccount | — |
| `pod-run-as-root` | 5.2.6 | podtemplate | — |
| `pod-writable-root-filesystem` | 5.2.11 | podtemplate | — |
| `pod-no-resource-limits` | — | podtemplate | — |

**Order is severity order.** A label value holds one slug, so an object planting
several carries the highest-severity match in the
`cortexsim.io/posture-finding` label and the full set in the
`cortexsim.io/posture-findings` annotation. Keep new entries in severity
position.

Tier `—` means **baseline**: a real finding a KSPM engine should surface, but
not privilege this engine asks permission for. Running as root in a container
and automounting a SA token are Kubernetes defaults; gating on them would fire
the consent prompt on every download, and after the fourth download the flags
are ceremony.

### The assertion join key

`cortexsim.io/posture-finding=<slug>` is the in-cluster analogue of
`CortexSimCSPMFinding = "<slug>"` in `infra/modules/aws/cspm`, which
`assertions/pos/POS-CSPM-001` already joins on. A POS assertion filters the
posture dataset on our label, **never** on the product's own finding vocabulary
— a query that guesses the product's classification names returns zero rows when
the guess is wrong and reports a correct platform as FAILED.

An authored threshold must equal `len(findings_for(posture))`. Generate it; do
not type it.

---

## 3 · Labels and annotations

Prefix: **`cortexsim.io/`**, on every object including the cluster-scoped ones.

**Labels** (selector-safe):

| key | value | on |
|---|---|---|
| `app.kubernetes.io/managed-by` | `cortexsim` | every object |
| `cortexsim.io/managed-by` | `push-generator` | **every object** — the sweep guarantee |
| `cortexsim.io/scenario` | `SIM-CDR-004` | every object |
| `cortexsim.io/plane` | `cdr` | every object |
| `cortexsim.io/delivery` | `embedded` \| `served` | every object |
| `cortexsim.io/posture-finding` | one slug | the object that **is** that finding |
| `app.kubernetes.io/name` / `-component` | `app_identity.*` | workload + pod template |
| `pod-security.kubernetes.io/{enforce,warn,audit}` | `privileged` | our namespace, **only** when `requires_privileged_psa()` |

**Annotations** (long values): `posture-findings`, `posture-findings-count`,
`cis-controls`, `delivery-contract`, `simcore-url`, `simcore-url-suspect`,
`bootstrap-sha256`, `risk-tier`, `expires-at`, `ttl-seconds`, `teardown`,
`manifest-file`, `uc-ref`, `tc-ref`, `mitre-technique`, `musl-caveat`.

We set PSA labels **only on the namespace we create**. Mutating a customer
namespace's PSA labels silently weakens admission for *other people's*
workloads and outlives our teardown.

---

## 4 · Consent — two orthogonal keys

```python
TIER_CONSENT_KEY = {
    "cluster_privilege": "cluster_privilege_authorized",
    "node_access":       "node_access_authorized",
}
```

They are **orthogonal, not nested**. `SIM-CDR-004` (wildcard RBAC) touches no
node; `SIM-CDR-003` (privileged escape) needs no RBAC. Requiring both keys for
either fires the gate on cases it does not describe. They are also, in a real
POV, **different approvals from different people**: "you may create a
ServiceAccount with cluster-read in a scratch namespace" is a platform-team yes;
"you may mount the node filesystem" is a different conversation.

Baseline is genuinely ungated. A namespaced, no-RBAC, no-host-anything workload
running a payload is not more dangerous than the bash bundle it replaces, which
has always been an ungated GET.

### The deployment kill switch

`CORTEXSIM_ALLOW_PRIVILEGED_K8S` (default **false**, config track) gates
`node_access` **only**. Two principals on two timescales: whoever owns this
SimCore sets the flag once on their own jumpbox; the operator supplies consent
per generation. A shared or demo SimCore cannot emit node-breakout bytes at all,
no matter what anyone POSTs. Tier 1 ships available-with-consent by default —
refusing it out of the box makes the CDR plane useless on a fresh install.

Refusal codes are **distinct**, because the fix is a different action by a
different person:

| code | HTTP | meaning |
|---|---|---|
| `CLUSTER_CONSENT_REQUIRED` | **409** | well-formed request, content cannot satisfy it as asked |
| `CLUSTER_PRIVILEGE_DISABLED` | **403** | the caller may not ask at all on this deployment |
| `PAYLOAD_TOO_LARGE_TO_EMBED` | 409 | see §5 |
| `BUNDLE_TARGET_UNSATISFIABLE` | 409 | unchanged, reserved |

Every refusal body names the **object** each capability lands on and the
**literal consent key string** the caller must send. "This scenario is
privileged" is not actionable; `securityContext.privileged=true on the pod
template` is. That field is what decides whether the gate is a decision or a
click.

### Where the gate goes — API track, read this

```python
from engine.k8s_manifest import required_consent_keys, parse_posture, check_cluster_consent
```

* **`required_consent_keys(posture)`** — call this **before** generating, to gate
  the request. Returns `()` for baseline, so the existing
  `GET /download?format=k8s` keeps working unchanged for every scenario in the
  corpus today (none declares `cluster_posture`).
* `generate_k8s(..., consent={...}, allow_privileged_k8s=bool)` enforces it again
  as defence in depth and raises `ClusterConsentRequired`. When `consent=None`
  the caller has taken responsibility.
* **The HTTP surface is the real boundary.** The dangerous artifact is the file
  that leaves SimCore — applied by `kubectl` on a laptop days later, on a host
  SimCore never sees. A generator-level gate is bypassable by any in-process
  caller anyway.
* **The gate reads the built object graph, not the declaration.** A scenario
  author who adds `hostPID: true` without updating anything must be refused, not
  silently cleared. `emitted_finding_slugs(objects)` is the probe.

**Honest limit, and it belongs in the docs not in a footnote:** consent is a
boolean in a JSON body with no authentication behind it. SimCore has no user
model. The gate stops accidents and creates an audit trail; it does not stop a
determined caller. The real controls are `CORTEXSIM_ALLOW_PRIVILEGED_K8S`, the
namespace denylist, and the §7 refusals — all of which hold regardless of what
the caller claims.

---

## 5 · Delivery — embedded is the default, and it is self-contained

```
delivery = "served" if posture.payloads else "embedded"
```

**`embedded` (default)** — the payload travels inline in a ConfigMap. The pod
needs **nothing** from SimCore at runtime. This holds the same invariant the
bash and PowerShell bundles hold. It does pull a container image from a
registry.

**`served` (opt-in)** — the pod fetches its payload, and any staged tool
artifacts, from SimCore. This is the **one documented exception** to the
push-bundle self-contained rule, and it exists because staged tool binaries
(linpeas.sh at ~800 KB, kubectl) exceed what a ConfigMap can carry.

> Making embedded the default is a deliberate reconciliation. Confining the
> relaxed invariant to the case that actually needs it is better than imposing
> cluster→SimCore reachability on every k8s POV and then documenting the
> relaxation in four places.

The relaxed contract, when it applies, is stated in four places and asserted by
`test_served_states_the_relaxed_contract_in_four_places`:

1. a fenced header block — `DELIVERY CONTRACT: cluster-to-simcore — NOT
   self-contained` — with a copy-pasteable `kubectl run … wget` preflight;
2. `cortexsim.io/delivery` as a **label** on every object;
3. `cortexsim.io/delivery-contract` + `cortexsim.io/simcore-url` annotations;
4. an `X-CortexSim-Bundle-Selfcontained: false` response header (API track).

### `PAYLOAD_TOO_LARGE_TO_EMBED`

Budget is **900 KiB** against the encoded length (Kubernetes caps a ConfigMap at
~1 MiB of etcd value). Over budget → **refuse**. Never truncate, never silently
omit. A silently-omitted payload is the same false-negative shape as a silent
no-op, one layer down.

`delivery="image"` — the payload baked into an image in the customer's own
registry — is a **known future value** and produces an informative `ValueError`
naming it, so a DC who tries it gets told rather than guessing.

### The SimCore URL

Precedence is the API track's: `?server=` → `CORTEXSIM_PUBLIC_URL` → derived
from the request `Host`. If it resolves to a loopback or docker-internal
address, `generate_k8s` emits a **`!! WARNING: … LOOPBACK address`** block in the
header and sets `cortexsim.io/simcore-url-suspect: "true"`. A pod resolves
`localhost` to *itself*; this is the single most likely way the served mode
fails in the field.

### Endpoints the payload track must serve

| endpoint | returns |
|---|---|
| `GET /api/k8s/payloads` | inventory; the **reachability probe** the manifest curls. **Always unauthenticated** — a probe that can fail for two different reasons is a debugging trap. |
| `GET /api/k8s/bootstrap/{scenario_id}` | `text/x-shellscript`, exactly `k8s_manifest.generate_bootstrap(scenario)`, plus `X-CortexSim-Bootstrap-SHA256` |
| `GET /api/k8s/bootstrap/{scenario_id}/sha256` | bare hex + `\n`, `text/plain` |
| `GET /api/k8s/payload/{name}` | a staged tool artifact + `X-CortexSim-Payload-SHA256` |
| `GET /api/k8s/payload/{name}/sha256` | bare hex + `\n` |
| `GET /api/k8s/posture-findings` | `k8s_manifest.vocabulary()` |

**These paths are permanent.** They are hard-coded inside
`k8s_manifest._SERVED_FETCH`, so they live verbatim in every manifest this engine
has ever emitted — files that sit in customers' GitOps repos and change tickets.
The payload shelf added a plane-agnostic second mount at **`/api/shelf/*`**
(`payloads` · `payload/{name}` · `payload/{name}/sha256`, plus `artifacts`,
`compose`, `resolve/{id}`, `stage`) from the **same handler functions** —
`_register_shelf_routes` registers one implementation on both routers. It is not
a move and deliberately **not a redirect**: a redirect would make a stale
manifest silently keep working, so the drift would never be discovered. New
consumers (the Go beacon, the console) use `/api/shelf/*`; K8s keeps `/api/k8s/*`
forever. Contract: [`payload-shelf.md`](payload-shelf.md).

**The Rust tools are a separate mount, and no manifest fetches one — yet.**
`GET /api/tools/binary/{tool}` (+ `/sha256`, + `/api/tools/binaries`) serves the
static musl builds of `signalbench` / `ackbarx` / `xdrtop` that
`core/Dockerfile`'s `rust-builder` stage bakes into `/app/rust-dist`. It is the
same idiom as the table above — plural collection, singular artifact,
`X-CortexSim-Tool-SHA256`, `no-store`, digest computed on SimCore and verified by
the consumer before execution — so a pod could fetch one with the identical shim.
It does not, because **no scenario references those three tools** in
`external_tools[]` or `cluster_posture.payloads`; wiring a fetch nobody needs
would be a path with no test and no user. Their consumer today is SimCore's own
`tools/instantiator.py` (image path `/app/rust-dist/...`) plus a DC doing a
verifiable manual fetch. Contract: [`payload-shelf.md`](payload-shelf.md)
§"The Rust tool shelf".

**`_resolve_payloads` now delegates to `payload_shelf.compose(consumer="k8s")`**
rather than keeping its own shelf lookup, so there is exactly one integrity walk
(two implementations of an integrity check drift, and the one that drifts is the
one nobody ran that quarter). The delegation is **narrow on purpose**: only
literal `cluster_posture.payloads` names go through it, never `external_tools`
— an adapter-derived artifact would land at the pack's `stage_path` instead of
`/cortexsim/tools/` and break the curl/wget shim. A/B generation of the
`SIM-CDR-001` served manifest with and without the change differs only in
wall-clock TTL fields; digests, shim, paths and `PAYLOAD_` ordering are identical.

`build_objects` also gained `_guard_served_shelf_auth`, which refuses to generate
a `delivery=served` manifest while `CORTEXSIM_K8S_PAYLOAD_AUTH=token`, because
`_SERVED_FETCH` is a bare `wget` with no header: the manifest would apply cleanly
and then 403 in the pod, which reads in a POV report as a detection miss. ⚠ That
refusal currently surfaces as a **500 `INTERNAL_ERROR`** carrying the right text,
because `core/api/scenarios.py` does not map
`ShelfAuthUnreachableFromCluster` — a 500 reads as "CortexSim is broken" rather
than "your shelf-auth setting makes this delivery mode unusable". The 409 handler
is written out in the exception's docstring.

`generate_bootstrap` is **deterministic**: the same scenario dict always renders
the same bytes, because its sha256 is baked into the manifest at generation
time. Never add a timestamp, a uuid, or unsorted iteration to it —
`test_bootstrap_is_deterministic_and_matches_the_baked_digest` renders the same
scenario at two different `now` values and asserts the payload ConfigMap is
identical. Generation-time metadata belongs in the **manifest**.

**The digests are baked into the manifest at generation time, on the DC's
SimCore, and travel with the file.** The pod must NOT fetch the expected digest
from `/sha256` — a substituted or MITM'd server serves a matching pair and every
check passes. The manifest is the out-of-band anchor; `/sha256` exists for
humans and for `curl` debugging. This diverges from the agent installer
(`core/api/agents.py`), which legitimately does fetch its digest; do not
"unify" them.

---

## 6 · Fail loud — the four mechanisms

A pod that exits 0 having done nothing is indistinguishable in XSIAM from a pod
that did everything and was missed. That reads as *"your detection stack
failed"*, and it has bitten this repo three times.

1. **`exit 78`** (`EX_CONFIG`), deliberately distinct from any code a TTP
   produces. Emitted on: unreachable SimCore, fetch failure, sha256 mismatch,
   missing `sha256sum`, empty digest, libc mismatch, missing verified payload,
   and any `require_bin` miss. An init container exiting non-zero puts the pod
   in `Init:CrashLoopBackOff` — loudly visible in `kubectl get pods` and in the
   customer's own cluster monitoring.
2. **`/dev/termination-log`** with `terminationMessagePolicy: File` set
   explicitly on both containers, so the reason surfaces in
   `kubectl describe pod`.
3. **`require_bin <tool> <step>`** before every step, derived from the step
   command via `push_generator.code_only` (which strips heredocs, comments and
   quoted strings). Scanned **conservatively on purpose**: a false positive
   makes the pod exit 78 on a step that would have worked — a manufactured
   failure — while a false negative merely lets the step fail with a non-zero rc
   the marker line records. This alone closes the live false negative where
   `ubuntu:22.04`'s missing `curl`/`python3` made 14 CDR scenarios unable to
   execute a step in *any* manifest this engine ever emitted.
4. **`.part` → `mv` after verification.** The runner's only entry is
   `/cortexsim/bootstrap.sh`, which does not exist until the digest matched. It
   is structurally impossible for the runner to find a runnable-but-unverified
   payload — that property comes from init-container ordering, not from a flag.
   There is **no `CHECKSUM_SKIPPED` degrade path**: in a pod *we* choose the
   image, so a missing digest tool is a packaging bug, not a field condition.

`integrity.env` (flat `KEY=VALUE`, sourced with `.`) is the verification path.
`integrity.json` is for humans and the console. Shell JSON parsing with `sed`
returns `""` on a format drift and `[ "" = "" ]` then passes — that is how an
integrity check quietly becomes a no-op. Empty `BOOTSTRAP_SHA256` and empty
computed digest are both hard failures.

### Per-step ledger

`kubectl logs` shows the same ledger the beacon POSTs:

```
[cortexsim] step=step-01 status=start identity=www-data t=2026-08-03T12:00:00Z
[cortexsim] step=step-01 status=ok rc=0 t=…
[cortexsim] scenario=SIM-CDR-004 status=complete steps_ok=2/2 t=…
```

The payload uses `set -uo pipefail`, **not** `set -e`: a step that legitimately
exits non-zero (an RBAC refusal, a denied `nsenter`) is a **result**, not a
bundle failure.

---

## 7 · Teardown — four independent guarantees

A privileged workload surviving a POV is the failure this section exists to
prevent, so it gets belt, braces and two spares.

1. **Namespace containment.** Everything namespaced lives in
   `cortexsim-{scenario_id_lower}`, always emitted as document 1.
2. **`kubectl delete -f <manifest>` is complete**, because the ClusterRole and
   ClusterRoleBinding are *named in the file*. `{manifest_path}` is now actually
   substituted — before this work it was substituted **nowhere**, so nine CDR
   scenarios declaring `cleanup.k8s_teardown` shipped a recipe that was a
   documented lie.
3. **Label sweep.** `cortexsim.io/managed-by=push-generator` is on **every**
   object including the cluster-scoped ones:
   ```
   kubectl get ns,clusterrole,clusterrolebinding -l cortexsim.io/managed-by=push-generator
   ```
   `No resources found` is the clean state. This command is in every manifest
   header and belongs in the engagement close-out checklist.
4. **The reaper Job**, mandatory whenever the workload is a Deployment/DaemonSet
   (no native TTL) or any cluster-scoped object exists. Its ClusterRole is
   **`resourceNames`-pinned to exactly the objects this manifest emits** —
   `test_reaper_rbac_is_resourcenames_pinned` asserts every rule has a non-empty
   `resourceNames`, no wildcard verb or resource, and no name the manifest did
   not emit. A resource-type-scoped rule here would itself be a cluster-wide
   RBAC deletion primitive in the customer's cluster.

The Job path additionally carries `activeDeadlineSeconds = ttl_seconds` — an
API-server-enforced bound needing no image and no RBAC.

> ### The reaper's own failure mode, stated not hidden
> It must pull `bitnami/kubectl:1.34`. In an air-gapped or registry-mirrored
> cluster that pull **fails**, the reaper sits in `ImagePullBackOff`, and the
> workload persists while the annotations still claim it expires. The manifest
> header says exactly this. **The reaper is a safety net, not the plan.**

**`ownerReferences`** would make `kubectl delete ns` sufficient forever, but it
needs the namespace UID, which does not exist at generation time. The generator
therefore emits the two-line post-apply `kubectl patch` in the header rather
than a field it cannot fill. **Whether that GC actually fires is unverified —
see §10.**

---

## 8 · Refusals — no consent key unlocks these

Enforced at **load time**, in `ClusterPosture`'s validator, so a content commit
can never smuggle one in.

| refusal | argument |
|---|---|
| **System namespaces** (`default`, `kube-*`, `openshift*`, `cert-manager`, `monitoring`, `argocd`, `flux-system`, …) | `default` is where a fat-fingered `-n` lands and where customers keep workloads — deleting it is not an option, so it can never be safely reaped. |
| **Namespace not starting `cortexsim-`** | we only ever create objects we own and label. We never adopt or relabel a customer namespace. |
| **Writable hostPath on `/`, `/etc`, `/usr`, `/boot`, `/root`, `/bin`, `/sbin`, `/lib`, `/var/lib/{etcd,kubelet,rancher,docker}`** | every escape scenario in the corpus gets its telemetry from *reading* the host and from the mount event itself. A writable node root is how you brick a node you do not own. Writable is allowed under `/tmp` and `/var/tmp`. |
| **hostPort 6443 / 2379 / 2380 / 10250 / 10256 / 10257 / 10259** | binding one takes down the API server, etcd or the kubelet on that node. |
| **`ttl_seconds` outside 60..43200** | a privileged workload with no bound is the failure this block exists to prevent. |

Enforced **by construction** — the model has no field that can express them, which
is stronger than a check:

* **No admission webhooks.** A `MutatingWebhookConfiguration` with
  `failurePolicy: Fail` whose backing pod is gone wedges the API server's
  admission path **cluster-wide**. Our workload is reaped on a timer, so it would
  *reliably* become that broken webhook. Zero fidelity cost — the RBAC-authorised
  `create` attempt is what the detection keys on, not the registration.
* **No control-plane targeting.** No toleration for
  `node-role.kubernetes.io/control-plane` or `:master`; no `nodeName`. And
  because a real 3-node k3s control-plane node was verified to carry **no
  taints**, omission is not enough — node-access manifests carry a **positive
  anti-affinity** (`DoesNotExist` on both keys). A privileged pod on a
  control-plane node owns etcd, the cluster CA and every secret in the cluster;
  the escape scenario is exactly as valid on a worker.
  *(The API server emits a deprecation warning on the `:master` key. It is kept
  deliberately, for older clusters.)*
* **No binding to a ClusterRole we did not emit**, in particular
  `cluster-admin`. `roleRef` always names our own generated role. KSPM flags the
  *shape*, so fidelity is unchanged, but a binding to the real `cluster-admin`
  is not ours to delete cleanly and is indistinguishable from a genuine admin
  grant in the customer's own review.
* **No subject we do not own** — no `system:anonymous`,
  `system:unauthenticated`, `system:authenticated` or any `system:*`. Every other
  refusal protects the cluster from our workload; this one protects it from
  *everyone else*, because it changes the live authorisation surface for
  principals that keep existing after we are gone.
* **No mutation of anything pre-existing** — no `patch`/`delete` of customer
  objects, no CRDs, no finalizers, no PSA relabelling of a namespace we did not
  create.

---

## 9 · A worked example — `SIM-CDR-004`

Posture: wildcard `ClusterRole`, automounted token, no privilege, no host
access. `auto` → **Deployment** (the posture is gated, and a Job reaped in
minutes cannot be discovered by a scan cycle measured in hours).

`findings_for()` yields **10**: `clusterrole-wildcard-verbs`,
`clusterrole-wildcard-resources`, `clusterrole-secrets-read-cluster-wide`,
`clusterrole-pod-exec`, `clusterrole-token-mint`, `clusterrole-impersonate`,
`clusterrolebinding-to-workload-sa`,
`serviceaccount-token-automounted`, `pod-run-as-root`, `pod-writable-root-filesystem`.
`namespace-pod-security-privileged` is **not** emitted — no privilege, no caps,
no host access, so PSA is not relaxed.

Consent: `cluster_privilege_authorized` only. **Not** `node_access_authorized`.

Emitted (11 documents):

```
Namespace/cortexsim-sim-cdr-004        ← no PSA labels; correct
ServiceAccount/ci-runner               ← serviceaccount-token-automounted
ClusterRole/cortexsim-sim-cdr-004      ← the wildcard rule; 5 clusterrole findings
ClusterRoleBinding/cortexsim-sim-cdr-004
ConfigMap/ci-runner-integrity          ← integrity.env + integrity.json
ConfigMap/ci-runner-payload            ← the bootstrap (embedded delivery)
Deployment/ci-runner                   ← debian:12-slim, anchor process 'ci-runner'
ServiceAccount/cortexsim-reaper-sim-cdr-004
ClusterRole/cortexsim-reaper-sim-cdr-004        ← resourceNames-pinned to 4 names + the ns
ClusterRoleBinding/cortexsim-reaper-sim-cdr-004
Job/cortexsim-reaper-sim-cdr-004                ← sleep 1800, delete, delete ns
```

The causality spine the pod actually produces:

```
containerd-shim (kubelet-spawned)
  └─ /usr/local/bin/ci-runner                 ← PID 1, comm == ci-runner   ★ CGO
       └─ /cortexsim/bootstrap.sh             ← one anchor shell, all steps
            ├─ ( step-01 subshell ) → run_as www-data → curl → deepce
            └─ ( step-02 subshell ) → run_as root     → kubectl
```

`cp $(command -v bash) /usr/local/bin/ci-runner && exec …` yields
`/proc/self/comm == ci-runner` — a real, sensor-visible fact, not a label. On
`libc: musl` the rename **cannot** work (alpine's shell is a busybox multi-call
binary dispatching on `argv[0]`), so the generator emits a header NOTE and a
`cortexsim.io/musl-caveat` annotation saying the anchor will appear as
`busybox`. It never fails hard on the rename — that would trade a cosmetic
problem for a total no-op. With `read_only_root_filesystem: true` a writable
`emptyDir` is mounted at `/usr/local/bin` so the rename still works.

---

## 10 · What is proven, and what is not

**Proven** — `kubectl apply --dry-run=server` against a live 3-node **K3s
v1.34.6 / v1.35.5** cluster, which runs full admission and **creates nothing**.
Baseline, cluster-privilege and node-access manifests were all accepted:
Namespace with PSA labels, ServiceAccount, wildcard ClusterRole,
ClusterRoleBinding, both ConfigMaps, the privileged + `hostPID` + `hostPath:/`
Deployment, the reaper's pinned RBAC, and the reaper Job.

That pass caught a real bug: `privileged: true` with
`allowPrivilegeEscalation: false` is rejected outright. It is fixed at the model
(§1) and regression-guarded.

**Not proven.** No `kind`, `minikube` or `k3d` is installed on this host, and
the only reachable cluster is a **production homelab** — the exact "customer
cluster" this whole gate exists to protect. **No resources were created.** So
these are API-shape verified only:

* that the pod schedules, pulls, and that the init-container → runner
  `emptyDir` handoff works;
* that a failing init container really yields `Init:CrashLoopBackOff` and that
  `/dev/termination-log` surfaces in `kubectl describe pod` (asserted from the
  Kubernetes contract, not observed);
* that the anchor rename produces `comm == <app>` **inside a pod** (verified in
  a bare container, not in a pod);
* that the reaper's delete calls land, and whether `ownerReferences` on a
  Namespace actually garbage-collects a ClusterRole;
* that the API server rejects an oversize ConfigMap at the ~1 MiB boundary (the
  900 KiB budget is set with headroom precisely because it was not measured).

**Close it like this**, once a throwaway cluster is authorised:

```bash
kind create cluster --name cortexsim            # leave the control-plane node UNTAINTED
kubectl apply -f /tmp/m.yaml
kubectl -n cortexsim-sim-cdr-004 rollout status deploy/ci-runner --timeout=120s
kubectl -n cortexsim-sim-cdr-004 logs -l app.kubernetes.io/name=ci-runner --all-containers

# the false-negative path MUST be loud
#   (re-generate with delivery=served and an unreachable SimCore URL)
kubectl -n … get pods    # expect Init:CrashLoopBackOff
kubectl -n … get pod … -o jsonpath='{.status.initContainerStatuses[0].state.terminated.exitCode}'
#   expect 78
kubectl -n … describe pod …   # expect the termination message

# teardown, including the ownerReferences GC
kubectl delete ns cortexsim-sim-cdr-004
kubectl get clusterrole,clusterrolebinding -l cortexsim.io/managed-by=push-generator
#   WITHOUT the ownerRef patch: expect them to SURVIVE — that is why guarantee 3 exists
#   WITH the patch applied first: expect them GONE
```

**Report what that last step actually does.** If the GC does not fire, the
`kubectl patch` recipe in the header is worthless and §7 rests on guarantees
2/3/4 alone — say so here rather than leaving a recipe that only reads like it
works.

---

## 11 · Tests

| file | guards |
|---|---|
| `tests/engine/test_push_generator_invariant.py` | bash/PowerShell byte-identity across the corpus vs `_golden/push_bundle_digests.json`; no SimCore reference in either; the K8s section stays below its banner |
| `tests/engine/test_k8s_manifest.py` | vocabulary uniqueness + reachability, tier derivation, consent, the load-time refusals, the no-slug-literal drift guard |
| `tests/engine/test_k8s_manifest_schema.py` | the whole corpus still loads; the block is optional; S-17 and the two-way back-fill; forbidden postures are rejected by the loader |
| `tests/engine/test_push_generator_k8s.py` | the emitted object graph — labels == vocabulary, PSA restraint, anti-affinity, pinned reaper RBAC, teardown recipes, delivery contract, exit-78 paths, payload determinism |

The golden digest file is regenerated with the **adapter catalog loaded**
(`tools/packs`), because `generate_bash` resolves its tool-install section from
that module singleton and an unloaded catalog silently emits
`Adapter X not in catalog` instead. The test loads it in an autouse fixture so
the guard is order-independent.

Run everything in the prod image — the host Python is 3.14:

```bash
docker run --rm -v "$PWD:/app" -w /app -e CORTEXSIM_BASE_DIR=/app \
  -e CORTEXSIM_ENV=development cortexsim:dev python -m pytest tests/ -q
```
