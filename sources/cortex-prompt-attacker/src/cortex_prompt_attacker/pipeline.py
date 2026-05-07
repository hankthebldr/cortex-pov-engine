"""
Pipeline — Probe → Mutators → Target → Scorers → Attempt.

One ``Pipeline`` runs one probe against one target with an ordered list of
mutators and a list of scorers. The ``Runner`` (separate module) drives a
list of probes through this pipeline and writes the JSONL.
"""

from __future__ import annotations

import dataclasses
from typing import Optional

from .attempt import Attempt
from .mutators import Mutator, build_mutator_chain
from .probes import Probe
from .scorers import Scorer, ScorerResult, build_scorers
from .targets import PromptTarget


@dataclasses.dataclass
class PipelineResult:
    attempt: Attempt
    scorer_results: list[ScorerResult]


class Pipeline:
    def __init__(
        self,
        target: PromptTarget,
        *,
        default_mutators: Optional[list[str]] = None,
        default_scorers: Optional[list[str]] = None,
    ) -> None:
        self.target = target
        self.default_mutators = default_mutators or []
        self.default_scorers = default_scorers or ["vulnerable_flag"]

    def _resolve_mutators(self, probe: Probe) -> list[Mutator]:
        # Probe-level mutators win over the pipeline default. Empty list on
        # the probe means "use default"; explicit non-empty means "use this".
        names = probe.mutators if probe.mutators else self.default_mutators
        return build_mutator_chain(names)

    def _resolve_scorers(self, probe: Probe) -> list[Scorer]:
        # Primary scorer first, then extended list, falling back to defaults.
        names: list[str] = []
        if probe.scorer:
            names.append(probe.scorer)
        names.extend(probe.extended_scorers)
        if not names:
            names = self.default_scorers
        return build_scorers(names)

    def _apply_mutators(self, prompt: str, mutators: list[Mutator]) -> tuple[str, list[str]]:
        applied: list[str] = []
        out = prompt
        for m in mutators:
            out = m.mutate(out)
            applied.append(m.name)
        return out, applied

    def run_probe(self, probe: Probe, *, seq: int = 0) -> PipelineResult:
        attempt = Attempt(
            seq=seq,
            probe_classname=probe.name,
            probe_params={
                "type": probe.type,
                "severity": probe.severity.value,
            },
            targets=[getattr(self.target, "url", "")],
            prompt=probe.prompt,
            owasp_id=probe.owasp_id,
            severity=probe.severity.value,
            goal=probe.goal or "",
        )
        attempt.start()

        try:
            mutators = self._resolve_mutators(probe)
            scorers = self._resolve_scorers(probe)
        except KeyError as exc:
            attempt.notes["error"] = f"resolve_failed: {exc}"
            attempt.complete("error")
            return PipelineResult(attempt=attempt, scorer_results=[])

        mutated, applied = self._apply_mutators(probe.prompt, mutators)
        attempt.mutated_prompt = mutated
        attempt.mutators_applied = applied

        response = self.target.send(mutated, target_path=probe.target_path)
        attempt.outputs.append(response.text)
        attempt.notes["status_code"] = response.status_code
        attempt.notes["elapsed_ms"] = response.elapsed_ms
        if response.error:
            attempt.notes["http_error"] = response.error
            attempt.complete("error")
            return PipelineResult(attempt=attempt, scorer_results=[])

        scorer_results: list[ScorerResult] = []
        for scorer in scorers:
            result = scorer.score(response, prompt=mutated)
            scorer_results.append(result)
            attempt.detector_results[result.name] = result.detected
            if result.matched_markers:
                attempt.detector_meta.setdefault(result.name, {})["markers"] = result.matched_markers
            if result.metric is not None:
                attempt.detector_meta.setdefault(result.name, {})["metric"] = result.metric

        # Outcome roll-up: any positive scorer ⇒ vuln; otherwise clean.
        outcome = "vuln" if any(r.detected for r in scorer_results) else "clean"
        attempt.complete(outcome)

        return PipelineResult(attempt=attempt, scorer_results=scorer_results)
