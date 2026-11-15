# Kubernetes Container Agent (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A DC applies one manifest served by their SimCore and gets an enrolled Linux beacon running in a pod that detonates scenarios dispatched from the console.

**Architecture:** A new module, `core/engine/agent_manifest.py`, builds the agent manifest as an object graph by composing the existing `ClusterPosture` model, consent check, reaper and header from `core/engine/k8s_manifest.py`. `GET /api/agents/manifest` serves it. The Go beacon gains native enrollment, runtime-fact reporting, PID 1 behaviour and an absolute expiry; the launch path gains a posture and tool gate for container agents.

**Tech Stack:** Python 3.11 / FastAPI / Pydantic / SQLAlchemy (SQLite), Go 1.21 stdlib only, React + vitest, `kind` for the real-pod proof.

**Spec:** `docs/superpowers/specs/2026-11-10-k8s-container-agent-design.md`. Read it first. This plan covers **Phase 1 only**; phases 1b, 2 and 3 are out of scope.

## Global Constraints

- Branch: `feature/agent-k8s-container-agent`, cut from `dev`. Stage by name (`git add <path>`); never `git add -A`. No PR unless asked.
- Go beacon stays **stdlib only**. No new module dependencies.
- Do not duplicate `ClusterPosture`, `check_cluster_consent`, `findings_for`, `_reaper`, `build_header` or `render`. Import them.
- The push path must not change: `GET /api/scenarios/{id}/download?format=k8s` still returns `GATED_FORMAT_REQUIRES_POST` for gated postures, and `tests/engine/test_push_generator_invariant.py` stays green untouched.
- Fixed names: namespace `cortexsim-agent`; Secret `cortexsim-enroll` with keys `server`, `token`, optional `artifact-token`; label `cortexsim.io/agent: "true"` on every object; in-pod dir `/cortexsim`; boot ConfigMap mount `/cortexsim-boot`.
- Defaults: `profile=baseline`, `image=debian:12-slim`, `prepare=packages`, `arch=amd64`, `ttl_seconds=43200` (bounds 60..43200).
- Never-ran exit code is `k8s_manifest.EXIT_NEVER_RAN` (78) with `terminationMessagePolicy: File`.
- Consent keys are `cluster_privilege_authorized` and `node_access_authorized`; only the literal string `true` counts. `authorized_by` is required with either.
- All API errors are structured: `{"error", "code", "detail"}`.
- Every new guard needs evidence it fails without the fix (Gate A): each task's "run it to make sure it fails" step is that evidence; keep the failing output for the PR body.
- Nothing here is tenant-verified. Do not write a doc line that implies a detection fired.

## Review Focus

Conditions the spec implies that are most likely to bite a DC, each pinned by a test in the task named:

1. **Node architecture differs from `arch`** (arm64 node, default amd64 manifest): the beacon fails with `exec format error` and the pod crash-loops with no reason. Expected: `start.sh` compares `uname -m` to the manifest's arch and exits 78 with `ARCH_MISMATCH` on the termination log. → Task 2.
2. **Prepare stage with no egress**: `apt-get update` hangs for minutes and the pod looks dead. Expected: each package-manager call is bounded by a timeout, a failure is recorded as `PREPARE_FAILED` in the beacon's runtime facts, and the beacon still starts. → Task 2, Task 6.
3. **Secret `server` value has a trailing slash or no scheme**: every fetch 404s or fails. Expected: the fetch script strips trailing slashes and exits 78 with `BAD_SERVER_URL` when the value has no `http://`/`https://` prefix. → Task 2.
4. **Container restart after the enrollment token is spent or expired**: Expected: the beacon reuses the id stored in `--state-dir` and does not call `/enroll` again. A rescheduled pod with a dead token exits non-zero with `ENROLL_DENIED` in its log, not a silent idle. → Task 7.
5. **Scenario needs `kubectl` on a profile with no RBAC, or a tool the pod lacks**: Expected: launch warns (RBAC) or refuses (tool) before dispatch, never a step that fails and scores as a miss. → Task 10.

---

## File Structure

| File | Responsibility |
|---|---|
| `core/engine/agent_manifest.py` (new) | Profiles, start/fetch scripts, object graph, header, `generate_agent_manifest` |
| `core/api/agents.py` (modify) | `GET /api/agents/manifest`; `runtime_facts` on enroll/register |
| `core/models.py`, `core/database.py` (modify) | `Agent.runtime_facts` JSON column + boot migration |
| `core/engine/agent_launch_gate.py` (new) | Posture-subset, tool-roster and RBAC checks for container agents |
| `core/engine/orchestrator.py` (modify) | Call the gate; populate step `required_binaries` for container agents |
| `agent/runtimefacts/` (new package) | Container detection, pod env, tool and account rosters |
| `agent/beacon/enroll.go` (new) | Native enrollment + state-dir id reuse |
| `agent/reaper_unix.go`, `agent/reaper_windows.go` (new) | PID 1 child reaping |
| `agent/main.go`, `agent/capabilities.go` (modify) | New flags, `container` capability, expiry |
| `agent/executor/platform_unix.go`, `agent/beacon/client.go` (modify) | Anchor shell selection, `ANCHOR NOT HONOURED`, absent-account degradation |
| `ui/src/components/console/TargetsView.jsx`, `ui/src/api/client.js` (modify) | Kubernetes tab |
| `deploy/kind/run-agent-e2e.sh` (new) | Real-pod proof |

---

### Task 1: Agent posture profiles

**Files:**
- Create: `core/engine/agent_manifest.py`
- Test: `tests/engine/test_agent_manifest_profiles.py`

**Interfaces:**
- Produces:
  - `AGENT_NAMESPACE = "cortexsim-agent"`, `AGENT_SCENARIO_ID = "AGENT"`, `PROFILES = ("baseline", "cluster-privilege", "lab-privileged")`
  - `class UnknownAgentProfile(ValueError)`
  - `agent_posture(profile: str, *, image: str = "debian:12-slim", ttl_seconds: int = 43200) -> ClusterPosture`
  - `profile_findings(profile: str) -> frozenset[str]` (finding slugs for a profile, via `k8s_manifest.findings_for`)

- [ ] **Step 1: Write the failing tests**

