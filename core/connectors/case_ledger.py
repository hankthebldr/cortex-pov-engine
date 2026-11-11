"""Run case ledger: did a run's chain stay in one native case, and if not, why.

PURE. No I/O, no tenant reads. Callers hand it the run's matched alerts and the
cases the tenant built from them (``get_incidents`` rows).

Two facts about XSIAM drive the classification:

* A resolved case is reopened for a matching new issue ONLY when it is
  "Resolved - Auto Resolved" (``resolved_auto_resolve``). Any other resolved
  status SEALS it, and the next issue starts a new case.
* Low-severity issues never create or join a case.

DISPOSITION IS NEVER EVIDENCE. Nothing here feeds ``detected``/``missed``, MTTD
or a detection-family verdict. The single permitted consumer of ``merge_class``
is the Cross-Source Correlation Rate guard, which withholds a FAIL (never a
PASS) when the value is ``SPLIT_SEALED``. Render ``CASE_DISCLAIMER`` with it.

An unrecognised tenant shape raises :class:`CaseShapeError`; it is never read
as ``open`` or as a default.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence

from connectors.base import ObservedAlert, ObservedIncident, coerce_utc
from connectors.socfw import TRIAGE_RESOLVE_COMMENT_PREFIX

MEDIUM_PLUS = frozenset({"medium", "high", "critical"})

CASE_CLOSED_BY = ("open", "socfw_auto_triage", "auto_resolved", "other_resolved")
ISSUE_CLOSED_BY = CASE_CLOSED_BY + ("socfw_ai_close", "unread")
MERGE_CLASSES = ("ONE_CASE", "SPLIT_SEALED", "SPLIT_NATIVE", "LOW_ONLY_NO_CASE",
                 "NO_CASE_YET", "PENDING")

CASE_DISCLAIMER = "Tenant disposition is shown for context. It is not detection evidence."

# Terminal and open case statuses, from secops-framework
# Packs/soc-framework-nist-ir/Scripts/SOCFWCaseSelect/SOCFWCaseSelect.py:25-28
# (TERMINAL_STATUSES, which also lists the bare "resolved"). Anything not in
# this map is a shape we do not understand and raises.
CASE_STATUS_MAP: dict[str, str] = {
    "new": "open",
    "under_investigation": "open",
    "resolved_auto_resolve": "auto_resolved",
    "resolved_threat_handled": "other_resolved",
    "resolved_known_issue": "other_resolved",
    "resolved_duplicate": "other_resolved",
    "resolved_false_positive": "other_resolved",
    "resolved_true_positive": "other_resolved",
    "resolved_security_testing": "other_resolved",
    "resolved_other": "other_resolved",
    "resolved": "other_resolved",
}

_ISSUE_OPEN = frozenset({"new", "in progress", "under investigation"})


class CaseShapeError(ValueError):
    """A tenant case/issue value outside the vocabulary this module pins."""


# Everything a corrupt STORED ledger can raise while it is read back. Both the
# refresh and the pair endpoint catch exactly this set and degrade with a code.
LEDGER_READ_ERRORS = (CaseShapeError, KeyError, TypeError, AttributeError)


@dataclass(frozen=True)
class LedgerAlert:
    external_id: str
    observed_at: datetime
    severity: Optional[str]
    case_id: Optional[str]
    issue_status: Optional[str]
    issue_reason: Optional[str]
    issue_comment: Optional[str]


@dataclass(frozen=True)
class LedgerCase:
    case_id: str
    status: str
    resolve_comment: Optional[str]
    resolved_at: Optional[datetime]
    starred: bool
    assigned: bool
    xdr_url: Optional[str]
    alert_count: int


# ── construction from tenant reads ────────────────────────────────────────

def ledger_alert(a: ObservedAlert, issue_status: Optional[dict[str, Optional[str]]]) -> LedgerAlert:
    """``issue_status`` is one entry of B2's map; ``None`` leaves the issue unread."""
    st = issue_status or {}
    return LedgerAlert(
        external_id=str(a.external_id) if a.external_id is not None else "",
        observed_at=coerce_utc(a.observed_at) or a.observed_at,
        severity=(a.severity or None) and str(a.severity).lower(),
        case_id=str(a.incident_id) if a.incident_id not in (None, "") else None,
        issue_status=st.get("status"),
        issue_reason=st.get("reason"),
        issue_comment=st.get("comment"),
    )


def _opt_str(v: Any) -> Optional[str]:
    return None if v is None or v == "" else str(v)


def _flag(raw: dict, key: str) -> bool:
    v = raw.get(key)
    if v is None:
        return False
    if isinstance(v, bool):
        return v
    raise CaseShapeError(f"case field {key!r} is not a boolean: {v!r}")


