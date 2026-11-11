#!/usr/bin/env bash
# ==============================================================================
# dev-up.sh — THE canonical CortexSim bring-up for a jumpbox operator.
#
# This is the container path: the image bakes agent-dist/ + rust-dist/ +
# payloads/ at build time, so this script needs no Go/Rust/Node toolchain and
# no git submodules on the host — only docker. (install.sh is the OTHER path:
# a full from-source bootstrap for contributors who are building the toolchain
# itself. dev-up-native.sh is the fallback for hosts with no Docker daemon.)
#
# Idempotent + safe to re-run:
#   1. Prereq check: docker present and its daemon reachable, with an
#      actionable fix (not a raw docker error) when it isn't.
#   2. If ./.env is missing, generate one from .env.example with a freshly
#      generated CORTEXSIM_SECRET and CORTEXSIM_ENV=development. An existing
#      .env is never clobbered — your secret survives re-runs. Generating one
#      while the cortex-pov-engine_simcore_db volume already exists warns
#      loudly: the credentials in it were encrypted with the OLD secret.
#   2b. An existing .env is checked for the two things an upgrade leaves stale:
#      - no CORTEXSIM_ALLOWED_HOSTS: SimCore would refuse this host's own name
#        (400 HOST_NOT_ALLOWED), so a bare run starts compose with this host's
#        names for THIS bring-up and says the value is not in .env; --apply
#        writes that one line (previous file kept under .env-backups/).
#      - a CORTEXSIM_VERSION that is not this tree's version: named before the
#        build re-tags new code under the old version. Never rewritten.
#   3. `docker compose up -d --build`.
#   4. Poll http://localhost:${CORTEXSIM_PORT}/api/health until it responds.
#      {"status":"degraded"} still counts as a successful bring-up (the app IS
#      up) and prints which component(s) are degraded and why, rather than
#      silently spinning for 90s and reporting a bare timeout for a server
#      that came up fine. Expect degraded on EVERY bring-up today: the
#      api_auth component reports API_UNAUTHENTICATED until SimCore has API
#      authentication (SEC-01). {"status":"ok"} prints success. Only a genuine
#      non-response times out as an actual failure.
#
# Usage:  scripts/dev-up.sh [--dev] [--apply]
#   --dev     overlay docker-compose.dev.yml (mounts core/, uvicorn --reload)
#   --apply   write the .env repair step 2b would otherwise apply to this
#             bring-up only (today: the CORTEXSIM_ALLOWED_HOSTS line). The
#             previous .env is kept under .env-backups/<UTC timestamp>/.env.
#             Without it, an existing .env is never written.
# ==============================================================================
set -euo pipefail

# Resolve repo root from this script's location so it runs from anywhere.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." >/dev/null 2>&1 && pwd)"
cd "${REPO_ROOT}"

ENV_FILE="${REPO_ROOT}/.env"
ENV_EXAMPLE="${REPO_ROOT}/.env.example"

log() { printf '\033[0;36m[dev-up]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[dev-up]\033[0m %s\n' "$*"; }
err() { printf '\033[0;31m[dev-up] ERROR:\033[0m %s\n' "$*" >&2; }

# Arguments are parsed before anything is started. An unrecognised one is
# refused: it used to be ignored, so a typo'd --apply read as having applied.
DEV_OVERLAY=0
APPLY=0
for arg in "$@"; do
  case "${arg}" in
    --dev|dev) DEV_OVERLAY=1 ;;
    --apply)   APPLY=1 ;;
    -h|--help)
      printf 'Usage: scripts/dev-up.sh [--dev] [--apply]\n'
      printf '  --dev     overlay docker-compose.dev.yml (mounts core/, uvicorn --reload)\n'
      printf '  --apply   write the missing CORTEXSIM_ALLOWED_HOSTS line to an existing .env\n'
      printf '            (the previous file is kept under .env-backups/)\n'
      exit 0 ;;
    *)
      err "unknown argument: ${arg}"
      err "Usage: scripts/dev-up.sh [--dev] [--apply]"
      exit 2 ;;
  esac
done

# Lines repeated after the bring-up. The image build prints hundreds of lines
# between a warning raised here and the moment the script returns.
REMINDERS=()