```python
from engine import k8s_manifest as km
from engine.agent_manifest import agent_posture, profile_findings, UnknownAgentProfile
import pytest

def test_baseline_is_ungated_and_mounts_token():
    p = agent_posture("baseline")
    assert p.is_gated() is False
    assert p.service_account.automount_token is True
    assert p.service_account.cluster_role_rules == []
    assert p.security_context.privileged is False
    assert p.host_access.host_pid is False and p.host_access.host_paths == []
    assert p.workload == "deployment" and p.replicas == 1
    assert p.namespace == "cortexsim-agent" and p.image == "debian:12-slim"

def test_cluster_privilege_needs_exactly_the_cluster_key():
    p = agent_posture("cluster-privilege")
    assert km.required_consent_keys(p) == ("cluster_privilege_authorized",)
    r = p.service_account.cluster_role_rules[0]
    assert (r.api_groups, r.resources, r.verbs) == (["*"], ["*"], ["*"])

def test_lab_privileged_is_the_cdr_yml_shape_with_readonly_host_root():
    p = agent_posture("lab-privileged")
    assert set(km.required_consent_keys(p)) == {"cluster_privilege_authorized", "node_access_authorized"}
    assert p.security_context.privileged is True
    assert p.security_context.allow_privilege_escalation is True   # privileged + False is rejected by the API server
    assert set(p.security_context.capabilities_add) == {"SYS_ADMIN", "NET_ADMIN"}
    assert p.host_access.host_pid is True
    hp = p.host_access.host_paths[0]
    assert (hp.host_path, hp.mount_path, hp.read_only) == ("/", "/host", True)

def test_profiles_are_nested_by_findings():
    assert profile_findings("baseline") <= profile_findings("cluster-privilege") <= profile_findings("lab-privileged")

def test_ttl_bounds_and_unknown_profile():
    with pytest.raises(Exception):
        agent_posture("baseline", ttl_seconds=43201)
    with pytest.raises(UnknownAgentProfile):
        agent_posture("root-everything")
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=core .venv/bin/python -m pytest tests/engine/test_agent_manifest_profiles.py -q`
Expected: FAIL, `ModuleNotFoundError: engine.agent_manifest`

- [ ] **Step 3: Implement the constants and the two functions**

Build each profile as a `ClusterPosture(...)`; let the model's own validators enforce the TTL bound. If `test_profiles_are_nested_by_findings` fails because a finding is planted by the *absence* of something (for example a no-resource-limits finding), give all three profiles the same `resources` so the nesting holds.

- [ ] **Step 4: Run to verify pass** (same command; expected: 5 passed)

- [ ] **Step 5: Commit**

```bash
git add core/engine/agent_manifest.py tests/engine/test_agent_manifest_profiles.py
git commit -m "feat(agent): posture profiles for the container agent"
```

---

### Task 2: Fetch and start scripts

**Files:**
- Modify: `core/engine/agent_manifest.py`
- Test: `tests/engine/test_agent_manifest_scripts.py`

**Interfaces:**
- Produces:
  - `fetch_script(*, arch: str, beacon_sha256: str) -> str` (POSIX `sh`, runs in `k8s_manifest.DEFAULT_FETCH_IMAGE`)
  - `start_script(*, arch: str, prepare: str, expires_at: str, profile: str) -> str` (POSIX `sh`, runs in the main image)
  - `PREPARE_MODES = ("packages", "none")`
  - `PROBE_TOOLS: tuple[str, ...]` = the names in `k8s_manifest._KNOWN_BINS` (import it; do not copy)

**Contract for `fetch_script`** (reads `/secret/server`):
1. Strip trailing `/` from the server value. No `http://` or `https://` prefix → write `BAD_SERVER_URL` to `/dev/termination-log`, `exit 78`.
2. `wget -q -T 60 -O /cortexsim/agent.part "$SERVER/api/agents/binary?os=linux&arch=<arch>"`. Failure → `BEACON_FETCH_FAILED`, exit 78.
3. `sha256sum` the file; empty computed digest or mismatch with the baked-in `beacon_sha256` → `BEACON_DIGEST_MISMATCH`, exit 78.
4. `chmod 0755`, then `mv agent.part cortexsim-agent`. The final name exists only after verification.

**Contract for `start_script`**:
1. `uname -m` mapped (`x86_64`→`amd64`, `aarch64`→`arm64`) must equal `<arch>`, else `ARCH_MISMATCH` on the termination log, `exit 78`.
2. `export PATH=/cortexsim/bin:$PATH` (Task 3 puts `kubectl` there).
3. If `prepare=packages`: detect `apt-get` or `apk`; run each package-manager call under `timeout 120`; install `bash curl wget python3 jq openssl tar ca-certificates util-linux` (apt adds `cron`; apk adds `runuser shadow`); create each account in the shared identity spec that is missing, with shell `/bin/sh`. Any failure appends a line to `/cortexsim/prepare.status` beginning `PREPARE_FAILED`; success writes `PREPARE_OK`. A failure never exits.
4. If `prepare=none`: write `PREPARE_SKIPPED`.
5. Run the beacon as a **child**, not with `exec`, forwarding termination: start `/cortexsim/cortexsim-agent --server "$(cat /secret/server)" --enroll-token-file /secret/token --state-dir /cortexsim/state --expires-at <expires_at> --posture-profile <profile> --prepare-status-file /cortexsim/prepare.status --subreaper --long-poll 30 --stream-output &`, then `trap 'kill -TERM $pid' TERM INT` and `wait $pid`.
6. If the beacon exits 0 (lifetime expired, Task 8), print `LIFETIME_EXPIRED` and idle in a `sleep 3600` loop so the pod does not crash-loop while it waits for the reaper. Any other exit code is passed through as the script's own.

Account names come from `engine.identity_spec` (the loader for `spec/identity_harness.json`); read that module for its accessor and use it.

- [ ] **Step 1: Write the failing tests**

```python
import subprocess, shutil, pytest
from engine.agent_manifest import fetch_script, start_script

SHA = "a" * 64

@pytest.mark.parametrize("s", [fetch_script(arch="amd64", beacon_sha256=SHA),
                               start_script(arch="amd64", prepare="packages", expires_at="2026-11-11T00:00:00Z", profile="baseline"),
                               start_script(arch="arm64", prepare="none", expires_at="2026-11-11T00:00:00Z", profile="lab-privileged")])
def test_scripts_parse_as_posix_sh(s):
    assert subprocess.run(["sh", "-n"], input=s, text=True).returncode == 0

def test_fetch_pins_the_carried_digest_and_never_asks_the_server_for_one():
    s = fetch_script(arch="arm64", beacon_sha256=SHA)
    assert SHA in s and "arch=arm64" in s
    assert "/binary/sha256" not in s
    assert s.index("sha256sum") < s.index("mv ")
    for code in ("BAD_SERVER_URL", "BEACON_FETCH_FAILED", "BEACON_DIGEST_MISMATCH"):
        assert code in s
    assert "exit 78" in s

def test_start_guards_arch_before_exec_and_bounds_package_calls():
    s = start_script(arch="amd64", prepare="packages", expires_at="2026-11-11T00:00:00Z", profile="baseline")
    assert s.index("ARCH_MISMATCH") < s.index("/cortexsim/cortexsim-agent --server")
    assert "exec /cortexsim/cortexsim-agent" not in s          # supervised child, see contract 5
    assert "trap " in s and "--subreaper" in s and "LIFETIME_EXPIRED" in s
    assert "timeout 120" in s and "PREPARE_FAILED" in s
    assert "--enroll-token-file /secret/token" in s and "--expires-at 2026-11-11T00:00:00Z" in s
    assert "cxs_" not in s

def test_prepare_none_installs_nothing():
    s = start_script(arch="amd64", prepare="none", expires_at="x", profile="baseline")
    assert "apt-get" not in s and "apk add" not in s and "PREPARE_SKIPPED" in s

def test_fetch_rejects_bad_server_in_a_real_shell(tmp_path):
    # Review Focus 3: execute the URL-normalisation prelude against a fake /secret.
    ...
```

