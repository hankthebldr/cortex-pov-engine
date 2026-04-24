#!/bin/bash
#
# CortexSim jumpbox content installer.
#
# Reads infra/modules/<provider>/<module>/content.yml for each enabled module
# and installs the declared tools into /opt/cortexsim/content/<module>/<tool>/.
# Produces /opt/cortexsim/content/installed.json for SimCore's content_loader.
#
# Install strategies supported:
#   git-clone       — shallow git clone
#   binary-release  — download latest GitHub release asset
#   pip-install     — pip install from a repo or package
#   docker-pull     — pull a container image (no local install path)
#
# Usage:
#   install-content.sh --modules=base,edr,cdr --repo-root=/home/ubuntu/cortexsim
#
set -euo pipefail

MODULES=""
REPO_ROOT="/home/ubuntu/cortexsim"
PROVIDER="aws"
DRY_RUN=0

while [ "${1:-}" != "" ]; do
  case "$1" in
    --modules=*)   MODULES="${1#*=}" ;;
    --repo-root=*) REPO_ROOT="${1#*=}" ;;
    --provider=*)  PROVIDER="${1#*=}" ;;
    --dry-run)     DRY_RUN=1 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
  shift
done

if [ -z "$MODULES" ]; then
  echo "ERROR: --modules is required" >&2
  exit 2
fi

CONTENT_DIR="/opt/cortexsim/content"
MANIFEST="${CONTENT_DIR}/installed.json"
mkdir -p "$CONTENT_DIR"

log()  { echo "[install-content] $(date +%H:%M:%S) $*"; }
fail() { log "ERROR: $*"; exit 1; }

# Require yq for parsing content.yml
if ! command -v yq >/dev/null 2>&1; then
  log "installing yq"
  if [ "$DRY_RUN" -eq 0 ]; then
    curl -sSL -o /usr/local/bin/yq \
      https://github.com/mikefarah/yq/releases/latest/download/yq_linux_amd64
    chmod +x /usr/local/bin/yq
  fi
fi

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "required command not found: $1"
}

require_cmd git
require_cmd curl

# ------------------------------------------------------------------
# Per-strategy installers
# ------------------------------------------------------------------

install_git_clone() {
  local repo="$1" dst="$2"
  log "git-clone $repo -> $dst"
  if [ "$DRY_RUN" -eq 1 ]; then return 0; fi
  if [ -d "$dst/.git" ]; then
    (cd "$dst" && git fetch --depth=1 origin && git reset --hard FETCH_HEAD) || \
      log "git update failed for $dst (continuing)"
  else
    mkdir -p "$(dirname "$dst")"
    git clone --depth=1 "https://github.com/$repo.git" "$dst" || \
      log "git-clone failed for $repo (continuing)"
  fi
}

install_binary_release() {
  local repo="$1" dst="$2" name="$3"
  log "binary-release $repo -> $dst"
  if [ "$DRY_RUN" -eq 1 ]; then return 0; fi
  mkdir -p "$dst"
  # Best-effort: fetch latest release metadata and grab a linux x86_64 asset
  local meta
  meta="$(curl -sSL "https://api.github.com/repos/$repo/releases/latest" || true)"
  local url
  url="$(echo "$meta" | grep -Eo 'https://[^"]*linux[^"]*(amd64|x86_64)[^"]*\.(tar\.gz|zip|tgz)' | head -1 || true)"
  if [ -z "$url" ]; then
    log "could not find linux asset for $repo — skipping"
    return 0
  fi
  local tmp; tmp="$(mktemp)"
  curl -sSL -o "$tmp" "$url" || { log "download failed"; return 0; }
  case "$url" in
    *.tar.gz|*.tgz) tar -xzf "$tmp" -C "$dst" ;;
    *.zip) unzip -q "$tmp" -d "$dst" ;;
  esac
  rm -f "$tmp"
}