# ---------------------------------------------------------------------------
# 0. Prereqs: docker present, its daemon reachable. Fail fast with the exact
#    fix rather than letting `docker compose up` surface a raw connection
#    error 90 seconds into a build. The daemon-unreachable branch specifically
#    covers the Docker-Desktop-hijacked-the-active-context trap: `docker
#    context ls` still shows a working context, just not the current one.
# ---------------------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
  err "docker not found on PATH."
  err "Install it:   curl -fsSL https://get.docker.com | sudo sh"
  err "(or use the full source bootstrap instead: ./install.sh)"
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  err "docker is installed but its daemon is not reachable from context '$(docker context show 2>/dev/null || echo unknown)'."
  alt=""
  while IFS= read -r ctx; do
    if DOCKER_CONTEXT="${ctx}" docker info >/dev/null 2>&1; then
      alt="${ctx}"
      break
    fi
  done < <(docker context ls --format '{{.Name}}' 2>/dev/null || true)
  if [[ -n "${alt}" ]]; then
    err "Context '${alt}' IS reachable. Fix:   export DOCKER_CONTEXT=${alt}"
  else
    err "Start the daemon, e.g.:   sudo systemctl start docker"
  fi
  exit 1
fi
log "docker: $(docker --version) — daemon reachable."


# This host's short and fully qualified names, lower-cased, deduplicated,
# comma-joined; anything that is not a plain DNS label sequence is dropped.
seed_allowed_hosts() {
  local h out=""
  for h in "$(hostname -s 2>/dev/null)" "$(hostname -f 2>/dev/null)" "$(hostname 2>/dev/null)"; do
    h="$(printf '%s' "${h}" | tr '[:upper:]' '[:lower:]')"
    case "${h}" in
      ""|localhost|localhost.*|*[!a-z0-9.-]*) continue ;;
    esac
    case ",${out}," in *",${h},"*) continue ;; esac
    out="${out:+${out},}${h}"
  done
  printf '%s' "${out}"
}

