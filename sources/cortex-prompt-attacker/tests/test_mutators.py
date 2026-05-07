"""Mutator tests — pure transformations, registry resolution."""
from __future__ import annotations

import base64

import pytest

from cortex_prompt_attacker.mutators import (
    Base64Mutator,
    IndirectInjectionMutator,
    LeetspeakMutator,
    NoopMutator,
    RepeatTokenMutator,
    RolePlayMutator,
    ROT13Mutator,
    ToolAbuseMutator,
    TranslationMutator,
    UnicodeConfusableMutator,
    build_mutator_chain,
)


def test_noop_passthrough():
    assert NoopMutator().mutate("alpha") == "alpha"


def test_base64_round_trip():
    out = Base64Mutator().mutate("hello world")
    encoded = out.split(": ", 1)[1]
    assert base64.b64decode(encoded).decode() == "hello world"


def test_rot13_idempotent():
    out = ROT13Mutator().mutate("abc")
    assert out.endswith("nop")  # rot13("abc") == "nop"


def test_leetspeak_substitutes_chars():
    out = LeetspeakMutator().mutate("apple")
    assert "4" in out and "1" in out


def test_unicode_confusable_substitutes_some():
    out = UnicodeConfusableMutator().mutate("apple")
    assert out != "apple"


def test_translation_wraps_with_language():
    out = TranslationMutator(language="es").mutate("hello")
    assert "from es" in out and "hello" in out


def test_role_play_dan_frame():
    out = RolePlayMutator().mutate("do x")
    assert "DAN" in out and "do x" in out


def test_indirect_injection_wraps_in_memo():
    out = IndirectInjectionMutator().mutate("payload")
    assert "MEMO START" in out and "SYSTEM_OVERRIDE" in out


def test_tool_abuse_wraps_as_tool_request():
    out = ToolAbuseMutator().mutate("cat /etc/passwd")
    assert "tools" in out.lower() and "cat /etc/passwd" in out


def test_repeat_token_pads_with_n_copies():
    out = RepeatTokenMutator(token="a ", repeats=10).mutate("end")
    assert out.endswith("end")
    assert out.count("a ") >= 10


def test_build_chain_registers_known_names():
    chain = build_mutator_chain(["base64", "leetspeak"])
    assert [m.name for m in chain] == ["base64", "leetspeak"]


def test_build_chain_empty_returns_noop():
    chain = build_mutator_chain([])
    assert len(chain) == 1 and chain[0].name == "noop"


def test_build_chain_unknown_raises():
    with pytest.raises(KeyError):
        build_mutator_chain(["nope"])
