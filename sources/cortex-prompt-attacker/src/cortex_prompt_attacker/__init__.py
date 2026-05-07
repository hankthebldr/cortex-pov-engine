"""
cortex-prompt-attacker — probe → mutator → target → scorer pipeline for
CortexSim AIRS detection validation.

Public surface:

  * Probe        — declarative YAML rule (promptmap-compatible schema)
  * Mutator      — composable, stateless prompt transformer (PyRIT-shape)
  * PromptTarget — abstract HTTP target; ships HTTPTarget
  * Scorer       — pluggable named detector (regex / substring / OWASP)
  * Pipeline     — runs Probe → ordered Mutators → Target → list of Scorers
  * Attempt      — garak-shape result record with as_dict() JSONL serialiser
  * Runner       — iterates probes, writes JSONL of Attempts

Drop-in compatibility: probes follow the promptmap rule schema (extended
with ``owasp_id`` / ``mutators`` / ``scorer`` / ``schema_version``).
No GPL code is imported — only the YAML schema is mirrored.
"""

from __future__ import annotations

from .attempt import Attempt
from .loader import load_probes_from_dir, load_probes_from_paths
from .mutators import (
    MUTATOR_REGISTRY,
    Base64Mutator,
    IndirectInjectionMutator,
    LeetspeakMutator,
    Mutator,
    NoopMutator,
    RepeatTokenMutator,
    RolePlayMutator,
    ROT13Mutator,
    ToolAbuseMutator,
    TranslationMutator,
    UnicodeConfusableMutator,
    build_mutator_chain,
)
from .pipeline import Pipeline, PipelineResult
from .probes import Probe, ProbeSeverity, ProbeType
from .runner import Runner
from .scorers import (
    SCORER_REGISTRY,
    JSONPathScorer,
    RegexScorer,
    Scorer,
    ScorerResult,
    SubstringScorer,
    build_scorers,
)
from .targets import HTTPTarget, PromptTarget, TargetResponse

__version__ = "1.0.0"

__all__ = [
    "Attempt",
    "Base64Mutator",
    "HTTPTarget",
    "IndirectInjectionMutator",
    "JSONPathScorer",
    "LeetspeakMutator",
    "MUTATOR_REGISTRY",
    "Mutator",
    "NoopMutator",
    "Pipeline",
    "PipelineResult",
    "Probe",
    "ProbeSeverity",
    "ProbeType",
    "PromptTarget",
    "ROT13Mutator",
    "RegexScorer",
    "RepeatTokenMutator",
    "RolePlayMutator",
    "Runner",
    "SCORER_REGISTRY",
    "Scorer",
    "ScorerResult",
    "SubstringScorer",
    "TargetResponse",
    "ToolAbuseMutator",
    "TranslationMutator",
    "UnicodeConfusableMutator",
    "__version__",
    "build_mutator_chain",
    "build_scorers",
    "load_probes_from_dir",
    "load_probes_from_paths",
]
