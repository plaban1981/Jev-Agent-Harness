"""Attach recorded shadows to the GateEvent a hook emits right after its ask."""

from __future__ import annotations

from typing import List

from ..events import EventSink, GateEvent
from .coverage import NON_DECISION_STAGES, PAIRABLE_HOOKS, QUESTION_KEY_HOOKS
from .dual import ShadowRecorder


class ShadowPairingSink:
    def __init__(self, downstream: EventSink, recorder: ShadowRecorder) -> None:
        self.downstream = downstream
        self.recorder = recorder
        self.hooks_seen: List[str] = []

    def emit(self, event: GateEvent) -> None:
        if event.hook not in self.hooks_seen:
            self.hooks_seen.append(event.hook)
        if event.hook in PAIRABLE_HOOKS and event.stage not in NON_DECISION_STAGES:
            allowed = {k for k, hooks in QUESTION_KEY_HOOKS.items() if event.hook in hooks}
            shadows = self.recorder.take_pending(lambda r: all(k in allowed for k in r.keys))
            if shadows:
                event.detail["shadow"] = [r.to_dict() for r in shadows]
                event.detail["model_calls"] = len(shadows)
            else:
                checks = event.detail.get("checks")
                if checks is not None and "jev" not in checks:
                    event.detail["model_calls"] = 0   # static deny / pre-check: free
        self.downstream.emit(event)
