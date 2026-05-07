"""
PromptTarget — abstract HTTP target the pipeline POSTs to.

Ships with one concrete implementation, ``HTTPTarget``, that speaks the
JSON dialect of the cortex-vulnerable-llm canary. Custom dialects can be
plugged in by subclassing ``PromptTarget`` and overriding ``send``.
"""

from __future__ import annotations

import abc
import dataclasses
from typing import Any, Optional

import httpx


@dataclasses.dataclass
class TargetResponse:
    status_code: int
    text: str
    json: Optional[dict[str, Any]] = None
    elapsed_ms: float = 0.0
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None and 200 <= self.status_code < 400


class PromptTarget(abc.ABC):
    """Abstract HTTP target. Subclasses implement ``send``."""

    @abc.abstractmethod
    def send(self, prompt: str, *, target_path: Optional[str] = None) -> TargetResponse:
        ...


class HTTPTarget(PromptTarget):
    """Posts JSON ``{"prompt": <prompt>}`` to ``url`` and parses JSON back.

    The body template can be overridden via ``body_template`` for targets
    that expect a different field name. Use ``{prompt}`` placeholder.
    """

    DEFAULT_BODY_TEMPLATE = '{{"prompt": {prompt}}}'

    def __init__(
        self,
        url: str,
        *,
        method: str = "POST",
        headers: Optional[dict[str, str]] = None,
        timeout_seconds: float = 30.0,
        body_template: Optional[str] = None,
        verify_tls: bool = False,
    ) -> None:
        self.url = url.rstrip("/")
        self.method = method.upper()
        self.headers = {
            "content-type": "application/json",
            "x-cortexsim-attacker": "cortex-prompt-attacker/1.0",
            **(headers or {}),
        }
        self.timeout_seconds = timeout_seconds
        self.body_template = body_template or self.DEFAULT_BODY_TEMPLATE
        self._client = httpx.Client(
            timeout=timeout_seconds,
            verify=verify_tls,
            follow_redirects=False,
        )

    def _build_url(self, target_path: Optional[str]) -> str:
        if target_path is None:
            return self.url
        if target_path.startswith("http://") or target_path.startswith("https://"):
            return target_path
        return f"{self.url}/{target_path.lstrip('/')}"

    def _build_body(self, prompt: str) -> str:
        # JSON-escape prompt then template it into the body.
        import json

        encoded_prompt = json.dumps(prompt)  # quoted + escaped
        return self.body_template.format(prompt=encoded_prompt)

    def send(self, prompt: str, *, target_path: Optional[str] = None) -> TargetResponse:
        url = self._build_url(target_path)
        body = self._build_body(prompt)
        try:
            resp = self._client.request(
                self.method, url, headers=self.headers, content=body,
            )
        except httpx.HTTPError as exc:
            return TargetResponse(
                status_code=0, text="", json=None, elapsed_ms=0.0,
                error=f"http_error: {exc}",
            )

        text = resp.text
        try:
            payload = resp.json()
        except Exception:
            payload = None
        return TargetResponse(
            status_code=resp.status_code,
            text=text,
            json=payload,
            elapsed_ms=resp.elapsed.total_seconds() * 1000.0,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "HTTPTarget":
        return self

    def __exit__(self, _t, _v, _tb) -> None:
        self.close()
