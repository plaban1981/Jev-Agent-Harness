"""One entry point for REST, WebSocket and tests."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Callable, Dict, Optional

from ..advisor import advice_summary, analyze
from ..events import GateEvent, ListEventSink
from ..jev import JevClient
from ..metrics import SavingsTracker
from ..openrouter import OpenRouterChat
from .context import RunContext
from .dual import DualClient, ShadowRecorder
from .judge import LLMJudge
from .pairing import ShadowPairingSink
from .scoreboard import build_scoreboard
from .session import PlaygroundSession
from .simulated import FakeChat, SimJudge, make_sim_jev
from .surfaces import SURFACES
from .thresholds import Thresholds


class ConfigError(RuntimeError):
    """The requested mode cannot run with the current configuration."""


class _CallbackSink:
    """Collect every GateEvent and stream it to the UI as it is emitted."""

    def __init__(self, events: ListEventSink, emit: Callable[[Dict[str, Any]], None]) -> None:
        self.events = events
        self.emit_fn = emit

    def emit(self, event: GateEvent) -> None:
        self.events.emit(event)
        self.emit_fn({"type": "decision", "event": event.to_dict()})


def _force_simulated(obj: Any) -> Any:
    """Simulated runs must never carry a 'measured' label anywhere."""
    if isinstance(obj, dict):
        for k, v in list(obj.items()):
            if k == "provenance" and v == "measured":
                obj[k] = "simulated"
            else:
                _force_simulated(v)
    elif isinstance(obj, list):
        for item in obj:
            _force_simulated(item)
    return obj


def execute(
    surface: str,
    inputs: Dict[str, Any],
    *,
    mode: str,
    session: PlaygroundSession,
    thresholds: Optional[Dict[str, Any]] = None,
    flags: Optional[Dict[str, Any]] = None,
    emit: Optional[Callable[[Dict[str, Any]], None]] = None,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    if surface not in SURFACES:
        raise KeyError(f"unknown surface '{surface}'; choose from {sorted(SURFACES)}")
    if mode not in ("live", "simulated"):
        raise ValueError("mode must be 'live' or 'simulated'")
    t = Thresholds.from_dict(thresholds)
    flags = dict(flags or {})
    if mode == "live" and not api_key:
        raise ConfigError("live mode needs OPENROUTER_API_KEY (set it in .env) - or pick simulated mode")
    emit_fn = emit or (lambda msg: None)

    recorder = ShadowRecorder()
    events = ListEventSink()
    sink = ShadowPairingSink(_CallbackSink(events, emit_fn), recorder)
    tracker = SavingsTracker()
    if mode == "live":
        chat = OpenRouterChat(api_key)
        plain = JevClient(api_key)
        judge = LLMJudge(chat, spend=session.spend)
    else:
        chat, plain, judge = FakeChat(), make_sim_jev(), SimJudge()
    dual = DualClient(plain, judge, recorder, t, force_jev_failure=bool(flags.get("force_jev_failure")))
    ctx = RunContext(
        jev=dual, plain_jev=plain, chat=chat, sink=sink, events=events, tracker=tracker,
        thresholds=t, session=session, mode=mode, recorder=recorder, flags=flags, api_key=api_key,
    )

    emit_fn({"type": "step_start", "surface": surface, "mode": mode})
    result = SURFACES[surface](dict(inputs or {}), ctx)

    benefits = list(result.pop("benefits", []))
    free = sum(1 for e in events.events if e.detail.get("model_calls") == 0)
    if free:
        benefits.append({"kind": "free_decisions",
                         "label": "Decisions that needed no model call (static list / pre-check)",
                         "value": free, "detail": {}, "provenance": "measured"})

    shadows = [r.to_dict() for r in recorder.all]
    source = "simulated" if mode == "simulated" else "measured"
    if mode == "simulated":
        for s in shadows:
            s["jev"]["cost_source"] = "simulated"
        _force_simulated(result)
        _force_simulated(benefits)
    session.add_shadows(shadows)

    return {
        "surface": surface,
        "mode": mode,
        "source": source,
        "result": result,
        "scoreboard": build_scoreboard(shadows, benefits, source=source),
        "session_scoreboard": build_scoreboard(session.shadows, [], source=source),
        "advice": advice_summary(analyze(tracker, events.events)),
        "spend": {"cap_usd": session.spend.cap_usd, "total_usd": session.spend.total,
                  "remaining_usd": session.spend.remaining},
        "thresholds": asdict(t),
        "events": [e.to_dict() for e in events.events],
    }
