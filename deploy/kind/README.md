# Container agent: real-pod proof on kind

`run-agent-e2e.sh` deploys the CortexSim container agent into a throwaway local
[kind](https://kind.sigs.k8s.io/) cluster from a running SimCore, and checks
that the pod enrolls, runs a scenario, honours an abort, is gated on its posture,
and degrades honestly when it cannot do what a scenario asks.

This proves the pod runs and reports. It does not prove any Cortex detection fires; that needs a cluster with the XSIAM agent DaemonSet and stays tenant-verified: 0.

## Run it

```bash
make agent-dist                       # once, if SimCore runs on the host (not the image)
# start SimCore listening on 0.0.0.0:8888 (docker compose, or uvicorn on the host)
make agent-k8s-e2e                    # = deploy/kind/run-agent-e2e.sh
```

Needs `kind`, `kubectl`, `docker`, `curl` and `jq`, internet access from the kind
node (Docker Hub pulls of `alpine:3.20`, `alpine/kubectl`, `debian:12-slim`, and
Debian / Alpine package mirrors for `prepare=packages`), and about 15 minutes.
Most of that is stage `offline`, which waits for SimCore's 5-minute offline
window.

| Variable | Default | Meaning |
|---|---|---|
| `SIMCORE_URL` | `http://host.docker.internal:8888` | SimCore as the **cluster** sees it. Written into the `cortexsim-enroll` Secret. If `host.docker.internal` does not resolve inside the kind node (Linux), the node's default gateway is used and the run says so. |
| `SIMCORE_API` | `$SIMCORE_URL` | SimCore as **this host** sees it: the script's own API calls and the manifest download (`kubectl apply -f <url>` runs on the host). |
| `OFFLINE_TIMEOUT` | `420` | Seconds to wait for the agent row to read `offline` after the namespace is deleted. |

Example against a host-run SimCore on port 8889:

```bash
SIMCORE_URL=http://host.docker.internal:8889 SIMCORE_API=http://127.0.0.1:8889 \
  deploy/kind/run-agent-e2e.sh
```

## What it touches

* Creates and deletes **only** the kind cluster `cortexsim-e2e` (deleted in an
  `EXIT` trap, success or failure). Refuses to start if one already exists.
* Writes the cluster's kubeconfig to a temp file and points `KUBECONFIG` at it, so
  your `~/.kube/config` and its current context are never read or changed. Every
  `kubectl` call also names `--context kind-cortexsim-e2e`.
* On SimCore: mints one enrollment token (`max_uses` 10), creates one Composer
  draft (`kind e2e abort probe`), and launches a handful of runs. They stay in
  SimCore's database as history.

## Stages

Each prints `[e2e] <stage> ok|FAIL <reason>`. The first `FAIL` dumps
`kubectl describe pod` and every container's logs, then exits 1.
`[e2e] observe ...` lines record what the pod actually did and are not
assertions. `[e2e] <stage> SKIP ...` names an assertion that could not run and
why; a skip is never a pass.

`[e2e] DEFECT <stage> ...` records a product defect the run hit where the stages
after it can still run (today: re-applying the manifest is rejected because the
reaper Job's pod template is immutable; a bash-less, interpreter-less pod cannot
enroll). The script works around it only to keep going, says so on the same
line, and exits 2 at the end.

| Stage | Asserts |
|---|---|
| `preconditions` | tools present, SimCore `/api/health` answers |
| `cluster` | cluster created, context is `kind-cortexsim-e2e`, node architecture read, SimCore reachable **from inside the node** |
| `deploy` | token minted, namespace + Secret created, baseline manifest applied straight from the manifest URL for the node's architecture |
| `rollout` | `deploy/cortexsim-agent` Ready within 300 s (then observes PID 1, the process list, `id -u` of an unknown user, `prepare.status`, the copied `kubectl`, the subreaper log line) |
| `enroll` | an `online` agent with `runtime_facts.container == true` whose `pod_name` is the real pod |
| `launch` | every `POSTURE_SCENARIOS` entry (default `SIM-CDR-001 SIM-CDR-015`) is refused `409 AGENT_POSTURE_INSUFFICIENT` on the baseline agent, except that a `PAYLOAD_NOT_STAGED` refusal (SimCore's shelf, checked before the agent gate) is printed as `SKIP` for that scenario; at least one must reach the agent gate. Then the first CDR scenario with no `kubectl` step and no external tools that the launch gate accepts reaches `complete` with output for every step |
| `abort` | a Composer draft (`sleep 300`, `cgo_anchor: apache2`) is aborted once `sleep 300` is a live process in the pod, and the run reads `aborted` |
| `cluster-privilege` | re-applied with `profile=cluster-privilege` + consent, the new agent reports that profile and every scenario refused in `launch` whose refusal named `cluster-privilege` as the satisfying profile is accepted (its run is observed for 120 s, then aborted if still going) |
| `digest-mismatch` | a manifest copy with one hex digit of the beacon digest flipped makes the `fetch` init container exit `78` with message `BEACON_DIGEST_MISMATCH` |
| `alpine` | `image=alpine:3.20` with `prepare=packages` enrolls and is observed (apk installs `bash`, so the anchor is honoured there); with `prepare=none` (no `bash`) a `cgo_anchor` scenario completes and its output carries `ANCHOR NOT HONOURED`. `prepare=none` is used because with `packages` the image has `bash` and the anchor is, correctly, honoured |
| `offline` | namespace deleted; the last agent row reads `offline` within `OFFLINE_TIMEOUT` |

The scenario for `launch` is chosen at run time, not hard-coded. Step commands
and `external_tools` are read from the scenario **detail** (the list API projects
steps without their commands). `cluster_posture` is not exposed on the API and
the tool scan is the engine's own, so the launch gate decides posture and tools;
every candidate it refuses is printed as an `observe ... skipped` line with its
refusal code.

Mid-step for `abort` is proven from the pod's process list, not from streamed
output: in causality-chained execution (every corpus scenario declares a
`cgo_anchor`) the beacon posts a step's output only when the step ends.

`lab-privileged` is deliberately not exercised: a privileged pod with the node's
root mounted is out of scope for an unattended test, even in kind.

## Last recorded run

See the PR body / task report for the full stage output of the most recent run
and which stages ran, passed or were skipped on that machine.
