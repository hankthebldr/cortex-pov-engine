#!/usr/bin/env bash
# =============================================================================
# CortexSim container agent — real-pod proof on a throwaway kind cluster.
# =============================================================================
# Creates the kind cluster `cortexsim-e2e`, deploys the container agent from a
# running SimCore's manifest endpoint, and asserts the pod enrolls, runs a
# scenario, aborts, honours the launch gate and degrades honestly. Deletes the
# cluster on exit, success or failure.
#
# This proves the pod runs and reports. It does not prove any Cortex detection
# fires; that needs a cluster with the XSIAM agent DaemonSet and stays
# tenant-verified: 0.
#
# Safety:
#   * The cluster gets its OWN kubeconfig file under a temp dir. The operator's
#     ~/.kube/config (and its current-context) is never read or written.
#   * Every kubectl call names --context kind-cortexsim-e2e explicitly.
#   * Only the cluster named cortexsim-e2e is ever created or deleted.
#
# Environment:
#   SIMCORE_URL   SimCore as the CLUSTER sees it. Goes into the enroll Secret.
#                 Default http://host.docker.internal:8888. If the host name
#                 does not resolve inside the kind node (Linux runners), the
#                 node's default gateway is substituted and the stage says so.
#   SIMCORE_API   SimCore as THIS HOST sees it, for the script's own curl calls
#                 and the manifest download. Default: SIMCORE_URL.
#   POSTURE_SCENARIOS  scenarios that must be refused on baseline and accepted
#                 on cluster-privilege (default "SIM-CDR-001 SIM-CDR-015").
#   OFFLINE_TIMEOUT  seconds to wait for the agent row to go offline after the
#                 namespace is deleted (default 420; SimCore marks offline at 5m).
#
# Every stage prints `[e2e] <stage> ok|FAIL <reason>`; observations that are
# recorded but not asserted print `[e2e] observe ...`. Exits non-zero on the
# first failure after dumping pod state.
# =============================================================================
set -Eeuo pipefail

CLUSTER=cortexsim-e2e
CTX="kind-${CLUSTER}"
NS=cortexsim-agent
NODE="${CLUSTER}-control-plane"
SIMCORE_URL="${SIMCORE_URL:-http://host.docker.internal:8888}"
SIMCORE_URL="${SIMCORE_URL%/}"
SIMCORE_API="${SIMCORE_API:-$SIMCORE_URL}"
SIMCORE_API="${SIMCORE_API%/}"
OFFLINE_TIMEOUT="${OFFLINE_TIMEOUT:-420}"
POSTURE_SCENARIOS="${POSTURE_SCENARIOS:-SIM-CDR-001 SIM-CDR-015}"
MANIFEST_TTL=3600

WORK="$(mktemp -d "${TMPDIR:-/tmp}/cortexsim-e2e.XXXXXX")"
export KUBECONFIG="$WORK/kubeconfig"
CLUSTER_CREATED=0
STAGE="preconditions"

say()     { echo "[e2e] $*"; }
ok()      { say "$STAGE ok${1:+ $1}"; }
observe() { say "observe $*"; }
k()       { kubectl --context "$CTX" "$@"; }
# Enrollment tokens never reach this script's output.
redact()  { sed -E 's/token=[^ ]+/token=<redacted>/g; s/cxs_[A-Za-z0-9_-]+/cxs_<redacted>/g'; }

dump() {
  [ "$CLUSTER_CREATED" = 1 ] || return 0
  _dump 2>&1 | redact
}
_dump() {
  echo "----- [e2e] diagnostics: pods -----"
  k -n "$NS" get pods -o wide 2>&1 || true
  echo "----- [e2e] diagnostics: describe pod -----"
  k -n "$NS" describe pod 2>&1 || true
  echo "----- [e2e] diagnostics: logs (all containers, every pod) -----"
  local p
  for p in $(k -n "$NS" get pods -o name 2>/dev/null); do
    echo "--- $p"
    k -n "$NS" logs "$p" --all-containers --prefix --tail=200 2>&1 || true
  done
}

FAILED=0
fail() {
  FAILED=1
  say "$STAGE FAIL $*" | redact
  dump
  exit 1
}

# An unexpected command failure under `set -e` exits without a stage line; the
# EXIT trap names the stage it died in (an ERR trap is not relied on: it does
# not fire for a failing last command inside a function on every bash).
cleanup() {
  local rc=$?
  if [ "$rc" != 0 ] && [ "$FAILED" = 0 ]; then
    say "$STAGE FAIL unexpected command failure (rc=$rc); see the output above"
    dump
  fi
  if [ "$CLUSTER_CREATED" = 1 ]; then
    if k delete clusterrole,clusterrolebinding -l cortexsim.io/agent=true --ignore-not-found >/dev/null 2>&1; then
      say "teardown ok cluster-scoped cortexsim.io/agent objects deleted"
    else
      say "teardown observe could not delete cluster-scoped cortexsim.io/agent objects (the cluster is deleted next)"
    fi
    if kind delete cluster --name "$CLUSTER" --kubeconfig "$KUBECONFIG" >/dev/null 2>&1; then
      say "teardown ok kind cluster $CLUSTER deleted"
    else
      say "teardown FAIL could not delete kind cluster $CLUSTER; run: kind delete cluster --name $CLUSTER"
    fi
  fi
  rm -rf "$WORK"
  exit "$rc"
}
trap cleanup EXIT

