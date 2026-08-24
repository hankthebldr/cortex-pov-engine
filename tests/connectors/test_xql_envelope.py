"""F-01 / F-02 — the XQL result envelope, and why parsing it wrong scored runs.

Every test here fails on the pre-fix tree. That matters more than usual: the
bug was 100 % provable offline and was simply never asserted, so "the suite is
green" was never evidence about this code path.

The shape below is not a guess. It is what this repo's own client test asserts
(``tests/integration/xsiam/test_client_xql.py`` ->
``reply["results"]["data"][0]["source"] == "okta"``) and what
``queries.shape_ingestion_results`` has always unwrapped.
"""
from __future__ import annotations

import asyncio

import pytest


def _reply(rows: int, *, number_of_results=None, bare_list=False):
    data = [{"i": i} for i in range(rows)]
    out = {"status": "SUCCESS", "results": data if bare_list else {"data": data}}
    if number_of_results is not None:
        out["number_of_results"] = number_of_results
    return out


class _Client:
    """Minimal XSIAM client stand-in — run_xql is the only surface used."""

    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    async def run_xql(self, query, timeframe, **kw):
        self.calls += 1
        return self.reply


# ── F-01: the row runner returned [] for every healthy tenant ───────────────


def test_rows_runner_returns_the_rows_the_tenant_actually_sent():
    """Pre-fix this returned [] — `results` is a dict, and iterating a dict
    yields its KEYS ("data"), which all fail the isinstance(r, dict) filter."""
    from engine.verifier import xsiam_rows_runner

    runner = xsiam_rows_runner(_Client(_reply(50)), {"relativeTime": 1000})
    rows = asyncio.run(runner("dataset = xdr_data"))
    assert len(rows) == 50, "the row runner must see the tenant's rows"


def test_rows_runner_also_handles_the_bare_list_envelope():
    from engine.verifier import xsiam_rows_runner

    runner = xsiam_rows_runner(_Client(_reply(3, bare_list=True)), {})
    assert len(asyncio.run(runner("q"))) == 3


def test_zero_rows_from_a_healthy_tenant_is_still_zero():
    """The fix must not make everything look populated — a real empty result
    set is the honest answer `fail` is built on."""
    from engine.verifier import xsiam_rows_runner

    runner = xsiam_rows_runner(_Client(_reply(0)), {})
    assert asyncio.run(runner("q")) == []


# ── F-02: the count runner scored a zero-row query as PASS ─────────────────


def test_count_runner_counts_zero_rows_as_zero():
    """Pre-fix: len({"data": []}) == 1, so a detection that never fired scored
    `rows(1) >= want(1)` -> PASS and the POV report rendered it green."""
    from engine.verifier import xsiam_query_runner

    runner = xsiam_query_runner(_Client(_reply(0)), {})
    assert asyncio.run(runner("q")) == 0


def test_count_runner_counts_fifty_rows_as_fifty():
    """The mirror failure: pre-fix a 50-row reply also counted 1, so a real
    detection failed an `expected_rows_min: 5` floor."""
    from engine.verifier import xsiam_query_runner

    runner = xsiam_query_runner(_Client(_reply(50)), {})
    assert asyncio.run(runner("q")) == 50


def test_count_runner_prefers_the_tenants_own_total():
    """number_of_results counts the whole result set, not just this page."""
    from engine.verifier import xsiam_query_runner

    runner = xsiam_query_runner(_Client(_reply(100, number_of_results=4821)), {})
    assert asyncio.run(runner("q")) == 4821


def test_count_runner_ignores_a_boolean_number_of_results():
    """bool is an int subclass; a JSON `true` here is drift, not a count."""
    from engine.verifier import xsiam_query_runner

    runner = xsiam_query_runner(_Client(_reply(7, number_of_results=True)), {})
    assert asyncio.run(runner("q")) == 7


# ── the mandated guard: an unknown envelope must RAISE, never return 0 ─────