For the last test, factor the server-normalisation lines so they read the path from `${CORTEXSIM_SECRET_DIR:-/secret}` and the termination log from `${CORTEXSIM_TERMLOG:-/dev/termination-log}`; run the script with `sh` against a temp dir containing `server` = `simcore:8888` and assert exit code 78 and `BAD_SERVER_URL` in the temp termination log. Add a second case with `http://simcore:8888/` and a stub `wget` on `PATH` asserting the URL it received has no `//api`.

- [ ] **Step 2: Run to verify failure** — `PYTHONPATH=core .venv/bin/python -m pytest tests/engine/test_agent_manifest_scripts.py -q` → FAIL, `ImportError: fetch_script`
- [ ] **Step 3: Implement both functions** as string templates rendered with explicit substitution (follow `core/api/agents.py::_render`, which uses `@@NAME@@` tokens so shell `$` and `{}` need no escaping).
- [ ] **Step 4: Run to verify pass**
- [ ] **Step 5: Commit** — `git add core/engine/agent_manifest.py tests/engine/test_agent_manifest_scripts.py && git commit -m "feat(agent): fetch and start scripts for the agent pod"`

---

### Task 3: Object graph and rendered manifest

**Files:**
- Modify: `core/engine/agent_manifest.py`
- Test: `tests/engine/test_agent_manifest_objects.py`

**Interfaces:**
- Consumes: Task 1 `agent_posture`; Task 2 scripts; from `k8s_manifest`: `findings_for`, `_reaper`, `_pod_security_labels`, `_finding_labels`, `_finding_annotations`, `_rbac`, `render`, `DEFAULT_FETCH_IMAGE`, `DEFAULT_REAPER_IMAGE`, `K8S_PREFIX`, `_iso`
- Produces:
  - `build_agent_objects(*, profile: str, image: str, arch: str, prepare: str, ttl_seconds: int, beacon_sha256: str, now: datetime, authorized_by: str | None) -> list[dict]`
  - `generate_agent_manifest(*, profile, image, arch, prepare, ttl_seconds, beacon_sha256, now, authorized_by, consent: dict[str, bool]) -> str`

**Decisions:**
- Objects, in order: Namespace, ServiceAccount, (ClusterRole + ClusterRoleBinding when the posture has `cluster_role_rules`), ConfigMap `cortexsim-agent-boot` (`fetch.sh`, `start.sh`), Deployment `cortexsim-agent`, then the reaper objects from `_reaper(scenario_id="AGENT", sid_lower="agent", ns="cortexsim-agent", ttl=ttl_seconds, …)` with `cluster_scoped` listing exactly the ClusterRole/Binding emitted here.
- Two init containers. `fetch` runs `fetch.sh` in `DEFAULT_FETCH_IMAGE`. `kubectl` runs in `DEFAULT_REAPER_IMAGE` and copies its `kubectl` binary to `/cortexsim/bin/kubectl`. This is how `kubectl` reaches an arbitrary image with no egress; `debian:12-slim` has no `kubectl` package.
- Main container `agent`: `command: ["/bin/sh", "/cortexsim-boot/start.sh"]`; mounts `emptyDir` `/cortexsim`, ConfigMap `/cortexsim-boot`, Secret `cortexsim-enroll` at `/secret` (read-only, `optional: false`); env `POD_NAME`, `POD_NAMESPACE`, `NODE_NAME` from the downward API; `terminationMessagePolicy: File` on all three containers; `terminationGracePeriodSeconds: 30`.
- Security context, host access and RBAC are translated from the posture the same way `k8s_manifest._pod_spec` does. Read that function and reuse its helpers; if a needed helper is inline there, extract it into a module-level function in `k8s_manifest.py` in this task and have `_pod_spec` call it (the push-bundle golden digests must not change).
- Annotations on every object: `cortexsim.io/expires-at` (`_iso(now + ttl)`), `cortexsim.io/agent-profile`, and `cortexsim.io/authorized-by` when `authorized_by` is set.
- Header: a comment block stating profile, each planted finding and the object it lands on, the consent keys recorded, `authorized_by`, the expiry, that the pod obeys whichever SimCore the Secret names, and this sentence verbatim: `# After a SimCore upgrade that changes the beacon, restarted pods exit 78 until this manifest is re-applied.`

- [ ] **Step 1: Write the failing tests**

```python
from datetime import datetime, timezone
import yaml
from engine.agent_manifest import build_agent_objects, generate_agent_manifest

NOW = datetime(2026, 11, 10, tzinfo=timezone.utc)
def objs(profile, **kw):
    a = dict(profile=profile, image="debian:12-slim", arch="amd64", prepare="packages",
             ttl_seconds=43200, beacon_sha256="a"*64, now=NOW, authorized_by=None)
    a.update(kw); return build_agent_objects(**a)
def by_kind(o, kind): return [x for x in o if x["kind"] == kind]
def pod(o): return by_kind(o, "Deployment")[0]["spec"]["template"]["spec"]

def test_baseline_has_no_privileged_field_anywhere():
    text = yaml.safe_dump(objs("baseline"))
    for needle in ("privileged: true", "hostPID", "hostPath", "ClusterRoleBinding"):
        assert needle not in text.replace("cortexsim-reaper", "")   # reaper RBAC is the only cluster-scoped object
    assert not [c for c in by_kind(objs("baseline"), "ClusterRole") if "reaper" not in c["metadata"]["name"]]

def test_lab_privileged_matches_the_lab_shape():
    p = pod(objs("lab-privileged", authorized_by="henry"))
    sc = p["containers"][0]["securityContext"]
    assert sc["privileged"] is True and set(sc["capabilities"]["add"]) == {"SYS_ADMIN", "NET_ADMIN"}
    assert p["hostPID"] is True
    hp = [v for v in p["volumes"] if "hostPath" in v][0]
    assert hp["hostPath"]["path"] == "/"
    m = [m for m in p["containers"][0]["volumeMounts"] if m["mountPath"] == "/host"][0]
    assert m["readOnly"] is True

def test_every_object_is_labelled_and_expiring():
    for o in objs("cluster-privilege", authorized_by="henry"):
        assert o["metadata"]["labels"]["cortexsim.io/agent"] == "true"
        assert o["metadata"]["annotations"]["cortexsim.io/expires-at"] == "2026-11-10T12:00:00Z"
        assert o["metadata"]["annotations"]["cortexsim.io/authorized-by"] == "henry"

def test_pod_wiring():
    p = pod(objs("baseline"))
    assert [c["name"] for c in p["initContainers"]] == ["fetch", "kubectl"]
    assert p["containers"][0]["command"] == ["/bin/sh", "/cortexsim-boot/start.sh"]
    assert {e["name"] for e in p["containers"][0]["env"]} >= {"POD_NAME", "POD_NAMESPACE", "NODE_NAME"}
    assert all(c["terminationMessagePolicy"] == "File" for c in p["initContainers"] + p["containers"])
    sec = [v for v in p["volumes"] if "secret" in v][0]["secret"]
    assert sec["secretName"] == "cortexsim-enroll"

def test_no_secret_object_and_no_token_in_the_manifest():
    text = generate_agent_manifest(profile="baseline", image="debian:12-slim", arch="amd64", prepare="packages",
                                   ttl_seconds=43200, beacon_sha256="a"*64, now=NOW, authorized_by=None, consent={})
    assert "kind: Secret" not in text and "cxs_" not in text
    assert "restarted pods exit 78 until this manifest is re-applied" in text
    assert list(yaml.safe_load_all(text))   # parses

def test_reaper_is_pinned_to_exactly_what_was_emitted():
    o = objs("cluster-privilege")
    emitted = {x["metadata"]["name"] for x in by_kind(o, "ClusterRole") + by_kind(o, "ClusterRoleBinding")}
    reaper_role = [r for r in by_kind(o, "ClusterRole") if "reaper" in r["metadata"]["name"]][0]
    names = {n for rule in reaper_role["rules"] for n in rule.get("resourceNames", [])}
    assert names == emitted
```

