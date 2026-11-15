"""SIM-MP-038: the collector queries, run over the records the eal steps render.

No tenant runs these queries, so this module evaluates their XQL text with a
small interpreter for the subset they use (filter / alter / comp / fields,
if, coalesce, in, contains, parse_timestamp, timestamp_diff,
json_extract_scalar, to_string, to_integer). Anything outside that subset
raises: a query the interpreter cannot read must not pass as "no rows".
It reads the query the scenario declares (the card's query verbatim, pinned
by test_sim_mp_038_shape.py), so a change to the query changes the result.

What it does not prove: that XSIAM's parser, null rules or collector field
mapping agree. tenant-verified stays 0.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import pytest
import yaml

# Bound at collection: see test_sim_mp_038_shape.py on scripts/eal_simulator.
from eal_simulator.registry import get_default_registry

REPO_ROOT = Path(__file__).resolve().parents[2]
SCENARIO = (REPO_ROOT / "scenarios" / "multi_plane"
            / "mp-038-pink-cl-cri-1147-vishing-passkey-m365-extortion.yml")
PRINCIPAL = "cortexsim-mp-038-pink@cortexsim-canary.invalid"
COLLECTOR_DATASET = "cortexsim_saas_raw"


# ── XQL subset interpreter ──────────────────────────────────────────────────

_TOKEN = re.compile(
    r'\s*(?:(?P<str>"(?:[^"\\]|\\.)*")|(?P<num>\d+(?:\.\d+)?)'
    r'|(?P<op>!=|>=|<=|=|>|<|\(|\)|,)|(?P<id>[A-Za-z_][A-Za-z0-9_]*))')
_NULL = object()  # the literal `null`, distinct from a missing field


def _tokens(text: str) -> list[tuple[str, Any]]:
    out, pos = [], 0
    text = text.strip()
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m or m.end() == pos:
            raise ValueError(f"cannot tokenise XQL at {text[pos:pos + 30]!r}")
        pos = m.end()
        kind = m.lastgroup
        val = m.group(kind)
        if kind == "str":
            val = json.loads(val)
        elif kind == "num":
            val = float(val) if "." in val else int(val)
        out.append((kind, val))
    return out


def _as_str(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _json_scalar(obj: Any, path: str) -> Any:
    if not path.startswith("$."):
        raise ValueError(f"unsupported JSON path {path!r}")
    node = json.loads(obj) if isinstance(obj, str) else obj
    for part in path[2:].split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return None if node is None or isinstance(node, (dict, list)) else _as_str(node)


def _parse_ts(fmt: str, s: Any) -> Any:
    if fmt != "%Y-%m-%dT%H:%M:%E*S%Ez":
        raise ValueError(f"unsupported parse_timestamp format {fmt!r}")
    return None if s is None else datetime.fromisoformat(s)


def _ts_diff(a: Any, b: Any, unit: str) -> Any:
    if unit != "SECOND":
        raise ValueError(f"unsupported timestamp_diff unit {unit!r}")
    return None if a is None or b is None else int((a - b).total_seconds())


_FUNCS: dict[str, Callable[..., Any]] = {
    "coalesce": lambda *a: next((v for v in a if v is not None), None),
    "json_extract_scalar": _json_scalar,
    "parse_timestamp": _parse_ts,
    "timestamp_diff": _ts_diff,
    "to_string": _as_str,
    "to_integer": lambda v: None if v is None else int(v),
}

Row = dict[str, Any]
Expr = Callable[[Row], Any]


class _Parser:
    def __init__(self, text: str):
        self.toks = _tokens(text)
        self.i = 0

    def peek(self, val: Any = None) -> bool:
        if self.i >= len(self.toks):
            return False
        return val is None or self.toks[self.i][1] == val

    def take(self, val: Any = None) -> Any:
        if not self.peek(val):
            got = self.toks[self.i] if self.i < len(self.toks) else "end"
            raise ValueError(f"expected {val!r}, got {got!r}")
        self.i += 1
        return self.toks[self.i - 1][1]

    def done(self) -> None:
        if self.i != len(self.toks):
            raise ValueError(f"unparsed XQL from token {self.toks[self.i]!r}")

    def expr(self) -> Expr:
        left = self.conj()
        while self.peek("or"):
            self.take()
            a, b = left, self.conj()
            left = lambda r, a=a, b=b: bool(a(r)) or bool(b(r))
        return left

    def conj(self) -> Expr:
        left = self.cmp()
        while self.peek("and"):
            self.take()
            a, b = left, self.cmp()
            left = lambda r, a=a, b=b: bool(a(r)) and bool(b(r))
        return left

    def cmp(self) -> Expr:
        left = self.atom()
        if self.peek("in"):
            self.take()
            self.take("(")
            items = [self.atom()]
            while self.peek(","):
                self.take()
                items.append(self.atom())
            self.take(")")
            return lambda r: (v := left(r)) is not None and v in [f(r) for f in items]
        if self.peek("contains"):
            self.take()
            right = self.atom()
            return lambda r: (v := left(r)) is not None and right(r) in v
        for op in ("!=", ">=", "<=", "=", ">", "<"):
            if self.peek(op):
                self.take()
                right = self.atom()
                return self._compare(op, left, right)
        return left

    @staticmethod
    def _compare(op: str, left: Expr, right: Expr) -> Expr:
        def run(r: Row) -> bool:
            a, b = left(r), right(r)
            if b is _NULL or a is _NULL:
                other = a if b is _NULL else b
                if op == "=":
                    return other is None
                if op == "!=":
                    return other is not None
                raise ValueError(f"cannot apply {op} to null")
            if a is None or b is None:
                return False  # a null operand compares to null: the row drops
            return {"=": a == b, "!=": a != b, ">=": a >= b,
                    "<=": a <= b, ">": a > b, "<": a < b}[op]
        return run

    def atom(self) -> Expr:
        kind, val = self.toks[self.i]
        self.i += 1
        if kind in ("str", "num"):
            return lambda r, v=val: v
        if kind == "op" and val == "(":
            inner = self.expr()
            self.take(")")
            return inner
        if kind != "id":
            raise ValueError(f"unexpected token {val!r}")
        if val == "null":
            return lambda r: _NULL
        if not self.peek("("):
            return lambda r, f=val: r.get(f)
        self.take("(")
        args: list[Expr] = []
        if not self.peek(")"):
            args.append(self.expr())
            while self.peek(","):
                self.take()
                args.append(self.expr())
        self.take(")")
        if val == "if":
            if len(args) != 3:
                raise ValueError("if() takes three arguments")
            c, t, e = args
            return lambda r: _unnull(t(r)) if c(r) is True else _unnull(e(r))
        if val not in _FUNCS:
            raise ValueError(f"unsupported XQL function {val}()")
        fn = _FUNCS[val]
        return lambda r: fn(*[_unnull(a(r)) for a in args])


def _unnull(v: Any) -> Any:
    return None if v is _NULL else v


def _agg(fn: str, values: list[Any]) -> Any:
    present = [v for v in values if v is not None]
    if fn == "min":
        return min(present) if present else None
    if fn == "max":
        return max(present) if present else None
    if fn == "count_distinct":
        return len({json.dumps(v, default=str) for v in present})
    if fn == "values":
        return sorted({json.dumps(v, default=str) for v in present})
    raise ValueError(f"unsupported comp aggregate {fn}()")


def run_xql(query: str, records: list[Row]) -> list[Row]:
    lines = [ln.strip() for ln in query.strip().split("\n")]
    head = lines[0]
    m = re.fullmatch(r"dataset = (\w+)", head)
    if not m or m.group(1) != COLLECTOR_DATASET:
        raise ValueError(f"only {COLLECTOR_DATASET} queries are evaluated here, got {head!r}")
    rows = [dict(r) for r in records]
    for line in lines[1:]:
        if not line.startswith("| "):
            raise ValueError(f"unsupported pipeline line {line!r}")
        stage, _, body = line[2:].partition(" ")
        p = _Parser(body)
        if stage == "filter":
            pred = p.expr()
            p.done()
            rows = [r for r in rows if pred(r) is True]
        elif stage == "alter":
            assigns = [(p.take(), p.take("="), p.expr())]
            while p.peek(","):
                p.take()
                assigns.append((p.take(), p.take("="), p.expr()))
            p.done()
            for r in rows:
                for name, _eq, e in assigns:
                    r[name] = _unnull(e(r))
        elif stage == "comp":
            aggs = []
            while True:
                fn = p.take()
                p.take("(")
                arg = None if p.peek(")") else p.expr()
                p.take(")")
                p.take("as")
                aggs.append((fn, arg, p.take()))
                if not p.peek(","):
                    break
                p.take()
            by = []
            if p.peek("by"):
                p.take()
                by.append(p.take())
                while p.peek(","):
                    p.take()
                    by.append(p.take())
            p.done()
            groups: dict[tuple, list[Row]] = {}
            for r in rows:
                groups.setdefault(tuple(_as_str(r.get(f)) for f in by), []).append(r)
            rows = []
            for key, members in groups.items():
                out = dict(zip(by, key))
                for fn, arg, name in aggs:
                    out[name] = (len(members) if fn == "count" and arg is None
                                 else _agg(fn, [arg(r) for r in members]))
                rows.append(out)
        elif stage == "fields":
            names = [p.take()]
            while p.peek(","):
                p.take()
                names.append(p.take())
            p.done()
            rows = [{n: r.get(n) for n in names} for r in rows]
        else:
            raise ValueError(f"unsupported XQL stage {stage!r}")
    return rows


# ── Rendering one or two runs of the scenario ───────────────────────────────

@pytest.fixture(scope="module")
def scenario() -> dict[str, Any]:
    return yaml.safe_load(SCENARIO.read_text(encoding="utf-8"))


def _collector_queries(scenario: dict[str, Any]) -> dict[str, str]:
    return {d["detection_id"]: d["verification_xql"]
            for s in scenario["steps"] for d in s.get("expected_detections") or []
            if d["verification_xql"].startswith(f"dataset = {COLLECTOR_DATASET}")}


def _render(scenario: dict[str, Any], *, run: str, shift_seconds: int = 0) -> list[Row]:
    """Every eal step's records, as one run renders them. ``shift_seconds``
    moves the run earlier, so two calls model two runs launched apart."""
    reg = get_default_registry()
    records: list[Row] = []
    for st in scenario["steps"]:
        if st.get("channel") != "eal":
            continue
        cls = reg.get(st["eal"]["plugin"])
        params = dict(st["eal"]["params"])
        params["event_time_offset_seconds"] -= shift_seconds
        model = cls.Meta.params_model(**params, collector_url="https://collector.lab.invalid/x")
        records += cls().records_for(model, sim_run_id=f"{run}-{st['id']}", iteration=0)
    return records


def test_every_collector_query_finds_one_runs_records(scenario):
    """D5-D10, CR-MP-0038 and CR-MP-0038-EXTORT each return a row over one run."""
    queries = _collector_queries(scenario)
    assert len(queries) == 8
    records = _render(scenario, run="one")
    empty = [d for d, q in queries.items() if not run_xql(q, records)]
    assert empty == []


def test_extortion_chain_holds_when_two_runs_overlap(scenario):
    """Two Pink runs 20 minutes apart in one window. CR-MP-0038-EXTORT must
    still return a row: measured from the first download, a later run's
    downloads cannot push the gap to the first extortion message below zero."""
    q = _collector_queries(scenario)["CR-MP-0038-EXTORT"]
    records = (_render(scenario, run="earlier", shift_seconds=1200)
               + _render(scenario, run="later"))
    rows = run_xql(q, records)
    assert len(rows) == 1 and rows[0]["UserId"] == PRINCIPAL


def test_sign_in_chain_needs_a_passkey_not_any_security_info(scenario):
    """CR-MP-0038's passkey stage reads the passkey the way D6 does. A
    security-info registration that is not a passkey must not complete it."""
    q = _collector_queries(scenario)["CR-MP-0038"]
    records = _render(scenario, run="one")
    audit = [r for r in records if r.get("activityDisplayName") == "User registered security info"]
    assert len(audit) == 1 and "passkey" in audit[0]["resultReason"]
    assert len(run_xql(q, records)) == 1
    audit[0]["resultReason"] = "User registered Microsoft Authenticator app"
    assert run_xql(q, records) == []


def test_records_name_one_principal(scenario):
    """target_user and the canary plant name the same principal, so no record
    carries a second canary-domain user besides the colleague recipients."""
    text = json.dumps(_render(scenario, run="one"))
    users = set(re.findall(r"[\w.+-]+@cortexsim-canary\.invalid", text))
    assert {u for u in users if not re.fullmatch(r"colleague-\d+@cortexsim-canary\.invalid", u)} == {PRINCIPAL}
