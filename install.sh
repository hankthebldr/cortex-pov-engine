#!/usr/bin/env bash
# ==============================================================================
# CortexSim install.sh — Jumpbox Bootstrap
# Version: 1.0
#
# Supported OS: Ubuntu 22.04 LTS+, Debian 12+
#
# Usage:
#   git clone <repo> && cd cortexsim && ./install.sh
#   curl -sSL https://raw.githubusercontent.com/<org>/cortexsim/main/install.sh | bash
#
# What this does (in order):
#   1. Check OS compatibility
#   2. Install system dependencies (git, curl, docker, go, rust, python3, node)
#   3. Initialize git submodules
#   4. Build Go agent  →  bin/cortexsim-agent
#   5. Build Rust tools (signalbench, ackbarx, xdrtop) via cargo build --release
#   6. Install Python deps for mocktaxii and gocortexbrokenbank
#   7. Build React UI  →  ui/dist/
#   8. Copy UI build   →  core/static/
#   9. docker-compose up -d --build
#  10. Print success banner
# ==============================================================================
set -euo pipefail

# ------------------------------------------------------------------------------
# Script directory — works whether called as ./install.sh or piped via curl|bash
# ------------------------------------------------------------------------------
_src="${BASH_SOURCE[0]:-}"
if [[ -n "$_src" && "$_src" != "bash" ]]; then
    SCRIPT_DIR="$(cd "$(dirname "$_src")" && pwd)"
else
    SCRIPT_DIR="$(pwd)"
fi
unset _src

# ------------------------------------------------------------------------------
# Globals (set here, may be overridden by _install_docker)
# ------------------------------------------------------------------------------
DOCKER_CMD="docker"
DOCKER_COMPOSE_CMD="docker compose"

# Required version minimums
REQUIRED_GO_MINOR=21        # go 1.21+
INSTALL_GO_VERSION="1.22.4" # version to install if upgrading
REQUIRED_NODE_MAJOR=18      # node 18+
REQUIRED_DEBIAN=12
REQUIRED_UBUNTU="22.04"

# ------------------------------------------------------------------------------
# Colors (disabled when not writing to a terminal)
# ------------------------------------------------------------------------------
if [[ -t 1 ]]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[1;33m'
    BLUE='\033[0;34m'
    CYAN='\033[0;36m'
    BOLD='\033[1m'
    NC='\033[0m'
else
    RED='' GREEN='' YELLOW='' BLUE='' CYAN='' BOLD='' NC=''
fi

# ------------------------------------------------------------------------------
# Logging helpers
# ------------------------------------------------------------------------------
log_step() { echo -e "\n${BLUE}[${NC}${BOLD}STEP${NC}${BLUE}]${NC} $*"; }
log_ok()   { echo -e "  ${GREEN}✓${NC} $*"; }
log_warn() { echo -e "  ${YELLOW}⚠${NC} $*" >&2; }
log_err()  { echo -e "\n${RED}✗ ERROR:${NC} $*\n" >&2; }
log_info() { echo -e "  ${BLUE}→${NC} $*"; }
die()      { log_err "$*"; exit 1; }

# Error trap: show line number on unexpected exits
trap '_ec=$?; [[ $_ec -ne 0 ]] && log_err "Unexpected failure at line ${LINENO} (exit code: ${_ec}). Check output above."' ERR

# ------------------------------------------------------------------------------
# Version helpers
# ------------------------------------------------------------------------------

# Returns 0 (true) if version $1 >= version $2 (dot-separated integers)
# Uses GNU sort -V (available on Ubuntu/Debian coreutils)
version_gte() {
    printf '%s\n%s\n' "$2" "$1" | sort -V -C
}

# ==============================================================================
# STEP 1 — OS Compatibility
# ==============================================================================
check_os() {
    log_step "Checking OS compatibility"

    [[ "$(uname -s)" == "Linux" ]] \
        || die "CortexSim requires Linux. Detected: $(uname -s). Deploy on Ubuntu 22.04+ or Debian 12+."

    [[ -f /etc/os-release ]] \
        || die "Cannot detect OS: /etc/os-release not found."

    # shellcheck source=/dev/null
    source /etc/os-release

    case "${ID:-unknown}" in
        ubuntu)
            version_gte "${VERSION_ID:-0}" "$REQUIRED_UBUNTU" \
                || die "Ubuntu ${REQUIRED_UBUNTU}+ required. Detected: ${VERSION_ID:-unknown}."
            ;;
        debian)
            version_gte "${VERSION_ID:-0}" "$REQUIRED_DEBIAN" \
                || die "Debian ${REQUIRED_DEBIAN}+ required. Detected: ${VERSION_ID:-unknown}."
            ;;
        *)
            log_warn "OS '${ID:-unknown} ${VERSION_ID:-}' is not officially tested."
            log_warn "Ubuntu 22.04+ or Debian 12 is recommended. Proceeding anyway."
            ;;
    esac

    log_ok "OS: ${PRETTY_NAME:-Linux}"
}

