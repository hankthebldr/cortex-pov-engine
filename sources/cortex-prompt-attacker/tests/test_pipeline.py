"""Pipeline tests — happy path, mutator chain ordering, scorer roll-up."""
from __future__ import annotations

from typing import Optional

from cortex_prompt_attacker.mutators import build_mutator_chain
from cortex_prompt_attacker.pipeline import Pipeline
from cortex_prompt_attacker.probes import Probe
from cortex_prompt_attacker.targets import PromptTarget, TargetResponse


class StubTarget(PromptTarget):
    """In-memory target that returns the canary's standard response shape."""

    def __init__(self, payload: dict, *, status: int = 200) -> None:
        self.url = "http://stub.invalid"
        self.payload = payload
        self.status = status
        self.last_prompt: Optional[str] = None
        self.last_path: Optional[str] = None

    def send(self, prompt: str, *, target_path: Optional[str] = None) -> TargetResponse:
        self.last_prompt = prompt
        self.last_path = target_path
        import json

        text = json.dumps(self.payload)
        return TargetResponse(
            status_code=self.status, text=text, json=self.payload, elapsed_ms=1.5,
        )


def _probe(**kw) -> Probe:
    base = {
        "name": "p", "type": "prompt_injection", "prompt": "hi",
        "owasp_id": "LLM01",
    }
    base.update(kw)
    return Probe.model_validate(base)


def test_clean_outcome_when_canary_returns_safe():
    t = StubTarget({"vulnerable": False, "text": "I cannot help"})
    p = _probe(scorer="vulnerable_flag")
    pipe = Pipeline(t)
    result = pipe.run_probe(p)
    assert result.attempt.outcome == "clean"
    assert result.attempt.detector_results["vulnerable_flag"] is False


def test_vuln_outcome_when_canary_marks_vulnerable():
    t = StubTarget({"vulnerable": True, "text": "compromised"})
    p = _probe(scorer="vulnerable_flag")
    result = Pipeline(t).run_probe(p)
    assert result.attempt.outcome == "vuln"


def test_target_path_override_passes_through():
    t = StubTarget({"vulnerable": True, "text": "x"})
    p = _probe(target_path="/owasp/llm06/agent")
    Pipeline(t).run_probe(p)
    assert t.last_path == "/owasp/llm06/agent"


def test_mutator_chain_applied_in_order():
    t = StubTarget({"vulnerable": False, "text": ""})
    p = _probe(prompt="HELLO", mutators=["leetspeak", "rot13"])
    result = Pipeline(t).run_probe(p)
    # leetspeak: HELLO → H3110, then rot13 → U3110
    assert "U3110" in (t.last_prompt or "")
    assert result.attempt.mutators_applied == ["leetspeak", "rot13"]


def test_unknown_mutator_marks_attempt_error():
    t = StubTarget({"vulnerable": False})
    p = _probe(mutators=["totally-not-real"])
    result = Pipeline(t).run_probe(p)
    assert result.attempt.outcome == "error"
    assert "resolve_failed" in result.attempt.notes.get("error", "")


def test_extended_scorers_compose_with_primary():
    t = StubTarget({
        "vulnerable": True,
        "text": "exec_shell ran",
        "tool_call": {"name": "exec_shell", "argument": "rm"},
    })
    p = _probe(
        scorer="vulnerable_flag",
        extended_scorers=["exec_shell_called", "tool_call_unsafe"],
    )
    result = Pipeline(t).run_probe(p)
    assert result.attempt.detector_results["vulnerable_flag"] is True
    assert result.attempt.detector_results["exec_shell_called"] is True
    assert result.attempt.detector_results["tool_call_unsafe"] is True
    assert result.attempt.outcome == "vuln"


def test_default_mutators_used_when_probe_omits():
    t = StubTarget({"vulnerable": False})
    pipe = Pipeline(t, default_mutators=["leetspeak"])
    p = _probe(prompt="apple", mutators=[])  # empty → use default
    Pipeline(t).run_probe(p)  # check chain via separate pipeline
    pipe.run_probe(p)
    assert any(c.isdigit() for c in (t.last_prompt or ""))


def test_chain_builder_exported():
    chain = build_mutator_chain(["base64"])
    assert chain[0].name == "base64"