install_pip() {
  local repo="$1"
  log "pip-install $repo"
  if [ "$DRY_RUN" -eq 1 ]; then return 0; fi
  pip3 install --quiet "git+https://github.com/$repo.git" || log "pip install failed for $repo"
}

install_docker_pull() {
  local image="$1"
  log "docker-pull $image"
  if [ "$DRY_RUN" -eq 1 ]; then return 0; fi
  if command -v docker >/dev/null 2>&1; then
    docker pull "$image" || log "docker pull failed for $image"
  else
    log "docker not present; skipping pull of $image"
  fi
}

# ------------------------------------------------------------------
# Manifest assembly
# ------------------------------------------------------------------

MANIFEST_ENTRIES=()

append_manifest() {
  local name="$1" install_path="$2" plane="$3" repo="$4" category="$5"
  local desc="installed via install-content.sh from $repo"
  local entry
  entry="$(jq -cn \
    --arg name "$name" \
    --arg ip "$install_path" \
    --arg plane "$plane" \
    --arg repo "$repo" \
    --arg cat "$category" \
    --arg desc "$desc" \
    '{name:$name, install_path:$ip, type:"content", plane:[$plane], description:$desc, repo:$repo, category:$cat}')"
  MANIFEST_ENTRIES+=("$entry")
}

# ------------------------------------------------------------------
# Process each selected module
# ------------------------------------------------------------------

IFS=',' read -ra MODULE_LIST <<< "$MODULES"

for module in "${MODULE_LIST[@]}"; do
  module="$(echo "$module" | tr -d ' ')"
  [ -z "$module" ] && continue

  manifest_path="${REPO_ROOT}/infra/modules/${PROVIDER}/${module}/content.yml"
  if [ ! -f "$manifest_path" ]; then
    log "no content.yml for module=$module (skipping)"
    continue
  fi

  log "processing module: $module"
  categories="$(yq eval '.tools | keys | .[]' "$manifest_path" 2>/dev/null || true)"

  while IFS= read -r category; do
    [ -z "$category" ] && continue
    count="$(yq eval ".tools.${category} | length" "$manifest_path")"
    for i in $(seq 0 $((count - 1))); do
      name="$(yq eval ".tools.${category}[$i].name" "$manifest_path")"
      repo="$(yq eval ".tools.${category}[$i].repo" "$manifest_path")"
      install="$(yq eval ".tools.${category}[$i].install" "$manifest_path")"
      install_path="$(yq eval ".tools.${category}[$i].install_path // \"\"" "$manifest_path")"
      image="$(yq eval ".tools.${category}[$i].image // \"\"" "$manifest_path")"

      if [ -z "$install_path" ] && [ "$install" != "docker-pull" ] && [ "$install" != "pip-install" ]; then
        install_path="${CONTENT_DIR}/${module}/${name}"
      fi

      case "$install" in
        git-clone)      install_git_clone "$repo" "$install_path" ;;
        binary-release) install_binary_release "$repo" "$install_path" "$name" ;;
        pip-install)    install_pip "$repo" ;;
        docker-pull)    install_docker_pull "$image" ;;
        *) log "unknown install strategy for $name: $install — skipping" ; continue ;;
      esac

      append_manifest "$name" "${install_path:-(docker)}" "$module" "$repo" "$category"
    done
  done <<< "$categories"
done

# Write manifest
if [ "$DRY_RUN" -eq 1 ]; then
  log "(dry-run) would write manifest with ${#MANIFEST_ENTRIES[@]} entries to $MANIFEST"
else
  entries_json="[]"
  if [ "${#MANIFEST_ENTRIES[@]}" -gt 0 ]; then
    entries_json="$(printf '%s\n' "${MANIFEST_ENTRIES[@]}" | jq -s '.')"
  fi
  jq -n --argjson tools "$entries_json" '{tools:$tools}' > "$MANIFEST"
  log "wrote manifest: $MANIFEST (${#MANIFEST_ENTRIES[@]} entries)"
fi

log "install-content.sh complete"
