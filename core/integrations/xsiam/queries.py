# core/integrations/xsiam/queries.py
"""Curated XQL for tenant health, plus THE XQL result-envelope unwrapper.

The ingestion-health *query* is config-as-content: schema drift across tenant
versions is a content edit here, not a code change.

:func:`result_rows` is the one place that knows what an XQL reply looks like.
It used to be three places — this module (correct) and two ad-hoc expressions in
``engine/verifier.py`` (both wrong). The verifier's copies did
``reply.get("results") or []`` and then iterated, but XSIAM returns ``results``
as ``{"data": [...]}``; iterating a dict yields its *keys*, so the row runner
returned zero rows for every healthy tenant and the count runner scored
``len({"data": []}) == 1`` for a query that matched nothing. A row count is a
real measurement, not a sentinel, so those fed straight into `fail` and `pass`
respectively. One unwrapper, imported everywhere, is the fix.
"""
from __future__ import annotations

from typing import Any

from .exceptions import XqlResultTruncated, XsiamQueryError

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


def result_rows(reply: Any) -> list[dict[str, Any]]:
    """Rows out of an XQL ``get_query_results`` reply. THE unwrapper.

    Tolerant of the two envelopes XSIAM is known to use::

        {"status": "SUCCESS", "results": {"data": [ {...}, ... ]}}   # observed
        {"status": "SUCCESS", "results": [ {...}, ... ]}             # bare list

    A reply with no ``results`` key at all is an empty result set — that is the
    honest reading and it is what ``shape_ingestion_results({})`` has always
    meant.

    A reply whose ``results`` is present but is neither an object nor an array
    is a **parse failure, not an empty tenant**, and it raises. That distinction
    is the whole point of this function: returning ``[]`` on a shape we do not
    understand lands on `fail` (zero rows < the authored floor) and blames the
    customer's detection for CortexSim's bug. Raising lands on `pending` via the
    probe/verifier exception paths, which is the truth — we did not measure.
    """
    return [r for r in _page(reply) if isinstance(r, dict)]


def _page(reply: Any) -> list[Any]:
    """The raw ``data`` array of a reply, before non-object entries are dropped.

    Its length is what the API compared against ``limit``, so it is the length
    :func:`complete_rows` must compare too.
    """
    if not isinstance(reply, dict):
        raise XsiamQueryError(
            f"unrecognised XQL reply: expected an object, got {type(reply).__name__}"
        )
    if "results" not in reply:
        return []
    results = reply["results"]
    if results is None:
        rows: Any = []
    elif isinstance(results, dict):
        rows = results.get("data") or []
    elif isinstance(results, list):
        rows = results
    else:
        raise XsiamQueryError(
            f"unrecognised XQL results envelope: expected object or array, got "
            f"{type(results).__name__}"
        )
    if not isinstance(rows, list):
        raise XsiamQueryError(
            f"unrecognised XQL results.data: expected array, got {type(rows).__name__}"
        )
    return rows


def complete_rows(reply: Any, *, limit: int) -> list[dict[str, Any]]:
    """The rows of a reply that is provably the whole answer, for measuring.

    A page fetched at ``limit`` that comes back shorter than ``limit`` is the
    whole result set. A page that comes back full may be the first slice of a
    larger one: a row count, a distinct count or a ratio read off it measures
    the page, not the tenant, so it raises :class:`XqlResultTruncated` and the
    caller resolves ``pending``. The one exception is a reply whose
    ``number_of_results`` says the set is exactly this page.

    A ``number_of_results`` larger than the page is not taken as the count.
    No tenant has confirmed what the field counts (PM-XQL-03), and scoring an
    unconfirmed number is the same mistake as scoring a partial page.

    A count pushed into XQL (``| comp count()``) comes back as one row and is
    never affected.
    """
    page = _page(reply)                # raises on an envelope we do not know
    if len(page) >= limit:
        total = reply.get("number_of_results")
        if isinstance(total, bool) or total != len(page):
            raise XqlResultTruncated(
                f"XQL returned {len(page)} row(s) at limit {limit} and "
                f"number_of_results={total!r}; the page may hold only part of "
                f"the answer, so it is not a measurement"
            )
    return [r for r in page if isinstance(r, dict)]


def row_count(reply: Any) -> int:
    """How many rows an XQL reply matched.

    Prefers the tenant's own ``number_of_results`` when it is an int (it counts
    the full result set, not just the page this call fetched), and falls back to
    the unwrapped row count. The fallback is reached whenever the field is
    absent — which is precisely why it must be correct rather than lucky. We do
    not have a tenant to confirm the field is always present, so both paths are
    treated as load-bearing.

    Raises :class:`XsiamQueryError` on an unrecognised envelope — never 0.
    """
    rows = result_rows(reply)          # raises on an envelope we do not know
    total = reply.get("number_of_results")
    if isinstance(total, bool):
        # bool is an int subclass; a JSON `true` here is drift, not a count.
        total = None
    if isinstance(total, int) and total >= 0:
        return total
    return len(rows)


def shape_ingestion_results(reply: dict[str, Any]) -> list[dict[str, Any]]:
    """Map a raw get_query_results reply into the ingestion-health contract.

    Row *field* names are tolerated in both spellings; the *envelope* is not —
    see :func:`result_rows`.
    """
    shaped: list[dict[str, Any]] = []
    for r in result_rows(reply):
        shaped.append({
            "source": r.get("source") or r.get("dataset"),
            "vendor": r.get("vendor"),
            "product": r.get("product"),
            "events": r.get("events") or r.get("count") or 0,
            "last_seen": r.get("last_seen") or r.get("_last_seen"),
        })
    return shaped