def ledger_case_from_incident(i: ObservedIncident) -> LedgerCase:
    """One ``get_incidents`` row -> :class:`LedgerCase`.

    Keys read from ``i.raw`` (public ``get_incidents`` row): ``resolve_comment``,
    ``resolved_timestamp`` (epoch ms), ``starred`` (bool), ``assigned_user_mail``
    (assigned = non-empty) and ``xdr_url``. Status comes from ``i.status``.
    """
    raw = i.raw or {}
    status = i.status or raw.get("status") or raw.get("status_progress")
    if not isinstance(status, str) or status not in CASE_STATUS_MAP:
        raise CaseShapeError(f"case {i.incident_id}: unrecognised status {status!r}")
    resolved_raw = raw.get("resolved_timestamp")
    resolved_at = None
    if resolved_raw not in (None, "", 0):
        resolved_at = coerce_utc(resolved_raw)
        if resolved_at is None:
            raise CaseShapeError(
                f"case {i.incident_id}: unparseable resolved_timestamp {resolved_raw!r}")
    return LedgerCase(
        case_id=str(i.incident_id), status=str(status),
        resolve_comment=_opt_str(raw.get("resolve_comment")),
        resolved_at=resolved_at,
        starred=_flag(raw, "starred"),
        assigned=bool(raw.get("assigned_user_mail")),
        xdr_url=_opt_str(raw.get("xdr_url")),
        alert_count=int(i.alert_count or 0),
    )


# ── disposition ───────────────────────────────────────────────────────────

def case_closed_by(c: LedgerCase) -> str:
    if not isinstance(c.status, str) or c.status not in CASE_STATUS_MAP:
        raise CaseShapeError(f"case {c.case_id}: unrecognised status {c.status!r}")
    base = CASE_STATUS_MAP[c.status]
    if base == "other_resolved" and (c.resolve_comment or "").startswith(TRIAGE_RESOLVE_COMMENT_PREFIX):
        return "socfw_auto_triage"
    return base


def case_sealed(c: LedgerCase) -> bool:
    """Resolved by anything except Auto Resolved: XSIAM will not reopen it."""
    return case_closed_by(c) in ("socfw_auto_triage", "other_resolved")


def issue_closed_by(a: LedgerAlert, *, ai_close_marker: Optional[str] = None) -> str:
    status = a.issue_status
    if status is None:
        return "unread"  # never "open": not having read it proves nothing
    if not isinstance(status, str):
        raise CaseShapeError(f"issue {a.external_id}: unrecognised status {status!r}")
    norm = " ".join(str(status).lower().replace("_", " ").split())
    reason = str(a.issue_reason or "").lower()
    if norm.startswith("resolved") or reason.startswith("resolved"):
        comment = a.issue_comment or ""
        if ai_close_marker and ai_close_marker in comment:
            return "socfw_ai_close"
        if comment.startswith(TRIAGE_RESOLVE_COMMENT_PREFIX):
            return "socfw_auto_triage"
        if "auto resolve" in norm or "auto resolve" in reason:
            return "auto_resolved"
        return "other_resolved"
    if norm in _ISSUE_OPEN:
        return "open"
    raise CaseShapeError(f"issue {a.external_id}: unrecognised status {status!r}")


# ── classification ────────────────────────────────────────────────────────

def _t(dt: datetime) -> datetime:
    out = coerce_utc(dt)
    if out is None:
        raise CaseShapeError(f"untrustworthy timestamp {dt!r}")
    return out


def classify_merge(alerts: Sequence[LedgerAlert], cases: Mapping[str, LedgerCase],
                   *, basis: str) -> tuple[str, str]:
    if basis != "alert_incident_ids":
        return "PENDING", f"basis {basis!r} makes no merge claim"
    mp = [a for a in alerts if (a.severity or "").lower() in MEDIUM_PLUS]
    if not mp:
        if alerts and all((a.severity or "").lower() == "low" for a in alerts):
            return "LOW_ONLY_NO_CASE", "only Low-severity issues; Low never creates or joins a case"
        return "PENDING", "no Medium+ alert to classify"
    with_case = [a for a in mp if a.case_id]
    if not with_case:
        return "NO_CASE_YET", "no Medium+ alert has been grouped into a case yet"
    if len(mp) < 2:
        return "PENDING", "a single Medium+ alert cannot split or merge"
    missing = sorted({a.case_id for a in with_case if a.case_id not in cases})
    if missing:
        return "PENDING", f"case {', '.join(missing)} named by an alert was not returned by the tenant"
    if len(with_case) < len(mp):
        return "PENDING", "some Medium+ alerts are not in a case yet"
    ids = {a.case_id for a in with_case}
    if len(ids) == 1:
        return "ONE_CASE", f"all Medium+ alerts are in case {next(iter(ids))}"
    first: dict[str, datetime] = {}
    for a in with_case:
        t = _t(a.observed_at)
        if a.case_id not in first or t < first[a.case_id]:
            first[a.case_id] = t
    ordered = sorted(first, key=lambda k: (first[k], k))
    for n, early in enumerate(ordered):
        c = cases[early]
        if not case_sealed(c):
            continue
        if c.resolved_at is None:
            # Sealed, but when? Without the time we cannot say whether it was
            # sealed before the later case began, and only SPLIT_SEALED
            # withholds a correlation FAIL: do not claim a native split.
            return "PENDING", f"case {early} was sealed but its resolve time is unknown"
        sealed_at = _t(c.resolved_at)
        for later in ordered[n + 1:]:
            if sealed_at < first[later]:
                return "SPLIT_SEALED", (
                    f"case {early} was resolved ({case_closed_by(c)}) before case {later} "
                    "received its first alert, so the platform could not reopen it")
    # Reached only when every earlier sealed case has a known resolve time and
    # none was sealed before a later case's first alert.
    return "SPLIT_NATIVE", f"alerts spread across cases {', '.join(ordered)} with no earlier case sealed first"