# ==============================================================================
# STEP 2 — System Dependencies
# ==============================================================================
install_system_deps() {
    log_step "Installing system dependencies"

    export DEBIAN_FRONTEND=noninteractive
    log_info "Updating apt package index..."
    sudo apt-get update -qq

    # --- git ---
    if command -v git &>/dev/null; then
        log_ok "git: $(git --version)"
    else
        sudo apt-get install -y --no-install-recommends git \
            || die "Failed to install git"
        log_ok "git installed: $(git --version)"
    fi

    # --- curl ---
    if command -v curl &>/dev/null; then
        log_ok "curl: $(curl --version | head -1)"
    else
        sudo apt-get install -y --no-install-recommends curl ca-certificates \
            || die "Failed to install curl"
        log_ok "curl installed"
    fi

    # --- python3 ---
    if command -v python3 &>/dev/null; then
        log_ok "python3: $(python3 --version)"
    else
        sudo apt-get install -y --no-install-recommends python3 python3-venv \
            || die "Failed to install python3"
        log_ok "python3 installed: $(python3 --version)"
    fi

    # --- pip3 ---
    if command -v pip3 &>/dev/null; then
        log_ok "pip3: $(pip3 --version | awk '{print $1, $2}')"
    else
        sudo apt-get install -y --no-install-recommends python3-pip \
            || die "Failed to install pip3"
        log_ok "pip3 installed"
    fi

    # --- Docker (includes compose) ---
    _install_docker

    # --- Go ---
    _install_go

    # --- Rust/Cargo ---
    _install_rust

    # --- Node.js ---
    _install_node

    log_ok "All system dependencies satisfied."
}

