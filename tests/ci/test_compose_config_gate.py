"""Docker Compose itself parses docker-compose.yml on every push.

Handed over by the deploy lane: e5890a1 moved the port mapping to the long
form with ``host_ip: "${CORTEXSIM_BIND:-}"`` and added a ``logging`` block.
The file was checked with a YAML parser and against the schema embedded in
one local Compose plugin, never by ``docker compose config``, and no CI step
ran Compose on it. The step's first run (CI 43033286928) proved the point:
Compose rejected the empty ``host_ip`` ("invalid ip address"), which every
YAML-level test had accepted, and the mapping went back to the short form.

The step runs in the ``backend`` job (docker is already there for the image
build), with a throwaway CORTEXSIM_SECRET because the file refuses to
interpolate without one. A green step says Compose accepts the file; it does
not say the stack comes up.
"""
from __future__ import annotations

from tests.ci._wiring import ci_jobs, step_index


def test_backend_job_runs_compose_config():
    backend = ci_jobs()["backend"]
    i = step_index(backend, "docker compose")
    step = backend["steps"][i]
    run = step["run"]
    assert "docker compose -f docker-compose.yml config -q" in run, run
    assert "if" not in step and "continue-on-error" not in step, "the parse gate must not be skippable"
    env = {**(backend.get("env") or {}), **(step.get("env") or {})}
    assert env.get("CORTEXSIM_SECRET"), "compose refuses to interpolate without CORTEXSIM_SECRET"
