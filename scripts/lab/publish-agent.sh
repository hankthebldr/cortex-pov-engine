#!/usr/bin/env bash
# ==============================================================================
# publish-agent.sh — build the beacon for the lab and publish a one-line installer.
#
# Run this ON THE CONTROL PLANE (the machine running SimCore). It:
#   1. Cross-compiles the Go beacon for linux/amd64 + linux/arm64 (CGO_ENABLED=0,
#      stdlib-only → a static ELF that runs on any glibc/musl victim, no Go needed
#      on the target).
#   2. Generates `enroll.sh` with the control-plane URL BAKED IN — the fix for the
#      "can't phone home" failure, where an installer fetched over localhost bakes
#      SERVER=http://localhost:8888 and the victim then dials itself.
#   3. Publishes all three under dist/agent/, which SimCore serves at /agent/.
#
# Then each victim is one line, with no Go and no scp:
#
#   curl -fsSL http://<cp>:8888/agent/enroll.sh | CORTEXSIM_TOKEN=cxs_… bash
#
# The enrollment token is NEVER written into enroll.sh. /agent/enroll.sh is a
# static file SimCore serves to anyone who can reach the port, with no
# authentication, and a token can be valid for 1000 uses and 30 days. Each
# victim passes its token at run time, as above. --token is refused.
#
# Usage:
#   scripts/lab/publish-agent.sh                        # auto-detect LAN IP
#   scripts/lab/publish-agent.sh --server http://192.168.1.50:8888
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../.." >/dev/null 2>&1 && pwd)"
cd "${REPO_ROOT}"

log() { printf '\033[0;36m[publish-agent]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[publish-agent]\033[0m %s\n' "$*"; }
err() { printf '\033[0;31m[publish-agent] ERROR:\033[0m %s\n' "$*" >&2; }

SERVER=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --server) SERVER="$2"; shift 2 ;;
    --token|--token=*)
      # Refused before anything is built or written, and the value is not echoed.
      err "--token is no longer accepted: it wrote the enrollment token into"
      err "dist/agent/enroll.sh, which SimCore serves without authentication at"
      err "/agent/enroll.sh. Anyone who could fetch that file could enroll agents"
      err "for as long as the token lived."
      err "Pass the token on each victim at run time instead:"
      err "    curl -fsSL <server>/agent/enroll.sh | CORTEXSIM_TOKEN=cxs_... bash"
      err "If a token was baked by an earlier run, revoke it"
      err "(DELETE /api/agents/enroll/tokens/<id>) and re-run this script without"
      err "--token: that rewrites enroll.sh without it."
      exit 2 ;;
    -h|--help) sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) printf 'unknown flag: %s\n' "$1" >&2; exit 2 ;;
  esac
done

# ---------------------------------------------------------------------------
# 1. Resolve the control-plane URL victims will dial.
#    NEVER localhost/127.0.0.1 — on a victim that resolves to the victim itself,
#    which is exactly the phone-home failure this script exists to prevent.
# ---------------------------------------------------------------------------
# `|| true`: with no .env, sed fails, and under `set -euo pipefail` that used to
# end the script on this line with exit 1 and no message at all.
PORT="$(sed -n 's/^CORTEXSIM_PORT=\([0-9][0-9]*\).*/\1/p' "${REPO_ROOT}/.env" 2>/dev/null | head -n1 || true)"
PORT="${PORT:-8888}"

detect_ip() {
  # macOS (control plane is typically the MBP), then Linux, then hostname -I.
  if command -v ipconfig >/dev/null 2>&1; then
    for iface in en0 en1; do
      ip="$(ipconfig getifaddr "$iface" 2>/dev/null || true)"
      [[ -n "$ip" ]] && { printf '%s' "$ip"; return; }
    done
  fi
  if command -v ip >/dev/null 2>&1; then
    ip route get 1.1.1.1 2>/dev/null | sed -n 's/.*src \([0-9.]*\).*/\1/p' | head -n1 && return
  fi
  hostname -I 2>/dev/null | awk '{print $1}'
}

if [[ -z "${SERVER}" ]]; then
  IP="$(detect_ip || true)"
  [[ -n "${IP}" ]] || { err "Could not auto-detect a LAN IP. Pass --server http://<ip>:${PORT}"; exit 1; }
  SERVER="http://${IP}:${PORT}"
  log "Auto-detected control plane: ${SERVER}"
fi

case "${SERVER}" in
  *localhost*|*127.0.0.1*)
    err "Refusing to bake ${SERVER}: on a victim that points at the victim itself."
    err "Pass the control plane's LAN address, e.g. --server http://192.168.1.50:${PORT}"
    exit 1 ;;
esac

