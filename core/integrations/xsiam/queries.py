# core/integrations/xsiam/queries.py
"""Curated XQL for tenant health, plus result shaping.

The ingestion-health *query* is config-as-content: schema drift across tenant
versions is a content edit here, not a code change.
"""
from __future__ import annotations

from typing import Any

# ── CONTRIBUTION POINT (Henry) ──────────────────────────────────────────────
# Finalize against your tenant's metrics schema; verify with the smoke test
# (Task 10). CONTRACT: one row per data source over the trailing window, with
# fields the shaper reads -> source, vendor, product, events, last_seen.
# The placeholder below is intentionally non-functional XQL; the build does not
# depend on it until the smoke test runs it for real.
INGESTION_HEALTH_XQL = """
// TODO(Henry): finalize against the tenant metrics schema. Suggested skeleton:
// dataset = metrics_source
// | comp count() as events, max(_time) as last_seen by source, vendor, product
// | sort desc events
""".strip()


def shape_ingestion_results(reply: dict[str, Any]) -> list[dict[str, Any]]:
    """Map a raw get_query_results reply into the ingestion-health contract.

    Tolerant of envelope variation: results may be {"data": [...]} or a bare
    list, and rows may use alternate field names.
    """
    results = reply.get("results") if isinstance(reply, dict) else None
    if isinstance(results, dict):
        rows = results.get("data") or []
    elif isinstance(results, list):
        rows = results
    else:
        rows = []

    shaped: list[dict[str, Any]] = []
    for r in rows:
        shaped.append({
            "source": r.get("source") or r.get("dataset"),
            "vendor": r.get("vendor"),
            "product": r.get("product"),
            "events": r.get("events") or r.get("count") or 0,
            "last_seen": r.get("last_seen") or r.get("_last_seen"),
        })
    return shaped
