"""Thin OpenRouter chat-completions client that returns *measured* results.

Every call reports wall-clock latency, prompt/completion tokens and the
billed cost from the response ``usage.cost`` (requested with
``usage: {"include": true}``). When the response carries no cost the cost is
computed from the catalog list price and flagged ``cost_source="list_price"``;
unknown models are flagged ``"unpriced"``. Nothing here guesses silently.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

from .config import OPENROUTER_BASE_URL
from .pricing import list_cost


class OpenRouterError(RuntimeError):
    """Network failure, non-200 status, or an unparseable response."""


@dataclass
class ChatResult:
    content: str
    served_model: str
    tokens_in: int
    tokens_out: int
    cost_usd: Optional[float]
    cost_source: str              # "usage" | "list_price" | "unpriced"
    latency_ms: float
    metadata: Dict[str, Any] = field(default_factory=dict)   # openrouter_metadata
    raw_usage: Dict[str, Any] = field(default_factory=dict)

    def router_meta(self) -> Dict[str, Any]:
        """The ``jev-router`` entry of ``openrouter_metadata.pipeline`` (or {})."""
        for step in self.metadata.get("pipeline") or []:
            if isinstance(step, dict) and step.get("name") == "jev-router":
                data = step.get("data")
                return data if isinstance(data, dict) else {}
        return {}


class OpenRouterChat:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = OPENROUTER_BASE_URL,
        timeout: float = 30.0,
        client: Optional[httpx.Client] = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = client or httpx.Client(timeout=httpx.Timeout(timeout))

    def complete(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        *,
        max_tokens: int = 512,
        temperature: Optional[float] = 0.0,
        extra_body: Optional[Dict[str, Any]] = None,
        want_metadata: bool = False,
    ) -> ChatResult:
        body: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "usage": {"include": True},
        }
        if temperature is not None:
            body["temperature"] = temperature
        if extra_body:
            body.update(extra_body)
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if want_metadata:
            headers["X-OpenRouter-Metadata"] = "enabled"

        t0 = time.perf_counter()
        try:
            resp = self._client.post(f"{self.base_url}/chat/completions", json=body, headers=headers)
        except Exception as exc:
            raise OpenRouterError(f"request failed: {exc}") from exc
        latency_ms = (time.perf_counter() - t0) * 1000.0
        if resp.status_code != 200:
            raise OpenRouterError(f"{resp.status_code}: {resp.text[:300]}")
        try:
            payload = resp.json()
        except ValueError as exc:
            raise OpenRouterError(f"non-JSON response: {exc}") from exc

        message = ((payload.get("choices") or [{}])[0].get("message") or {})
        content = message.get("content") or ""
        if not isinstance(content, str):
            content = str(content)
        served = payload.get("model") or model
        usage = payload.get("usage") or {}
        tokens_in = int(usage.get("prompt_tokens") or 0)
        tokens_out = int(usage.get("completion_tokens") or 0)
        cost = usage.get("cost")
        if cost is not None:
            cost_usd, source = float(cost), "usage"
        else:
            cost_usd = list_cost(served, tokens_in, tokens_out)
            source = "list_price" if cost_usd is not None else "unpriced"
        meta = payload.get("openrouter_metadata")
        return ChatResult(
            content=content,
            served_model=served,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost_usd,
            cost_source=source,
            latency_ms=latency_ms,
            metadata=meta if isinstance(meta, dict) else {},
            raw_usage=usage if isinstance(usage, dict) else {},
        )