_install_docker() {
    # Install Docker engine if absent
    if command -v docker &>/dev/null; then
        log_ok "docker: $(docker --version)"
    else
        log_info "Installing Docker via get.docker.com..."
        curl -fsSL https://get.docker.com | sudo sh \
            || die "Docker installation failed. See: https://docs.docker.com/engine/install/ubuntu/"
        log_ok "Docker installed: $(docker --version)"
    fi

    # Ensure Docker daemon is running
    if ! docker info &>/dev/null 2>&1; then
        log_info "Starting Docker daemon..."
        sudo systemctl start docker 2>/dev/null || true
        # Give daemon a moment to start
        local retries=5
        while ! docker info &>/dev/null 2>&1 && [[ $retries -gt 0 ]]; do
            retries=$((retries - 1))
            read -rt 1 _ 2>/dev/null || true  # portable 1-second wait (no sleep)
        done
        docker info &>/dev/null 2>&1 \
            || log_warn "Docker daemon may not be running. Try: sudo systemctl start docker"
    fi

    # Add user to docker group (avoids needing root for all docker commands)
    if groups "$USER" 2>/dev/null | grep -q '\bdocker\b'; then
        log_ok "User '$USER' is already in docker group"
        DOCKER_CMD="docker"
    else
        sudo usermod -aG docker "$USER" \
            || log_warn "Could not add '$USER' to docker group — you may need to prefix docker commands with sudo"
        log_warn "Added '$USER' to docker group."
        log_warn "Using 'sudo docker' for this session. Run 'newgrp docker' or log out/in to apply permanently."
        DOCKER_CMD="sudo docker"
    fi

    # Detect docker compose (v2 plugin preferred, v1 standalone fallback)
    if ${DOCKER_CMD} compose version &>/dev/null 2>&1; then
        DOCKER_COMPOSE_CMD="${DOCKER_CMD} compose"
        log_ok "docker compose (v2 plugin): $($DOCKER_CMD compose version --short 2>/dev/null || echo 'available')"
    elif command -v docker-compose &>/dev/null; then
        if [[ "$DOCKER_CMD" == "sudo docker" ]]; then
            DOCKER_COMPOSE_CMD="sudo docker-compose"
        else
            DOCKER_COMPOSE_CMD="docker-compose"
        fi
        log_ok "docker-compose (v1): $(docker-compose --version)"
    else
        log_info "Installing docker-compose plugin..."
        if sudo apt-get install -y --no-install-recommends docker-compose-plugin 2>/dev/null; then
            DOCKER_COMPOSE_CMD="${DOCKER_CMD} compose"
            log_ok "docker-compose plugin installed"
        else
            # Fallback: download standalone binary
            local os arch
            os="$(uname -s)"
            arch="$(uname -m)"
            sudo curl -fsSL \
                "https://github.com/docker/compose/releases/latest/download/docker-compose-${os}-${arch}" \
                -o /usr/local/bin/docker-compose \
                || die "Failed to install docker-compose. Try manually: https://docs.docker.com/compose/install/"
            sudo chmod +x /usr/local/bin/docker-compose
            if [[ "$DOCKER_CMD" == "sudo docker" ]]; then
                DOCKER_COMPOSE_CMD="sudo docker-compose"
            else
                DOCKER_COMPOSE_CMD="docker-compose"
            fi
            log_ok "docker-compose standalone installed"
        fi
    fi
}

_install_go() {
    local required_minor="$REQUIRED_GO_MINOR"

    if command -v go &>/dev/null; then
        local installed
        installed="$(go version | grep -oP '\bgo\K[0-9]+\.[0-9]+(?:\.[0-9]+)?' | head -1)"
        local installed_minor
        installed_minor="$(echo "$installed" | cut -d. -f2)"
        if [[ "$(echo "$installed" | cut -d. -f1)" -ge 1 && "$installed_minor" -ge "$required_minor" ]]; then
            log_ok "go: go${installed} (satisfies >= 1.${required_minor})"
            return
        fi
        log_warn "go ${installed} found but 1.${required_minor}+ required — installing ${INSTALL_GO_VERSION}"
    fi

    local arch
    case "$(uname -m)" in
        x86_64)        arch="amd64" ;;
        aarch64|arm64) arch="arm64" ;;
        *) die "Unsupported architecture for Go install: $(uname -m). Install Go 1.${required_minor}+ manually." ;;
    esac

    local tarball="go${INSTALL_GO_VERSION}.linux-${arch}.tar.gz"
    log_info "Downloading Go ${INSTALL_GO_VERSION}..."
    curl -fsSL "https://go.dev/dl/${tarball}" -o "/tmp/${tarball}" \
        || die "Failed to download Go ${INSTALL_GO_VERSION} from go.dev"

    sudo rm -rf /usr/local/go
    sudo tar -C /usr/local -xzf "/tmp/${tarball}" \
        || die "Failed to extract Go tarball"
    rm -f "/tmp/${tarball}"

    export PATH="/usr/local/go/bin:$PATH"

    # Persist to shell config if not already present
    for profile in "$HOME/.bashrc" "$HOME/.profile"; do
        if [[ -f "$profile" ]] && ! grep -q '/usr/local/go/bin' "$profile"; then
            echo 'export PATH=/usr/local/go/bin:$PATH' >> "$profile"
            break
        fi
    done

    log_ok "go installed: $(go version)"
}

