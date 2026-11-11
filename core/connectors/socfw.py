"""Every SOC Framework fact CortexSim reads, and the pure parsers over it.

Pinned to ``soc-optimization-unified`` 3.31.4 (secops-framework commit
``d222ba87``). Nothing outside this module may hard-code a framework string or
shape. Nothing here talks to a tenant: callers fetch, these functions parse.

Rule (CLAUDE.md, "tolerance hides bugs"): an unrecognised shape raises
:class:`ResponseLayerShapeError`. It is never read as empty or as defaults.

Where the facts come from (all under ``Packs/soc-optimization-unified``):
  * ``SOCFWConfig.py``: ``SOCFWTenantConfig`` is a JSON object. ``command=mode``
    writes only its ``shadow`` section (``{action: {actor: bool}}``); the mode
    NAME is not stored, it is inferred (``matching_mode``). ``command=triage``
    writes ``{"triage": {"score_threshold": number|"off", "window_hours": number}}``.
  * ``SOCFWConfigModes_data.json``: ``modes.<name>.shadow`` and ``.jobs``.
  * ``SOCAutoTriageScoreFilter.py`` ``resolve_settings``: an empty/None tenant
    value falls back to the JOB argument; ``off`` means the score is ignored.
  * ``SOCCommandWrapper.py`` execution rows: ``action_taken`` / ``action_actor``
    / ``execution_mode`` in ``xsiam_socfw_ir_execution_raw``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

FRAMEWORK_PINNED_VERSION = "3.31.4"
CORE_PACK_ID = "soc-optimization-unified"
LIFECYCLE_PACK_IDS = ("soc-framework-nist-ir", "soc-framework-nist-ir-ai")
CONFIG_LISTS = ("SOCFWTenantConfig", "SOCFWConfigModes")
EXECUTION_DATASET = "xsiam_socfw_ir_execution_raw"
CASE_LEDGER_DATASET = "xsiam_socfw_case_ledger_raw"
TRIAGE_RESOLVE_COMMENT_PREFIX = "SOC Framework Auto Triage:"
TRIAGE_DEFAULT_WINDOW_HOURS = 6.0
TRIAGE_DEFAULT_SCORE_THRESHOLD = 40
TRIAGE_JOB_NAME = "Auto Triage V3"

# Execution-row vocabulary (SOCAutoTriageScoreFilter.py:380-381, SOCCommandWrapper.py:900-907).
TRIAGE_CLOSED_ACTION = "auto_triage_closed"
AI_CLOSE_ACTION = "soc-close-issue"
AI_ACTOR = "ai"
PRODUCTION_MODE = "production"
# SOCFWConfig.py: the mode name reported when the shadow section matches nothing / nothing is set.
MODE_DEFAULT = "default"
MODE_CUSTOM = "custom"

STATES = ("ABSENT", "PRESENT_UNREAD", "PRESENT", "UNKNOWN")
SOURCES = ("tenant_config", "job_default", "xql_evidence", "unknown")


class ResponseLayerShapeError(ValueError):
    """A tenant or framework reply did not have the shape 3.31.4 produces."""


@dataclass(frozen=True)
class TenantConfigView:
    mode: Optional[str]
    triage_window_hours: Optional[float]
    triage_score_threshold: Optional[int]
    triage_score_off: bool
    ai_close_live: Optional[bool]
    # Mode's Auto Triage job switch (True = "on"); not part of the brief's field list,
    # defaulted so the five documented fields stay positional-compatible.
    triage_job_on: Optional[bool] = None
    # True when a ``triage`` override section was present at all.
    triage_overridden: bool = False


@dataclass(frozen=True)
class ExecutionEvidence:
    triage_closed_production: int
    ai_close_production: int


@dataclass(frozen=True)
class AutoTriage:
    live: Optional[bool]
    window_hours: Optional[float]
    score_threshold: Optional[int]
    score_off: bool
    source: str


@dataclass(frozen=True)
class ResponseLayer:
    state: str
    code: str
    framework_version: Optional[str]
    packs: dict
    mode: Optional[str]
    auto_triage: AutoTriage
    ai_close: str
    leftover_lists: bool
    sources_used: tuple
    read_at: datetime

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "code": self.code,
            "framework_version": self.framework_version,
            "packs": dict(self.packs),
            "mode": self.mode,
            "auto_triage": {
                "live": self.auto_triage.live,
                "window_hours": self.auto_triage.window_hours,
                "score_threshold": self.auto_triage.score_threshold,
                "score_off": self.auto_triage.score_off,
                "source": self.auto_triage.source,
            },
            "ai_close": self.ai_close,
            "leftover_lists": self.leftover_lists,
            "sources_used": list(self.sources_used),
            "read_at": self.read_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ResponseLayer":
        try:
            at = d["auto_triage"]
            return cls(
                state=d["state"],
                code=d["code"],
                framework_version=d["framework_version"],
                packs=dict(d["packs"]),
                mode=d["mode"],
                auto_triage=AutoTriage(
                    live=at["live"],
                    window_hours=at["window_hours"],
                    score_threshold=at["score_threshold"],
                    score_off=at["score_off"],
                    source=at["source"],
                ),
                ai_close=d["ai_close"],
                leftover_lists=d["leftover_lists"],
                sources_used=tuple(d["sources_used"]),
                read_at=datetime.fromisoformat(d["read_at"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ResponseLayerShapeError(f"ResponseLayer dict: {exc!r}") from exc


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def parse_installed_packs(reply: Any) -> dict:
    """``GET /xsoar/contentpacks/metadata/installed`` -> {pack id: currentVersion}."""
    if not isinstance(reply, list):
        raise ResponseLayerShapeError(f"installed packs: expected a list, got {type(reply).__name__}")
    packs: dict = {}
    for i, row in enumerate(reply):
        if not isinstance(row, dict):
            raise ResponseLayerShapeError(f"installed packs[{i}]: expected an object")
        pid, ver = row.get("id"), row.get("currentVersion")
        if not isinstance(pid, str) or not pid or not isinstance(ver, str):
            raise ResponseLayerShapeError(
                f"installed packs[{i}]: need string 'id' and 'currentVersion'")
        packs[pid] = ver
    return packs


def parse_list_names(reply: Any) -> frozenset:
    """``GET /lists/names`` -> set of list names."""
    if not isinstance(reply, list) or not all(isinstance(n, str) for n in reply):
        raise ResponseLayerShapeError("list names: expected a list of strings")
    return frozenset(reply)


def _as_bool(path: str, value: Any) -> bool:
    # Same coercion as SOCFWConfig._as_bool, except an unreadable value raises.
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("true", "false"):
        return text == "true"
    raise ResponseLayerShapeError(f"{path}: expected true/false, got {value!r}")


def _to_number(path: str, value: Any) -> float:
    if isinstance(value, bool):
        raise ResponseLayerShapeError(f"{path}: expected a number, got {value!r}")
    try:
        return float(str(value).strip())
    except ValueError as exc:
        raise ResponseLayerShapeError(f"{path}: expected a number, got {value!r}") from exc


def _tenant_shadow(doc: dict) -> dict:
    section = doc.get("shadow", {})
    if not isinstance(section, dict):
        raise ResponseLayerShapeError("SOCFWTenantConfig.shadow: expected an object")
    out: dict = {}
    for action, per in section.items():
        if not isinstance(per, dict):
            raise ResponseLayerShapeError(f"SOCFWTenantConfig.shadow.{action}: expected an object")
        for actor, val in per.items():
            out[(action, actor)] = _as_bool(f"SOCFWTenantConfig.shadow.{action}.{actor}", val)
    return out


def _modes(modes_doc: Any) -> dict:
    modes = modes_doc.get("modes") if isinstance(modes_doc, dict) else None
    if not isinstance(modes, dict) or not modes:
        raise ResponseLayerShapeError("SOCFWConfigModes: 'modes' missing or not an object")
    parsed: dict = {}
    for name, m in modes.items():
        if not isinstance(m, dict):
            raise ResponseLayerShapeError(f"SOCFWConfigModes.modes.{name}: expected an object")
        shadow = m.get("shadow", {})
        jobs = m.get("jobs", {})
        if not isinstance(shadow, dict) or not isinstance(jobs, dict):
            raise ResponseLayerShapeError(f"SOCFWConfigModes.modes.{name}: shadow/jobs must be objects")
        flat: dict = {}
        for action, actors in shadow.items():
            if not isinstance(actors, dict):
                raise ResponseLayerShapeError(f"SOCFWConfigModes.modes.{name}.shadow.{action}: expected an object")
            for actor, val in actors.items():
                flat[(action, actor)] = _as_bool(f"SOCFWConfigModes.modes.{name}.shadow.{action}.{actor}", val)
        parsed[name] = {"shadow": flat, "jobs": jobs}
    return parsed


def _infer_mode(tenant: dict, modes: dict) -> str:
    """SOCFWConfig.matching_mode over the shadow switches (the jobs are not in the list)."""
    switches = []
    for m in modes.values():
        for key in m["shadow"]:
            if key not in switches:
                switches.append(key)
    if all(tenant.get(k) is None for k in switches):
        return MODE_DEFAULT
    for name, m in modes.items():
        if all(tenant.get(k) == m["shadow"].get(k) for k in switches):
            return name
    return MODE_CUSTOM


def parse_tenant_config(doc: Any, modes_doc: Any) -> TenantConfigView:
    """Read ``SOCFWTenantConfig`` against ``SOCFWConfigModes``."""
    if not isinstance(doc, dict):
        raise ResponseLayerShapeError(f"SOCFWTenantConfig: expected an object, got {type(doc).__name__}")
    modes = _modes(modes_doc)
    tenant = _tenant_shadow(doc)
    mode = _infer_mode(tenant, modes)

    # Effective shadow: the mode's map first, tenant overrides on top
    # (SOCCommandWrapper.py:658-687 precedence).
    effective = dict(modes[mode]["shadow"]) if mode in modes else {}
    effective.update(tenant)
    ai_shadow = effective.get(("soc-close-issue", AI_ACTOR))
    ai_close_live = None if ai_shadow is None else (ai_shadow is False)

    job_state = (modes[mode]["jobs"].get(TRIAGE_JOB_NAME) if mode in modes else None)
    if job_state in ("on", "off"):
        job_on: Optional[bool] = job_state == "on"
    elif job_state is None:
        job_on = None
    else:
        raise ResponseLayerShapeError(f"SOCFWConfigModes.modes.{mode}.jobs.{TRIAGE_JOB_NAME}: got {job_state!r}")

    triage = doc.get("triage", {})
    if not isinstance(triage, dict):
        raise ResponseLayerShapeError("SOCFWTenantConfig.triage: expected an object")
    window: Optional[float] = None
    threshold: Optional[int] = None
    off = False
    # Empty/None means "not overridden" (SOCAutoTriageScoreFilter.resolve_settings), not off.
    raw_t = triage.get("score_threshold")
    if raw_t not in (None, ""):
        if str(raw_t).strip().lower() == "off":
            off = True
        else:
            n = _to_number("SOCFWTenantConfig.triage.score_threshold", raw_t)
            if n < 0:
                raise ResponseLayerShapeError("SOCFWTenantConfig.triage.score_threshold: must be 0 or more")
            threshold = int(n) if n.is_integer() else n  # type: ignore[assignment]
    raw_w = triage.get("window_hours")
    if raw_w not in (None, ""):
        window = _to_number("SOCFWTenantConfig.triage.window_hours", raw_w)
        if window <= 0:
            raise ResponseLayerShapeError("SOCFWTenantConfig.triage.window_hours: must be more than 0")

    return TenantConfigView(
        mode=mode,
        triage_window_hours=window,
        triage_score_threshold=threshold,
        triage_score_off=off,
        ai_close_live=ai_close_live,
        triage_job_on=job_on,
        triage_overridden=bool(triage),
    )


def summarize_execution_rows(rows: list) -> ExecutionEvidence:
    """Count production closes from ``xsiam_socfw_ir_execution_raw`` aggregate rows.

    Each row: ``action_taken``, ``execution_mode``, ``n`` (and ``action_actor``
    for AI close). A ``soc-close-issue`` row counts as an AI close only when it
    names ``action_actor == "ai"``; analysts write the same action.
    """
    if not isinstance(rows, list):
        raise ResponseLayerShapeError("execution rows: expected a list")
    triage = ai = 0
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ResponseLayerShapeError(f"execution rows[{i}]: expected an object")
        for key in ("action_taken", "execution_mode", "n"):
            if key not in row:
                raise ResponseLayerShapeError(f"execution rows[{i}]: missing '{key}'")
        try:
            n = int(float(str(row["n"])))
        except ValueError as exc:
            raise ResponseLayerShapeError(f"execution rows[{i}].n: got {row['n']!r}") from exc
        if row["execution_mode"] != PRODUCTION_MODE:
            continue
        if row["action_taken"] == TRIAGE_CLOSED_ACTION:
            triage += n
        elif row["action_taken"] == AI_CLOSE_ACTION and row.get("action_actor") == AI_ACTOR:
            ai += n
    return ExecutionEvidence(triage_closed_production=triage, ai_close_production=ai)


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def assemble_response_layer(
    *,
    packs: Optional[dict],
    list_names: Optional[frozenset],
    tenant_config: Optional[TenantConfigView],
    evidence: Optional[ExecutionEvidence],
    sources_used: list,
    read_at: datetime,
    shape_error: Optional[str] = None,
) -> ResponseLayer:
    no_triage = AutoTriage(None, None, None, False, "unknown")
    leftover = bool(list_names) and any(n in list_names for n in CONFIG_LISTS)

    # Without the installed-packs read, neither present nor absent can be claimed.
    if packs is None:
        return ResponseLayer(
            state="UNKNOWN",
            code="RESPONSE_LAYER_SHAPE_UNRECOGNIZED" if shape_error else "RESPONSE_LAYER_UNREAD",
            framework_version=None, packs={}, mode=None, auto_triage=no_triage,
            ai_close="unknown", leftover_lists=leftover,
            sources_used=tuple(sources_used), read_at=read_at)

    framework = {k: v for k, v in packs.items()
                 if k == CORE_PACK_ID or k in LIFECYCLE_PACK_IDS}
    if CORE_PACK_ID not in packs:
        return ResponseLayer(
            state="ABSENT", code="RESPONSE_LAYER_ABSENT", framework_version=None,
            packs=framework, mode=None, auto_triage=no_triage, ai_close="unknown",
            leftover_lists=leftover, sources_used=tuple(sources_used), read_at=read_at)

    if tenant_config is None and evidence is None:
        state, code = "PRESENT_UNREAD", "RESPONSE_LAYER_UNREAD"
    elif tenant_config is None:
        # Evidence alone proves the layer runs but the config was not read.
        state, code = "PRESENT_UNREAD", "RESPONSE_LAYER_UNREAD"
    else:
        state, code = "PRESENT", "RESPONSE_LAYER_OK"

    tc = tenant_config
    live: Optional[bool] = None
    if evidence is not None and evidence.triage_closed_production > 0:
        live, source = True, "xql_evidence"
    elif tc is not None and tc.triage_job_on is not None:
        live, source = tc.triage_job_on, "tenant_config"
    elif tc is not None and tc.triage_overridden:
        source = "tenant_config"
    elif tc is not None:
        source = "job_default"
    else:
        source = "unknown"

    window = threshold = None
    off = False
    if tc is not None:
        off = tc.triage_score_off
        window = tc.triage_window_hours if tc.triage_window_hours is not None else TRIAGE_DEFAULT_WINDOW_HOURS
        if not off:
            threshold = (tc.triage_score_threshold if tc.triage_score_threshold is not None
                         else TRIAGE_DEFAULT_SCORE_THRESHOLD)

    if evidence is not None and evidence.ai_close_production > 0:
        ai_close = "live"
    elif tc is not None and tc.ai_close_live is not None:
        ai_close = "live" if tc.ai_close_live else "shadow"
    else:
        ai_close = "unknown"

    return ResponseLayer(
        state=state, code=code, framework_version=packs[CORE_PACK_ID], packs=framework,
        mode=tc.mode if tc is not None else None,
        auto_triage=AutoTriage(live, window, threshold, off, source),
        ai_close=ai_close, leftover_lists=leftover,
        sources_used=tuple(sources_used), read_at=read_at)