# http METHOD PATH [JSON] -> prints the status code; body lands in $WORK/body.
http() {
  local m=$1 p=$2 d=${3:-}
  if [ -n "$d" ]; then
    curl -sS --max-time 30 -o "$WORK/body" -w '%{http_code}' -X "$m" \
      -H 'Content-Type: application/json' --data "$d" "$SIMCORE_API$p"
  else
    curl -sS --max-time 30 -o "$WORK/body" -w '%{http_code}' -X "$m" "$SIMCORE_API$p"
  fi
}
body()     { cat "$WORK/body"; }
err_code() { jq -r '.code // .detail.code // empty' "$WORK/body" 2>/dev/null; }

# The newest Ready, not-terminating agent pod.
current_pod() {
  k -n "$NS" get pods -l app.kubernetes.io/name=cortexsim-agent -o json | jq -r '
    [.items[] | select(.metadata.deletionTimestamp == null)
              | select(any(.status.conditions[]?; .type == "Ready" and .status == "True"))]
    | sort_by(.metadata.creationTimestamp) | last | .metadata.name // empty'
}

# fetch_manifest QUERY OUTFILE — the manifest for this node's architecture.
fetch_manifest() {
  local q=$1 out=$2 code
  code=$(curl -sS --max-time 30 -o "$out" -w '%{http_code}' \
    "$SIMCORE_API/api/agents/manifest?arch=$ARCH&ttl_seconds=$MANIFEST_TTL&$q")
  [ "$code" = 200 ] || fail "manifest ($q) returned HTTP $code: $(head -c 400 "$out")"
}

# defect MSG — a PRODUCT defect this run observed, recorded only on the one
# signature its caller expects (anything else is a FAIL). Not fatal, so the
# stages after it still run; the script exits 2 at the end.
DEFECTS=0
defect() {
  DEFECTS=$((DEFECTS + 1))
  say "DEFECT $STAGE $*"
}

# reapply FILE — `kubectl apply` a fresh manifest over a running agent, the way
# the manifest header tells an operator to. Any rejection is a FAIL.
reapply() {
  k apply -f "$1" >/dev/null 2>"$WORK/apply.err" && return 0
  redact < "$WORK/apply.err"
  fail "re-applying $1 over the running agent was rejected: $(tr '\n' ' ' < "$WORK/apply.err" | redact | head -c 400)"
}

# pod_names -> the current agent pod names, one per line.
pod_names() {
  k -n "$NS" get pods -l app.kubernetes.io/name=cortexsim-agent -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}'
}

# wait_new_pod BEFORE_FILE -> sets NPOD to the newest agent pod whose name is
# not in BEFORE_FILE (the names recorded before the change), or fails.
wait_new_pod() {
  NPOD=""
  for _ in $(seq 1 60); do
    NPOD=$(k -n "$NS" get pods -l app.kubernetes.io/name=cortexsim-agent -o json \
      | jq -r --rawfile before "$1" '($before | split("\n")) as $b
          | [.items[] | select(.metadata.deletionTimestamp == null)
                      | select(.metadata.name as $n | $b | index($n) | not)]
          | sort_by(.metadata.creationTimestamp) | last | .metadata.name // empty')
    [ -n "$NPOD" ] && return 0
    sleep 2
  done
  fail "no new agent pod appeared within 120s"
}

rollout() {
  k -n "$NS" rollout status deploy/cortexsim-agent --timeout=300s >/dev/null \
    || fail "deploy/cortexsim-agent did not become ready in 300s"
}

# wait_agent POD -> sets AGENT_ROW (JSON) and AGENT to the online container
# agent whose runtime_facts.pod_name is POD.
wait_agent() {
  try_agent "$1" 60 && return 0
  fail "no online container agent reports pod_name=$1 after 180s (online container agents report: ${OTHERS:-none})"
}

# try_agent POD TRIES -> like wait_agent, but returns 1 instead of failing.
try_agent() {
  local pod=$1 row
  OTHERS=""
  for _ in $(seq 1 "$2"); do
    http GET /api/agents >/dev/null
    row=$(jq -c --arg p "$pod" '[.agents[]
        | select(.runtime_facts.container == true and .status == "online"
                 and .runtime_facts.pod_name == $p)] | first // empty' "$WORK/body")
    if [ -n "$row" ]; then
      AGENT_ROW=$row
      AGENT=$(jq -r '.agent_id // .id' <<<"$row")
      return 0
    fi
    OTHERS=$(jq -r '[.agents[] | select(.runtime_facts.container == true and .status == "online")
        | .runtime_facts.pod_name] | join(",")' "$WORK/body")
    sleep 3
  done
  return 1
}

# newest_pod -> the most recently created, not-terminating agent pod (Ready or not).
newest_pod() {
  k -n "$NS" get pods -l app.kubernetes.io/name=cortexsim-agent -o json | jq -r '
    [.items[] | select(.metadata.deletionTimestamp == null)]
    | sort_by(.metadata.creationTimestamp) | last | .metadata.name // empty'
}

# wait_run RUN_ID TIMEOUT -> prints the terminal status (or "timeout").
wait_run() {
  local id=$1 t=$2 s
  for _ in $(seq 1 "$t"); do
    http GET "/api/runs/$id" >/dev/null
    s=$(jq -r '.status' "$WORK/body")
    case "$s" in complete|completed|failed|aborted|staged|beacon_lost) echo "$s"; return 0 ;; esac
    sleep 1
  done
  echo timeout
}

