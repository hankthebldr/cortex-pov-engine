"""scripts/dev-up-native.sh binds where CORTEXSIM_BIND says (RG-GUARD-06).

The native launcher started uvicorn with a literal ``--host 0.0.0.0``, so a
host-run SimCore listened on every interface whatever .env said, while
/api/health's api_auth quoted CORTEXSIM_BIND as if it applied. Compose honours
the setting; the native launcher now does too, and logs the address it binds.

Static: the script installs a venv and builds the UI, so it is not executed
here. The guard is that the launch line reads the variable after .env is
sourced, and that no literal every-interface bind remains.
"""
from __future__ import annotations

import re
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "dev-up-native.sh"


def _text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_uvicorn_binds_the_configured_address():
    text = _text()
    launch = [ln for ln in text.splitlines() if "uvicorn\" main:app" in ln]
    assert len(launch) == 1, launch
    assert '--host "${BIND}"' in launch[0], launch[0]
    assert "--host 0.0.0.0" not in text


def test_the_address_comes_from_cortexsim_bind_after_env_is_sourced():
    text = _text()
    sourced = text.index('. "${ENV_FILE}"')
    bind = re.search(r'^BIND="\$\{CORTEXSIM_BIND:-0\.0\.0\.0\}"$', text, re.M)
    assert bind, "BIND must default to 0.0.0.0 only when CORTEXSIM_BIND is unset or empty"
    assert bind.start() > sourced, "BIND is read before .env is sourced"
    assert "${BIND}" in text[text.index("log \"Starting SimCore"):][:200]