- [ ] **Step 2: Run to verify failure** — `PYTHONPATH=core .venv/bin/python -m pytest tests/engine/test_agent_manifest_objects.py -q`
- [ ] **Step 3: Implement** `build_agent_objects` and `generate_agent_manifest`.
- [ ] **Step 4: Run to verify pass**, then run the push-path guards unchanged: `PYTHONPATH=core .venv/bin/python -m pytest tests/engine/test_push_generator_invariant.py tests/engine/test_k8s_manifest.py -q` → all pass.
- [ ] **Step 5: Commit** — stage `core/engine/agent_manifest.py`, `core/engine/k8s_manifest.py` (only if a helper was extracted) and the test file. Message: `feat(agent): build the container agent manifest`

---

### Task 4: `GET /api/agents/manifest`

**Files:**
- Modify: `core/api/agents.py` (add the route **above** every `/{agent_id}` route)
- Test: `tests/api/test_agents_manifest.py`

**Interfaces:**
- Consumes: `generate_agent_manifest`, `agent_posture`, `PROFILES`, `PREPARE_MODES`; `k8s_manifest.check_cluster_consent`, `ClusterConsentRequired`; `engine.orchestrator.allow_privileged_k8s`; this file's `_resolve_binary_or_404`, `_sha256_of`, `_normalize_target`, `_client_ip`
- Produces: `GET /api/agents/manifest?profile=&image=&prepare=&arch=&ttl_seconds=&cluster_privilege_authorized=&node_access_authorized=&authorized_by=` → `text/yaml`, `Content-Disposition: attachment; filename="cortexsim-agent.yaml"`

**Behaviour:**

| Condition | Status | `code` |
|---|---|---|
| unknown `profile` / `prepare` / `arch`, `ttl_seconds` out of 60..43200 | 400 | `BAD_PROFILE` / `BAD_PREPARE` / existing arch code / `BAD_TTL` |
| `image` empty, contains whitespace, or longer than 255 | 400 | `BAD_IMAGE` |
| any consent key present, `authorized_by` blank | 400 | `AUTHORIZED_BY_REQUIRED` |
| gated profile, a needed key absent or not exactly `true` | 409 | `CLUSTER_CONSENT_REQUIRED` (body from `ClusterConsentRequired`, as `core/api/scenarios.py` builds it, including the literal missing keys) |
| `lab-privileged`, kill switch off | 403 | `CLUSTER_PRIVILEGE_DISABLED` |
| beacon binary for `arch` not built | 404 | the existing `_resolve_binary_or_404` body |

On success with a gated profile, `logger.warning` one line with profile, keys, `authorized_by` and `_client_ip(request)`. `beacon_sha256` is `_sha256_of(_resolve_binary_or_404("linux", arch))`.

- [ ] **Step 1: Write the failing tests** (use the app/client fixtures `tests/api/test_agents_deploy.py` uses, and its technique for supplying a fake `agent-dist` binary)

```python
def test_baseline_is_a_bare_get(client, fake_agent_dist):
    r = client.get("/api/agents/manifest")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/yaml")
    assert fake_agent_dist.sha256("linux", "amd64") in r.text

def test_gated_profile_without_keys_is_409_naming_the_keys(client, fake_agent_dist):
    r = client.get("/api/agents/manifest?profile=cluster-privilege")
    assert r.status_code == 409
    d = r.json()["detail"] if "detail" in r.json() else r.json()
    assert d["code"] == "CLUSTER_CONSENT_REQUIRED" and "cluster_privilege_authorized" in str(d)

@pytest.mark.parametrize("v", ["1", "yes", "True", "TRUE", ""])
def test_only_literal_true_is_consent(client, fake_agent_dist, v):
    r = client.get(f"/api/agents/manifest?profile=cluster-privilege&cluster_privilege_authorized={v}&authorized_by=h")
    assert r.status_code == 409

def test_consent_without_authorized_by_is_400(client, fake_agent_dist):
    r = client.get("/api/agents/manifest?profile=cluster-privilege&cluster_privilege_authorized=true")
    assert r.status_code == 400

def test_lab_privileged_refused_while_kill_switch_off(client, fake_agent_dist, monkeypatch):
    monkeypatch.delenv("CORTEXSIM_ALLOW_PRIVILEGED_K8S", raising=False)
    r = client.get("/api/agents/manifest?profile=lab-privileged&cluster_privilege_authorized=true&node_access_authorized=true&authorized_by=h")
    assert r.status_code == 403

def test_lab_privileged_served_with_keys_and_switch(client, fake_agent_dist, monkeypatch):
    monkeypatch.setenv("CORTEXSIM_ALLOW_PRIVILEGED_K8S", "true")
    r = client.get("/api/agents/manifest?profile=lab-privileged&cluster_privilege_authorized=true&node_access_authorized=true&authorized_by=henry")
    assert r.status_code == 200 and "privileged: true" in r.text and "authorized-by: henry" in r.text

def test_never_downgrades_silently(client, fake_agent_dist):
    r = client.get("/api/agents/manifest?profile=lab-privileged")
    assert r.status_code != 200

def test_push_path_gate_is_unchanged(client):
    # pick any scenario id whose cluster_posture is gated (SIM-CDR-004)
    r = client.get("/api/scenarios/SIM-CDR-004/download?format=k8s")
    assert r.status_code == 409 and "GATED_FORMAT_REQUIRES_POST" in r.text

def test_route_is_not_swallowed_by_agent_id_routes(client, fake_agent_dist):
    assert client.get("/api/agents/manifest").status_code == 200
```

- [ ] **Step 2: Run to verify failure** — `PYTHONPATH=core .venv/bin/python -m pytest tests/api/test_agents_manifest.py -q` → 404s
- [ ] **Step 3: Implement the route.** Strict consent parsing is a local helper `_consent_true(v: Optional[str]) -> bool` returning `v == "true"`.
- [ ] **Step 4: Run to verify pass**
- [ ] **Step 5: Commit** — `git add core/api/agents.py tests/api/test_agents_manifest.py && git commit -m "feat(api): serve the container agent manifest"`

---

### Task 5: `Agent.runtime_facts`

