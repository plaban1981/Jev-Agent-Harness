"""Offline fake for the Jev Decisions API.

``FakeJevClient`` implements the same ``ask(state, questions)`` interface as
:mod:`jev_harness.jev.client` but answers from a scripted map keyed by
question name.  A value may be:

* a dict shaped like a Decisions API answer
  (``{"type": "noul", "noul": 0.93}``),
* a callable ``fn(state, question) -> answer dict`` for predicate hooks,
* the string ``"RAISE"`` to simulate a Jev outage for that question.

Every call is recorded on ``.calls`` for test assertions.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Mapping, Optional

from ..config import JEV_COST_PER_CALL, JEV_INPUT_TOKENS, JEV_LATENCY_MS, JEV_OUTPUT_TOKENS
from ..metrics import TokenUsage
from .client import JevAnswer, JevCallResult, JevError

ScriptValue = Any  # answer dict | callable | "RAISE"


class FakeJevClient:
    def __init__(self, script: Optional[Mapping[str, ScriptValue]] = None) -> None:
        self.script: Dict[str, ScriptValue] = dict(script or {})
        self.calls: List[Dict[str, Any]] = []
        self.always_raise = False
        self.last_usage = {}
        self.fake_latency_ms = JEV_LATENCY_MS  # deterministic offline latency

    # -- scripting helpers ----------------------------------------------------
    def set(self, question: str, value: ScriptValue) -> None:
        self.script[question] = value

    def set_many(self, mapping: Mapping[str, ScriptValue]) -> None:
        self.script.update(mapping)

    def fail_next(self, n: int = 1) -> None:
        """Make the next ``n`` ask() calls raise JevError (simulates outage)."""
        self._fail_budget = getattr(self, "_fail_budget", 0) + n

    # -- interface ------------------------------------------------------------
    def canned_usage(self, questions: Mapping[str, Dict]) -> TokenUsage:
        """Canned per-call usage mirroring captured OpenRouter cookbook runs."""
        return TokenUsage(
            input_tokens=JEV_INPUT_TOKENS,
            output_tokens=JEV_OUTPUT_TOKENS,
            cost_usd=JEV_COST_PER_CALL,
        )

    def ask_with_usage(self, state: Any, questions: Mapping[str, Dict]) -> JevCallResult:
        answers = self._resolve(state, questions)
        usage = self.canned_usage(questions)
        self.last_usage = usage.__dict__
        return JevCallResult(
            answers=answers,
            usage=usage,
            latency_ms=self.fake_latency_ms,  # deterministic
        )

    def ask(self, state: Any, questions: Mapping[str, Dict]) -> Dict[str, JevAnswer]:
        """Backward-compatible: answers only."""
        return self.ask_with_usage(state, questions).answers

    def _resolve(self, state: Any, questions: Mapping[str, Dict]) -> Dict[str, JevAnswer]:
        self.calls.append({"state": state, "questions": dict(questions)})
        budget = getattr(self, "_fail_budget", 0)
        if self.always_raise or budget > 0:
            if budget > 0:
                self._fail_budget = budget - 1
            raise JevError("fake Jev outage")

        parsed: Dict[str, JevAnswer] = {}
        for name, q in questions.items():
            if name not in self.script:
                raise JevError(f"FakeJevClient has no scripted answer for question '{name}'")
            value = self.script[name]
            if callable(value):
                value = value(state, q)
            if value == "RAISE":
                raise JevError(f"fake Jev outage on question '{name}'")
            ans = JevClient_parse(name, value)
            if ans is None:
                raise JevError(f"scripted answer for '{name}' invalid: {value!r}")
            parsed[name] = ans
        return parsed


def JevClient_parse(name: str, raw: Any) -> Optional[JevAnswer]:
    """Reuse the real client's parser so fakes share validation semantics.

    Convenience shorthands: a bare float/int becomes a noul answer, a bare
    string becomes a choice answer — handy for scripted predicates.
    """
    from .client import JevClient

    if isinstance(raw, (int, float)):
        raw = {"type": "noul", "noul": float(raw)}
    elif isinstance(raw, str):
        raw = {"type": "choice", "choice": raw,
               "probabilities": {raw: 1.0}, "confidence": 1.0}
    return JevClient._parse_answer(name, raw)
