"""HOOK 4 — After result: Quality gate (verified-cascade cookbook wording).

``judge()`` asks a Jev ``choice`` question with criteria
supported / unsupported / declined.  ``QualityMiddleware`` is an
``after_model`` hook: unsupported final answers with retries remaining
inject a corrective user message and loop back to the ``model`` node via
``Command(goto="model")``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command

from ..config import ACCEPT_CONFIDENCE
from ..events import HOOK4_QUALITY, EventSink, GateEvent, get_sink
from ..jev import FakeJevClient, JevClient, choice
from ..metrics import SavingsTracker

SUPPORT_CRITERIA = ["supported", "unsupported", "declined"]

SUPPORT_INSTRUCTIONS = (
    "Decide whether the answer is supported by the context. "
    "'supported': the answer addresses the question and every fact, number "
    "or policy in the answer appears in the excerpts/context. "
    "'unsupported': the answer states something that is not in the context, "
    "or answers a different question than the one asked. "
    "'declined': the answer says the context does not cover the question."
)


@dataclass
class QualityVerdict:
    verdict: str                  # "supported" | "unsupported" | "declined"
    confidence: float
    probabilities: Dict[str, float] = field(default_factory=dict)
    usage: Dict[str, Any] = field(default_factory=dict)

    @property
    def accepted(self) -> bool:
        return self.verdict == "supported" and self.confidence >= ACCEPT_CONFIDENCE


def judge(
    objective: str,
    answer: str,
    context: str,
    jev: JevClient | FakeJevClient,
    event_sink: Optional[EventSink] = None,
    tracker: Optional[SavingsTracker] = None,
) -> QualityVerdict:
    """Ask Jev whether ``answer`` is supported by ``context`` for ``objective``."""
    sink = get_sink(event_sink)
    state = {"objective": objective, "answer": answer, "context": context}
    questions = {"support": choice(SUPPORT_INSTRUCTIONS, SUPPORT_CRITERIA)}
    result = jev.ask_with_usage(state, questions)
    rec = (
        tracker.record_jev_call(HOOK4_QUALITY, questions, result.usage,
                                result.latency_ms, state_obj=state)
        if tracker is not None else None
    )
    a = result.answers["support"]
    usage = getattr(jev, "last_usage", {}) or {}
    verdict = QualityVerdict(
        verdict=a.choice_value,
        confidence=a.confidence or 0.0,
        probabilities=a.probabilities,
        usage=usage,
    )
    sink.emit(
        GateEvent(
            hook=HOOK4_QUALITY,
            stage="judge",
            decision=verdict.verdict.upper(),
            detail={
                "objective": objective[:200],
                "confidence": verdict.confidence,
                "probabilities": verdict.probabilities,
                "accepted": verdict.accepted,
                **({"usage": usage} if usage else {}),
            },
            **(rec.event_kwargs() if rec else {}),
        )
    )
    return verdict


class QualityMiddleware(AgentMiddleware):
    """Retries the model once when the final answer is unsupported."""

    def __init__(
        self,
        jev: JevClient | FakeJevClient,
        *,
        max_retries: int = 1,
        event_sink: Optional[EventSink] = None,
        tracker: Optional[SavingsTracker] = None,
    ) -> None:
        self.jev = jev
        self.max_retries = max_retries
        self.tracker = tracker
        self.sink = get_sink(event_sink)
        self.retries_used = 0

    def after_model(self, state, runtime):
        messages = state.get("messages", []) if isinstance(state, dict) else []
        last = messages[-1] if messages else None
        if not isinstance(last, AIMessage) or last.tool_calls:
            return None  # not a final answer — nothing to judge yet

        objective = ""
        context_bits: List[str] = []
        for msg in messages:
            if isinstance(msg, HumanMessage) and not objective:
                objective = str(msg.content)
            elif getattr(msg, "type", None) == "tool":
                context_bits.append(str(msg.content))
        context = "\n".join(context_bits) or "(no tool context)"

        verdict = judge(objective, str(last.content), context, self.jev, self.sink, self.tracker)
        if verdict.verdict == "unsupported" and self.retries_used < self.max_retries:
            self.retries_used += 1
            return Command(
                goto="model",
                update={
                    "messages": [
                        HumanMessage(
                            content=(
                                "Your previous answer was judged UNSUPPORTED "
                                f"(confidence {verdict.confidence:.2f}): it "
                                "stated something not in the retrieved "
                                "context or answered a different question. "
                                "Answer again using ONLY facts present in "
                                "the tool results."
                            )
                        )
                    ]
                },
            )
        return None
