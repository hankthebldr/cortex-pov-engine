"""Scorer tests — primitive + named OWASP scorers."""
from __future__ import annotations

import pytest

from cortex_prompt_attacker.scorers import (
    ExecShellCalledScorer,
    FabricatedFactScorer,
    JSONPathScorer,
    PIILeakScorer,
    RAGPoisonedMatchScorer,
    RegexScorer,
    SecretLeakScorer,
    SubstringScorer,
    SystemPromptLeakScorer,
    UnboundedTokensScorer,
    VulnerableFlagScorer,
    build_scorers,
)
from cortex_prompt_attacker.targets import TargetResponse


def _resp(text="", status=200, payload=None, error=None):
    return TargetResponse(
        status_code=status, text=text, json=payload, error=error,
    )


def test_regex_scorer_hit():
    s = RegexScorer("alpha", r"alpha")
    assert s.score(_resp("contains alpha"), prompt="x").detected


def test_regex_scorer_no_hit_when_response_errored():
    s = RegexScorer("alpha", r"alpha")
    r = s.score(_resp(text="alpha", error="boom"), prompt="x")
    assert not r.detected


def test_substring_scorer_case_insensitive_default():
    s = SubstringScorer("xyz", needles=["AKIA"])
    assert s.score(_resp("found akia0001"), prompt="x").detected


def test_jsonpath_eq_scorer():
    s = JSONPathScorer("flag", path="vulnerable", expected=True)
    assert s.score(_resp(payload={"vulnerable": True}), prompt="x").detected


def test_jsonpath_ge_metric_returned():
    s = JSONPathScorer("tokens", path="token_count", expected=8000, op=">=")
    r = s.score(_resp(payload={"token_count": 9000}), prompt="x")
    assert r.detected
    assert r.metric == 9000


def test_jsonpath_op_in():
    s = JSONPathScorer("rule", path="rule_name",
                      expected=("poisoned_match", "instruction_in_doc"), op="in")
    assert s.score(_resp(payload={"rule_name": "poisoned_match"}), prompt="x").detected


def test_system_prompt_leak_named():
    s = SystemPromptLeakScorer()
    body = '{"text":"hidden context: AKIA0000CORTEXSIMCANARY"}'
    assert s.score(_resp(body), prompt="x").detected


def test_secret_leak_regex():
    s = SecretLeakScorer()
    assert s.score(_resp("token=AKIA0000CANARY12"), prompt="x").detected


def test_pii_leak_regex():
    s = PIILeakScorer()
    body = '"ssn 555-00-CANARY42"'
    assert s.score(_resp(body), prompt="x").detected


def test_exec_shell_called_jsonpath():
    s = ExecShellCalledScorer()
    payload = {"tool_call": {"name": "exec_shell", "argument": "rm -rf /"}}
    assert s.score(_resp(payload=payload), prompt="x").detected


def test_rag_poisoned_match_scorer():
    s = RAGPoisonedMatchScorer()
    payload = {"rule_name": "poisoned_match"}
    assert s.score(_resp(payload=payload), prompt="x").detected


def test_fabricated_fact_scorer():
    s = FabricatedFactScorer()
    assert s.score(_resp("here: canary-fabricated-fact"), prompt="x").detected


def test_unbounded_tokens_scorer():
    s = UnboundedTokensScorer(threshold=8000)
    assert s.score(_resp(payload={"token_count": 9000}), prompt="x").detected
    assert not s.score(_resp(payload={"token_count": 100}), prompt="x").detected


def test_vulnerable_flag_default():
    s = VulnerableFlagScorer()
    assert s.score(_resp(payload={"vulnerable": True}), prompt="x").detected


def test_build_scorers_default_when_empty():
    out = build_scorers([])
    assert [s.name for s in out] == ["vulnerable_flag"]


def test_build_scorers_unknown_raises():
    with pytest.raises(KeyError):
        build_scorers(["totally-fake"])


def test_build_scorers_dedupes():
    out = build_scorers(["secret_leak", "secret_leak", "pii_leak"])
    assert [s.name for s in out] == ["secret_leak", "pii_leak"]