_install_rust() {
    if command -v cargo &>/dev/null; then
        log_ok "cargo: $(cargo --version)"
        return
    fi

    log_info "Installing Rust via rustup (this may take a few minutes)..."
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
        | sh -s -- -y --no-modify-path \
        || die "Rust installation failed. Try manually: https://rustup.rs/"

    export PATH="$HOME/.cargo/bin:$PATH"

    # Persist to shell config if not already present
    for profile in "$HOME/.bashrc" "$HOME/.profile"; do
        if [[ -f "$profile" ]] && ! grep -q '\.cargo/bin' "$profile"; then
            echo 'export PATH="$HOME/.cargo/bin:$PATH"' >> "$profile"
            break
        fi
    done

    log_ok "cargo installed: $(cargo --version)"
}

_install_node() {
    local required_major="$REQUIRED_NODE_MAJOR"

    if command -v node &>/dev/null; then
        local installed_major
        installed_major="$(node --version | grep -oP '^v\K[0-9]+')"
        if [[ "$installed_major" -ge "$required_major" ]]; then
            log_ok "node: $(node --version) (satisfies >= v${required_major})"
            log_ok "npm: $(npm --version)"
            return
        fi
        log_warn "node v${installed_major} found but v${required_major}+ required — upgrading via NodeSource"
    fi

    log_info "Installing Node.js ${required_major}.x via NodeSource..."
    curl -fsSL "https://deb.nodesource.com/setup_${required_major}.x" | sudo -E bash - \
        || die "NodeSource setup script failed. Try manually: https://nodejs.org/en/download/package-manager"
    sudo apt-get install -y --no-install-recommends nodejs \
        || die "Failed to install nodejs"

    log_ok "node installed: $(node --version)"
    log_ok "npm installed: $(npm --version)"
}

# ==============================================================================
# STEP 3 — Git Submodules
# ==============================================================================
init_submodules() {
    log_step "Initializing git submodules"

    cd "$SCRIPT_DIR"

    if [[ ! -f .gitmodules ]]; then
        log_warn ".gitmodules not found — skipping submodule init."
        log_warn "If this is a fresh clone, ensure the repo includes .gitmodules."
        return
    fi

    git submodule update --init --recursive \
        || die "git submodule update failed. Check network access and SSH/token permissions."

    log_ok "All submodules initialized."
}

# ==============================================================================
# STEP 4 — Build Go Agent
# ==============================================================================
build_go_agent() {
    log_step "Building Go agent (bin/cortexsim-agent)"

    cd "$SCRIPT_DIR"

    if [[ ! -d agent ]]; then
        log_warn "agent/ directory not found — skipping Go agent build."
        return
    fi

    mkdir -p bin

    # Idempotency: skip rebuild if binary is newer than all .go source files
    if [[ -f bin/cortexsim-agent ]]; then
        local stale
        stale="$(find agent/ -name '*.go' -newer bin/cortexsim-agent 2>/dev/null | head -1)"
        if [[ -z "$stale" ]]; then
            log_ok "cortexsim-agent is up to date — skipping rebuild."
            return
        fi
        log_info "Source change detected (${stale}) — rebuilding..."
    fi

    log_info "Running: go build -o bin/cortexsim-agent ."
    (cd agent && go build -o ../bin/cortexsim-agent .) \
        || die "Go agent build failed. Check agent/ source and ensure go ${REQUIRED_GO_MINOR}+ is on PATH."

    log_ok "Built: bin/cortexsim-agent"
}