# A DNS name in SERVER must be one SimCore answers to. Under docker compose the
# container does not know this host's name, so SimCore refuses every name that
# is not in CORTEXSIM_ALLOWED_HOSTS with 400 HOST_NOT_ALLOWED, and
# `curl -fsSL .../agent/enroll.sh | bash` shows a victim only "curl: (22)".
# Read from .env, which is what compose reads; a heads-up, not a refusal,
# because a host-run SimCore admits its own hostname without being told.
server_host="${SERVER#*://}"
server_host="${server_host%%/*}"
case "${server_host}" in
  \[*) server_host="" ;;                      # [v6]:port, an IP literal
  *)   server_host="${server_host%%:*}" ;;
esac
server_host="$(printf '%s' "${server_host}" | tr '[:upper:]' '[:lower:]')"
if [[ -n "${server_host}" && ! "${server_host}" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  allowed="$(sed -n 's/^CORTEXSIM_ALLOWED_HOSTS=//p' "${REPO_ROOT}/.env" 2>/dev/null | tail -n1 | tr '[:upper:]' '[:lower:]' | tr -d ' "' || true)"
  admitted=0
  IFS=',' read -r -a patterns <<< "${allowed}"
  for pattern in ${patterns[@]+"${patterns[@]}"}; do
    case "${pattern}" in
      "*") admitted=1 ;;
      "*."*) if [[ "${server_host}" == *"${pattern#\*}" ]]; then admitted=1; fi ;;
      *) if [[ "${server_host}" == "${pattern}" ]]; then admitted=1; fi ;;
    esac
  done
  if (( ! admitted )); then
    warn "NOTE: '${server_host}' is not in CORTEXSIM_ALLOWED_HOSTS in ${REPO_ROOT}/.env."
    warn "A SimCore under docker compose refuses names it does not list (400"
    warn "HOST_NOT_ALLOWED), which a victim sees only as 'curl: (22)'. Add it and"
    warn "re-run scripts/dev-up.sh, or publish with --server http://<ip>:${PORT}."
  fi
fi

# ---------------------------------------------------------------------------
# 2. Cross-compile. Stdlib-only + CGO_ENABLED=0 → portable static ELF.
# ---------------------------------------------------------------------------
command -v go >/dev/null 2>&1 || { err "Go 1.21+ required on the control plane to build the beacon."; exit 1; }

OUT="${REPO_ROOT}/dist/agent"
mkdir -p "${OUT}"

for arch in amd64 arm64; do
  log "Building linux/${arch} …"
  ( cd "${REPO_ROOT}/agent" && CGO_ENABLED=0 GOOS=linux GOARCH="${arch}" \
      go build -trimpath -o "${OUT}/cortexsim-agent-${arch}" . )
done

# ---------------------------------------------------------------------------
# 3. Generate the victim-side installer with SERVER baked. Never a token: this
#    file is served without authentication.
# ---------------------------------------------------------------------------
cat > "${OUT}/enroll.sh" <<ENROLL
#!/usr/bin/env bash
# CortexSim victim enrollment — generated by scripts/lab/publish-agent.sh.
# No Go, no scp: downloads a static beacon, enrolls for a server-assigned id,
# and installs a systemd unit when it can.
#
#   curl -fsSL ${SERVER}/agent/enroll.sh | CORTEXSIM_TOKEN=cxs_… bash
set -euo pipefail

SERVER="\${CORTEXSIM_SERVER:-${SERVER}}"
TOKEN="\${CORTEXSIM_TOKEN:-}"
INTERVAL="\${CORTEXSIM_INTERVAL:-10}"

say() { printf '[cortexsim] %s\n' "\$*"; }
die() { printf '[cortexsim] ERROR: %s\n' "\$*" >&2; exit 1; }

[ -n "\${TOKEN}" ] || die "No enrollment token. Mint one on the control plane:
  curl -s -X POST \${SERVER}/api/agents/enroll/tokens -H 'Content-Type: application/json' \\\\
    -d '{\"label\":\"lab-victims\",\"ttl_seconds\":86400,\"max_uses\":25}'
then re-run with CORTEXSIM_TOKEN=cxs_…"

# --- reachability preflight: fail loudly HERE, not silently in the beacon loop.
# No -f on this curl: -f throws the response body away, and SimCore's Host
# guard puts both the reason and the fix in the body of its 400. With -f a
# server that had just answered was reported as unreachable.
say "control plane : \${SERVER}"
HEALTH="\$(curl -sS --max-time 5 "\${SERVER}/api/health" 2>/dev/null || true)"
case "\${HEALTH}" in
  *HOST_NOT_ALLOWED*)
    REFUSED="\${SERVER#*://}"; REFUSED="\${REFUSED%%[:/]*}"
    die "SimCore answered, and refused the name '\${REFUSED}' (400 HOST_NOT_ALLOWED).
  It answers to localhost, IP addresses and the names in CORTEXSIM_ALLOWED_HOSTS.
  - on the control plane: add '\${REFUSED}' to CORTEXSIM_ALLOWED_HOSTS in .env and
    re-run scripts/dev-up.sh
  - or point this installer at the control plane's IP address:
    CORTEXSIM_SERVER=http://<ip>:<port>" ;;
  *'"status"'*) ;;
  *)
    die "cannot reach \${SERVER}/api/health from \$(hostname).
  - is SimCore up on the control plane?
  - is the control-plane firewall allowing inbound \${SERVER##*:}?  (macOS blocks by default)
  - can this host route to it?   curl -v \${SERVER}/api/health" ;;
esac

# --- architecture
case "\$(uname -m)" in
  x86_64|amd64) ARCH=amd64 ;;
  aarch64|arm64) ARCH=arm64 ;;
  *) die "unsupported architecture: \$(uname -m)" ;;