**Files:**
- Modify: `core/models.py` (`Agent`), `core/database.py` (boot migration beside the `agents ADD COLUMN last_ip` block), `core/api/agents.py` (`RegisterRequest`, `EnrollRequest`, both handlers, list response)
- Test: `tests/api/test_agent_runtime_facts.py`

**Interfaces:**
- Produces: nullable JSON column `agents.runtime_facts`; request field `runtime_facts: Optional[dict] = None` on both request models; `Agent.to_dict()["runtime_facts"]`.
- Shape the beacon sends (Task 6) and the gate reads (Task 10):

```json
{"container": true, "pod_name": "…", "pod_namespace": "…", "node_name": "…",
 "posture_profile": "baseline", "prepare_status": "PREPARE_OK",
 "tools": ["curl", "python3"], "accounts": ["www-data"], "anchor_rename": true}
```

- An old beacon that omits the field stores `NULL`. `NULL` means "not a container agent, facts unknown", never "no tools".
- `register` must update `runtime_facts` on re-registration (the beacon re-registers; that is how the roster refreshes) but must **not** overwrite a stored value with `None`.

- [ ] **Step 1: Write the failing tests**: enroll with facts → `GET /api/agents` returns them; enroll without → `runtime_facts is None`; register with new facts replaces; register with the field omitted keeps the stored value; a DB created before this change gains the column at boot (use the pattern of the existing `last_ip` migration test in `tests/api/test_agent_last_ip.py`).
- [ ] **Step 2: Run to verify failure**
- [ ] **Step 3: Implement.** Add `ALTER TABLE agents ADD COLUMN runtime_facts JSON` to CLAUDE.md's list of production migrations in Task 13, not here.
- [ ] **Step 4: Run to verify pass**, plus `tests/api/test_agent_enrollment.py tests/api/test_agents_api.py`.
- [ ] **Step 5: Commit** — `feat(api): store container runtime facts on the agent row`

---

### Task 6: Beacon runtime facts

**Files:**
- Create: `agent/runtimefacts/facts.go`, `agent/runtimefacts/facts_test.go`
- Modify: `agent/capabilities.go`, `agent/capabilities_test.go`

**Interfaces:**
- Produces (package `runtimefacts`):
  - `type Facts struct { Container bool; PodName, PodNamespace, NodeName, PostureProfile, PrepareStatus string; Tools, Accounts []string; AnchorRename bool }` with the JSON tags from Task 5
  - `func Collect(opts Options) Facts`
  - `type Options struct { PostureProfile, PrepareStatusFile string; ProbeTools, ProbeAccounts []string; Getenv func(string) string; LookPath func(string) (string, error); ReadFile func(string) ([]byte, error); LookupUser func(string) error }` (nil funcs default to the `os`/`exec`/`user` ones)
  - `var DefaultProbeTools []string` — the same names as `k8s_manifest._KNOWN_BINS`
- `agentCapabilities(goos string, inContainer bool) []string` — appends `"container"` when `inContainer`. Update the one caller in `main.go` and the existing tests.

**Decisions:**
- `Container` is true when `KUBERNETES_SERVICE_HOST` is set, or `/.dockerenv` exists, or `/run/.containerenv` exists.
- `PrepareStatus` is the first line of the status file, or `""` if unreadable.
- `AnchorRename` is true when `bash` resolves on `PATH` (Task 9 uses the same rule).
- Add a Go test that reads `core/engine/k8s_manifest.py`, extracts the `_KNOWN_BINS` tuple with a regexp, and fails if it differs from `DefaultProbeTools`. Model it on `agent/identity/harness_spec_test.go`, which guards the identity spec the same way.

- [ ] **Step 1: Write the failing tests**: table test over injected `Getenv`/`LookPath`/`ReadFile`/`LookupUser` covering: in-cluster env → `Container` true with pod fields from `POD_NAME`/`POD_NAMESPACE`/`NODE_NAME`; tools filtered to those `LookPath` resolves, sorted; accounts filtered likewise; status file `PREPARE_FAILED: apt-get update timed out` → `PrepareStatus` starts with `PREPARE_FAILED` (Review Focus 2); missing status file → `""`; the drift guard; `agentCapabilities("linux", true)` contains `container` and `agentCapabilities("windows", true)` still omits `identity-harness`.
- [ ] **Step 2: Run to verify failure** — `cd agent && go test ./runtimefacts/ ./ -run 'Facts|Capabilit' -count=1`
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run to verify pass**, then `cd agent && go vet ./... && make -C .. test-agent-cross`.
- [ ] **Step 5: Commit** — `feat(agent): report container runtime facts`

---

### Task 7: Native enrollment

**Files:**
- Create: `agent/beacon/enroll.go`, `agent/beacon/enroll_test.go`
- Modify: `agent/main.go`, `agent/beacon/client.go` (`Register` gains the facts)

**Interfaces:**
- Consumes: `runtimefacts.Facts`
- Produces:
  - `func Enroll(serverURL, token, hostname, goos, desiredName string, capabilities, interpreters []string, facts *runtimefacts.Facts) (agentID string, err error)` — POSTs `/api/agents/enroll`; on a non-2xx returns `*EnrollError{Status int, Code string}` (code parsed from the structured error body, `ENROLL_DENIED` on 403)
  - `func LoadOrEnroll(stateDir string, enroll func() (string, error)) (agentID string, reused bool, err error)` — reads `<stateDir>/agent-id`; if present and non-empty returns it without calling `enroll`; otherwise calls `enroll`, writes the file `0600` (creating the dir `0700`), returns the id
  - `(*BeaconClient).SetRuntimeFacts(f *runtimefacts.Facts)`; `Register` and `reRegister` include `"runtime_facts"` when set
- New flags in `main.go`: `--enroll-token-file`, `--state-dir` (default `/var/lib/cortexsim-agent`), `--posture-profile`, `--prepare-status-file`. `--id` stays required **unless** `--enroll-token-file` is given; giving both is an error.

**Decisions:**
- The token is read from the file and trimmed; an empty file is a fatal `ENROLL_TOKEN_EMPTY`.
- `desiredName` is `POD_NAME` when set, else the hostname.
- Enrollment failure at startup is **fatal** with a non-zero exit and one log line `ENROLL_DENIED` / `SERVER_UNREACHABLE` (unlike `Register`, which warns and continues): a pod that cannot enroll must crash-loop visibly, not idle. Retry `SERVER_UNREACHABLE` 5 times with 3 s spacing before exiting.
- After a successful or reused enrollment, call the existing `Register` once so interpreters and facts are current.

- [ ] **Step 1: Write the failing tests** with `httptest.Server`:
  - enroll success returns the server's `agent_id` and the request body carried `token`, `hostname`, `desired_name`, `capabilities`, `runtime_facts`
  - 403 with `{"detail":{"code":"ENROLL_DENIED"}}` → `EnrollError{Status: 403, Code: "ENROLL_DENIED"}`
  - `LoadOrEnroll` on an empty dir calls enroll once and writes `agent-id` with mode `0600`
  - `LoadOrEnroll` with an existing id calls enroll **zero** times (Review Focus 4: use a func that fails the test if invoked)
  - `LoadOrEnroll` with a whitespace-only file enrolls again
  - `main_test.go`: `--id` and `--enroll-token-file` together exit non-zero; neither exits non-zero
