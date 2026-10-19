"""Thin fail-closed client for the OpenRouter Decisions API (Jev).

Contract: :meth:`JevClient.ask` either returns a dict of typed answers or
raises :class:`JevError`.  Callers (the gate hooks) treat *any* failure as
"review" — never as approve.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional

import httpx

from ..config import (
    DECISIONS_URL,
    JEV_COST_PER_CALL,
    JEV_MODEL,
    JEV_OUTPUT_TOKENS,
)
from ..metrics import TokenUsage

__all__ = ["JevError", "JevAnswer", "JevCallResult", "JevClient", "parse_answer", "to_wire"]


class JevError(Exception):
    """Raised on any Decisions API failure (network, timeout, non-2xx, bad shape)."""


@dataclass
class JevAnswer:
    """One typed answer from the Decisions API."""

    type: str                                  # "noul" | "choice" | "score"
    value: Any                                 # float for noul/score, str for choice
    probabilities: Dict[str, float] = field(default_factory=dict)
    confidence: Optional[float] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def noul(self) -> float:
        return float(self.value)

    @property
    def choice_value(self) -> str:
        return str(self.value)


@dataclass
class JevCallResult:
    """Answers plus measured usage/latency for one Decisions call."""

    answers: Dict[str, JevAnswer]
    usage: TokenUsage
    latency_ms: float


def to_wire(questions: Mapping[str, Dict]) -> Dict[str, Dict]:
    """Translate the harness's question dicts to the Decisions API schema.

    * ``choice``: ``criteria`` must be an object {option: description}; a list
      of option names becomes {name: name}.
    * ``score``: takes ``criteria`` (ordered level descriptions) and has no
      ``min``/``max``.
    * ``noul`` is already in wire form.
    """
    wire: Dict[str, Dict] = {}
    for name, q in questions.items():
        q = dict(q)
        if q.get("type") == "choice" and isinstance(q.get("criteria"), (list, tuple)):
            q["criteria"] = {str(c): str(c) for c in q["criteria"]}
        elif q.get("type") == "score":
            q.pop("min", None)
            q.pop("max", None)
        wire[name] = q
    return wire


def parse_answer(name: str, raw: Any) -> Optional[JevAnswer]:
    """Parse one typed answer dict (noul | choice | score); None if malformed.

    Shared by JevClient, FakeJevClient and the Playground's LLMJudge so all
    three apply identical validation.
    """
    if not isinstance(raw, dict):
        return None
    typ = raw.get("type")
    if typ == "noul":
        if "noul" not in raw:
            return None
        return JevAnswer(type="noul", value=float(raw["noul"]),
                         confidence=float(raw["noul"]), raw=raw)
    if typ == "choice":
        probs = raw.get("probabilities") or {}
        chosen = raw.get("choice")
        if chosen is None or not isinstance(probs, dict):
            return None
        return JevAnswer(
            type="choice",
            value=str(chosen),
            probabilities={str(k): float(v) for k, v in probs.items()},
            confidence=float(raw.get("confidence", probs.get(chosen, 0.0) or 0.0)),
            raw=raw,
        )
    if typ == "score":
        if "score" not in raw:
            return None
        return JevAnswer(type="score", value=float(raw["score"]), confidence=1.0, raw=raw)
    return None


class JevClient:
    """Synchronous Decisions API client with an 8s default timeout."""

    def __init__(
        self,
        api_key: str,
        *,
        model: str = JEV_MODEL,
        base_url: str = DECISIONS_URL,
        timeout: float = 8.0,
        client: Optional[httpx.Client] = None,
    ) -> None:
        self.model = model
        self.base_url = base_url
        self.timeout = timeout
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(timeout),
            headers={"Authorization": f"Bearer {api_key}"},
        )
        self.last_usage: Dict[str, Any] = {}

    def ask_with_usage(self, state: Any, questions: Mapping[str, Dict]) -> JevCallResult:
        """Evaluate ``questions`` against ``state`` with measured usage/latency.

        Raises JevError on failure (fail-closed contract unchanged).
        """
        body = {"model": self.model, "state": state, "questions": to_wire(questions)}
        t0 = time.perf_counter()
        try:
            resp = self._client.post(self.base_url, json=body)
        except Exception as exc:  # network / timeout — fail closed upstream
            raise JevError(f"decisions request failed: {exc}") from exc
        latency_ms = (time.perf_counter() - t0) * 1000.0
        if resp.status_code != 200:
            raise JevError(f"decisions API returned {resp.status_code}: {resp.text[:200]}")
        try:
            payload = resp.json()
        except ValueError as exc:
            raise JevError(f"decisions API returned non-JSON: {exc}") from exc
        usage = self._extract_usage(payload, state, questions)
        self.last_usage = usage.__dict__
        answers = payload.get("answers")
        if not isinstance(answers, dict):
            raise JevError("decisions response missing 'answers'")
        parsed: Dict[str, JevAnswer] = {}
        for name, raw in answers.items():
            ans = self._parse_answer(name, raw)
            if ans is None:
                raise JevError(f"answer for '{name}' missing/invalid: {raw!r}")
            parsed[name] = ans
        return JevCallResult(answers=parsed, usage=usage, latency_ms=latency_ms)

    def ask(self, state: Any, questions: Mapping[str, Dict]) -> Dict[str, JevAnswer]:
        """Backward-compatible: answers only. Use ask_with_usage() for metrics."""
        return self.ask_with_usage(state, questions).answers

    @staticmethod
    def _extract_usage(payload: Dict[str, Any], state: Any, questions: Mapping[str, Dict]) -> TokenUsage:
        """Pull usage from the response; estimate tokens when absent.

        The Decisions API reports ``usage.cost`` reliably; token counts are
        estimated as len(serialized)/4 when not returned.
        """
        raw_usage = payload.get("usage") or {}
        cost = raw_usage.get("cost")
        try:
            cost_usd = float(cost) if cost is not None else JEV_COST_PER_CALL
        except (TypeError, ValueError):
            cost_usd = JEV_COST_PER_CALL
        in_toks = raw_usage.get("prompt_tokens") or raw_usage.get("input_tokens")
        out_toks = raw_usage.get("completion_tokens") or raw_usage.get("output_tokens")
        if not in_toks:
            try:
                import json as _json
                in_toks = int(len(_json.dumps(
                    {"state": state, "questions": dict(questions)}, default=str
                )) / 4)
            except Exception:
                in_toks = 400
        if not out_toks:
            out_toks = JEV_OUTPUT_TOKENS
        return TokenUsage(input_tokens=int(in_toks), output_tokens=int(out_toks), cost_usd=cost_usd)

    @staticmethod
    def _parse_answer(name: str, raw: Any) -> Optional[JevAnswer]:
        return parse_answer(name, raw)
