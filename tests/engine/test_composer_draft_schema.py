"""Unit tests for the Composer draft schema + draft→ORM converter.

Covers the frozen contract's Phase-1 backend acceptance:
  * a minimal valid draft validates and converts with the right sentinels;
  * a broken causality spine (forward ref / two roots) is rejected;
  * a duplicate step id is rejected;
  * a bad detection type is rejected;
  * an empty draft (no steps / no detection anywhere) is rejected.

No DB and no network — the schema and converter are pure.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from engine.composer_draft_schema import (
    DraftScenarioSchema,
    draft_to_orm_kwargs,
    orm_sentinels,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _step(step_id: str, *, causality=None, detections=None, command="whoami"):
    step: dict = {
        "id": step_id,
        "name": f"Step {step_id}",
        "command": command,
        "identity": "www-data",
        "mitre_technique": "T1059",
        "expected_detections": (
            detections
            if detections is not None
            else [{"plane": "EDR", "type": "BIOC", "description": "shell exec"}]
        ),
    }
    if causality is not None:
        step["causality"] = causality
    return step


def _draft(**overrides) -> dict:
    base = {
        "name": "My composed chain",
        "plane": "EDR",
        "steps": [_step("step-01")],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# acceptance
# ---------------------------------------------------------------------------


def test_accepts_minimal_valid_draft():
    schema = DraftScenarioSchema(**_draft())
    assert schema.name == "My composed chain"
    assert schema.plane == "EDR"
    assert len(schema.steps) == 1
    # UNBOUND is a legal saved state — no index FK is run at rest.
    assert schema.tc_ref is None
    assert schema.uc_ref is None


def test_converter_fills_sentinels_and_derives_fields():
    schema = DraftScenarioSchema(**_draft())
    kwargs = draft_to_orm_kwargs(schema)

    # status + id + tag
    assert kwargs["status"] == "draft"
    assert kwargs["scenario_id"].startswith("SIM-DRAFT-")
    assert "composer-draft" in kwargs["tags"]

    # sentinels for the columns a from-scratch draft lacks
    assert kwargs["uc_ref"] == orm_sentinels["uc_ref"] == "UNBOUND"
    assert kwargs["tc_ref"] == "UNBOUND"
    assert kwargs["uc_name"] == "(draft — unbound)"
    assert kwargs["tc_name"] == "(draft — unbound)"
    assert kwargs["version"] == "0.1-draft"
    assert kwargs["mitre_tactic"] == "TA0000"
    assert kwargs["mitre_tactic_name"] == "Uncategorized"
    # mitre_technique falls back to the first step's technique
    assert kwargs["mitre_technique"] == "T1059"
    # nullable=False string, never None
    assert kwargs["mitre_technique_name"] == ""

    # detection_types derived from the union of the steps' detections
    assert kwargs["detection_types"] == ["BIOC"]

    # pull always supported (agent beacon); push derived from command text
    assert kwargs["pull_supported"] is True
    assert isinstance(kwargs["push_supported"], bool)

    # unbound draft claims no entitlements + no tc_refs
    assert kwargs["required_base_platform"] == []
    assert kwargs["required_addons"] == []
    assert kwargs["tc_refs"] == []

    # execution_identity sentinel when omitted
    assert kwargs["execution_identity"] == {"default": "direct", "options": ["direct"]}


def test_author_precedence_and_tag_dedupe():
    schema = DraftScenarioSchema(**_draft(author="henry", tags=["composer-draft", "x"]))
    kwargs = draft_to_orm_kwargs(schema, author="composer")
    # explicit draft author wins over the caller default
    assert kwargs["author"] == "henry"
    # 'composer-draft' present exactly once
    assert kwargs["tags"].count("composer-draft") == 1
    assert "x" in kwargs["tags"]


def test_detection_types_union_multiple_types_sorted():
    draft = _draft(
        steps=[
            _step("step-01", detections=[
                {"plane": "EDR", "type": "BIOC", "description": "a"},
                {"plane": "NDR", "type": "Correlation", "description": "b"},
            ]),
            _step("step-02", causality={"parent_step": "step-01"}, detections=[
                {"plane": "EDR", "type": "Analytics", "description": "c"},
            ]),
        ]
    )
    kwargs = draft_to_orm_kwargs(DraftScenarioSchema(**draft))
    assert kwargs["detection_types"] == ["Analytics", "BIOC", "Correlation"]


def test_provided_tc_ref_populates_tc_refs():
    schema = DraftScenarioSchema(**_draft(uc_ref="UC-EDR-01", tc_ref="TC-EDR-03"))
    kwargs = draft_to_orm_kwargs(schema)
    assert kwargs["tc_ref"] == "TC-EDR-03"
    assert kwargs["tc_refs"] == ["TC-EDR-03"]


def test_valid_causality_spine_accepted():
    draft = _draft(
        steps=[
            _step("step-01"),
            _step("step-02", causality={"parent_step": "step-01", "pivot": "process_lineage"}),
            _step("step-03", causality={"parent_step": "step-02", "pivot": "network_session"}),
        ]
    )
    schema = DraftScenarioSchema(**draft)
    assert len(schema.steps) == 3


# ---------------------------------------------------------------------------
# rejection
# ---------------------------------------------------------------------------


def test_rejects_empty_steps():
    with pytest.raises(ValidationError):
        DraftScenarioSchema(**_draft(steps=[]))


def test_rejects_forward_causality_ref():
    # step-01 declares its parent as a LATER step — a forward ref.
    draft = _draft(
        steps=[
            _step("step-01", causality={"parent_step": "step-02"}),
            _step("step-02"),
        ]
    )
    with pytest.raises(ValidationError):
        DraftScenarioSchema(**draft)


def test_rejects_self_causality_ref():
    draft = _draft(steps=[_step("step-01", causality={"parent_step": "step-01"})])
    with pytest.raises(ValidationError):
        DraftScenarioSchema(**draft)


def test_rejects_unknown_causality_parent():
    draft = _draft(
        steps=[
            _step("step-01"),
            _step("step-02", causality={"parent_step": "step-99"}),
        ]
    )
    with pytest.raises(ValidationError):
        DraftScenarioSchema(**draft)


def test_rejects_two_roots_in_declared_spine():
    # Once any step declares causality, at most one step may omit it (the root).
    draft = _draft(
        steps=[
            _step("step-01"),  # root (no causality)
            _step("step-02"),  # second root — invalid
            _step("step-03", causality={"parent_step": "step-01"}),
        ]
    )
    with pytest.raises(ValidationError):
        DraftScenarioSchema(**draft)


def test_rejects_duplicate_step_id():
    draft = _draft(steps=[_step("step-01"), _step("step-01")])
    with pytest.raises(ValidationError):
        DraftScenarioSchema(**draft)


def test_rejects_bad_detection_type():
    draft = _draft(
        steps=[_step("step-01", detections=[
            {"plane": "EDR", "type": "NOT_A_TYPE", "description": "x"},
        ])]
    )
    with pytest.raises(ValidationError):
        DraftScenarioSchema(**draft)


def test_rejects_bad_pivot():
    draft = _draft(
        steps=[
            _step("step-01"),
            _step("step-02", causality={"parent_step": "step-01", "pivot": "made_up"}),
        ]
    )
    with pytest.raises(ValidationError):
        DraftScenarioSchema(**draft)


def test_rejects_bad_plane():
    with pytest.raises(ValidationError):
        DraftScenarioSchema(**_draft(plane="NOPE"))


def test_rejects_draft_with_no_detection_anywhere():
    # Every step is detection-less → the derived detection_types union is empty.
    draft = _draft(
        steps=[
            _step("step-01", detections=[]),
            _step("step-02", causality={"parent_step": "step-01"}, detections=[]),
        ]
    )
    with pytest.raises(ValidationError):
        DraftScenarioSchema(**draft)


def test_step_may_be_detectionless_if_chain_has_one():
    # A single step with no detection is fine as long as ANOTHER step carries
    # one — the on-canvas "NO EXPECTED DETECTION" gap is a legal saved state.
    draft = _draft(
        steps=[
            _step("step-01", detections=[
                {"plane": "EDR", "type": "BIOC", "description": "a"},
            ]),
            _step("step-02", causality={"parent_step": "step-01"}, detections=[]),
        ]
    )
    schema = DraftScenarioSchema(**draft)
    kwargs = draft_to_orm_kwargs(schema)
    assert kwargs["detection_types"] == ["BIOC"]


def test_draft_step_may_omit_identity_and_technique():
    """A blank step the DC just dropped on the canvas carries no identity and no
    technique yet (both are strict-required on the corpus StepSchema). The draft
    schema must accept it — otherwise a from-scratch save 422s — and the
    converter defaults identity to 'direct' so the run path is well-defined,
    while mitre_technique may stay unset (bound later from a TTP card)."""
    blank = {
        "id": "step-01",
        "name": "New command step",
        "command": "whoami",
        "expected_detections": [{"plane": "EDR", "type": "BIOC", "description": "x"}],
    }
    schema = DraftScenarioSchema(name="wip", plane="EDR", steps=[blank])
    assert schema.steps[0].identity is None
    assert schema.steps[0].mitre_technique is None

    kwargs = draft_to_orm_kwargs(schema)
    assert kwargs["steps"][0]["identity"] == "direct"
    # A blank chain still has a plane and its detection type union.
    assert kwargs["status"] == "draft"


# ---------------------------------------------------------------------------
# Phase 2 — stitch_context (additive/optional)
# ---------------------------------------------------------------------------


def test_draft_without_stitch_context_is_none_and_orm_null():
    """A context-less draft is byte-identical to today: field None, ORM NULL."""
    schema = DraftScenarioSchema(**_draft())
    assert schema.stitch_context is None
    kwargs = draft_to_orm_kwargs(schema)
    assert kwargs["stitch_context"] is None


def test_accepts_valid_stitch_context_block_and_persists_declared_keys():
    sc = {
        "src_ip": {"resolve": "auto_ip"},
        "dst_ip": {"literal": "203.0.113.10"},
        "dst_port": {"literal": 443},
        "account": {"resolve": "canary_principal"},
        "host": {"resolve": "from_agent"},
    }
    schema = DraftScenarioSchema(**_draft(stitch_context=sc))
    assert schema.stitch_context is not None
    kwargs = draft_to_orm_kwargs(schema)
    stored = kwargs["stitch_context"]
    # Declared-only, exact entries preserved (literals verbatim, resolves named).
    assert stored == sc
    # Undeclared keys are NOT persisted (exclude_none), not stored as null legs.
    assert "src_port" not in stored
    assert "cloud_resource" not in stored


def test_empty_stitch_context_block_normalises_to_orm_null():
    schema = DraftScenarioSchema(**_draft(stitch_context={}))
    kwargs = draft_to_orm_kwargs(schema)
    assert kwargs["stitch_context"] is None


def test_rejects_unknown_stitch_directive():
    with pytest.raises(ValidationError):
        DraftScenarioSchema(**_draft(stitch_context={"src_ip": {"resolve": "auto_bogus"}}))


def test_rejects_stitch_directive_on_incompatible_key():
    # auto_5tuple is rejected on host (not one of the five tuple keys).
    with pytest.raises(ValidationError):
        DraftScenarioSchema(**_draft(stitch_context={"host": {"resolve": "auto_5tuple"}}))


def test_rejects_stitch_entry_with_both_literal_and_resolve():
    with pytest.raises(ValidationError):
        DraftScenarioSchema(
            **_draft(stitch_context={"src_ip": {"literal": "10.0.0.1", "resolve": "auto_ip"}})
        )


def test_rejects_unknown_stitch_key():
    with pytest.raises(ValidationError):
        DraftScenarioSchema(**_draft(stitch_context={"not_an_entity": {"literal": "x"}}))