# Print the value of KEY's last assignment in .env. Returns 1 when the file has
# no such line, so "absent" and "present but empty" stay distinguishable.
# Quotes are stripped; on an unquoted value so are an inline comment and
# trailing blanks, which is how compose reads the file.
env_file_value() {
  local key="$1" line
  line="$(grep -E "^[[:space:]]*(export[[:space:]]+)?${key}=" "${ENV_FILE}" 2>/dev/null | tail -n1 || true)"
  [[ -n "${line}" ]] || return 1
  line="${line#*=}"
  line="${line%$'\r'}"
  case "${line}" in
    \"*\") line="${line#\"}"; line="${line%\"}" ;;
    \'*\') line="${line#\'}"; line="${line%\'}" ;;
    *)
      line="${line%%[[:space:]]#*}"
      line="${line%"${line##*[![:space:]]}"}"
      ;;
  esac
  printf '%s' "${line}"
}

# Copy .env aside before --apply writes it. The path ends in a file named
# ".env" on purpose: .gitignore already ignores that name at any depth, so the
# copy (which holds the master secret) cannot be staged by accident.
backup_env_file() {
  local stamp dir
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  dir="${REPO_ROOT}/.env-backups/${stamp}"
  mkdir -p "${dir}"
  chmod 700 "${REPO_ROOT}/.env-backups" "${dir}" 2>/dev/null || true
  cp -p "${ENV_FILE}" "${dir}/.env"
  chmod 600 "${dir}/.env" 2>/dev/null || true
  printf '%s' ".env-backups/${stamp}/.env"
}

# Write CORTEXSIM_ALLOWED_HOSTS=<value> into an existing .env: fill the last
# (empty) assignment in place when there is one, otherwise append. No other
# line is rewritten, the file's mode is kept, and the move is atomic.
persist_allowed_hosts() {
  local value="$1" tmp last
  tmp="${ENV_FILE}.tmp.$$"
  cp -p "${ENV_FILE}" "${tmp}"
  last="$(grep -nE '^[[:space:]]*(export[[:space:]]+)?CORTEXSIM_ALLOWED_HOSTS=' "${ENV_FILE}" 2>/dev/null | tail -n1 | cut -d: -f1 || true)"
  if [[ -n "${last}" ]]; then
    # value comes from seed_allowed_hosts: [a-z0-9.,-] only, so it carries no
    # character awk treats specially in -v or in a sub() replacement.
    awk -v n="${last}" -v v="${value}" '
      NR == n { sub(/CORTEXSIM_ALLOWED_HOSTS=.*/, "CORTEXSIM_ALLOWED_HOSTS=" v) }
      { print }
    ' "${ENV_FILE}" > "${tmp}"
  else
    {
      cat "${ENV_FILE}"
      # A last line without a newline would otherwise be glued to ours.
      [[ -z "$(tail -c1 "${ENV_FILE}")" ]] || printf '\n'
      printf '# Added by scripts/dev-up.sh --apply: names SimCore answers to besides\n'
      printf '# localhost and IP addresses (Host guard, 400 HOST_NOT_ALLOWED otherwise).\n'
      printf 'CORTEXSIM_ALLOWED_HOSTS=%s\n' "${value}"
    } > "${tmp}"
  fi
  mv "${tmp}" "${ENV_FILE}"
}

# An EXISTING .env that admits no host name. dev-up.sh seeds the variable only
# when it generates .env, so every deployment that already had one (an upgrade,
# a worktree reusing the root .env) booted with an empty allowlist: inside the
# container gethostname() is the container id, this host's own name was refused
# with 400 HOST_NOT_ALLOWED, every beacon started as --server http://<name>:port
# went offline, and this script printed nothing.
check_allowed_hosts() {
  # The shell wins over .env in compose's ${VAR:-} interpolation. A value the
  # operator exported is theirs, the empty string included.
  if [[ -n "${CORTEXSIM_ALLOWED_HOSTS+set}" ]]; then
    if [[ -z "${CORTEXSIM_ALLOWED_HOSTS}" ]]; then
      log "CORTEXSIM_ALLOWED_HOSTS is exported empty in this shell, which overrides .env:"
      log "SimCore will answer by localhost and IP address only."
    else
      log "CORTEXSIM_ALLOWED_HOSTS=${CORTEXSIM_ALLOWED_HOSTS} comes from this shell and overrides .env for this bring-up."
    fi
    return 0
  fi

  local current seed backup
  current="$(env_file_value CORTEXSIM_ALLOWED_HOSTS || true)"
  [[ -z "${current}" ]] || return 0

  seed="$(seed_allowed_hosts)"
  if [[ -z "${seed}" ]]; then
    warn "================================================================"
    warn "WARNING: ${ENV_FILE} sets no CORTEXSIM_ALLOWED_HOSTS, and this host's"
    warn "name could not be read to supply one. SimCore will answer by localhost"
    warn "and IP address only: a beacon or browser that uses a DNS name gets"
    warn "400 HOST_NOT_ALLOWED. If any name is used to reach this host, add"
    warn "    CORTEXSIM_ALLOWED_HOSTS=<name>[,<name>...]"
    warn "to ${ENV_FILE} and re-run."
    warn "================================================================"
    REMINDERS+=("CORTEXSIM_ALLOWED_HOSTS is empty: SimCore answers by localhost and IP address only (400 HOST_NOT_ALLOWED for any DNS name). Set it in .env if a name is used.")
    return 0
  fi

  if (( APPLY )); then
    backup="$(backup_env_file)"
    persist_allowed_hosts "${seed}"
    log "--apply: wrote CORTEXSIM_ALLOWED_HOSTS=${seed} to ${ENV_FILE}."
    log "         No other line was changed. Previous file: ${backup} (mode 600; it holds your secret)."
    log "         Add any other name beacons use to reach this host (CNAME, /etc/hosts alias, Tailscale name)."
    return 0
  fi

  warn "================================================================"
  warn "WARNING: ${ENV_FILE} sets no CORTEXSIM_ALLOWED_HOSTS."
  warn "SimCore answers to localhost, IP addresses and the names in that"
  warn "variable. Inside the container this host's own name is not known, so"
  warn "without it every beacon started as --server http://<this-host-name>:<port>"
  warn "and every console session opened by name gets 400 HOST_NOT_ALLOWED,"
  warn "and the fleet reads offline."
  warn "For THIS bring-up only, compose is started with:"
  warn "    CORTEXSIM_ALLOWED_HOSTS=${seed}"
  warn "That value is NOT in .env: a later plain 'docker compose up' drops it."
  warn "  - To persist it:  scripts/dev-up.sh --apply"
  warn "    (adds that one line to .env; the previous file is kept under .env-backups/)"
  warn "  - Or add the line yourself, with any other name beacons use (CNAME,"
  warn "    /etc/hosts alias, Tailscale name)."
  warn "  - To answer by IP address only on purpose, export it empty:"
  warn "    CORTEXSIM_ALLOWED_HOSTS= scripts/dev-up.sh"
  warn "================================================================"
  export CORTEXSIM_ALLOWED_HOSTS="${seed}"
  REMINDERS+=("CORTEXSIM_ALLOWED_HOSTS=${seed} was passed to compose for this bring-up only. It is not in .env, so a plain 'docker compose up' drops it and this host's name is refused (400 HOST_NOT_ALLOWED). Persist it: scripts/dev-up.sh --apply")
}

# The version this checkout's code reports at /api/health.
tree_version() {
  sed -n 's/^APP_VERSION[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' \
    "${REPO_ROOT}/core/api/health.py" 2>/dev/null | head -n1
}

# The version docker-compose.yml falls back to when nothing sets CORTEXSIM_VERSION.
compose_default_version() {
  sed -n 's/.*image:.*\${CORTEXSIM_VERSION:-\([^}]*\)}.*/\1/p' \
    "${REPO_ROOT}/docker-compose.yml" 2>/dev/null | head -n1
}

# ---------------------------------------------------------------------------
# 1. Ensure .env exists with a real secret (development mode).
# ---------------------------------------------------------------------------
gen_secret() {
  # Prefer python's secrets; fall back to openssl. Either yields >= 32 bytes
  # of high entropy, which satisfies the production master-key guard too.
  if command -v python3 >/dev/null 2>&1; then
    python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
  elif command -v openssl >/dev/null 2>&1; then
    openssl rand -base64 48 | tr -d '\n'
  else
    err "Need python3 or openssl to generate CORTEXSIM_SECRET. Install one and re-run."
    exit 1
  fi
}

ENV_PREEXISTING=0
if [[ -f "${ENV_FILE}" ]]; then
  ENV_PREEXISTING=1
  if (( APPLY )); then
    log ".env already present — keeping it; --apply adds only a missing CORTEXSIM_ALLOWED_HOSTS."
  else
    log ".env already present — leaving it untouched."
  fi
else
  if [[ ! -f "${ENV_EXAMPLE}" ]]; then
    err ".env.example not found at ${ENV_EXAMPLE} — cannot bootstrap .env."
    exit 1
  fi
  log "No .env found — generating one from .env.example (development mode)."

  # A surviving data volume holds every stored credential encrypted under the
  # PREVIOUS .env's CORTEXSIM_SECRET. A freshly generated secret cannot open
  # them, and nothing else will say so until a tenant test fails in front of
  # the customer. Read-only check; warn, do not stop (starting fresh is a
  # legitimate choice — it just has to be a conscious one).
  DB_VOLUME="cortex-pov-engine_simcore_db"
  if command -v docker >/dev/null 2>&1 && docker volume inspect "${DB_VOLUME}" >/dev/null 2>&1; then
    warn "================================================================"
    warn "WARNING: docker volume '${DB_VOLUME}' already exists, and it is"
    warn "about to be paired with a NEWLY GENERATED CORTEXSIM_SECRET."
    warn "Every credential stored in it (customer tenant API keys) was"
    warn "encrypted with the OLD secret and will be unreadable; SimCore will"
    warn "report it as MASTER_KEY_MISMATCH in /api/health."
    warn "  - To keep them: restore the old .env (or put the old"
    warn "    CORTEXSIM_SECRET into ${ENV_FILE}) and re-run this script."
    warn "  - To start over: re-enter each credential under the new secret"
    warn "    (PUT /api/credentials/integrations)."
    warn "================================================================"
  fi

  SECRET="$(gen_secret)"

  # Render the template to a temp file and move it into place, rather than
  # `cp` then `sed -i`. Two reasons, both of which bit on macOS:
  #   - BSD sed's -i REQUIRES a backup-suffix argument, so the GNU-style
  #     `sed -i -e ...` parses `-e` AS that suffix and dies with
  #     "sed: -e: No such file or directory". Redirecting to a temp file
  #     needs no -i at all and behaves identically on BSD and GNU.
  #   - `cp` first, edit second is not atomic. Under `set -e` a failing sed
  #     aborted the script having ALREADY created .env, holding the
  #     template's literal placeholder secret. The next run then took the
  #     "already present — leaving it untouched" branch and booted with it.
  # The host guard admits localhost and IP literals, and inside the container
  # gethostname() is the container id. Seed this jumpbox's own names so a
  # beacon or DC reaching it by DNS/NetBIOS name is not refused with 400
  # HOST_NOT_ALLOWED (which `curl -fsSL ... | bash` hides behind "error: 400").
  ALLOWED_HOSTS="$(seed_allowed_hosts)"
  tmp_env="${ENV_FILE}.tmp.$$"
  sed \
    -e 's/^CORTEXSIM_ENV=.*/CORTEXSIM_ENV=development/' \
    -e "s|^CORTEXSIM_SECRET=.*|CORTEXSIM_SECRET=${SECRET}|" \
    -e "s|^CORTEXSIM_ALLOWED_HOSTS=.*|CORTEXSIM_ALLOWED_HOSTS=${ALLOWED_HOSTS}|" \
    "${ENV_EXAMPLE}" > "${tmp_env}"
  chmod 600 "${tmp_env}" 2>/dev/null || true
  mv "${tmp_env}" "${ENV_FILE}"
  log "Wrote ${ENV_FILE} with a freshly generated CORTEXSIM_SECRET."
  if [[ -n "${ALLOWED_HOSTS}" ]]; then
    log "CORTEXSIM_ALLOWED_HOSTS=${ALLOWED_HOSTS} (add any other name beacons use to reach this host)."
  else
    warn "Could not read this host's name: CORTEXSIM_ALLOWED_HOSTS is empty, so SimCore"
    warn "answers only by localhost / IP. Set it in ${ENV_FILE} if beacons use a DNS name."
  fi
fi

# ---------------------------------------------------------------------------
# 1b. Validate the secret we are about to boot with — whether this run
#     generated it or inherited an existing .env. docker-compose.yml's
#     ${CORTEXSIM_SECRET:?...} guard only asserts NON-EMPTY, so
#     .env.example's literal "replace-me-with-a-generated-32plus-byte-secret"
#     satisfies it and the stack comes up encrypting the integration
#     credential vault — which holds customer Cortex tenant API keys — with a
#     value that is committed, public, and identical for every operator.
#     Refuse, and print the exact fix.
# ---------------------------------------------------------------------------
CURRENT_SECRET="$(sed -n 's/^CORTEXSIM_SECRET=//p' "${ENV_FILE}" | head -n1)"
if [[ -z "${CURRENT_SECRET}" || "${CURRENT_SECRET}" == replace-me-* || ${#CURRENT_SECRET} -lt 32 ]]; then
  err "${ENV_FILE} carries a placeholder or too-short CORTEXSIM_SECRET."
  err "That value encrypts the integration-credential vault, so it must not be"
  err "the template default. Regenerate it with:"
  err "    rm ${ENV_FILE} && scripts/dev-up.sh"
  exit 1
fi

# ---------------------------------------------------------------------------
# 1c. An .env this run did not generate: does it admit this host's name?
#     (A generated one was seeded above and has already said so.)
# ---------------------------------------------------------------------------
if (( ENV_PREEXISTING )); then
  check_allowed_hosts
fi

# Read CORTEXSIM_PORT from .env for the health-check URL (default 8888).
PORT="$(sed -n 's/^CORTEXSIM_PORT=\([0-9][0-9]*\).*/\1/p' "${ENV_FILE}" | head -n1)"
PORT="${PORT:-8888}"

# ---------------------------------------------------------------------------
# 1d. CORTEXSIM_VERSION drives the image tag and the container name
#     (cortex-pov-engine-simcore:<v>, cortex-pov-engine-simcore-v<v>). Compose
#     resolves it shell first, then .env, then its own default; this reads the
#     same three in the same order, so the line printed below is what compose
#     will do. /api/health reports the CODE's version (APP_VERSION), which a
#     preserved .env knows nothing about: an upgrade that keeps an old pin
#     re-tags the new code under the old version. Named, never rewritten: a pin
#     that differs from the tree can be deliberate.
# ---------------------------------------------------------------------------
TREE_VERSION="$(tree_version || true)"
if [[ -n "${CORTEXSIM_VERSION:-}" ]]; then
  VERSION="${CORTEXSIM_VERSION}"
  VERSION_SOURCE="this shell exports"
elif VERSION="$(env_file_value CORTEXSIM_VERSION)" && [[ -n "${VERSION}" ]]; then
  VERSION_SOURCE=".env pins"
else
  VERSION="$(compose_default_version || true)"
  VERSION_SOURCE="docker-compose.yml defaults to"
fi

if [[ -z "${TREE_VERSION}" ]]; then
  log "Could not read APP_VERSION from core/api/health.py, so CORTEXSIM_VERSION was not compared with this tree's version."
elif [[ -z "${VERSION}" ]]; then
  log "Could not read the CORTEXSIM_VERSION default from docker-compose.yml, so it was not compared with this tree's version (${TREE_VERSION})."
elif [[ "${VERSION}" != "${TREE_VERSION}" ]]; then
  warn "================================================================"
  warn "WARNING: ${VERSION_SOURCE} CORTEXSIM_VERSION=${VERSION}, which disagrees"
  warn "with this tree's version ${TREE_VERSION} (core/api/health.py APP_VERSION)."
  warn "The build below will re-tag this tree's code as"
  warn "    cortex-pov-engine-simcore:${VERSION}"
  warn "and name the container cortex-pov-engine-simcore-v${VERSION}, while"
  warn "/api/health reports version ${TREE_VERSION}. An image that already holds"
  warn "the ${VERSION} tag loses it, so it can no longer be rolled back to by tag."
  warn "  - If the pin is stale: set CORTEXSIM_VERSION=${TREE_VERSION} (in .env,"
  warn "    or wherever it is set) and re-run."
  warn "  - If the tag is deliberate, nothing to do."
  warn "================================================================"
  REMINDERS+=("CORTEXSIM_VERSION=${VERSION} (${VERSION_SOURCE} it) disagrees with this tree's version ${TREE_VERSION}: the container is cortex-pov-engine-simcore-v${VERSION} while /api/health reports ${TREE_VERSION}. Set CORTEXSIM_VERSION=${TREE_VERSION} unless the tag is deliberate.")
fi
VERSION="${VERSION:-unknown}"

# ---------------------------------------------------------------------------
# 2b. Adapter-source preflight (non-fatal).
#     SimCore boots and serves the UI without the tier-2 adapter source trees
#     (e.g. sources/atomic-red-team) — those are executed by the agent on the
#     target, not by SimCore. So a miss here is a heads-up, not a blocker: it
#     tells you the atomic-backed EDR/MP scenarios won't detonate until you run
#     `git submodule update --init --recursive` (or, if a gitlink is missing,
#     `git submodule add --force <url> <path>`). install.sh enforces this hard.
# ---------------------------------------------------------------------------
if [[ -x "${REPO_ROOT}/scripts/check-adapter-sources.sh" ]]; then
  if ! "${REPO_ROOT}/scripts/check-adapter-sources.sh" >/dev/null 2>&1; then
    log "Heads-up: a tier-2 adapter source tree is missing — atomic-backed"
    log "scenarios won't detonate. Details: scripts/check-adapter-sources.sh"
  fi
fi

# ---------------------------------------------------------------------------
# 3. Bring the stack up.
# ---------------------------------------------------------------------------
if docker compose version >/dev/null 2>&1; then
  DC=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  DC=(docker-compose)
else
  err "Docker Compose not found. Install Docker Desktop or the compose plugin."
  exit 1
fi

COMPOSE_FILES=(-f docker-compose.yml)
if (( DEV_OVERLAY )); then
  log "Continuous dev mode enabled: overlaying docker-compose.dev.yml (mounts core/ with --reload)"
  COMPOSE_FILES+=(-f docker-compose.dev.yml)
fi

log "Version ${VERSION}: image cortex-pov-engine-simcore:${VERSION}, container cortex-pov-engine-simcore-v${VERSION}"
log "Building and starting SimCore: ${DC[*]} ${COMPOSE_FILES[*]} up -d --build"
"${DC[@]}" "${COMPOSE_FILES[@]}" up -d --build

# ---------------------------------------------------------------------------
# 4. Poll health until it responds (timeout ~90s). "ok" and "degraded" both
#    mean the app came up — see the header comment. Only silence times out.
# ---------------------------------------------------------------------------

# Print each degraded component's code + detail from a /api/health body.
# python3 (exact, key-order-independent) when available; otherwise point the
# operator at the URL rather than guess with fragile text parsing.
print_degraded_components() {
  local body="$1"
  if command -v python3 >/dev/null 2>&1; then
    printf '%s' "${body}" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
    for name, comp in (d.get("components") or {}).items():
        if isinstance(comp, dict) and comp.get("status") == "degraded":
            print("    - %s: %s -- %s" % (name, comp.get("code", "?"), comp.get("detail", "")))
except Exception:
    pass
' 2>/dev/null || true
  else
    log "  (install python3 to see per-component detail, or: curl -s ${HEALTH_URL})"
  fi
}

# Extract the top-level "status" field. python3 first (correct regardless of
# key order); the sed fallback is anchored to the START of the body
# (`^{"status":...`) rather than scanned greedily — a plain `.*"status":...`
# scan matches the LAST "status" key in the blob, which is a nested
# component's, not the top-level one, on every response that has more than
# one component (i.e. every real response). Verified: an unanchored version
# of this extractor reported "ok" against a live "degraded" response.
extract_status() {
  local body="$1"
  if command -v python3 >/dev/null 2>&1; then
    printf '%s' "${body}" | python3 -c 'import json,sys
try:
    print(json.load(sys.stdin).get("status",""))
except Exception:
    pass' 2>/dev/null || true
  else
    printf '%s' "${body}" | sed -n 's/^{[[:space:]]*"status"[[:space:]]*:[[:space:]]*"\([a-z_]*\)".*/\1/p' | head -n1
  fi
}

# Repeat, as the last thing on screen, every warning raised before the build.
print_reminders() {
  (( ${#REMINDERS[@]} )) || return 0
  local r
  warn "Still open from this run:"
  for r in "${REMINDERS[@]}"; do
    warn "  - ${r}"
  done
  printf '\n'
}

HEALTH_URL="http://localhost:${PORT}/api/health"
log "Waiting for ${HEALTH_URL} ..."

deadline=$(( $(date +%s) + 90 ))
while :; do
  BODY="$(curl -fsS --max-time 3 "${HEALTH_URL}" 2>/dev/null || true)"
  if [[ -n "${BODY}" ]]; then
    HSTATUS="$(extract_status "${BODY}")"
    case "${HSTATUS}" in
      ok)
        log "SimCore is healthy."
        printf '\n  \033[0;32m✓ CortexSim is up:\033[0m  http://localhost:%s\n\n' "${PORT}"
        print_reminders
        exit 0
        ;;
      degraded)
        warn "SimCore booted but reports DEGRADED — this can be normal on first boot (e.g. no agents enrolled yet)."
        print_degraded_components "${BODY}"
        printf '\n  \033[1;33m⚠ CortexSim is up (degraded):\033[0m  http://localhost:%s\n'  "${PORT}"
        printf '    Full detail: curl -s %s | python3 -m json.tool\n\n' "${HEALTH_URL}"
        print_reminders
        exit 0
        ;;
    esac
  fi
  if (( $(date +%s) >= deadline )); then
    err "Timed out waiting for ${HEALTH_URL} after 90s — no response at all."
    err "Check logs with: ${DC[*]} logs -f simcore"
    exit 1
  fi
  sleep 2
done
