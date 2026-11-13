"""
CortexSim configuration — reads from environment variables with sensible defaults.
All settings can be overridden via environment or a .env file.
"""

from pydantic import SecretStr
from pydantic_settings import BaseSettings


_FORBIDDEN_MASTER_KEYS = {"changeme", "change-me", "default", "secret", ""}
_MIN_MASTER_KEY_LEN = 32

#: The placeholder `.env.example` ships, verbatim. It is 46 characters, so it
#: cleared the length floor, and it is not in the list above, so before SEC-11 it
#: booted — in BOTH envs — and encrypted the credential vault with a key that is
#: committed, public and identical for every DC. tests/test_master_key_placeholder.py
#: reads the live value from .env.example, so this constant cannot drift silently.
_ENV_EXAMPLE_PLACEHOLDER = "replace-me-with-a-generated-32plus-byte-secret"
#: Any value with this prefix is a template placeholder, not a key.
_PLACEHOLDER_PREFIX = "replace-me"


class MasterKeyError(RuntimeError):
    """Raised at boot when CORTEXSIM_SECRET is missing, default, or too weak."""


class Settings(BaseSettings):
    CORTEXSIM_PORT: int = 8888
    CORTEXSIM_ENV: str = "production"
    CORTEXSIM_SECRET: str = "changeme"
    # Declared because `.env.example` tells operators to set it and
    # docker-compose.yml consumes it (`${CORTEXSIM_VERSION:-1.1.1}` drives both
    # the image tag and container_name). Nothing in the app branches on the
    # value — it is deployment metadata — but pydantic-settings defaults
    # BaseSettings to `extra='forbid'` and this class reads `.env`, so leaving
    # it undeclared meant an operator who followed `.env.example` and ran
    # SimCore ON THE HOST (the documented local-dev path in CLAUDE.md) got a
    # boot-time ValidationError naming a variable the repo had just instructed
    # them to add. The container path never hit it: `.env` is gitignored and is
    # not COPYed into the image, so compose passes these through as real env
    # vars and the file is absent.
    #
    # Declaring it is the honest fix; widening to `extra='ignore'` would have
    # silenced this one typo-class of bug along with every genuine one. Note the
    # two sources are NOT symmetric: pydantic-settings already ignores an unknown
    # `os.environ` var (EnvSettingsSource looks up declared names only), while
    # DotEnvSettingsSource hands every key in the file to the model. So
    # `extra='forbid'` buys typo protection in exactly one place — the `.env`
    # file, which is where a DC actually sets things. A silent
    # CORTEXSIM_STRICT_REFSS would read as "absent" and quietly change boot
    # behaviour. Declare the next compose-only var here too; tests/test_config.py
    # fails until you do.
    #
    # Defaults to "unknown" rather than a pinned version: no workflow reads this
    # variable (release.yml derives the image tag from the git tag),
    # docker-compose.yml carries its own `:-1.1.1` fallback, and core/main.py
    # already hardcodes FastAPI's own `version`. A plausible-looking default here
    # would let a deploy that was never told its version report a specific one
    # anyway — degraded should read as degraded.
    CORTEXSIM_VERSION: str = "unknown"
    CORTEXSIM_BASE_DIR: str = "/app"
    CORTEXSIM_LOG_FILE: str = "logs/cortexsim.log"
    CORTEXSIM_SCENARIOS_DIR: str = "scenarios"
    CORTEXSIM_STATIC_DIR: str = "core/static"

    # Auto-reconcile loop (measurement loop). OFF by default — it makes outbound
    # calls to a configured Cortex tenant, so it must be opted into explicitly.
    # When on, a background task periodically reconciles recently-finished runs
    # that still have unobserved detections against every configured connector
    # integration, setting observed_at/MTTD without a manual trigger.
    CORTEXSIM_AUTO_RECONCILE: bool = False
    CORTEXSIM_AUTO_RECONCILE_INTERVAL: int = 300          # seconds between sweeps
    CORTEXSIM_AUTO_RECONCILE_LOOKBACK: int = 86400        # consider runs newer than this
    CORTEXSIM_AUTO_RECONCILE_WINDOW: int = 3600           # match window per result
    # Case-ledger refresh sweep (runs inside the auto-reconcile loop only).
    # Disposition drifts after a run (Auto Triage seals cases after a window that
    # is the shipped default and tenant-configurable), so a stored ledger is
    # re-read; read-only, cases only, counted and capped.
    CORTEXSIM_CASE_LEDGER_WINDOW_HOURS: int = 24          # refresh ledgers of runs started within this
    CORTEXSIM_CASE_LEDGER_MAX_RUNS_PER_SWEEP: int = 20    # runs refreshed per sweep
    CORTEXSIM_CASE_LEDGER_MIN_INTERVAL_MINUTES: int = 60  # skip a ledger read more recently than this
    # A delivered run whose agent is offline and silent this long is terminalised
    # as beacon_lost by the heartbeat sweep (the XDR agent may kill the beacon).
    CORTEXSIM_BEACON_LOST_AFTER_SECONDS: int = 180

    # Match window for Analytics / ABIOC expectations, used by every reconcile
    # path (manual /observations, /reconcile, the auto sweep) via
    # connectors.service.window_by_type(). The per-detector activation and test
    # periods live in the *Cortex XDR Analytics Alert Reference Guide*
    # (docs-cortex.paloaltonetworks.com, "Cortex XDR Analytics Alert Reference
    # Guide" -- each detector page lists its activation period and test period).
    # This default is the largest window /reconcile already accepts (86400 s); a
    # longer window can only delay a `missed`, never manufacture one. Tighten
    # against the guide for the detectors a POV actually exercises.
    CORTEXSIM_RECONCILE_WINDOW_ANALYTICS: int = 86400

    # Scenario id of the built-in canary (a benign trigger the agent is expected
    # to terminate) used by POST /api/runs/canary to prove a block-mode profile.
    # Empty = no canary shipped: block mode is attested-only, the honest default.
    CORTEXSIM_CANARY_SCENARIO_LINUX: str = ""

    # Auto-verify phase of the reconcile sweep. OFF by default and, unlike
    # reconcile, it runs XQL against the customer's tenant — a metered resource
    # their own analysts share. Requires an integration of kind 'xsiam_tenant'
    # (the reconcile 'xsiam' credential is a DIFFERENT kind and does NOT enable
    # this). Piggybacks on the auto-reconcile timer, so it does nothing unless
    # CORTEXSIM_AUTO_RECONCILE is also on — main.py warns at boot if it is not.
    #
    # Offline scoring (Run.tc_verdict from local MTTD + thresholds) is NOT
    # gated by this: it makes zero outbound calls, so gating it would gate
    # honesty rather than risk.
    CORTEXSIM_AUTO_VERIFY: bool = False
    CORTEXSIM_AUTO_VERIFY_MAX_ATTEMPTS: int = 3           # give up after N sweeps
    CORTEXSIM_AUTO_VERIFY_BACKOFF_SECONDS: int = 600      # doubled per attempt, capped at 4h
    CORTEXSIM_AUTO_VERIFY_MAX_QUERIES_PER_SWEEP: int = 50  # hard stop on tenant queries
    CORTEXSIM_XSIAM_MAX_REQUESTS_PER_MINUTE: int = 60  # per-tenant request bucket (tuning.py reads the env)
    CORTEXSIM_AUTO_VERIFY_TIMEFRAME_SECONDS: int = 3600   # XQL lookback per query

    # UC/TC foreign-key strictness. When false (the default for one release),
    # a scenario whose uc_ref/tc_ref does not resolve against the master index
    # snapshot logs S-10/S-11/S-12 as a WARNING and still loads. When true, the
    # same conditions reject the scenario at boot.
    #
    # Flipped true at the Phase 1 DoD: `docs/uc_tc_mapping/crosswalk-v2.2.csv`
    # now covers all 161 scenarios and the corpus boots with zero
    # S-10/S-11/S-12/S-15. Keeping it on is what stops the join rotting again —
    # a new scenario with an unresolvable ref fails at boot instead of silently
    # inflating the coverage number. Set false to load a corpus mid-re-key.
    CORTEXSIM_STRICT_REFS: bool = True

    # Dataset requirement registry strictness (spec/dataset_requirements.yml).
    # When true, boot raises DatasetRequirementsError if any dataset a detection
    # card's xql_queries reads has no registry entry. Off by default: an
    # unregistered dataset logs a warning and /api/health reports it degraded.
    CORTEXSIM_STRICT_DATASETS: bool = False


    # XSIAM API harness — master safety switches for the operation executor.
    # Both OFF by default: the harness pulls freely from a configured tenant
    # (read ops), but any write/delete operation additionally requires the
    # matching flag here AND per-request consent, and defaults to dry-run.
    # These are the "does not write to Cortex" invariant's off-switch — flip
    # them only for a tenant you are explicitly authorized to mutate.
    CORTEXSIM_XSIAM_ALLOW_WRITE: bool = False
    CORTEXSIM_XSIAM_ALLOW_DESTRUCTIVE: bool = False

    # PAN-OS NGFW connection is read-only in v1. The write flag is designed in
    # spec section 7 and has no consumer yet; the budget caps one preflight run.
    CORTEXSIM_PANOS_ALLOW_WRITE: bool = False
    CORTEXSIM_PANOS_MAX_CALLS_PER_PREFLIGHT: int = 120

    # Same posture, different surface: a pull-mode step whose declared
    # `requires_interpreters` is absent on the target may EITHER be reported
    # honestly as an environment gap (the default — nothing is ever installed
    # on a customer host implicitly) OR, if the operator explicitly authorizes
    # it for a run (LaunchRequest.allow_runtime_install=true) AND this
    # deployment-wide flag is also on, the beacon may attempt a package-manager
    # install to satisfy it. Both gates must be true — mirroring
    # CORTEXSIM_XSIAM_ALLOW_WRITE's two-key posture — so a single mis-set
    # request body can never mutate a customer endpoint on its own.
    CORTEXSIM_AGENT_ALLOW_RUNTIME_INSTALL: bool = False

    # Comma-separated http(s) origins this SimCore is also reachable at, for
    # names a request cannot show (a LAN IP behind NAT, a DNS name the cluster
    # uses). The agent installer's ?server= and a served k8s bundle's `server`
    # must be the origin the request arrived on or one of these; anything else
    # is 400 SERVER_NOT_THIS_SIMCORE. Scheme and host case, default ports and
    # trailing slashes are normalised. Empty = only the request's own origin.
    CORTEXSIM_PUBLIC_URLS: str = ""

    # MCP server (Model Context Protocol) over this REST API. The in-process
    # streamable-HTTP mount at /mcp is OPT-IN and off by default: the SimCore
    # port has no authentication, so exposing a tool surface on it is a
    # deliberate decision, not a default. The stdio entry point needs no flag.
    CORTEXSIM_MCP_HTTP: bool = False
    # Bearer token a client must present at /mcp. Empty with the flag on means
    # /mcp is NOT mounted. It only admits a client to /mcp: it is not a tenant
    # credential and does not authenticate /api.
    CORTEXSIM_MCP_TOKEN: SecretStr = SecretStr("")
    # Comma-separated Host headers the /mcp mount accepts (DNS-rebinding guard),
    # in addition to loopback. Empty = loopback only.
    CORTEXSIM_MCP_ALLOWED_HOSTS: str = ""
    # MCP tools that act on a customer host (cortexsim.run.launch, cortexsim.run.abort)
    # refuse with MCP_HOST_ACTION_DISABLED unless this is true. Both transports
    # read it; tenant-class tools are unaffected.
    CORTEXSIM_MCP_ALLOW_HOST_ACTIONS: bool = False
    # Ceiling on MCP tool calls that reach a customer tenant, per minute, so an
    # agent loop cannot burn the tenant's XQL quota. Same posture as the other
    # quota guards: a spent budget degrades to pending, never to a verdict.
    CORTEXSIM_MCP_MAX_TENANT_CALLS_PER_MINUTE: int = 30
    # Base URL the stdio MCP server uses to reach SimCore's REST API.
    CORTEXSIM_URL: str = "http://127.0.0.1:8888"

    # SQLAlchemy statement echo. Previously tied to CORTEXSIM_ENV=development,
    # which meant a dev boot log carried every aiosqlite statement TWICE (once
    # from the sqlalchemy.engine logger, once through the app formatter). A DC
    # reading the boot log for a real error scrolled past hundreds of duplicated
    # lines to find it. Opt in explicitly when you actually want statement-level
    # tracing.
    CORTEXSIM_SQL_ECHO: bool = False

    # ── Exposure (SEC-07 / SEC-10) ──────────────────────────────────────────
    # SimCore's operator and agent APIs are UNAUTHENTICATED (SEC-01 is open);
    # /api/health reports it as api_auth: API_UNAUTHENTICATED and boot warns.
    #
    # CORTEXSIM_BIND: the host address docker-compose.yml publishes the port on
    # ("${CORTEXSIM_BIND:+${CORTEXSIM_BIND}:}${CORTEXSIM_PORT:-8888}:8888",
    # target 8888; an empty host_ip does not parse). The app does not bind it —
    # uvicorn's --host does — it is read so the boot warning and the health
    # detail can NAME what is exposed. Empty means compose's default, every
    # host address, IPv4 and IPv6; 0.0.0.0 means IPv4 only.
    # scripts/dev-up-native.sh passes it to uvicorn --host directly.
    CORTEXSIM_BIND: str = ""
    # Host names the console/API answers to beyond localhost, IP literals and
    # this machine's own hostname/FQDN — the DNS-rebinding guard
    # (core/security/host_guard.py). Comma list; "*.lab.example" matches
    # subdomains; "*" disables the check.
    CORTEXSIM_ALLOWED_HOSTS: str = ""
    # Origins allowed to read the API cross-origin. Empty (the default) mounts
    # no CORS middleware at all: the console is same-origin and needs none.
    # Credentials are never allowed, even when this is set.
    CORTEXSIM_CORS_ORIGINS: str = ""
    # Comma list of CIDRs whose X-Forwarded-For is believed when an agent's
    # last_ip (and so a run's stitched src_ip) is recorded. Empty, the default,
    # trusts no proxy: the socket peer is the address. core/api/agents.py reads
    # the process environment first and this field second, so a value set only
    # in a host-run `.env` is honoured. It used to be undeclared, and an
    # uncommented `.env` line made Settings() refuse to boot.
    CORTEXSIM_TRUSTED_PROXIES: str = ""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