# ==============================================================================
# STEP 5 — Build Rust Tools
# ==============================================================================
build_rust_tools() {
    log_step "Building Rust tools from submodules"

    cd "$SCRIPT_DIR"

    # These are the three Rust tools required for Phase 1
    # (gcgit is also Rust but not listed as a Phase 1 build target)
    local rust_tools=("sources/signalbench" "sources/ackbarx" "sources/xdrtop")

    for tool_path in "${rust_tools[@]}"; do
        local tool_name
        tool_name="$(basename "$tool_path")"

        if [[ ! -d "$tool_path" ]]; then
            log_warn "${tool_name}: ${tool_path} not found — skipping (submodule not initialized?)."
            continue
        fi

        if [[ ! -f "${tool_path}/Cargo.toml" ]]; then
            log_warn "${tool_name}: Cargo.toml not found — skipping."
            continue
        fi

        local binary="${tool_path}/target/release/${tool_name}"

        # Idempotency: skip if release binary is newer than Cargo.toml
        if [[ -f "$binary" && "$binary" -nt "${tool_path}/Cargo.toml" ]]; then
            log_ok "${tool_name}: already built — $(${binary} --version 2>/dev/null || echo 'binary present')"
            continue
        fi

        log_info "Building ${tool_name} (cargo build --release)..."
        (cd "$tool_path" && cargo build --release) \
            || die "cargo build --release failed for ${tool_name}. Check Rust toolchain and sources/${tool_name}."

        log_ok "${tool_name}: built → ${binary}"
    done
}

# ==============================================================================
# STEP 6 — Python Dependencies
# ==============================================================================
install_python_deps() {
    log_step "Installing Python dependencies for submodule tools"

    cd "$SCRIPT_DIR"

    local python_tools=("sources/mocktaxii" "sources/gocortexbrokenbank")

    for tool_path in "${python_tools[@]}"; do
        local tool_name
        tool_name="$(basename "$tool_path")"

        if [[ ! -d "$tool_path" ]]; then
            log_warn "${tool_name}: ${tool_path} not found — skipping."
            continue
        fi

        local req="${tool_path}/requirements.txt"
        if [[ ! -f "$req" ]]; then
            log_warn "${tool_name}: requirements.txt not found — skipping."
            continue
        fi

        log_info "${tool_name}: pip3 install -r requirements.txt..."
        pip3 install --quiet -r "$req" \
            || die "pip3 install failed for ${tool_name}. Check ${req} and Python environment."

        log_ok "${tool_name}: Python dependencies installed."
    done
}

# ==============================================================================
# STEP 7 — Build React UI
# ==============================================================================
build_ui() {
    log_step "Building React UI (ui/)"

    cd "$SCRIPT_DIR"

    if [[ ! -d ui ]]; then
        log_warn "ui/ directory not found — skipping React build."
        return
    fi

    if [[ ! -f ui/package.json ]]; then
        log_warn "ui/package.json not found — skipping React build."
        return
    fi

    cd ui

    # npm install: only if node_modules absent or package.json changed since last install
    if [[ ! -d node_modules || package.json -nt node_modules/.package-lock.json ]]; then
        log_info "Running npm install..."
        npm install --silent \
            || die "npm install failed. Check ui/package.json and npm registry access."
        log_ok "npm dependencies installed."
    else
        log_ok "npm dependencies are up to date."
    fi

    log_info "Running npm run build..."
    npm run build \
        || die "npm run build failed. Check ui/ source and vite.config.js."

    cd ..
    log_ok "React UI built → ui/dist/"
}

# ==============================================================================
# STEP 8 — Copy UI Build to core/static/
# ==============================================================================
copy_ui_to_core() {
    log_step "Copying UI build to core/static/"

    cd "$SCRIPT_DIR"

    local src="ui/dist"
    local dst="core/static"

    if [[ ! -d "$src" ]]; then
        log_warn "ui/dist not found — skipping copy. (Did the UI build succeed?)"
        return
    fi

    mkdir -p "$dst"

    # -r: recursive  -u: only copy if source is newer (idempotent)
    cp -ru "${src}/." "${dst}/" \
        || die "Failed to copy ${src}/ to ${dst}/."

    log_ok "UI assets copied → ${dst}/"
}

