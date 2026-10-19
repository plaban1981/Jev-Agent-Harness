"""Offline stand-ins: heuristic Jev, a judge that echoes it, and (Task 8) FakeChat.

Everything produced here is labelled ``simulated`` in the UI and never used
to make a savings claim.
"""

from __future__ import annotations

import re
import threading
from typing import Any, Dict, List, Mapping, Optional

from ..config import FRONTIER_MODEL
from ..jev import FakeJevClient
from ..metrics import TokenUsage
from ..openrouter import ChatResult
from ..pricing import estimate_tokens, list_cost
from .judge import JudgeCallResult

_KEYWORDS = {
    "security": ("phish", "password", "alert", "breach", "ceo", "gift card", "malware"),
    "coding": ("test", "refactor", "bug", "code", "function", "module", "write"),
    "devops": ("deploy", "incident", "outage", "release", "rollback", "pager"),
    "billing_support": ("refund", "invoice", "plan", "billing", "charge", "order"),
}


def _choice(options: List[str], picked: str, p: float = 0.9) -> Dict[str, Any]:
    rest = [o for o in options if o != picked]
    probs = {picked: p}
    for o in rest:
        probs[o] = round((1 - p) / max(1, len(rest)), 4)
    return {"type": "choice", "choice": picked, "probabilities": probs, "confidence": p}


def _category(state, q):
    text = str(state.get("request", "")).lower()
    options = q["criteria"]
    for cat, words in _KEYWORDS.items():
        if cat in options and any(w in text for w in words):
            return _choice(options, cat)
    return _choice(options, options[0], 0.6)


def _skill(state, q):
    text = str(state.get("request", "")).lower()
    options = q["criteria"]
    for opt in options:
        if any(tok in text for tok in opt.split("_") if len(tok) > 3):
            return _choice(options, opt)
    return _choice(options, options[0], 0.6)


def _reversible(state, q):
    cmd = str(state.get("commands", "")).lower()
    return 0.12 if any(w in cmd for w in ("delete", "drop ", "push", "deploy", "rm ")) else 0.95


def _serves_task(state, q):
    return 0.4 if "cleanup" in str(state.get("commands", "")).lower() else 0.93


def _policy_covers(state, q):
    reason = str(state.get("ticket", {}).get("reason", "")).lower()
    if any(w in reason for w in ("late", "damaged", "defective", "duplicate")):
        return 0.95
    return 0.03 if "goodwill" in reason else 0.55


def _words(text: str) -> List[str]:
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 3]


def _support(state, q):
    answer = str(state.get("answer", ""))
    low = answer.lower()
    if "not cover" in low or "not covered" in low or "cannot" in low:
        return _choice(q["criteria"], "declined", 0.95)
    words = _words(answer)
    context = set(_words(str(state.get("context", ""))))
    overlap = (sum(1 for w in words if w in context) / len(words)) if words else 0.0
    if overlap >= 0.25:
        return _choice(q["criteria"], "supported", 0.95)
    return _choice(q["criteria"], "unsupported", 0.85)


def _relevance(state, q):
    qt = set(_words(str(state.get("query", ""))))
    ct = set(_words(str(state.get("chunk", "")) + " " + str(state.get("title", ""))))
    frac = (len(qt & ct) / len(qt)) if qt else 0.0
    return {"type": "score", "score": round(min(5.0, 1.0 + 6.0 * frac), 1)}


def make_sim_jev() -> FakeJevClient:
    jev = FakeJevClient()
    jev.set_many({
        "category": _category, "skill": _skill,
        "reversible": _reversible, "serves_task": _serves_task,
        "customer_asked": 0.96, "right_order": 0.96, "policy_covers": _policy_covers,
        "support": _support, "relevance": _relevance,
        "shows_item": 0.94,
        "color": _choice(["red", "green", "blue"], "red", 0.91),
    })
    return jev


class SimJudge:
    """Echoes a (separate) simulated Jev with frontier-LLM-shaped canned usage."""

    def __init__(self, inner: Optional[FakeJevClient] = None) -> None:
        self.inner = inner or make_sim_jev()
        self.calls = 0

    def ask_with_usage(self, state: Any, questions: Mapping[str, Dict]) -> JudgeCallResult:
        self.calls += 1
        res = self.inner.ask_with_usage(state, questions)
        tin, tout = res.usage.input_tokens + 250, 80
        return JudgeCallResult(
            answers=res.answers,
            usage=TokenUsage(tin, tout, list_cost(FRONTIER_MODEL, tin, tout) or 0.0),
            latency_ms=2000.0,
            model=FRONTIER_MODEL,
            cost_source="simulated",
        )


SIM_LATENCY_MS = {
    "anthropic/claude-sonnet-5.5": 2400.0,
    "anthropic/claude-haiku-5.5": 700.0,
    "google/gemini-2.5-flash": 650.0,
    "typesafe/jev-router": 900.0,
}


class FakeChat:
    """Offline stand-in for OpenRouterChat: deterministic, priced from the catalog."""

    def __init__(self, answers: Optional[Dict[str, str]] = None,
                 router_pick: str = "google/gemini-2.5-flash") -> None:
        self.answers = dict(answers or {})
        self.router_pick = router_pick
        self.calls: List[str] = []
        self._lock = threading.Lock()

    def complete(self, model, messages, *, max_tokens=512, temperature=0.0,
                 extra_body=None, want_metadata=False) -> ChatResult:
        with self._lock:
            self.calls.append(model)
        is_router = model == "typesafe/jev-router"
        served = self.router_pick if is_router else model
        text = self.answers.get(model) or f"[simulated answer from {served}]"
        tin = sum(estimate_tokens(str(m.get("content", ""))) for m in messages)
        tout = min(max_tokens, 120)
        cost = list_cost(served, tin, tout)
        meta = {}
        if is_router:
            meta = {"pipeline": [{"name": "jev-router", "data": {
                "reason": "simulated: request looks easy",
                "resolved_models": [served], "pipeline": "default",
                "candidates": [{"model": served, "effort": "low", "tier": "low",
                                "estimated_cost": cost}]}}]}
        return ChatResult(
            content=text, served_model=served, tokens_in=tin, tokens_out=tout,
            cost_usd=cost, cost_source="simulated",
            latency_ms=SIM_LATENCY_MS.get(served, 1000.0), metadata=meta, raw_usage={},
        )