- [ ] **Step 2: Run to verify failure** — `cd agent && go test ./beacon/ -run Enroll -count=1`
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run to verify pass** — `cd agent && go test ./... -race -count=1`
- [ ] **Step 5: Commit** — `feat(agent): enroll natively from a token file`

---

### Task 8: Orphan reaping, shutdown and absolute expiry

**Files:**
- Create: `agent/reaper_unix.go` (`//go:build !windows`), `agent/reaper_windows.go`, `agent/reaper_unix_test.go`
- Modify: `agent/main.go`, `agent/beacon/client.go` (`Run`)

**Interfaces:**
- Produces:
  - `--subreaper` flag. When set on linux the beacon calls `prctl(PR_SET_CHILD_SUBREAPER, 1)` via `syscall.RawSyscall(syscall.SYS_PRCTL, 36, 1, 0)` (build-tagged file; no-op on darwin and windows) so orphans of finished steps reparent to it. `start.sh` is PID 1 and supervises the beacon (Task 2), so the beacon itself is not PID 1.
  - `func startChildReaper(ctx context.Context)` — no-op unless `--subreaper`; on unix, a goroutine that on `SIGCHLD` calls `reapOnce()`. Windows file: empty function.
  - `func reapOnce() int` — loops `syscall.Wait4(-1, &ws, syscall.WNOHANG, nil)` until it returns `<= 0`; returns how many it collected.
  - `--expires-at <RFC3339>` flag → `client.ExpiresAt time.Time` (zero = never)
  - In `Run`: before each poll, if `ExpiresAt` is set and passed, log `LIFETIME_EXPIRED`, stop polling and return. A task already executing finishes first.

**Decisions:**
- The reaper must not steal the exit status of steps the executor is waiting on. `os/exec`'s `cmd.Wait` and a blanket `Wait4(-1)` race. Avoid the race: the reaper only runs `Wait4(-1, WNOHANG)` while `beacon.ActiveSteps()` (a new `atomic.Int32` incremented around each step's `Start`…`Wait`) is zero. Orphans left by a finished step are reaped at the next idle moment.
- On `SIGTERM` the existing context cancel already kills the in-flight step's process group. Add a bound: after cancel, `main` waits at most 20 s for `Run` to return, then exits 0 (the pod's grace period is 30 s).
- After `LIFETIME_EXPIRED`, `main` exits 0; `start.sh` (Task 2, contract 6) then idles until the reaper removes the pod.

- [ ] **Step 1: Write the failing tests**:
  - `Run` with `ExpiresAt` in the past returns promptly without calling the poll endpoint (httptest server that fails on any request)
  - `Run` with `ExpiresAt` 200 ms ahead polls, then returns within 2 s
  - `reapOnce()`: spawn `sh -c 'sleep 0 & exit 0'`-style orphan via a re-exec'd helper process, assert `reapOnce` collects it and returns its count; and with `ActiveSteps() > 0`, `reapOnce` returns 0 without calling `Wait4`
  - a step run while the reaper goroutine is on still reports its real exit code 7 for `sh -c 'exit 7'`, 50 times in a loop under `-race`
- [ ] **Step 2: Run to verify failure** — `cd agent && go test ./... -run 'Expire|Reap|ExitCode' -race -count=1`
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run to verify pass**, plus `make test-agent-cross`.
- [ ] **Step 5: Commit** — `feat(agent): reap orphans, honour SIGTERM and expire on schedule`

---

### Task 9: Honest anchor and identity degradation

**Files:**
- Modify: `agent/executor/platform_unix.go` (`newAnchorCmd`), `agent/executor/chained.go`, `agent/beacon/client.go` (chained path + step output), `agent/identity/harness.go` or `agent/identity/platform.go` (wherever `ResolveFor` lives)
- Test: `agent/executor/chained_test.go`, `agent/beacon/identity_degradation_test.go`

**Interfaces:**
- Produces:
  - `executor.AnchorRenameSupported() bool` — true when `bash` resolves on `PATH`
  - `newAnchorCmd(cgoImage string) (cmd *exec.Cmd, renamed bool)` — with a non-empty `cgoImage`: if `bash` resolves, run `bash` with `Args[0] = cgoImage`, `renamed = true`; otherwise run `sh` **un-renamed**, `renamed = false`
  - `(*ChainSession).AnchorRenamed() bool`
  - The run record line, written once per task to the first step's `/output` when a `cgo_anchor` was requested and `renamed` is false: `ANCHOR NOT HONOURED: requested <image_name>; this image has only a multi-call shell, so the chain runs under 'sh'. Detections keyed on the anchor process name will not match.`
  - For a step whose resolved identity is `runuser` and whose account does not exist (`user.Lookup` fails): do not invoke `runuser`. Run the step `direct` and prefix its output with the same `IDENTITY NOT HONOURED` text the Windows path emits, naming the missing account. Count it in `degradedSteps`.

**Decisions:**
- Read `agent/identity/platform.go` and `identity_degradation_test.go` first; the Windows degradation already has a `Resolution` type with a degraded flag and message. Extend that mechanism with a second reason, do not add a parallel one.
- `runtimefacts.Facts.AnchorRename` (Task 6) must call `executor.AnchorRenameSupported` so the two never disagree.

- [ ] **Step 1: Write the failing tests**:
  - with `PATH` set to a temp dir holding only a symlinked `sh`: `newAnchorCmd("nginx")` returns `renamed == false`, `cmd.Args[0] == "sh"`, and a `ChainSession` started from it executes `echo ok` successfully (this is the alpine case that dies today)
  - with `bash` on `PATH`: `renamed == true`, `Args[0] == "nginx"`, and `/proc/<pid>/cmdline` of the anchor starts with `nginx` (skip when `/proc` is absent)
  - a task with `cgo_anchor` on the sh-only `PATH` produces exactly one `/output` body containing `ANCHOR NOT HONOURED`
  - a step with identity `no-such-acct-xyz` runs, exits 0 for `true`, and its output contains `IDENTITY NOT HONOURED` and `no-such-acct-xyz`; `runuser` is not exec'd (stub `runuser` on `PATH` that writes a marker file; assert the marker is absent)
- [ ] **Step 2: Run to verify failure** — `cd agent && go test ./executor/ ./beacon/ -run 'Anchor|NotHonoured' -count=1`
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run to verify pass** — `cd agent && go test ./... -race -count=1 && make -C .. test-agent-cross`
- [ ] **Step 5: Commit** — `fix(agent): degrade honestly on multi-call shells and absent accounts`

---

### Task 10: Launch gate for container agents

**Files:**
- Create: `core/engine/agent_launch_gate.py`
- Modify: `core/engine/orchestrator.py` (`launch`, before the task is enqueued; and where steps are built, near the `required_binaries` assignment)
- Test: `tests/engine/test_agent_launch_gate.py`, `tests/api/test_runs_container_agent_gate.py`

