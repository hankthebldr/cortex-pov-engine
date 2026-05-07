"""
Runner — iterates probes through the pipeline and writes JSONL.

A single run produces one ``run_meta`` line followed by one ``attempt``
line per probe×iteration. Probes can be repeated via ``iterations`` to
exercise stochastic mutators or DoS-class scorers (LLM10).
"""

from __future__ import annotations

import dataclasses
import json
import sys
from io import TextIOBase
from pathlib import Path
from typing import Iterable, Optional, TextIO

from .attempt import run_meta
from .pipeline import Pipeline, PipelineResult
from .probes import Probe


@dataclasses.dataclass
class RunSummary:
    probes_total: int
    iterations: int
    attempts_run: int
    vuln_count: int
    clean_count: int
    error_count: int

    @property
    def vuln_rate(self) -> float:
        if self.attempts_run == 0:
            return 0.0
        return self.vuln_count / self.attempts_run

    def to_dict(self) -> dict[str, float | int]:
        return {
            **dataclasses.asdict(self),
            "vuln_rate": round(self.vuln_rate, 4),
        }


class Runner:
    def __init__(
        self,
        pipeline: Pipeline,
        *,
        iterations: int = 1,
        out_stream: Optional[TextIO] = None,
    ) -> None:
        self.pipeline = pipeline
        self.iterations = max(1, int(iterations))
        self.out_stream = out_stream or sys.stdout

    def run(self, probes: Iterable[Probe]) -> RunSummary:
        probes = list(probes)
        total_attempts = 0
        vuln = 0
        clean = 0
        errors = 0

        meta = run_meta(
            probes=len(probes),
            target_url=getattr(self.pipeline.target, "url", ""),
            mutators=list(self.pipeline.default_mutators),
            scorers=list(self.pipeline.default_scorers),
        )
        self._emit(meta)

        seq = 0
        for probe in probes:
            for _i in range(self.iterations):
                result = self.pipeline.run_probe(probe, seq=seq)
                seq += 1
                total_attempts += 1
                if result.attempt.outcome == "vuln":
                    vuln += 1
                elif result.attempt.outcome == "clean":
                    clean += 1
                else:
                    errors += 1
                self._emit(result.attempt.as_dict())

        return RunSummary(
            probes_total=len(probes),
            iterations=self.iterations,
            attempts_run=total_attempts,
            vuln_count=vuln,
            clean_count=clean,
            error_count=errors,
        )

    def _emit(self, payload: dict) -> None:
        line = json.dumps(payload, separators=(",", ":"), default=str)
        self.out_stream.write(line + "\n")
        # Flush if the stream supports it so subprocess consumers see lines
        # as they're written (the EAL plugin tails the JSONL).
        flush = getattr(self.out_stream, "flush", None)
        if callable(flush):
            try:
                flush()
            except (BrokenPipeError, OSError):  # pragma: no cover
                pass


def open_jsonl_writer(path: str | Path) -> TextIO:
    """Open a path for line-buffered append."""
    return open(path, "a", encoding="utf-8", buffering=1)
