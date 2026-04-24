"""
CortexSim configuration — reads from environment variables with sensible defaults.
All settings can be overridden via environment or a .env file.
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    CORTEXSIM_PORT: int = 8888
    CORTEXSIM_ENV: str = "production"
    CORTEXSIM_SECRET: str = "changeme"
    CORTEXSIM_BASE_DIR: str = "/app"
    CORTEXSIM_LOG_FILE: str = "logs/cortexsim.log"
    CORTEXSIM_SCENARIOS_DIR: str = "scenarios"
    CORTEXSIM_STATIC_DIR: str = "core/static"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