@pytest.mark.parametrize("bad", [
    {"status": "SUCCESS", "results": "unexpected-string"},
    {"status": "SUCCESS", "results": 42},
    {"status": "SUCCESS", "results": {"data": "not-an-array"}},
    "not-a-dict-at-all",
])
def test_an_unrecognised_envelope_raises_rather_than_measuring_zero(bad):
    """The tempting "fix" is tolerance: return 0/[] for anything unknown. That
    preserves the exact bug — a parse failure scored as a detection failure.

    Raising routes through verify_results' exception path to `pending`, which
    is the truth: we did not measure."""
    from engine.verifier import xsiam_query_runner, xsiam_rows_runner
    from integrations.xsiam.exceptions import XsiamQueryError

    with pytest.raises(XsiamQueryError):
        asyncio.run(xsiam_query_runner(_Client(bad), {})("q"))
    with pytest.raises(XsiamQueryError):
        asyncio.run(xsiam_rows_runner(_Client(bad), {})("q"))


def test_an_unrecognised_envelope_lands_on_pending_not_fail():
    """The end-to-end version of the guard above, at the verdict level.

    `fail` here would say the customer's detection did not fire. `pass` would
    say it did. Both are claims CortexSim has no basis for."""
    from engine.verifier import verify_results, xsiam_query_runner

    class _R:
        verification_xql = "dataset = xdr_data"
        kpi_verdict = "pending"
        verified_at = None
        id = 1
        detection_id = "bioc-1"
        ttp_ref = None

    r = _R()
    runner = xsiam_query_runner(_Client({"results": "unexpected-string"}), {})
    counts = asyncio.run(verify_results([r], runner))
    assert r.kpi_verdict == "pending"
    assert counts["fail"] == 0 and counts["pass"] == 0


def test_no_results_key_at_all_is_an_empty_set_not_an_error():
    """A SUCCESS reply with nothing to report is legitimately empty; only a
    results value we cannot READ is a parse failure."""
    from integrations.xsiam.queries import result_rows, row_count

    assert result_rows({"status": "SUCCESS"}) == []
    assert row_count({"status": "SUCCESS"}) == 0
    assert result_rows({"status": "SUCCESS", "results": None}) == []


def test_shape_ingestion_results_uses_the_same_unwrapper():
    """One unwrapper, not two. This function was the only correct copy in the
    repo; it must now be built ON the shared one rather than beside it."""
    from integrations.xsiam.queries import shape_ingestion_results

    rows = shape_ingestion_results({"results": {"data": [
        {"source": "okta_audit", "vendor": "okta", "product": "idp",
         "events": 1234, "last_seen": 1717200000000}]}})
    assert rows[0]["source"] == "okta_audit"

    from integrations.xsiam.exceptions import XsiamQueryError
    with pytest.raises(XsiamQueryError):
        shape_ingestion_results({"results": "drifted"})


def test_exactly_one_unwrapper_exists_in_the_tree():
    """PART 7 item 6, mechanically. The next edit to one of two copies is how
    F-01 happened; a cheap structural check is cheaper than the next drift.

    AST rather than grep, deliberately: the fixed code *documents* the old
    expression in its docstrings, and a text search cannot tell an executable
    copy from a comment describing why the copy was removed.
    """
    import ast
    import pathlib

    core = pathlib.Path(__file__).resolve().parents[2] / "core"
    allowed = {
        # THE unwrapper.
        core / "integrations" / "xsiam" / "queries.py",
        # Pass-through only: GET /api/xsiam/tenants/{n}/xql/{id} forwards the
        # envelope to the caller verbatim. It never counts or iterates it, so it
        # cannot mis-measure — it hands the raw reply to a human. If that
        # endpoint ever starts INTERPRETING `results`, it must use result_rows.
        core / "api" / "xsiam.py",
    }
    offenders: list[str] = []

    for path in core.rglob("*.py"):
        if "__pycache__" in str(path) or path in allowed:
            continue
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:  # pragma: no cover — another agent mid-edit
            continue
        for node in ast.walk(tree):
            # <expr>.get("results") / <expr>["results"] outside the one unwrapper
            hit = (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "results"
            ) or (
                isinstance(node, ast.Subscript)
                and isinstance(node.slice, ast.Constant)
                and node.slice.value == "results"
            )
            if hit:
                offenders.append(f"{path}:{node.lineno}")

    assert offenders == [], (
        f"ad-hoc XQL envelope unwrapping found at {offenders}; import "
        f"integrations.xsiam.queries.result_rows / row_count instead")
