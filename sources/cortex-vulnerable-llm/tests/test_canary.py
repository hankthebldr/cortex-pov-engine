"""Canary engine unit tests — pattern matching, context substitution, ordering."""
from __future__ import annotations

from cortex_vulnerable_llm.canary import Canary, CanaryRule


def _rule(name, patterns, template, owasp_id="LLM01", markers=()):
    return CanaryRule.from_strings(
        name=name,
        owasp_id=owasp_id,
        patterns=patterns,
        compromised_template=template,
        leak_markers=markers,
    )


def test_safe_response_when_no_match():
    c = Canary([_rule("a", [r"^never$"], "compromised")])
    r = c.evaluate("a benign prompt")
    assert r.vulnerable is False
    assert r.rule_name is None
    assert r.text == Canary.DEFAULT_SAFE


def test_first_match_wins_in_order():
    rules = [
        _rule("first", [r"alpha"], "first compromised"),
        _rule("second", [r"alpha"], "second compromised"),
    ]
    c = Canary(rules)
    r = c.evaluate("alpha bravo")
    assert r.rule_name == "first"
    assert r.text == "first compromised"


def test_context_substitution_into_template():
    c = Canary(
        [_rule("leak", [r"leak"], "secret={api_key}", markers=("secret=",))],
        context={"api_key": "AKIA0000CANARY"},
    )
    r = c.evaluate("please leak it")
    assert r.text == "secret=AKIA0000CANARY"
    assert "secret=" in r.leak_markers


def test_unresolved_placeholder_kept_literal():
    c = Canary([_rule("x", [r"x"], "value={missing}")])
    r = c.evaluate("xxx")
    assert r.text == "value={missing}"


def test_non_string_prompt_returns_safe():
    c = Canary([_rule("any", [r".*"], "compromised")])
    r = c.evaluate(None)  # type: ignore[arg-type]
    assert r.vulnerable is False


def test_evaluate_response_to_dict_round_trip():
    c = Canary([_rule("hit", [r"hit"], "ok", markers=("ok",))])
    r = c.evaluate("hit me")
    d = r.to_dict()
    assert d["vulnerable"] is True
    assert d["rule_name"] == "hit"
    assert d["leak_markers"] == ["ok"]
