"""End-to-end smoke tests for CortexSim.

These tests require a *running* SimCore instance reachable at
``CORTEXSIM_SMOKE_URL`` (default ``http://localhost:8888``).  They are
deliberately separate from unit tests under ``tests/`` because they exercise
real HTTP + DB + orchestrator, not isolated modules.

Run via ``scripts/smoke/lab-smoke.sh`` (which brings up compose first) or
directly with ``pytest tests/smoke -v`` against an already-running SimCore.
"""