**Interfaces:**
- Consumes: `agent_manifest.profile_findings`, `k8s_manifest.parse_posture`, `findings_for`, `required_binaries`; `Agent.runtime_facts`
- Produces:

```python
@dataclass(frozen=True)
class GateRefusal:
    code: str            # AGENT_POSTURE_INSUFFICIENT | AGENT_TOOL_ABSENT
    error: str
    detail: dict         # see below

@dataclass(frozen=True)
class GateResult:
    refusal: Optional[GateRefusal]
    warnings: list[dict]  # [{"code": "AGENT_NO_RBAC_FOR_KUBECTL", "steps": [...]}]

def evaluate(scenario: dict, agent_runtime_facts: Optional[dict], *, allow_missing_tools: bool = False) -> GateResult
def step_required_binaries(command: str) -> list[str]   # thin wrapper over k8s_manifest.required_binaries
```

**Rules:**
1. `agent_runtime_facts` is `None`, or `container` is falsy → `GateResult(None, [])`. Host beacons are untouched.
2. **Posture.** `needed = {f.slug for f in findings_for(parse_posture(scenario))}` (empty when the scenario has no `cluster_posture`). `have = profile_findings(facts["posture_profile"])`. If `needed - have` is non-empty → refuse `AGENT_POSTURE_INSUFFICIENT` with `detail = {"missing_findings": sorted(...), "agent_profile": ..., "satisfying_profile": <first of PROFILES whose findings ⊇ needed, or None>}`.
3. **Tools.** For each step, `step_required_binaries(command)` minus `facts["tools"]`. If any step has a gap and `allow_missing_tools` is false → refuse `AGENT_TOOL_ABSENT` with `detail = {"missing": [{"step": id, "tools": [...]}], "prepare_status": facts.get("prepare_status"), "override": "allow_missing_tools"}`. An unknown profile string in the facts → refuse `AGENT_POSTURE_INSUFFICIENT` with `agent_profile` echoed.
4. **RBAC warning.** If any step requires `kubectl` and the agent profile is `baseline`, add the `AGENT_NO_RBAC_FOR_KUBECTL` warning. Never a refusal.
5. Orchestrator: refusals go through the existing `_refuse_after_seed(..., http_status=409)` so the run row ends `failed`, not `pending`. Warnings are attached to the launch response under `warnings` (follow how the existing launch response carries advisory fields; if none exists, add `warnings: list` to `LaunchResult`). `allow_missing_tools` arrives in the launch request body beside `consent`.
6. Orchestrator: when the target agent's facts say `container`, set each step's `required_binaries` to the union of what is already there and `step_required_binaries(command)`. The beacon's existing `preflightTools` then stops a step whose tool vanished after the roster was taken, with `TOOL_ABSENT`.

- [ ] **Step 1: Write the failing unit tests**

```python
BASE = {"container": True, "posture_profile": "baseline", "tools": ["curl", "python3"], "accounts": []}

def test_host_beacon_is_untouched():
    assert evaluate({"steps": [{"id": "s1", "command": "kubectl get po"}]}, None) == GateResult(None, [])

def test_posture_gap_names_the_satisfying_profile(load_scenario):
    r = evaluate(load_scenario("SIM-CDR-004"), BASE)       # needs a wildcard ClusterRole
    assert r.refusal.code == "AGENT_POSTURE_INSUFFICIENT"
    assert r.refusal.detail["satisfying_profile"] in ("cluster-privilege", "lab-privileged")

def test_baseline_tier_scenario_needing_a_role_is_still_refused_on_baseline(load_scenario):
    r = evaluate(load_scenario("SIM-CDR-001"), BASE)       # findings, not tiers
    assert r.refusal and r.refusal.code == "AGENT_POSTURE_INSUFFICIENT"

def test_missing_tool_refuses_and_names_step():
    sc = {"steps": [{"id": "s1", "command": "jq . /etc/os-release"}]}
    r = evaluate(sc, BASE)
    assert r.refusal.code == "AGENT_TOOL_ABSENT" and r.refusal.detail["missing"] == [{"step": "s1", "tools": ["jq"]}]

def test_override_turns_tool_refusal_off_but_not_posture():
    sc = {"steps": [{"id": "s1", "command": "jq ."}]}
    assert evaluate(sc, BASE, allow_missing_tools=True).refusal is None

def test_kubectl_on_baseline_warns_never_refuses():
    facts = dict(BASE, tools=["kubectl"])
    r = evaluate({"steps": [{"id": "s1", "command": "kubectl get secrets"}]}, facts)
    assert r.refusal is None and r.warnings[0]["code"] == "AGENT_NO_RBAC_FOR_KUBECTL"

def test_quoted_tool_names_do_not_trip_the_gate():
    r = evaluate({"steps": [{"id": "s1", "command": "echo 'run jq later'"}]}, BASE)
    assert r.refusal is None
```

  If `SIM-CDR-001`'s posture turns out to plant no finding beyond baseline's (check `findings_for` output before trusting the test), then baseline cannot be distinguished by findings alone; in that case extend rule 2 to also require `scenario namespace_role_rules ⊆ profile namespace_role_rules` and keep the test.

- [ ] **Step 2: Write the failing API tests**: enroll an agent with `runtime_facts` (`baseline`, no `jq`); launching a `jq` scenario returns 409 `AGENT_TOOL_ABSENT` and the run row is `failed`; the same launch with `allow_missing_tools: true` is accepted and the queued task's step carries `required_binaries` including `jq`; launching against an agent with `runtime_facts = None` behaves exactly as before (assert the queued step's `required_binaries` is unchanged from `dev`'s behaviour).
- [ ] **Step 3: Run to verify failure**
- [ ] **Step 4: Implement** the module, then the two orchestrator call sites.
- [ ] **Step 5: Run to verify pass**, then the launch regressions: `PYTHONPATH=core .venv/bin/python -m pytest tests/api -k "run or launch or agent" -q`
- [ ] **Step 6: Commit** — `feat(api): gate launches on a container agent's posture and tools`

---

### Task 11: Console — Kubernetes tab

**Files:**
- Modify: `ui/src/api/client.js`, `ui/src/components/console/TargetsView.jsx`
- Test: `ui/src/components/__tests__/DeployAgentKubernetes.test.jsx`, `ui/src/api/__tests__/` (beside the existing `agentInstallUrl` test, if one exists)

**Interfaces:**
- Produces: `agentManifestUrl({ profile = 'baseline', image, arch, prepare, ttlSeconds, consent = {}, authorizedBy } = {}) -> string`. Omits every parameter that equals its default. Adds a consent key only when its value is `true`. Never includes a token.

**Behaviour:**
- TargetsView's deploy surface already offers Linux/macOS/Windows; add **Kubernetes**. It shows, in order: the manifest command, then the Secret command. The token is minted through the existing mint flow with `max_uses: 50` and `ttl_seconds: 43200`, and appears only in the Secret command.
- A profile select (`baseline` default). Choosing a gated profile reveals one checkbox per required consent key, labelled with what it grants (`cluster_privilege_authorized`: "Bind a wildcard ClusterRole to the agent"; `node_access_authorized`: "Privileged pod with host PID and the node's root mounted read-only"), and an `Authorized by` text field. The manifest command is rendered **only** when every required box is ticked and the field is non-empty; until then show "Tick each capability and name who authorised it to get the command."
- Below the commands, verbatim: "This pod runs commands sent by this SimCore. It removes itself after 12 hours."
- An agent row whose `runtime_facts.container` is true shows its `posture_profile` and node, and `prepare_status` when it starts with `PREPARE_FAILED`.

