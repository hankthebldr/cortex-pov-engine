"""Runner tests — JSONL output, run_meta header, summary roll-up."""
from __future__ import annotations

import io
import json

from cortex_prompt_attacker.pipeline import Pipeline
from cortex_prompt_attacker.probes import Probe
from cortex_prompt_attacker.runner import Runner

from tests.test_pipeline import StubTarget


def _probe(name="p", **kw):
    base = {
        "name": name, "type": "prompt_injection", "prompt": "hi",
        "owasp_id": "LLM01", "scorer": "vulnerable_flag",
    }
    base.update(kw)
    return Probe.model_validate(base)


def test_runner_writes_run_meta_header_and_attempt_lines():
    t = StubTarget({"vulnerable": True, "text": "ok"})
    pipe = Pipeline(t)
    buf = io.StringIO()
    runner = Runner(pipe, iterations=1, out_stream=buf)
    summary = runner.run([_probe("a"), _probe("b")])

    lines = [json.loads(L) for L in buf.getvalue().splitlines()]
    assert lines[0]["entry_type"] == "run_meta"
    assert lines[0]["probes_total"] == 2
    assert lines[1]["entry_type"] == "attempt"
    assert {L["probe_classname"] for L in lines[1:]} == {"a", "b"}
    assert summary.attempts_run == 2
    assert summary.vuln_count == 2
    assert summary.vuln_rate == 1.0


def test_iterations_repeat_each_probe():
    t = StubTarget({"vulnerable": False})
    pipe = Pipeline(t)
    buf = io.StringIO()
    summary = Runner(pipe, iterations=3, out_stream=buf).run([_probe("p")])
    assert summary.attempts_run == 3
    assert summary.clean_count == 3


def test_summary_to_dict_contains_vuln_rate():
    t = StubTarget({"vulnerable": True})
    summary = Runner(Pipeline(t), out_stream=io.StringIO()).run([_probe()])
    d = summary.to_dict()
    assert d["vuln_rate"] == 1.0
    assert d["vuln_count"] == 1