# ==============================================================================
# STEP 9 — Start SimCore via Docker Compose
# ==============================================================================
start_simcore() {
    log_step "Starting SimCore (docker-compose up -d)"

    cd "$SCRIPT_DIR"

    if [[ ! -f docker-compose.yml ]]; then
        log_warn "docker-compose.yml not found — skipping docker-compose up."
        log_warn "Run '${DOCKER_COMPOSE_CMD} up -d --build' manually once docker-compose.yml exists."
        return
    fi

    log_info "Running: ${DOCKER_COMPOSE_CMD} up -d --build"
    ${DOCKER_COMPOSE_CMD} up -d --build \
        || die "docker-compose up failed. Check Docker daemon status and docker-compose.yml syntax."

    log_ok "SimCore is running."
}

# ==============================================================================
# STEP 10 — Success Banner
# ==============================================================================
print_success_banner() {
    local hostname port
    hostname="$(hostname -f 2>/dev/null || hostname)"
    port="${CORTEXSIM_PORT:-8888}"

    echo ""
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "  ${BOLD}CortexSim${NC} — Detection Simulation Engine"
    echo -e "  ${GREEN}✓ Installation complete${NC}"
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo -e "  ${BOLD}SimCore URL:${NC}   http://${hostname}:${port}"
    echo -e "  ${BOLD}Local URL:${NC}     http://localhost:${port}"
    echo -e "  ${BOLD}Auth:${NC}          None (Phase 1 — jumpbox-controlled access)"
    echo ""
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "  ${BOLD}Quick Start${NC}"
    echo ""
    echo -e "  Start agent:"
    echo -e "    ${BOLD}./bin/cortexsim-agent --server http://localhost:${port} --id my-jumpbox --interval 10${NC}"
    echo ""
    echo -e "  Manage SimCore:"
    echo -e "    ${BOLD}${DOCKER_COMPOSE_CMD} ps${NC}               # status"
    echo -e "    ${BOLD}${DOCKER_COMPOSE_CMD} logs -f simcore${NC}  # live logs"
    echo -e "    ${BOLD}${DOCKER_COMPOSE_CMD} down${NC}             # stop"
    echo -e "    ${BOLD}${DOCKER_COMPOSE_CMD} up -d --build${NC}    # restart/rebuild"
    echo ""
    echo -e "  Scenario library:  ${BOLD}${SCRIPT_DIR}/scenarios/${NC}"
    echo -e "  Logs:              ${BOLD}${SCRIPT_DIR}/logs/cortexsim.log${NC}"
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""

    # Warn if docker group membership isn't active yet
    if ! groups "$USER" 2>/dev/null | grep -q '\bdocker\b'; then
        echo -e "  ${YELLOW}NOTE:${NC} Run ${BOLD}newgrp docker${NC} or log out/in to use docker without sudo."
        echo ""
    fi
}

# ==============================================================================
# Main
# ==============================================================================
main() {
    echo ""
    echo -e "${BOLD}CortexSim Installer${NC} v1.0 — Jumpbox Bootstrap"
    echo -e "Working directory: ${SCRIPT_DIR}"
    echo -e "Date: $(date '+%Y-%m-%d %H:%M:%S %Z')"
    echo ""

    check_os            # Step 1
    install_system_deps # Step 2
    init_submodules     # Step 3
    build_go_agent      # Step 4
    build_rust_tools    # Step 5
    install_python_deps # Step 6
    build_ui            # Step 7
    copy_ui_to_core     # Step 8
    start_simcore       # Step 9
    print_success_banner # Step 10
}

main "$@"
