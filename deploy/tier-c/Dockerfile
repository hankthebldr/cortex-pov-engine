# syntax=docker/dockerfile:1
# ===========================================================================
# Tier-C isolated runner image  —  cortexsim-tier-c-runner
# ---------------------------------------------------------------------------
# e2e execution methodology, Tier C, increment 1 (infra).
# See deploy/tier-c/README.md and docs/design/e2e-execution-methodology.md.
#
# This image is an ephemeral, audited, network-sinkholed sandbox that runs a
# CortexSim *push bundle* (self-contained bash) under the SAME identity
# harness the bundle uses on a customer host — then dumps the auditd log so a
# DC can prove "the right binaries fired under the right identities" without
# touching a customer endpoint.
#
# What it is NOT:
#   - a C2 sandbox (no real attack frameworks are staged here)
#   - networked (the compose profile pins it to an internal bridge with no
#     egress; DNS + HTTP resolve to the sidecar sinkhole)
#
# Build (do NOT build as part of CI fast-path — it pulls Ubuntu + auditd):
#   docker build -t cortexsim-tier-c-runner deploy/tier-c/
# ===========================================================================
FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8

# ── Stage 1: real binaries that push bundles call ─────────────────────────
#   These must be the REAL tools — Tier C executes the bundle for real, it
#   does not mock the binary. Network-touching tools (curl/wget/dig/nc) only
#   ever reach the sinkhole because the compose network has no egress and
#   /etc/resolv.conf points at the sinkhole's DNS.
RUN apt-get update && apt-get install -y --no-install-recommends \
        bash coreutils findutils grep gawk sed jq \
        curl wget dnsutils netcat-openbsd openssl ca-certificates \
        util-linux sudo passwd procps psmisc iproute2 \
        auditd audispd-plugins \
    && rm -rf /var/lib/apt/lists/*

# ── Stage 2: identity-harness service accounts ────────────────────────────
#   Mirror EXACTLY the usernames the CortexSim identity harness wraps in
#   core/engine/push_generator.py (run_as case statement):
#     www-data | postgres | mysql | node | python3 | nobody | svc-backup
#   nobody already exists in the base image; the rest are created here as
#   system accounts with a real shell so `runuser -l <user> -c ...` works and
#   produces the expected setuid/process-causality transition in the audit
#   log. We do NOT give them a password — the harness uses runuser/su, never
#   an interactive login.
RUN set -eux; \
    for u in www-data postgres mysql node python3 svc-backup; do \
        if ! id -u "$u" >/dev/null 2>&1; then \
            useradd --system --create-home --shell /bin/bash "$u"; \
        else \
            usermod --shell /bin/bash "$u" || true; \
        fi; \
    done; \
    # nobody ships with /usr/sbin/nologin — give it a real shell so a step
    # declared `identity: nobody` actually forks under uid 65534.
    usermod --shell /bin/bash nobody || true; \
    # Seed a couple of credential-shaped bait files so credential-access
    # scenarios have something to open (and the audit watch fires). These are
    # SYNTHETIC and inert — no real keys.
    install -d -m 0700 /root/.aws; \
    printf '[default]\naws_access_key_id = AKIATIERC0EXAMPLE000\naws_secret_access_key = tierc-synthetic-not-a-real-secret-00000000\n' \
        > /root/.aws/credentials; \
    chmod 0600 /root/.aws/credentials

# ── Stage 3: auditd ruleset (ground-truth telemetry) ──────────────────────
COPY audit.rules /etc/audit/rules.d/cortexsim-tier-c.rules

# ── Stage 4: entrypoint ───────────────────────────────────────────────────
COPY entrypoint.sh /usr/local/bin/tier-c-entrypoint
RUN chmod 0755 /usr/local/bin/tier-c-entrypoint

# Where the operator mounts the push bundle (read-only) and where we drop the
# audit log + observed-signals summary (a writable results volume).
VOLUME ["/bundle", "/results"]
ENV CORTEXSIM_BUNDLE_DIR=/bundle \
    CORTEXSIM_RESULTS_DIR=/results

# auditd needs CAP_AUDIT_CONTROL + CAP_AUDIT_WRITE to load rules and read the
# audit netlink socket; the compose profile grants exactly those (and nothing
# else) and runs the container with no host network. The entrypoint degrades
# gracefully to execve-from-/proc accounting if the kernel audit subsystem is
# unavailable (e.g. nested/unprivileged CI runners).
ENTRYPOINT ["/usr/local/bin/tier-c-entrypoint"]
