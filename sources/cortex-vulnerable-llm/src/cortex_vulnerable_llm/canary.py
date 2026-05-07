"""
Canary LLM — a deterministic, regex-driven stand-in for a real model.

The canary is the single primitive every OWASP blueprint uses to decide
whether an incoming prompt should produce a "compromised" response or a
canned "safe" response. There is no neural network, no API call, no
randomness — just a table of named patterns. This keeps AIRS POVs
reproducible, key-free, and auditable.

Each ``CanaryRule`` declares:
  * ``name``         — stable identifier (logged in responses for scoring)
  * ``owasp_id``     — which OWASP class triggered (e.g. "LLM01")
  * ``patterns``     — list of compiled regex patterns; ANY match fires
  * ``compromised``  — the response template to emit on match
                       (may include ``{leak}`` / ``{captured}`` placeholders)

The default safe response is intentionally bland. Scorers in
``cortex-prompt-attacker`` look for the compromised text or any of the
substrings in ``leak_markers`` to decide vuln/no-vuln.
"""

from __future__ import annotations

import dataclasses
import re
from typing import Iterable, Optional, Pattern


@dataclasses.dataclass(frozen=True)
class CanaryResponse:
    """Result of a canary decision — what to return + why."""

    text: str
    vulnerable: bool
    rule_name: Optional[str] = None
    owasp_id: Optional[str] = None
    leak_markers: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "text": self.text,
            "vulnerable": self.vulnerable,
            "rule_name": self.rule_name,
            "owasp_id": self.owasp_id,
            "leak_markers": list(self.leak_markers),
        }


@dataclasses.dataclass
class CanaryRule:
    name: str
    owasp_id: str
    patterns: list[Pattern[str]]
    compromised_template: str
    leak_markers: tuple[str, ...] = ()

    @classmethod
    def from_strings(
        cls,
        name: str,
        owasp_id: str,
        patterns: Iterable[str],
        compromised_template: str,
        leak_markers: Iterable[str] = (),
    ) -> "CanaryRule":
        compiled = [re.compile(p, re.IGNORECASE | re.DOTALL) for p in patterns]
        return cls(
            name=name,
            owasp_id=owasp_id,
            patterns=compiled,
            compromised_template=compromised_template,
            leak_markers=tuple(leak_markers),
        )

    def matches(self, prompt: str) -> bool:
        return any(p.search(prompt) for p in self.patterns)


class Canary:
    """Holds an ordered rule list and renders the response for a prompt.

    Rule order matters: the first rule that matches wins. Put the most
    specific rules first.
    """

    DEFAULT_SAFE = "I'm sorry, I can't help with that request."

    def __init__(
        self,
        rules: Iterable[CanaryRule],
        safe_response: str = DEFAULT_SAFE,
        context: Optional[dict[str, str]] = None,
    ) -> None:
        self.rules: list[CanaryRule] = list(rules)
        self.safe_response = safe_response
        # ``context`` is the substitution dict for compromised templates.
        # Typical entries: ``system_prompt``, ``api_key``, ``rag_payload``.
        self.context: dict[str, str] = dict(context or {})

    def evaluate(self, prompt: str) -> CanaryResponse:
        if not isinstance(prompt, str):
            return CanaryResponse(text=self.safe_response, vulnerable=False)

        for rule in self.rules:
            if rule.matches(prompt):
                text = rule.compromised_template.format_map(_SafeDict(self.context))
                return CanaryResponse(
                    text=text,
                    vulnerable=True,
                    rule_name=rule.name,
                    owasp_id=rule.owasp_id,
                    leak_markers=rule.leak_markers,
                )

        return CanaryResponse(text=self.safe_response, vulnerable=False)

    def update_context(self, **kwargs: str) -> None:
        self.context.update(kwargs)


class _SafeDict(dict):
    """str.format_map helper that leaves unresolved placeholders intact.

    A blueprint may emit a compromised template with placeholders the
    runtime context didn't fill — better to render ``{system_prompt}``
    literally than to crash a probe response.
    """

    def __missing__(self, key: str) -> str:  # pragma: no cover - cosmetic
        return "{" + key + "}"
