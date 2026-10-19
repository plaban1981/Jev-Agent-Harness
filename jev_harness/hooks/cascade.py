"""Standalone Verified Cascade demo (verified-cascade cookbook).

draftAnswer (cheap model) -> verifyAnswer (Jev choice) ->
  supported & confidence >= 0.8  -> SEND draft
  declined                       -> HANDOFF
  otherwise                      -> escalate to frontier model, re-verify;
                                    double failure -> HANDOFF.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Union

from ..config import ACCEPT_CONFIDENCE, FRONTIER_MODEL
from ..events import HOOK_CASCADE, EventSink, GateEvent, get_sink
from ..jev import FakeJevClient, JevClient
from ..metrics import SavingsTracker
from .quality import QualityVerdict, judge


@dataclass
class CascadeResult:
    route: str                     # "send" | "handoff"
    model: str
    answer: str
    verdicts: List[QualityVerdict] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "route": self.route,
            "model": self.model,
            "answer": self.answer,
            "verdicts": [
                {"verdict": v.verdict, "confidence": v.confidence,
                 "probabilities": v.probabilities}
                for v in self.verdicts
            ],
        }


def run_cascade(
    question: str,
    excerpts: str,
    jev: JevClient | FakeJevClient,
    *,
    draft_answer: str,
    frontier_answer: Union[str, Callable[[], str]],
    cheap_model: str = "draft-cheap-model",
    frontier_model: str = FRONTIER_MODEL,
    accept_confidence: float = ACCEPT_CONFIDENCE,
    event_sink: Optional[EventSink] = None,
    tracker: Optional[SavingsTracker] = None,
) -> CascadeResult:
    sink = get_sink(event_sink)
    verdicts: List[QualityVerdict] = []

    # 1. Draft with the cheap model.
    draft = draft_answer
    v1 = judge(question, draft, excerpts, jev, sink, tracker)
    verdicts.append(v1)
    sink.emit(GateEvent(
        hook=HOOK_CASCADE, stage="draft_verify", decision=v1.verdict.upper(),
        detail={"model": cheap_model, "confidence": v1.confidence},
    ))
    if v1.verdict == "supported" and v1.confidence >= accept_confidence:
        return CascadeResult("send", cheap_model, draft, verdicts)
    if v1.verdict == "declined":
        return CascadeResult("handoff", cheap_model, draft, verdicts)

    # 2. Escalate to the frontier model.
    escalated = frontier_answer() if callable(frontier_answer) else frontier_answer
    v2 = judge(question, escalated, excerpts, jev, sink, tracker)
    verdicts.append(v2)
    sink.emit(GateEvent(
        hook=HOOK_CASCADE, stage="frontier_verify", decision=v2.verdict.upper(),
        detail={"model": frontier_model, "confidence": v2.confidence},
    ))
    if v2.verdict == "supported" and v2.confidence >= accept_confidence:
        return CascadeResult("send", frontier_model, escalated, verdicts)
    return CascadeResult("handoff", frontier_model, escalated, verdicts)