launch_body() { # SCENARIO AGENT [EXTRA_JSON]
  local extra=${3:-}
  [ -n "$extra" ] || extra='{}'
  jq -cn --arg s "$1" --arg a "$2" --argjson x "$extra" \
    '{scenario_id: $s, mode: "pull", target_agent_id: $a} + $x'
}

# calls_kubectl FILE -> true when a step command in the scenario detail calls
# kubectl. Reads the DETAIL document: the list API projects steps without
# their commands.
calls_kubectl() {
  jq -e '[.steps[].command // ""]
    | any(test("(^|[^A-Za-z0-9_./-])kubectl([^A-Za-z0-9_.-]|$)"))' "$1" >/dev/null
}

# pexec SCRIPT -> runs SCRIPT under sh in the agent container; never fails the
# caller (used for observations only).
pexec() { k -n "$NS" exec "$POD" -c agent -- sh -c "$1" 2>&1 || true; }

# ---------------------------------------------------------------------------
# 1. preconditions
# ---------------------------------------------------------------------------
for t in kind kubectl docker curl jq; do
  command -v "$t" >/dev/null 2>&1 || fail "required tool '$t' not on PATH"
done
code=$(http GET /api/health) || fail "SimCore not reachable at $SIMCORE_API"
[ "$code" = 200 ] || fail "SimCore /api/health at $SIMCORE_API returned HTTP $code"
if kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  fail "a kind cluster named $CLUSTER already exists; delete it first (kind delete cluster --name $CLUSTER)"
fi
ok "tools present; SimCore answers at $SIMCORE_API"

# ---------------------------------------------------------------------------
# 2. cluster
# ---------------------------------------------------------------------------
STAGE="cluster"
CLUSTER_CREATED=1
kind create cluster --name "$CLUSTER" --kubeconfig "$KUBECONFIG" --wait 120s >"$WORK/kind.log" 2>&1 \
  || { cat "$WORK/kind.log"; fail "kind create cluster failed"; }
cur=$(kubectl --kubeconfig "$KUBECONFIG" config current-context 2>/dev/null || true)
[ "$cur" = "$CTX" ] || fail "current context of the e2e kubeconfig is '$cur', not $CTX; refusing to continue"
ARCH=$(k get node -o jsonpath='{.items[0].status.nodeInfo.architecture}')
[ -n "$ARCH" ] || fail "could not read the node architecture"

# Can the node reach SimCore under the name the pod will use?
sc_host=$(printf '%s' "$SIMCORE_URL" | sed -E 's#^[a-z]+://([^/:]+).*#\1#')
if [ "$sc_host" = host.docker.internal ] \
   && ! docker exec "$NODE" getent hosts host.docker.internal >/dev/null 2>&1; then
  gw=$(docker exec "$NODE" sh -c "ip route | awk '/^default/{print \$3; exit}'")
  [ -n "$gw" ] || fail "host.docker.internal does not resolve in the node and no default gateway was found"
  SIMCORE_URL=$(printf '%s' "$SIMCORE_URL" | sed "s#host.docker.internal#$gw#")
  observe "host.docker.internal does not resolve in $NODE; using the node's default gateway: SIMCORE_URL=$SIMCORE_URL"
else
  observe "resolver in $NODE: $(docker exec "$NODE" getent hosts "$sc_host" 2>&1 | head -1)"
fi
docker exec "$NODE" curl -sf --max-time 30 -o /dev/null "$SIMCORE_URL/api/health" \
  || fail "SimCore is not reachable from inside the kind node at $SIMCORE_URL"
ok "context=$CTX arch=$ARCH; node reaches SimCore at $SIMCORE_URL"

# ---------------------------------------------------------------------------
# 3. deploy (baseline)
# ---------------------------------------------------------------------------
STAGE="deploy"
code=$(http POST /api/agents/enroll/tokens '{"label":"kind-e2e","ttl_seconds":7200,"max_uses":10}')
[ "$code" = 200 ] || [ "$code" = 201 ] || fail "token mint returned HTTP $code: $(body)"
jq -r '.token' "$WORK/body" > "$WORK/token"
[ -s "$WORK/token" ] && [ "$(cat "$WORK/token")" != null ] || fail "token mint returned no token"
printf '%s' "$SIMCORE_URL" > "$WORK/server"
chmod 600 "$WORK/token"
k create namespace "$NS" >/dev/null
k -n "$NS" create secret generic cortexsim-enroll \
  --from-file=server="$WORK/server" --from-file=token="$WORK/token" >/dev/null
