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

    msg = (
        f"CORTEXSIM_SECRET is misconfigured: "
        f"{'empty or default value' if is_forbidden else f'shorter than {_MIN_MASTER_KEY_LEN} bytes'}. "
        f"Set CORTEXSIM_SECRET to a high-entropy value (>= {_MIN_MASTER_KEY_LEN} bytes) before booting. "
        f"Recommended: `export CORTEXSIM_SECRET=$(op read 'op://Private/cortexsim-master/key')`"
    )

    if env == "production":
        raise MasterKeyError(msg)

    import logging
    logging.getLogger("cortexsim.config").warning(
        "Boot proceeding in dev mode but %s", msg
    )


settings = Settings()