# ── ledger dict ───────────────────────────────────────────────────────────

def _iso(dt: Optional[datetime]) -> Optional[str]:
    return None if dt is None else _t(dt).isoformat()


def build_case_ledger(alerts: Sequence[LedgerAlert], cases: Mapping[str, LedgerCase], *,
                      basis: str, read_at: datetime, ai_close_marker: Optional[str] = None,
                      code: str = "CASE_LEDGER_OK",
                      issue_override: Optional[Mapping[str, str]] = None) -> dict:
    """``issue_override`` carries a stored ``issue_closed_by`` forward for alerts
    whose issue status is not re-read (a cases-only refresh); it wins over what
    the (unset) restored issue fields would compute, which is always ``unread``.
    """
    merge_class, reason = classify_merge(alerts, cases, basis=basis)
    override = issue_override or {}
    per_issue = {a.external_id: (override[a.external_id] if a.external_id in override
                                 else issue_closed_by(a, ai_close_marker=ai_close_marker))
                 for a in alerts}
    case_rows = []
    shared = False
    for cid in sorted(cases):
        c = cases[cid]
        mine = [a for a in alerts if a.case_id == cid]
        other = max(0, c.alert_count - len(mine))
        shared = shared or other > 0
        cb = case_closed_by(c)
        closed_first = (cb == "auto_resolved" and bool(mine)
                        and all(per_issue[a.external_id] not in ("open", "unread") for a in mine))
        case_rows.append({
            "case_id": cid, "status": c.status, "closed_by": cb, "sealed": case_sealed(c),
            "starred": c.starred, "assigned": c.assigned, "xdr_url": c.xdr_url,
            "resolved_at": _iso(c.resolved_at), "resolve_comment": c.resolve_comment,
            "issues_from_run": len(mine), "issues_other": other,
            "issues_closed_first": closed_first,
        })
    return {
        "read_at": _iso(read_at), "basis": basis, "merge_class": merge_class,
        "merge_reason": reason, "code": code, "shared_with_other_runs": shared,
        "cases": case_rows,
        "alerts": [{"external_id": a.external_id, "observed_at": _iso(a.observed_at),
                    "severity": a.severity, "case_id": a.case_id,
                    "issue_closed_by": per_issue[a.external_id]} for a in alerts],
    }


def ledger_from_dict(d: dict) -> tuple[list[LedgerAlert], dict[str, LedgerCase]]:
    """Inverse of :func:`build_case_ledger` for what classification needs.

    Issue status/reason/comment are not stored, so restored alerts carry them
    unset; ``issue_closed_by`` is read from the stored dict, not recomputed.
    """
    try:
        alerts = [LedgerAlert(a["external_id"], _t(datetime.fromisoformat(a["observed_at"])),
                              a["severity"], a["case_id"], None, None, None)
                  for a in d["alerts"]]
        cases = {}
        for c in d["cases"]:
            ra = c["resolved_at"]
            cases[c["case_id"]] = LedgerCase(
                c["case_id"], c["status"], c["resolve_comment"],
                None if ra is None else _t(datetime.fromisoformat(ra)),
                c["starred"], c["assigned"], c["xdr_url"],
                c["issues_from_run"] + c["issues_other"])
    except (KeyError, TypeError, ValueError) as e:
        raise CaseShapeError(f"unrecognised stored case ledger: {e}") from e
    return alerts, cases


def pair_merge(a: Optional[dict], b: Optional[dict]) -> dict:
    """Classify across a report/block run pair as one population of alerts and cases."""
    if a is None or b is None:
        return {"merge_class": "PENDING", "basis": None,
                "merge_reason": "one run of the pair has no case ledger yet"}
    basis = a.get("basis") if a.get("basis") == b.get("basis") else "mixed"
    aa, ac = ledger_from_dict(a)
    ba, bc = ledger_from_dict(b)
    cases = dict(ac)
    for cid, c in bc.items():
        # Same case in both runs: keep the fresher read.
        if cid not in cases or (b.get("read_at") or "") >= (a.get("read_at") or ""):
            cases[cid] = c
    mc, reason = classify_merge(aa + ba, cases, basis=basis)
    return {"merge_class": mc, "merge_reason": reason, "basis": basis}