k -n "$NS" label secret cortexsim-enroll cortexsim.io/agent=true >/dev/null
k apply -f "$SIMCORE_API/api/agents/manifest?arch=$ARCH&ttl_seconds=$MANIFEST_TTL" >/dev/null 2>"$WORK/apply.err" \
  || { cat "$WORK/apply.err"; fail "kubectl apply -f <manifest URL> failed"; }
ok "token minted, namespace + Secret created, baseline manifest applied from URL"

# ---------------------------------------------------------------------------
# 4. rollout
# ---------------------------------------------------------------------------
STAGE="rollout"
rollout
POD=$(current_pod)
[ -n "$POD" ] || fail "rollout finished but no Ready agent pod found"
if k -n "$NS" get job cortexsim-reaper-agent >/dev/null 2>&1; then
  fail "a Job named cortexsim-reaper-agent exists; the agent's reaper must be a Deployment"
fi
k -n "$NS" wait --for=condition=Available deploy/cortexsim-reaper-agent --timeout=120s >/dev/null \
  || fail "deploy/cortexsim-reaper-agent is not Available within 120s"
ok "pod $POD Ready (readiness = beacon stored its id); reaper is deploy/cortexsim-reaper-agent, Available, no Job"

# ---------------------------------------------------------------------------
# 5. enroll
# ---------------------------------------------------------------------------
STAGE="enroll"
wait_agent "$POD"
got_pod=$(jq -r '.runtime_facts.pod_name' <<<"$AGENT_ROW")
[ "$got_pod" = "$POD" ] || fail "agent pod_name=$got_pod, real pod is $POD"
observe "runtime_facts: $(jq -c '.runtime_facts | {container, pod_name, pod_namespace, posture_profile, prepare_status, tools}' <<<"$AGENT_ROW")"
ok "agent $AGENT online, container=true, pod_name=$got_pod"
# Observations of the enrolled baseline pod.
# shellcheck disable=SC2016  # expanded by the pod's shell, not this one
PS_SCRIPT='for d in /proc/[0-9]*; do
  c=$(tr "\0" " " < $d/cmdline 2>/dev/null); [ -n "$c" ] || continue
  printf "pid=%s ppid=%s cmd=%s; " "${d#/proc/}" "$(sed -n "s/^PPid:[[:space:]]*//p" $d/status)" "$c"
