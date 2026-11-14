"""A fake Cortex tenant for the tenant-reader tests: an ``HttpFetcher`` by path.

``FakeTenant`` answers the five read routes the reader uses, each from canned
rows, in the wire shapes the vendor pages document (spec §3.4):

* ``issues`` — a window ``issue/search`` (no filter on ``id``): pages
  ``issues[search_from:search_to]``, with ``FILTER_COUNT`` = the window's rows
  and ``TOTAL_COUNT`` = a tenant-wide number, as the 2026-11-12 spike observed;
* ``issues_by_id`` — an ``issue/search`` filtered by ``id`` ``in``: the rows of
  ``probe`` (default: ``issues``) whose id is in the filter, ints or strings;
* ``cases`` — ``case/search``, filtered by ``case_id`` ``in`` when one is sent;
* ``endpoints`` — ``endpoints/get_endpoint``, filtered by ``hostname`` ``in``,
  in the lowercase ``reply.endpoints`` envelope;
* ``tenant_info`` — ``system/get_tenant_info``, the object reply
  ``{"reply": tenant_info}`` (default ``TENANT_INFO``: licence fields and no
  version field, as PANW's page documents it).

Every request body is recorded under its route in ``calls``. ``status`` maps a
route to an HTTP status it answers instead; ``replies`` maps a route to a raw
reply (a dict, or a callable taking the ``request_data``) for envelope tests.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Optional, Union

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "tenant_reader"

#: Pass as ``filter_count`` to leave ``FILTER_COUNT`` out of the window reply.
OMIT = object()

ROUTES = ("issues", "issues_by_id", "cases", "endpoints", "tenant_info")

#: The documented ``get_tenant_info`` reply: licence fields, no version field.
TENANT_INFO = {"xsiam_premium_expiration": 1798761600000,
               "purchased_xsiam_premium": {"users": 10, "gb": 100, "agents": 500}}


def fixture(name: str) -> dict:
    """One reply file from ``tests/fixtures/tenant_reader/``, parsed."""
    return json.loads((FIXTURES / name).read_text())


def _filter_values(request_data: dict, field: str) -> Optional[list]:
    for f in request_data.get("filters") or []:
        if f.get("field") == field and f.get("operator") == "in":
            return list(f.get("value") or [])
    return None


class FakeTenant:
    def __init__(
        self,
        *,
        issues: Optional[list[dict]] = None,
        probe: Optional[list[dict]] = None,
        cases: Optional[list[dict]] = None,
        endpoints: Optional[list[dict]] = None,
        filter_count: Any = None,
        tenant_total: int = 4200,
        status: Optional[dict[str, int]] = None,
        replies: Optional[dict[str, Union[dict, Callable[[dict], dict]]]] = None,
        tenant_info: Optional[dict] = None,
    ) -> None:
        self.issues = list(issues or [])
        self.probe = list(probe) if probe is not None else None
        self.cases = list(cases or [])
        self.endpoints = list(endpoints or [])
        self.filter_count = filter_count
        self.tenant_total = tenant_total
        self.status = dict(status or {})
        self.replies = dict(replies or {})
        self.tenant_info = dict(TENANT_INFO if tenant_info is None else tenant_info)
        self.calls: dict[str, list[dict]] = {r: [] for r in ROUTES}
        self.urls: list[str] = []

    @property
    def total_calls(self) -> int:
        return sum(len(v) for v in self.calls.values())

    def route_for(self, url: str, request_data: dict) -> Optional[str]:
        if "issue/search" in url:
            return "issues_by_id" if _filter_values(request_data, "id") is not None else "issues"
        if "case/search" in url:
            return "cases"
        if "endpoints/get_endpoint" in url:
            return "endpoints"
        if "system/get_tenant_info" in url:
            return "tenant_info"
        return None

    def __call__(self, method: str, url: str, headers: dict, body: Optional[bytes],
                 timeout: float) -> "tuple[int, str]":
        assert method == "POST", method
        self.urls.append(url)
        request_data = json.loads(body or b"{}").get("request_data") or {}
        route = self.route_for(url, request_data)
        if route is None:
            return 404, json.dumps({"reply": {"err_msg": "no such path"}})
        self.calls[route].append(request_data)
        if route in self.status:
            return self.status[route], "{}"
        if route in self.replies:
            reply = self.replies[route]
            return 200, json.dumps(reply(request_data) if callable(reply) else reply)
        return 200, json.dumps(getattr(self, f"_{route}")(request_data))

    # ── canned answers ────────────────────────────────────────────────────

    def _page(self, rows: list[dict], request_data: dict) -> list[dict]:
        start = int(request_data.get("search_from", 0))
        end = int(request_data.get("search_to", start + 100))
        return rows[start:end]

    def _issues(self, request_data: dict) -> dict:
        reply: dict[str, Any] = {"DATA": self._page(self.issues, request_data),
                                 "TOTAL_COUNT": self.tenant_total}
        if self.filter_count is not OMIT:
            reply["FILTER_COUNT"] = (len(self.issues) if self.filter_count is None
                                     else self.filter_count)
        return {"reply": reply}

    def _issues_by_id(self, request_data: dict) -> dict:
        want = {str(v) for v in _filter_values(request_data, "id") or []}
        pool = self.probe if self.probe is not None else self.issues
        rows = [r for r in pool if str(r.get("id")) in want]
        return {"reply": {"DATA": rows, "TOTAL_COUNT": self.tenant_total,
                          "FILTER_COUNT": len(rows)}}

    def _cases(self, request_data: dict) -> dict:
        ids = _filter_values(request_data, "case_id")
        rows = (self.cases if ids is None
                else [c for c in self.cases if str(c.get("case_id")) in {str(i) for i in ids}])
        return {"reply": {"DATA": self._page(rows, request_data),
                          "TOTAL_COUNT": self.tenant_total, "FILTER_COUNT": len(rows)}}

    def _endpoints(self, request_data: dict) -> dict:
        names = _filter_values(request_data, "hostname")
        want = {str(n).lower() for n in names or []}
        rows = (self.endpoints if names is None
                else [e for e in self.endpoints
                      if str(e.get("endpoint_name", "")).lower() in want])
        page = self._page(rows, request_data)
        return {"reply": {"total_count": len(rows), "result_count": len(page),
                          "endpoints": page}}

    def _tenant_info(self, request_data: dict) -> dict:
        return {"reply": dict(self.tenant_info)}