- [ ] **Step 1: Write the failing tests**: `agentManifestUrl()` → `/api/agents/manifest`; with `lab-privileged` + both keys + `authorizedBy: 'henry'` → contains all four params and no `token`; with one key `false` → that key absent. Component: selecting `lab-privileged` hides the command until both boxes are ticked and the name is filled; the Secret command contains the minted token and the manifest command does not; an agent with container facts renders its profile.
- [ ] **Step 2: Run to verify failure** — `cd ui && npx vitest run src/components/__tests__/DeployAgentKubernetes.test.jsx`
- [ ] **Step 3: Implement**, using existing Cortex tokens and the deploy surface's current markup patterns. No new CSS file.
- [ ] **Step 4: Run to verify pass** — `cd ui && npx vitest run && npx vite build`
- [ ] **Step 5: Commit** — `feat(ui): Kubernetes tab for deploying the container agent`

---

### Task 12: Real-pod proof on kind

**Files:**
- Create: `deploy/kind/run-agent-e2e.sh`, `deploy/kind/README.md`
- Modify: `Makefile` (target `agent-k8s-e2e`), `.github/workflows/ci.yml` (opt-in step behind PR label `agent-k8s-e2e`, modelled on the `tier-c-e2e` step)

**Script contract** (bash, `set -euo pipefail`, every stage prints `[e2e] <stage> ok|FAIL <reason>`; exits non-zero on the first failure and dumps `kubectl -n cortexsim-agent describe pod` and logs):

1. Preconditions: `kind`, `kubectl`, `docker`, `curl`, `jq` present; SimCore answering at `$SIMCORE_URL` (default `http://host.docker.internal:8888`) from inside a kind node. Refuse to run if the current kube context is not `kind-cortexsim-e2e` after cluster creation. Never touch another context.
2. `kind create cluster --name cortexsim-e2e`.
3. Mint a token (`max_uses` 10); create namespace + Secret; `kubectl apply -f "$SIMCORE_URL/api/agents/manifest"`.
4. Wait for `rollout status deploy/cortexsim-agent` (timeout 300 s).
5. Poll `GET /api/agents` until an agent with `runtime_facts.container == true` and `status == "online"` appears; assert `pod_name` equals the real pod name.
6. Launch a scenario chosen at run time: the first CDR scenario with no `cluster_posture` and no `kubectl` step whose tools are all in the agent's roster (select with `jq` from `GET /api/scenarios` + the agent row; fail loudly if none qualifies). Assert per-step output arrives and the run reaches `completed`.
7. Launch a long-running draft or scenario, `POST /api/runs/{id}/abort` mid-step, assert `aborted`.
8. Re-apply with `profile=cluster-privilege` + consent; launch `SIM-CDR-001`; assert it is accepted. Then assert the same launch against a `baseline` agent was refused with `AGENT_POSTURE_INSUFFICIENT` (do the baseline attempt in stage 6's cluster state, before the re-apply).
9. Negatives: (a) apply a copy of the manifest with one hex digit of the beacon digest flipped; assert the pod's `fetch` init container terminates with exit code 78 and message `BEACON_DIGEST_MISMATCH`. (b) apply with `image=alpine:3.20`; launch a scenario with a `cgo_anchor`; assert the run completes and its output contains `ANCHOR NOT HONOURED`.
10. `kubectl delete namespace cortexsim-agent`; assert the agent row goes `offline` within the sweep window; `kind delete cluster --name cortexsim-e2e` in an `EXIT` trap.

- [ ] **Step 1: Write the script and README** (README states what this proves and, verbatim, "This proves the pod runs and reports. It does not prove any Cortex detection fires; that needs a cluster with the XSIAM agent DaemonSet and stays tenant-verified: 0.").
- [ ] **Step 2: `bash -n deploy/kind/run-agent-e2e.sh` and `shellcheck` it** (if `shellcheck` is installed; `command -v` guard).
- [ ] **Step 3: Run it locally** if `kind` and Docker are available: `make agent-k8s-e2e`. Record the full stage output in the PR body. If `kind` is not installed on the machine, say so in the PR body in those words and do not claim the proof; do not install `kind` without asking.
- [ ] **Step 4: Add the Makefile target and the labelled CI step.**
- [ ] **Step 5: Commit** — `test(agent): real-pod proof for the container agent on kind`

---

### Task 13: Documentation

**Files:**
- Modify: `docs/reference/api-and-agent-surface.md` (§1.6: the manifest endpoint, parameters, refusal codes, new beacon flags, `runtime_facts`, the three launch-gate codes), `docs/reference/k8s-delivery.md` (opening: a cloud-native target now has **two** paths, push manifest or enrolled agent, and when to use each; §10: which previously unobserved items Task 12 observed, only if Task 12 actually ran), `CLAUDE.md` (Agent onboarding paragraph; add `ALTER TABLE agents ADD COLUMN runtime_facts JSON`; route count), `CHANGELOG.md` (`[Unreleased]`), `docs/superpowers/specs/2026-11-10-k8s-container-agent-design.md` (status line → "Phase 1 built"; note the two plan-level decisions below)
- Test: none new; run `make validate` (per project memory, the host suites alone miss `check-ground-truth`).

Plan-level decisions to record in the spec, since they refine it:
1. `kubectl` reaches the pod from a second init container, not a package manager.
2. `start.sh` is PID 1 and supervises the beacon as a child (needed for the post-expiry idle), so the beacon is a child subreaper. The spec's §7 item 3 said the beacon is PID 1.

- [ ] **Step 1: Regenerate the route count** — boot the app in a test and count routes the way CLAUDE.md's number was produced; do not hand-edit the number.
- [ ] **Step 2: Write the doc changes.** State plainly in each: nothing here is tenant-verified.
- [ ] **Step 3: Run** `make validate` and the full host suite `PYTHONPATH=core .venv/bin/python -m pytest tests/ --ignore=tests/smoke --ignore=tests/e2e_isolated -q`, `cd agent && go test ./... -race -count=1`, `cd ui && npx vitest run`. All green, or the failure reported verbatim.
- [ ] **Step 4: Commit** — `docs(agent): document the Kubernetes container agent`

---

## Task order and dependencies

```
1 → 2 → 3 → 4 ─────────────┐
5 ──────────────┬──────────┤
6 → 7 → 8 → 9 ──┤          ├→ 12 → 13
                └→ 10 ─────┤
4, 5 → 11 ─────────────────┘
```

Tasks 1–4 (Python manifest), 5, and 6–9 (Go) are independent tracks. `start.sh` (Task 2) names beacon flags that Tasks 7 and 8 add; the names in Task 2's contract are the contract for both.
