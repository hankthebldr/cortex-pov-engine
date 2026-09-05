"""StepSchema / DraftStepSchema channel contract — Phase 3a foundation.

Pure Pydantic model tests: no DB, no YAML on disk. A step gains three optional,
additive fields — ``channel`` ('agent'|'eal', absent ⇒ 'agent'), ``target`` (a
second-endpoint agent id, agent-channel only) and ``eal`` ({plugin, params},
eal-channel only). The load-bearing property is byte-identity: a step that
declares NONE of them persists exactly as the pre-3a corpus did, because the
loader/draft dump strips the three keys when unset
(``omit_unset_channel_fields``).
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from engine.composer_draft_schema import (
    DraftScenarioSchema,
    DraftStepSchema,
    draft_to_orm_kwargs,
)
from engine.scenario_loader import (
    EalStepSchema,
    StepSchema,
    effective_channel,
    omit_unset_channel_fields,
)


def _base(**overrides):
    kwargs = dict(
        id="step-01", name="n", command="cmd", identity="root",
        mitre_technique="T1059",
    )
    kwargs.update(overrides)
    return kwargs


# ---------------------------------------------------------------------------
# defaults / back-compat
# ---------------------------------------------------------------------------


def test_channel_defaults_to_none_meaning_agent():
    step = StepSchema(**_base())
    assert step.channel is None
    assert step.target is None
    assert step.eal is None
    assert effective_channel(step) == "agent"


def test_bare_step_dump_omits_the_three_channel_keys():
    """Byte-identity: a step declaring none of the channel fields must dump
    exactly as it did pre-3a — the three keys are ABSENT, not null."""
    dumped = omit_unset_channel_fields(StepSchema(**_base()).model_dump())
    assert "channel" not in dumped
    assert "target" not in dumped
    assert "eal" not in dumped


def test_effective_channel_reads_from_a_plain_dict():
    assert effective_channel({}) == "agent"
    assert effective_channel({"channel": None}) == "agent"
    assert effective_channel({"channel": "agent"}) == "agent"
    assert effective_channel({"channel": "eal"}) == "eal"


# ---------------------------------------------------------------------------
# agent channel + second-endpoint target
# ---------------------------------------------------------------------------


def test_accepts_explicit_agent_channel():
    step = StepSchema(**_base(channel="agent"))
    assert effective_channel(step) == "agent"


def test_accepts_agent_step_with_a_second_endpoint_target():
    step = StepSchema(**_base(target="jumpbox-b"))
    assert step.target == "jumpbox-b"
    assert effective_channel(step) == "agent"
    dumped = omit_unset_channel_fields(step.model_dump())
    assert dumped["target"] == "jumpbox-b"
    assert "channel" not in dumped  # channel still unset ⇒ omitted
    assert "eal" not in dumped


def test_rejects_empty_string_target():
    with pytest.raises(ValidationError):
        StepSchema(**_base(target="   "))


def test_rejects_target_on_an_eal_step():
    with pytest.raises(ValidationError):
        StepSchema(**_base(channel="eal", target="jumpbox-b",
                           eal={"plugin": "ngfw_eal_emitter"}))


# ---------------------------------------------------------------------------
# eal channel
# ---------------------------------------------------------------------------


def test_accepts_an_eal_step_with_a_plugin():
    step = StepSchema(**_base(channel="eal", eal={"plugin": "ngfw_eal_emitter"}))
    assert effective_channel(step) == "eal"
    assert isinstance(step.eal, EalStepSchema)
    assert step.eal.plugin == "ngfw_eal_emitter"
    assert step.eal.params == {}
    dumped = omit_unset_channel_fields(step.model_dump())
    assert dumped["channel"] == "eal"
    assert dumped["eal"] == {"plugin": "ngfw_eal_emitter", "params": {}}
    assert "target" not in dumped


def test_accepts_an_eal_step_with_params():
    step = StepSchema(
        **_base(channel="eal", eal={"plugin": "okta_sso", "params": {"user": "svc"}})
    )
    assert step.eal.params == {"user": "svc"}


def test_rejects_eal_channel_with_no_eal_block():
    with pytest.raises(ValidationError):
        StepSchema(**_base(channel="eal"))


def test_rejects_eal_channel_with_empty_plugin():
    with pytest.raises(ValidationError):
        StepSchema(**_base(channel="eal", eal={"plugin": "  "}))


def test_rejects_eal_block_on_a_non_eal_step():
    with pytest.raises(ValidationError):
        StepSchema(**_base(eal={"plugin": "ngfw_eal_emitter"}))


def test_rejects_eal_block_on_an_explicit_agent_step():
    with pytest.raises(ValidationError):
        StepSchema(**_base(channel="agent", eal={"plugin": "ngfw_eal_emitter"}))


# ---------------------------------------------------------------------------
# channel enum
# ---------------------------------------------------------------------------


def test_rejects_unknown_channel():
    with pytest.raises(ValidationError):
        StepSchema(**_base(channel="beacon"))


# ---------------------------------------------------------------------------
# DraftStepSchema inherits the whole contract in one place
# ---------------------------------------------------------------------------


def test_draft_step_inherits_channel_fields():
    step = DraftStepSchema(**_base(channel="eal", eal={"plugin": "email_emitter"}))
    assert effective_channel(step) == "eal"
    assert step.eal.plugin == "email_emitter"


def test_draft_step_rejects_bad_channel():
    with pytest.raises(ValidationError):
        DraftStepSchema(**_base(channel="nope"))


# ---------------------------------------------------------------------------
# persistence through draft_to_orm_kwargs
# ---------------------------------------------------------------------------


def _draft(steps):
    return {
        "name": "channel draft",
        "plane": "NDR",
        "steps": steps,
    }


def _dstep(step_id, **overrides):
    base = {
        "id": step_id,
        "name": f"Step {step_id}",
        "command": "curl http://c2",
        "identity": "www-data",
        "mitre_technique": "T1071",
        "expected_detections": [
            {"plane": "NDR", "type": "Correlation", "description": "c2 beacon"}
        ],
    }
    base.update(overrides)
    return base


def test_draft_to_orm_persists_channel_fields():
    draft = DraftScenarioSchema(
        **_draft(
            [
                _dstep("step-01", channel="eal", eal={"plugin": "ngfw_eal_emitter",
                                                       "params": {"dst_port": "443"}}),
                _dstep("step-02", target="jumpbox-b", causality={"parent_step": "step-01"}),
            ]
        )
    )
    kwargs = draft_to_orm_kwargs(draft)
    s0, s1 = kwargs["steps"]
    assert s0["channel"] == "eal"
    assert s0["eal"] == {"plugin": "ngfw_eal_emitter", "params": {"dst_port": "443"}}
    assert "target" not in s0
    assert s1["target"] == "jumpbox-b"
    assert "channel" not in s1  # unset ⇒ omitted
    assert "eal" not in s1


def test_draft_to_orm_bare_step_omits_channel_keys():
    """A Phase-1/2 draft (no channel fields) persists byte-identically."""
    draft = DraftScenarioSchema(**_draft([_dstep("step-01")]))
    kwargs = draft_to_orm_kwargs(draft)
    s0 = kwargs["steps"][0]
    assert "channel" not in s0
    assert "target" not in s0
    assert "eal" not in s0
