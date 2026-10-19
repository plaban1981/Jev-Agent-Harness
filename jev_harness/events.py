"""Structured gate events.

Every Jev decision point in the harness emits a :class:`GateEvent`.  The
events are consumed by tests (ListEventSink), the demo scripts and streamed
over WebSocket to the UI dashboard.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol


HOOK1_SKILLS = "hook1_skills"
HOOK2_ROUTER = "hook2_router"
HOOK3_RISK_GATE = "hook3_risk_gate"
HOOK4_QUALITY = "hook4_quality"
HOOK_REFUND = "hook3_refund_gate"
HOOK_RAG = "rag"
HOOK_MULTIMODAL = "multimodal"
HOOK_CASCADE = "cascade"
HOOK_ADVISOR = "advisor"


@dataclass
class GateEvent:
    hook: str
    stage: str
    decision: str
    detail: Dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)
    # --- measured Jev-call metrics + baseline savings (optional) -------------
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    cost_usd: Optional[float] = None
    latency_ms: Optional[float] = None
    baseline_tokens_in: Optional[int] = None
    baseline_cost_usd: Optional[float] = None
    baseline_latency_ms: Optional[float] = None
    tokens_saved: Optional[int] = None
    cost_saved_usd: Optional[float] = None
    latency_saved_ms: Optional[float] = None

    _METRIC_FIELDS = (
        "tokens_in", "tokens_out", "cost_usd", "latency_ms",
        "baseline_tokens_in", "baseline_cost_usd", "baseline_latency_ms",
        "tokens_saved", "cost_saved_usd", "latency_saved_ms",
    )

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "hook": self.hook,
            "stage": self.stage,
            "decision": self.decision,
            "detail": self.detail,
            "ts": self.ts,
        }
        for f in self._METRIC_FIELDS:
            v = getattr(self, f)
            if v is not None:
                d[f] = v
        return d


class EventSink(Protocol):
    def emit(self, event: GateEvent) -> None: ...


class NullEventSink:
    def emit(self, event: GateEvent) -> None:  # noqa: D102
        return None


class ListEventSink:
    """In-memory sink used by tests and the synchronous demo runner."""

    def __init__(self) -> None:
        self.events: List[GateEvent] = []

    def emit(self, event: GateEvent) -> None:
        self.events.append(event)

    def by_hook(self, hook: str) -> List[GateEvent]:
        return [e for e in self.events if e.hook == hook]

    def hooks_fired(self) -> List[str]:
        seen: List[str] = []
        for e in self.events:
            if e.hook not in seen:
                seen.append(e.hook)
        return seen


def get_sink(event_sink: Optional[EventSink]) -> EventSink:
    return event_sink if event_sink is not None else NullEventSink()
