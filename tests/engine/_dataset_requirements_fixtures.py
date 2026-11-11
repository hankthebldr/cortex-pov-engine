"""Shared fixtures for the dataset requirement registry tests."""
from __future__ import annotations

GOOD = {"panw_ngfw_traffic_raw": {
    "produced_by": "strata-ngfw",
    "cortex_side": [{"kind": "data_source", "name": None, "verified": {"by": "", "on": "", "how": ""}}],
    "source_side": [{"product": "strata-ngfw", "requirement": "log_forwarding",
                     "detail": "Traffic logs forwarded to the Cortex tenant",
                     "verified": {"by": "", "on": "", "how": ""}}],
    "emulated_by": ["ngfw_eal_emitter"]}}
