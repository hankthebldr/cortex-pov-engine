"""Test-side mirror of the Composer's fork path (console → drafts-API body).

When a DC opens a corpus scenario in the Composer and saves it, the console
runs ``draftFromScenario`` and then ``draftToApi`` (both in
``ui/src/components/console/composerDraft.js``) over the detail document
``GET /api/scenarios/{id}`` returned, and POSTs the result to
``/api/scenarios/drafts``. The backend suite cannot execute that JavaScript
(the CI ``backend`` job runs inside the prod image, which ships no node), so
this module mirrors the two functions field for field and the drafts-API tests
fork through it. A key the JS starts emitting that this mirror omits is a key
the backend tests never exercise — keep the two in step, and say so in
``ui/src/components/__tests__/composerDraft.test.js`` when either changes.

Fields the console fork does NOT carry today are deliberately not invented
here: ``execution_identity``, ``external_tools``, ``tc_refs[]``,
``pov_scenario_id``, the scenario-level MITRE block, per-step
``platform_variants`` and per-detection ``verification_xql`` all fall away in
``draftFromScenario`` / ``normalizeStep`` and the server sentinel-fills them.
That is a fidelity gap of its own, outside this mirror's remit; mirroring it
faithfully is what keeps these tests honest about what the console really
sends.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
SCENARIOS_DIR = REPO_ROOT / "scenarios"


def load_corpus_doc(scenario_id: str) -> dict[str, Any]:
    """The detail document the Composer fetches for ``scenario_id``: the real
    YAML through the real strict loader into a ``Scenario`` row, then
    ``to_dict()``. Raises rather than returning a fabricated scenario."""
    from engine.scenario_loader import (  # noqa: PLC0415
        _find_yaml_files,
        _parse_and_validate,
        _schema_to_orm_kwargs,
    )
    from models import Scenario  # noqa: PLC0415

    needle = f"scenario_id: {scenario_id}"
    for path in _find_yaml_files(str(SCENARIOS_DIR)):
        if needle not in Path(path).read_text(encoding="utf-8"):
            continue
        schema, err = _parse_and_validate(path)
        if err is not None or schema is None:
            raise AssertionError(f"{path} failed the strict loader: {err}")
        if schema.scenario_id == scenario_id:
            return Scenario(**_schema_to_orm_kwargs(schema)).to_dict()
    raise LookupError(f"{scenario_id} is not in the corpus under {SCENARIOS_DIR}")


# ---------------------------------------------------------------------------
# draftFromScenario
# ---------------------------------------------------------------------------


def _normalize_step(raw: dict[str, Any]) -> dict[str, Any]:
    """Mirrors ``normalizeStep``: one scenario step as a camelCase draft step."""
    dets = raw["expected_detections"] if isinstance(raw.get("expected_detections"), list) else []
    causality = raw.get("causality") or {}
    eal = raw["eal"] if isinstance(raw.get("eal"), dict) else None
    return {
        "id": raw.get("id"),
        "name": raw.get("name") or "(unnamed step)",
        "command": raw["command"] if isinstance(raw.get("command"), str) else None,
        "identity": raw.get("identity") or None,
        "technique": raw.get("mitre_technique") or None,
        "platforms": list(raw["platforms"]) if isinstance(raw.get("platforms"), list) else [],
        "channel": raw["channel"] if isinstance(raw.get("channel"), str) else None,
        "target": raw["target"] if isinstance(raw.get("target"), str) else None,
        "eal": (
            {
                "plugin": eal["plugin"] if isinstance(eal.get("plugin"), str) else None,
                "params": dict(eal["params"]) if isinstance(eal.get("params"), dict) else {},
            }
            if eal is not None
            else None
        ),
        "causalityParent": causality.get("parent_step") or None,
        "causalityPivot": causality.get("pivot") or None,
        "detections": [
            {
                "plane": d.get("plane") or None,
                "type": d.get("type") or None,
                "description": d.get("description") or None,
                "ttpRef": d.get("ttp_ref") or None,
                "detectionId": d.get("detection_id") or None,
            }
            for d in dets
            if isinstance(d, dict)
        ],
        "authored": False,
    }


def draft_from_scenario(doc: dict[str, Any]) -> dict[str, Any]:
    """Mirrors ``draftFromScenario`` over a detail document."""
    cgo = doc.get("cgo_anchor") or None
    cleanup = doc.get("cleanup") or {}
    cgo_display: Optional[str] = None
    if cgo:
        cgo_display = " / ".join(
            p for p in (cgo.get("image_name"), cgo.get("primary_username")) if p
        ) or None
    return {
        "originId": doc.get("scenario_id") or None,
        "name": doc.get("name") or None,
        "plane": doc.get("plane") or None,
        "ucRef": doc.get("uc_ref") or None,
        "tcRef": doc.get("tc_ref") or None,
        "moatTier": doc.get("moat_tier") or None,
        "cgo": cgo_display,
        "teardown": list(cleanup["commands"]) if isinstance(cleanup.get("commands"), list) else [],
        # parseStitchContext → emitStitchContext round-trips byte-identically
        # (pinned by the vitest), so the wire form is carried verbatim here.
        "stitchContext": doc.get("stitch_context") or None,
        "layout": doc.get("composer_layout") or None,
        "laneOverrides": doc.get("composer_lanes") or None,
        # Only an AUTHORED refusal is a floor, so only `false` is ever carried;
        # a derived value, a declared `true` and "nothing declared" all read as
        # null and leave the server to its own derivation.
        "pushSupported": False if doc.get("push_supported_declared") is False else None,
        "threatReport": doc.get("threat_report") or None,
        "threatReportUrl": doc.get("threat_report_url") or None,
        "steps": [
            _normalize_step(s) for s in (doc.get("steps") or []) if isinstance(s, dict)
        ],
        "scenarioId": None,
        "status": None,
        "author": None,
        "tags": [],
    }


# ---------------------------------------------------------------------------
# draftToApi
# ---------------------------------------------------------------------------


def _cgo_anchor_from_display(cgo: Optional[str]) -> Optional[dict[str, str]]:
    """Mirrors ``_cgoAnchorFromDisplay``: "image / user" back to the anchor."""
    if not cgo:
        return None
    image_name, _, primary_username = (p.strip() for p in cgo.partition(" / "))
    if not image_name:
        return None
    anchor = {"image_name": image_name}
    if primary_username:
        anchor["primary_username"] = primary_username
    return anchor


def draft_to_api(draft: dict[str, Any], *, author: str = "composer") -> dict[str, Any]:
    """Mirrors ``draftToApi``: the frozen snake_case ``DraftCreateRequest``."""
    steps: list[dict[str, Any]] = []
    for s in draft["steps"]:
        step: dict[str, Any] = {
            "id": s["id"],
            "name": s["name"],
            "command": s["command"] if s["command"] is not None else "",
            "identity": s["identity"] if s["identity"] is not None else "",
            "mitre_technique": s["technique"] if s["technique"] is not None else "",
            "expected_detections": [],
        }
        for d in s["detections"]:
            det = {"plane": d["plane"], "type": d["type"], "description": d["description"]}
            if d["ttpRef"]:
                det["ttp_ref"] = d["ttpRef"]
            if d["detectionId"]:
                det["detection_id"] = d["detectionId"]
            step["expected_detections"].append(det)
        if s["causalityParent"]:
            step["causality"] = {
                "parent_step": s["causalityParent"],
                "pivot": s["causalityPivot"] or "process_lineage",
            }
        if s["platforms"]:
            step["platforms"] = s["platforms"]
        # Phase-3a channel routing is OMIT-WHEN-DEFAULT.
        eff = s["channel"] or "agent"
        if eff != "agent":
            step["channel"] = eff
        if eff == "agent" and s["target"]:
            step["target"] = s["target"]
        if eff == "eal":
            eal = s["eal"] or {}
            step["eal"] = {"plugin": eal.get("plugin") or "", "params": eal.get("params") or {}}
        steps.append(step)

    body: dict[str, Any] = {
        "name": draft["name"] or "",
        "plane": draft["plane"] or "",
        "author": draft["author"] or author,
        "tags": list(draft["tags"]) if isinstance(draft.get("tags"), list) else [],
        "steps": steps,
    }
    if draft["ucRef"]:
        body["uc_ref"] = draft["ucRef"]
    if draft["tcRef"]:
        body["tc_ref"] = draft["tcRef"]
    if draft["stitchContext"]:
        body["stitch_context"] = draft["stitchContext"]
    if draft["layout"]:
        body["composer_layout"] = draft["layout"]
    if draft["laneOverrides"]:
        body["composer_lanes"] = draft["laneOverrides"]
    anchor = _cgo_anchor_from_display(draft["cgo"])
    if anchor:
        body["cgo_anchor"] = anchor
    if draft["teardown"]:
        body["cleanup"] = {"commands": list(draft["teardown"])}
    if draft.get("pushSupported") is False:
        body["push_supported"] = False
    if draft.get("threatReport"):
        body["threat_report"] = draft["threatReport"]
    if draft.get("threatReportUrl"):
        body["threat_report_url"] = draft["threatReportUrl"]
    return body


def fork_body_like_the_ui(doc: dict[str, Any], *, author: str = "composer") -> dict[str, Any]:
    """``draftToApi(draftFromScenario(doc))`` — the body the console POSTs."""
    return draft_to_api(draft_from_scenario(doc), author=author)