done'
# shellcheck disable=SC2016
ZOMBIE_SCRIPT='for f in /proc/[0-9]*/stat; do
  s=$(cat "$f" 2>/dev/null) || continue
  st=${s##*) }; st=${st%% *}
  [ "$st" = Z ] && printf "zombie %s; " "$s"
done; echo end'
observe "pid1 cmdline: $(pexec "tr '\\0' ' ' < /proc/1/cmdline")"
observe "processes: $(pexec "$PS_SCRIPT" | tr '\n' ' ')"
observe "unknown user: $(pexec 'id -u no-such-user-xyz; echo rc=$?' | tr '\n' ' ')"
observe "prepare.status: $(pexec 'cat /cortexsim/prepare.status' | tr '\n' ' ')"
observe "kubectl copied by init container: $(pexec '/cortexsim/bin/kubectl version --client 2>&1 | head -1')"
observe "beacon log (subreaper): $({ k -n "$NS" logs "$POD" -c agent 2>&1 || true; } | grep -i 'subreaper' | head -2 | redact | tr '\n' ' ' || true)"


# ---------------------------------------------------------------------------
# 6. launch
# ---------------------------------------------------------------------------
STAGE="launch"
# The baseline half of stage 8. Each POSTURE_SCENARIOS entry declares a
# cluster_posture a baseline pod does not hold (SIM-CDR-001: a namespace
# secrets Role; SIM-CDR-015: a namespace pods Role). Consent is supplied, so a
# refusal must be the container-agent posture gate. PAYLOAD_NOT_STAGED is the
# one other refusal tolerated: it is a property of SimCore's shelf, not the pod,
# and it is reported as SKIP for that scenario, never as a pass.
POSTURE_REFUSED=""
for sid in $POSTURE_SCENARIOS; do
  code=$(http POST /api/runs "$(launch_body "$sid" "$AGENT" '{"consent":{"simulation_authorized":true}}')")
  ec=$(err_code)
  if [ "$code" = 409 ] && [ "$ec" = PAYLOAD_NOT_STAGED ]; then
    say "$STAGE SKIP $sid baseline refusal: SimCore's payload shelf has not staged its tools, and the shelf check refuses before the agent posture gate runs"
    continue
  fi
  [ "$code" = 409 ] && [ "$ec" = AGENT_POSTURE_INSUFFICIENT ] \
    || fail "$sid against the baseline agent: expected 409 AGENT_POSTURE_INSUFFICIENT, got HTTP $code $ec: $(body | head -c 400)"
  sat=$(jq -r '.detail.detail.satisfying_profile // .detail.satisfying_profile // empty' "$WORK/body")
  observe "baseline $sid refused AGENT_POSTURE_INSUFFICIENT (satisfying_profile=${sat:-none}): $(jq -r '.detail.error // .error' "$WORK/body" | head -c 300)"
  POSTURE_REFUSED="$POSTURE_REFUSED $sid:${sat:-none}"
done
[ -n "$POSTURE_REFUSED" ] || fail "no POSTURE_SCENARIOS entry ($POSTURE_SCENARIOS) reached the agent posture gate"

# Candidates: CDR, pull-capable, no step calls kubectl, no external tools
# (both read from the scenario detail; the list projects steps without
# commands). The
# launch gate is the authority on posture and tools (cluster_posture is not on
# the list API and the tool scan is the engine's own); a gate refusal skips to
# the next candidate, and each skip is printed.
http GET /api/scenarios >/dev/null
jq -r '.scenarios[]
  | select(.plane == "CDR" and .pull_supported == true and .status == "active")
  | .scenario_id' "$WORK/body" > "$WORK/candidates"
SCEN=""; RUN=""
while read -r sid; do
  code=$(http GET "/api/scenarios/$sid" )
  [ "$code" = 200 ] || continue
  [ "$(jq '.external_tools | length' "$WORK/body")" = 0 ] || continue
  calls_kubectl "$WORK/body" && continue
  code=$(http POST /api/runs "$(launch_body "$sid" "$AGENT")")
  if [ "$code" = 200 ] || [ "$code" = 201 ]; then
    SCEN=$sid; RUN=$(jq -r '.run_id' "$WORK/body"); break
  fi
  observe "candidate $sid skipped: HTTP $code $(err_code)"
done < "$WORK/candidates"
[ -n "$SCEN" ] || fail "no CDR scenario qualifies (no kubectl, no external tools, accepted by the launch gate)"
n_steps=$( (http GET "/api/scenarios/$SCEN" >/dev/null; jq '.steps | length' "$WORK/body") )
status=$(wait_run "$RUN" 300)
http GET "/api/runs/$RUN" >/dev/null
out=$(jq -r '.output // ""' "$WORK/body")
observe "run $RUN ($SCEN) status=$status summary: $(printf '%s' "$out" | sed -n '/COMPLETION SUMMARY/,$p' | tr '\n' ' ' | head -c 500)"
missing=""
for sid in $( (http GET "/api/scenarios/$SCEN" >/dev/null; jq -r '.steps[].id' "$WORK/body") ); do
  grep -qF -- "· $sid ·" <<<"$out" || missing="$missing $sid"
done
[ -n "$out" ] || fail "run $RUN ($SCEN) recorded no output"
[ -z "$missing" ] || fail "run $RUN ($SCEN) output names no output for step(s):$missing"
case "$status" in complete|completed) ;; *) fail "run $RUN ($SCEN) ended '$status', not complete" ;; esac
observe "zombies in the pod after $SCEN completed: $(pexec "$ZOMBIE_SCRIPT" | tr '\n' ' ')"
ok "$SCEN run $RUN complete, output for all $n_steps steps; refused on baseline with AGENT_POSTURE_INSUFFICIENT:$POSTURE_REFUSED"

# ---------------------------------------------------------------------------
# 7. abort
# ---------------------------------------------------------------------------
STAGE="abort"
http GET "/api/scenarios/$SCEN" >/dev/null
draft=$(jq -c '{
  name: "kind e2e abort probe",
  plane: "CDR",
  uc_ref: .uc_ref, tc_ref: .tc_ref, tc_refs: [.tc_ref],
  cgo_anchor: {image_name: "apache2"},
  steps: [{id: "step-01", name: "long sleep", identity: "direct",
           command: "echo e2e-abort-started; sleep 300; echo e2e-abort-NOT-aborted",
           expected_detections: [{plane: "CDR", type: "BIOC", description: "abort probe"}]}]
}' "$WORK/body")
code=$(http POST /api/scenarios/drafts "$draft")
[ "$code" = 201 ] || fail "draft create returned HTTP $code: $(body | head -c 400)"
DRAFT=$(jq -r '.scenario_id' "$WORK/body")
code=$(http POST /api/runs "$(launch_body "$DRAFT" "$AGENT")")
[ "$code" = 200 ] || [ "$code" = 201 ] || fail "draft launch returned HTTP $code $(err_code): $(body | head -c 400)"
ARUN=$(jq -r '.run_id' "$WORK/body")
# Mid-step is proven from the pod, not from streamed output: the step's
# `sleep 300` must be a live process before the abort is sent.
started=0
for _ in $(seq 1 90); do
  if pexec "$PS_SCRIPT" | grep -q 'cmd=sleep 300'; then started=1; break; fi
  sleep 1
done
[ "$started" = 1 ] || fail "draft run $ARUN: no 'sleep 300' process appeared in $POD within 90s"
procs=$(pexec "$PS_SCRIPT")
observe "processes mid-step (cgo_anchor apache2 requested): $(tr '\n' ' ' <<<"$procs")"
# The anchor rename: a process whose /proc/<pid>/cmdline starts with the
# anchor image name must exist while the step runs. sleep 300 is running (just
# proven), so its absence has exactly one signature.
if grep -qE '(^|; )pid=[0-9]+ ppid=[0-9]+ cmd=apache2( |;)' <<<"$procs"; then
  observe "anchor rename present: $(grep -oE 'pid=[0-9]+ ppid=[0-9]+ cmd=apache2 ' <<<"$procs" | tr '\n' ' ')"