def validate_master_key(secret: str, *, env: str) -> None:
    """Boot-time guard for CORTEXSIM_SECRET.

    Refuses to start production with default, empty, or too-short secrets.
    In development, logs a warning instead of raising so local pytest runs
    against the in-tree default still work. The credentials layer still
    encrypts in dev — the operator just knows the lab key.

    Exception: the ``.env.example`` placeholder (anything starting
    ``replace-me``) is refused in BOTH environments — it is public, and the
    vault it would encrypt is real in development too (SEC-11).

    Why: the master key encrypts every credential the operator entrusts to
    CortexSim. A 'changeme' default would render the layer cryptographically
    worthless against anyone with read access to the SQLite file. Refusing
    to boot is the right failure mode for production.
    """
    stripped = (secret or "").strip()

    # SEC-11 — the published template placeholder is refused in EVERY env.
    # "Development" is what docker-compose.yml defaults to, and the vault it
    # encrypts holds real customer tenant keys whatever the env says, so the
    # dev-mode escape hatch below must not cover a key the whole internet has.
    if stripped == _ENV_EXAMPLE_PLACEHOLDER or stripped.lower().startswith(_PLACEHOLDER_PREFIX):
        raise MasterKeyError(
            "CORTEXSIM_SECRET is still the placeholder from .env.example "
            f"({stripped[:24]}...). That value is committed to the repository, so "
            "every credential encrypted with it is readable by anyone with the "
            "database file. This is refused in development AND production.\n"
            "\n"
            "To fix:\n"
            "  1. Generate a real secret:\n"
            "       python3 -c 'import secrets; print(secrets.token_urlsafe(48))'\n"
            "  2. Put it in .env as CORTEXSIM_SECRET=<value> (or run "
            "scripts/dev-up.sh, which writes one for you).\n"
            "  3. Re-run the boot command."
        )

    is_forbidden = stripped.lower() in _FORBIDDEN_MASTER_KEYS
    is_short = len(stripped) < _MIN_MASTER_KEY_LEN

    if not (is_forbidden or is_short):
        return

    reason = (
        "it is empty or a placeholder value (e.g. 'changeme')"
        if is_forbidden
        else f"it is shorter than the required {_MIN_MASTER_KEY_LEN} bytes "
        f"(got {len(stripped)})"
    )

    # Actionable, copy-pasteable remediation. The guard runs once at boot, so a
    # verbose message here is cheap and saves the operator a trip to the docs.
    msg = (
        f"CORTEXSIM_SECRET is misconfigured: {reason}.\n"
        f"\n"
        f"This key encrypts every credential CortexSim stores, so production "
        f"refuses to boot with a weak or default value.\n"
        f"\n"
        f"To fix:\n"
        f"  1. Generate a high-entropy secret (>= {_MIN_MASTER_KEY_LEN} bytes):\n"
        f"       export CORTEXSIM_SECRET=$(openssl rand -hex 32)\n"
        f"  2. Persist it in your local .env (see .env.example for the full template):\n"
        f"       echo \"CORTEXSIM_SECRET=$CORTEXSIM_SECRET\" >> .env\n"
        f"     In production, source it from a secrets manager instead, e.g.:\n"
        f"       export CORTEXSIM_SECRET=$(op read 'op://Private/cortexsim-master/key')\n"
        f"  3. Re-run the boot command.\n"
        f"\n"
        f"Note: development mode (CORTEXSIM_ENV=development) only logs this "
        f"warning and boots anyway, so local pytest and dev servers keep working."
    )

    if env == "production":
        raise MasterKeyError(msg)

    import logging
    logging.getLogger("cortexsim.config").warning(
        "Boot proceeding in dev mode but %s", msg
    )


settings = Settings()
