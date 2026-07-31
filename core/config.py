"""
CortexSim configuration — reads from environment variables with sensible defaults.
All settings can be overridden via environment or a .env file.
"""

from pydantic_settings import BaseSettings


_FORBIDDEN_MASTER_KEYS = {"changeme", "change-me", "default", "secret", ""}
_MIN_MASTER_KEY_LEN = 32


class MasterKeyError(RuntimeError):
    """Raised at boot when CORTEXSIM_SECRET is missing, default, or too weak."""


class Settings(BaseSettings):
    CORTEXSIM_PORT: int = 8888
    CORTEXSIM_ENV: str = "production"
    CORTEXSIM_SECRET: str = "changeme"
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

    # UC/TC foreign-key strictness. When false (the default for one release),
    # a scenario whose uc_ref/tc_ref does not resolve against the master index
    # snapshot logs S-10/S-11/S-12 as a WARNING and still loads. When true, the
    # same conditions reject the scenario at boot.
    #
    # It ships false because the crosswalk that re-keys the corpus onto v2.2 is
    # authored incrementally — flipping this before the crosswalk is complete
    # would fail most of the corpus at boot and make the engine unusable. Flip
    # it once `docs/uc_tc_mapping/crosswalk-v2.2.csv` covers every scenario.
    CORTEXSIM_STRICT_REFS: bool = False

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


def validate_master_key(secret: str, *, env: str) -> None:
    """Boot-time guard for CORTEXSIM_SECRET.

    Refuses to start production with default, empty, or too-short secrets.
    In development, logs a warning instead of raising so local pytest runs
    against the in-tree default still work. The credentials layer still
    encrypts in dev — the operator just knows the lab key.

    Why: the master key encrypts every credential the operator entrusts to
    CortexSim. A 'changeme' default would render the layer cryptographically
    worthless against anyone with read access to the SQLite file. Refusing
    to boot is the right failure mode for production.
    """
    stripped = (secret or "").strip()
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