else
  defect "cgo_anchor apache2 requested on debian (sh is dash, renamed) but no process cmdline starts with 'apache2' while the step runs: $(tr '\n' ' ' <<<"$procs" | head -c 600)"
fi
http GET "/api/runs/$ARUN" >/dev/null
observe "output SimCore holds for $ARUN mid-step: $(jq -c '.output' "$WORK/body" | head -c 200)"
code=$(http POST "/api/runs/$ARUN/abort")
[ "$code" = 200 ] || fail "abort returned HTTP $code: $(body | head -c 300)"
status=$(wait_run "$ARUN" 60)
[ "$status" = aborted ] || fail "run $ARUN is '$status' after abort, not aborted"
# "Gone" needs a sample that actually came from the pod: one carrying PID 1's
# line. A failed `kubectl exec` prints an error, matches no step process, and
# would otherwise pass; it keeps polling and the stage fails on timeout.
gone=0
for _ in $(seq 1 30); do
  sample=$(pexec "$PS_SCRIPT")
  if grep -qE '(^|; )pid=1 ' <<<"$sample" \
     && ! grep -qE 'cmd=(sleep 300|apache2)( |;)' <<<"$sample"; then gone=1; break; fi
  sleep 1
done
[ "$gone" = 1 ] || fail "run $ARUN reads aborted but its step is still alive in $POD after 30s: $(pexec "$PS_SCRIPT" | tr '\n' ' ')"
observe "processes after abort: $(pexec "$PS_SCRIPT" | tr '\n' ' ')"
ok "draft run $ARUN aborted mid-step; its process group is gone from the pod"

# ---------------------------------------------------------------------------
# 8. cluster-privilege
# ---------------------------------------------------------------------------
STAGE="cluster-privilege"
fetch_manifest "profile=cluster-privilege&cluster_privilege_authorized=true&authorized_by=e2e" "$WORK/cp.yaml"
reapply "$WORK/cp.yaml"
rollout
POD=$(current_pod)
wait_agent "$POD"
prof=$(jq -r '.runtime_facts.posture_profile' <<<"$AGENT_ROW")
[ "$prof" = cluster-privilege ] || fail "re-applied agent reports posture_profile=$prof"
accepted=""
for pair in $POSTURE_REFUSED; do
  sid=${pair%%:*}; sat=${pair#*:}
  [ "$sat" = cluster-privilege ] || { observe "$sid needs profile '$sat', not cluster-privilege; not launched here"; continue; }
  code=$(http POST /api/runs "$(launch_body "$sid" "$AGENT" '{"consent":{"simulation_authorized":true}}')")
  [ "$code" = 200 ] || [ "$code" = 201 ] \
    || fail "$sid against the cluster-privilege agent: HTTP $code $(err_code): $(body | head -c 400)"
  CRUN=$(jq -r '.run_id' "$WORK/body")
  # Acceptance is the assertion; the run itself may need minutes (SIM-CDR-015
  # sleeps), so it is observed for 120 s and aborted if still going.
  st=$(wait_run "$CRUN" 120)
  if [ "$st" = timeout ]; then http POST "/api/runs/$CRUN/abort" >/dev/null; st="still running at 120s, aborted ($(wait_run "$CRUN" 60))"; fi
  http GET "/api/runs/$CRUN" >/dev/null
  observe "$sid run $CRUN on cluster-privilege agent ended $st: $(jq -r '.output // ""' "$WORK/body" | sed -n '/COMPLETION SUMMARY/,$p' | tr '\n' ' ' | head -c 400)"
  accepted="$accepted $sid"
done
[ -n "$accepted" ] || fail "no scenario refused on baseline names cluster-privilege as its satisfying profile"
ok "accepted on agent $AGENT (posture_profile=cluster-privilege):$accepted"

# ---------------------------------------------------------------------------
# 8b. downgrade
# ---------------------------------------------------------------------------
STAGE="downgrade"
# Re-applying baseline over the cluster-privilege agent must take the cluster
# grant away, not leave it behind for the new pod's ServiceAccount.
fetch_manifest "" "$WORK/down.yaml"
pod_names > "$WORK/before"
reapply "$WORK/down.yaml"
rollout
wait_new_pod "$WORK/before"
POD=$NPOD
rules=$(k get clusterrole cortexsim-agent -o jsonpath='{.rules}' 2>&1) \
  || fail "clusterrole/cortexsim-agent is not readable after the downgrade: $rules"
case "$rules" in ""|"[]"|"null") ;; *) fail "clusterrole/cortexsim-agent still grants rules after the baseline re-apply: $rules" ;; esac
cani=$(pexec '/cortexsim/bin/kubectl auth can-i list secrets --all-namespaces' | head -1 | tr -d '[:space:]')
[ "$cani" = no ] || fail "from pod $POD, 'kubectl auth can-i list secrets --all-namespaces' answered '$cani', want no"
wait_agent "$POD"
prof=$(jq -r '.runtime_facts.posture_profile' <<<"$AGENT_ROW")
[ "$prof" = baseline ] || fail "downgraded agent reports posture_profile=$prof, want baseline"
ok "baseline re-applied over cluster-privilege: clusterrole/cortexsim-agent rules=${rules:-<empty>}; pod $POD can-i list secrets -A = no; agent $AGENT posture_profile=baseline"