esac

BIN_DIR=/usr/local/bin
[ -w "\${BIN_DIR}" ] 2>/dev/null || BIN_DIR="\${HOME}/.local/bin"
mkdir -p "\${BIN_DIR}"
BIN="\${BIN_DIR}/cortexsim-agent"

say "downloading beacon (linux/\${ARCH}) …"
curl -fsSL "\${SERVER}/agent/cortexsim-agent-\${ARCH}" -o "\${BIN}" \\
  || die "download failed: \${SERVER}/agent/cortexsim-agent-\${ARCH}"
chmod +x "\${BIN}"

# --- enroll: SimCore assigns the id; we never invent one
say "enrolling …"
RESP="\$(curl -fsSL -X POST "\${SERVER}/api/agents/enroll" \\
  -H 'Content-Type: application/json' \\
  -d "{\\"token\\":\\"\${TOKEN}\\",\\"hostname\\":\\"\$(hostname)\\",\\"os\\":\\"linux\\"}")" \\
  || die "enrollment rejected — token invalid, expired, revoked, or out of uses"

AGENT_ID="\$(printf '%s' "\${RESP}" | sed -n 's/.*"agent_id":[[:space:]]*"\\([^"]*\\)".*/\\1/p')"
[ -n "\${AGENT_ID}" ] || die "no agent_id in enroll response: \${RESP}"
say "enrolled as: \${AGENT_ID}"

# --- run: systemd when we can (survives reboot), else foreground
# /run/systemd/system is the canonical "systemd is PID 1" test. A systemctl
# binary alone is not enough — containers and WSL ship one that cannot
# reach a bus, and blindly calling it aborts the install under set -e.
if [ "\$(id -u)" = "0" ] && [ -d /run/systemd/system ] && command -v systemctl >/dev/null 2>&1; then
  cat > /etc/systemd/system/cortexsim-agent.service <<UNIT
[Unit]
Description=CortexSim beacon agent
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=\${BIN} --server \${SERVER} --id \${AGENT_ID} --interval \${INTERVAL}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
  if systemctl daemon-reload 2>/dev/null && systemctl enable --now cortexsim-agent 2>/dev/null; then
    say "installed + started systemd unit: cortexsim-agent"
    say "logs: journalctl -u cortexsim-agent -f"
  else
    say "systemd present but unusable — falling back to foreground"
    rm -f /etc/systemd/system/cortexsim-agent.service
    exec "\${BIN}" --server "\${SERVER}" --id "\${AGENT_ID}" --interval "\${INTERVAL}"
  fi
else
  say "no systemd/root — running in foreground (Ctrl-C to stop)"
  exec "\${BIN}" --server "\${SERVER}" --id "\${AGENT_ID}" --interval "\${INTERVAL}"
fi
ENROLL
chmod +x "${OUT}/enroll.sh"

# ---------------------------------------------------------------------------
# 4. Publish into the static mount for native mode. Docker mode picks the same
#    dist/agent up via the read-only bind mount in docker-compose.yml.
# ---------------------------------------------------------------------------
if [[ -d "${REPO_ROOT}/core/static" ]]; then
  mkdir -p "${REPO_ROOT}/core/static/agent"
  cp "${OUT}/cortexsim-agent-amd64" "${OUT}/cortexsim-agent-arm64" "${OUT}/enroll.sh" \
     "${REPO_ROOT}/core/static/agent/"
  log "Published into core/static/agent/ (native mode)."
  log "NOTE: a UI rebuild replaces core/static — re-run this script afterwards."
else
  log "core/static not present (Docker-only mode) — serving from the dist/agent bind mount."
fi

printf '\n  \033[0;32m✓ Beacon published.\033[0m  Enroll each victim with one line:\n\n'
printf '    curl -fsSL %s/agent/enroll.sh | CORTEXSIM_TOKEN=cxs_… bash\n\n' "${SERVER}"
printf '  Mint the token first:\n'
printf "    curl -s -X POST %s/api/agents/enroll/tokens -H 'Content-Type: application/json' \\\\\n" "${SERVER}"
printf '      -d '"'"'{"label":"lab-victims","ttl_seconds":86400,"max_uses":25}'"'"'\n\n'