# ---------------------------------------------------------------------------
# 9a. digest mismatch
# ---------------------------------------------------------------------------
STAGE="digest-mismatch"
fetch_manifest "" "$WORK/flip.yaml"
digest=$(curl -sSf --max-time 30 "$SIMCORE_API/api/agents/binary/sha256?os=linux&arch=$ARCH" | grep -oE '[0-9a-f]{64}' | head -1)
{ [ -n "$digest" ] && grep -q "$digest" "$WORK/flip.yaml"; } \
  || fail "the served beacon digest '${digest:-none}' is not in the manifest"
first=${digest:0:1}; [ "$first" = 0 ] && repl=1 || repl=0
flipped="${repl}${digest:1}"
sed -i.bak "s/$digest/$flipped/" "$WORK/flip.yaml"
grep -q "$flipped" "$WORK/flip.yaml" || fail "digest flip did not land in the manifest copy"
pod_names > "$WORK/before"
reapply "$WORK/flip.yaml"
wait_new_pod "$WORK/before"
seen=""
for _ in $(seq 1 60); do
  seen=$(k -n "$NS" get pod "$NPOD" -o json | jq -r '
    [.status.initContainerStatuses[]? | select(.name == "fetch")
     | (.state.terminated // .lastState.terminated) | select(. != null)
     | "\(.exitCode) \(.message // "" | gsub("\\s+$"; ""))"] | first // empty')
  [ -n "$seen" ] && break
  sleep 3
done
[ "$seen" = "78 BEACON_DIGEST_MISMATCH" ] \
  || fail "fetch init container: expected '78 BEACON_DIGEST_MISMATCH', saw '${seen:-nothing in 180s}'"
ok "new pod $NPOD: fetch init container exited 78 with BEACON_DIGEST_MISMATCH"

# ---------------------------------------------------------------------------
# 9b. alpine
# ---------------------------------------------------------------------------
STAGE="alpine"
# prepare=packages: apk installs bash and runuser and creates the accounts.
fetch_manifest "image=alpine:3.20" "$WORK/alpine.yaml"
pod_names > "$WORK/before"
reapply "$WORK/alpine.yaml"
rollout
wait_new_pod "$WORK/before"
POD=$NPOD
wait_agent "$POD"
observe "alpine+packages runtime_facts: $(jq -c '.runtime_facts | {posture_profile, prepare_status, tools}' <<<"$AGENT_ROW")"
pstat=$(pexec 'cat /cortexsim/prepare.status' | tr '\n' ' ' | sed 's/ *$//')
[ "$pstat" = PREPARE_OK ] || fail "alpine+packages prepare.status is '$pstat', not PREPARE_OK"
wd=$(pexec 'id -u www-data && echo rc=0 || echo rc=1' | tr '\n' ' ')
grep -q 'rc=0' <<<"$wd" || fail "alpine+packages: id -u www-data failed: $wd"
observe "alpine+packages unknown user: $(pexec 'id -u no-such-user-xyz; echo rc=$?' | tr '\n' ' ')"
observe "alpine+packages runuser/bash: $(pexec 'command -v runuser; command -v bash; echo rc=$?' | tr '\n' ' ')"
ok "alpine:3.20 prepare=packages enrolled; prepare.status=PREPARE_OK; id -u www-data -> $wd"

# prepare=none: no bash, so the anchor cannot be honoured. The pod must still
# enroll, and the run must say so.
fetch_manifest "image=alpine:3.20&prepare=none" "$WORK/alpine-none.yaml"
pod_names > "$WORK/before"
reapply "$WORK/alpine-none.yaml"
rollout
wait_new_pod "$WORK/before"
POD=$NPOD
wait_agent "$POD"
observe "alpine prepare=none runtime_facts: $(jq -c '.runtime_facts | {posture_profile, prepare_status, tools}' <<<"$AGENT_ROW")"
http GET /api/scenarios >/dev/null
jq -r '.scenarios[]
  | select(.pull_supported == true and .status == "active" and .cgo_anchor != null)
  | select(.plane == "CDR") | .scenario_id' "$WORK/body" > "$WORK/anchor-candidates"
ANS=""; BRUN=""
while read -r sid; do
  code=$(http GET "/api/scenarios/$sid")
  [ "$code" = 200 ] || continue
  [ "$(jq '.external_tools | length' "$WORK/body")" = 0 ] || continue
  calls_kubectl "$WORK/body" && continue
  code=$(http POST /api/runs "$(launch_body "$sid" "$AGENT")")
  if [ "$code" = 200 ] || [ "$code" = 201 ]; then ANS=$sid; BRUN=$(jq -r '.run_id' "$WORK/body"); break; fi
  observe "anchor candidate $sid skipped: HTTP $code $(err_code)"
done < "$WORK/anchor-candidates"
[ -n "$ANS" ] || fail "no cgo_anchor scenario is accepted by the gate on the alpine prepare=none agent"
status=$(wait_run "$BRUN" 300)
http GET "/api/runs/$BRUN" >/dev/null
out=$(jq -r '.output // ""' "$WORK/body")
summary=$(sed -n '/COMPLETION SUMMARY/,$p' <<<"$out")
observe "run $BRUN ($ANS) status=$status summary: $(tr '\n' ' ' <<<"$summary" | head -c 700)"
case "$status" in complete|completed) ;; *) fail "run $BRUN ($ANS) ended '$status', not complete" ;; esac
grep -q 'ANCHOR NOT HONOURED' <<<"${out%%--- COMPLETION SUMMARY*}" || fail "run $BRUN ($ANS) step output has no ANCHOR NOT HONOURED line"
grep -q 'ANCHOR NOT HONOURED' <<<"$summary" || fail "run $BRUN ($ANS) completion summary has no ANCHOR NOT HONOURED"
ok "$ANS on alpine prepare=none (no bash) enrolled, completed, and recorded ANCHOR NOT HONOURED in output and summary"

# ---------------------------------------------------------------------------
# 9c. enroll denied
# ---------------------------------------------------------------------------
STAGE="enroll-denied"
# A garbage token in the Secret, then a fresh pod: it must fail to enroll and
# say why in its termination message. Then the real token goes back and a
# fresh pod enrolls; that agent is the one the offline stage watches.
put_secret() { # TOKEN_FILE
  k -n "$NS" create secret generic cortexsim-enroll \
    --from-file=server="$WORK/server" --from-file=token="$1" --dry-run=client -o yaml \
    | k apply -f - >/dev/null
}
printf 'cxs_e2e-not-a-real-token' > "$WORK/bad-token"
put_secret "$WORK/bad-token"
pod_names > "$WORK/before"
k -n "$NS" delete pod "$POD" --wait=false >/dev/null
wait_new_pod "$WORK/before"
msg=""
for _ in $(seq 1 60); do
  msg=$(k -n "$NS" get pod "$NPOD" -o json | jq -r '[.status.containerStatuses[]? | select(.name == "agent")
        | (.lastState.terminated // .state.terminated) | select(. != null) | .message // ""] | first // empty')
  [ -n "$msg" ] && break
  sleep 3
done
grep -q 'ENROLL_DENIED' <<<"$msg" || fail "pod $NPOD with a garbage token: last termination message is '${msg:-none in 180s}', want ENROLL_DENIED"
k -n "$NS" describe pod "$NPOD" | grep -q 'FATAL ENROLL_DENIED' \
  || fail "kubectl describe pod $NPOD does not show the FATAL ENROLL_DENIED reason"
observe "termination message of $NPOD: $(redact <<<"$msg" | tr '\n' ' ' | head -c 300)"
put_secret "$WORK/token"
pod_names > "$WORK/before"
k -n "$NS" delete pod "$NPOD" --wait=false >/dev/null
rollout
wait_new_pod "$WORK/before"
POD=$NPOD
wait_agent "$POD"
ok "garbage token -> termination message carries ENROLL_DENIED (and describe shows it); real token restored, pod $POD enrolled as $AGENT"

# ---------------------------------------------------------------------------
# 10. teardown
# ---------------------------------------------------------------------------
STAGE="offline"
POD=$(current_pod)
[ -n "$POD" ] || fail "no Ready agent pod before the namespace is deleted"
wait_agent "$POD"
observe "agent $AGENT of running pod $POD is online immediately before the namespace is deleted"
k delete namespace "$NS" --wait=true --timeout=180s >/dev/null
gone=""
for _ in $(seq 1 $((OFFLINE_TIMEOUT / 10))); do
  http GET /api/agents >/dev/null
  gone=$(jq -r --arg a "$AGENT" '.agents[] | select((.agent_id // .id) == $a) | .status' "$WORK/body")
  [ "$gone" = offline ] && break
  sleep 10
done
[ "$gone" = offline ] || fail "agent $AGENT is '$gone' ${OFFLINE_TIMEOUT}s after namespace deletion, not offline"
ok "namespace deleted; agent $AGENT went offline"
for o in clusterrole/cortexsim-agent clusterrolebinding/cortexsim-agent clusterrole/cortexsim-reaper-agent clusterrolebinding/cortexsim-reaper-agent; do
  if k get "$o" >/dev/null 2>&1; then observe "$o still exists after the namespace was deleted"
  else observe "$o is gone after the namespace was deleted"; fi
done
if [ "$DEFECTS" -gt 0 ]; then
  FAILED=1
  say "RESULT FAIL all stages ran; $DEFECTS product defect(s) recorded (DEFECT lines above)"
  exit 2
fi
say "RESULT PASS all stages passed (this proves the pod runs and reports; no Cortex detection is proven)"
