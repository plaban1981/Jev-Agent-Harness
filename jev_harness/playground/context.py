"""Everything a surface needs for one run, bundled."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from ..events import ListEventSink
from ..metrics import SavingsTracker
from .dual import ShadowRecorder
from .session import PlaygroundSession
from .spend import SpendGuard
from .thresholds import Thresholds


@dataclass
class RunContext:
    jev: Any                      # what the hooks call: DualClient (live or simulated)
    plain_jev: Any                # the underlying Jev client (no shadow judge)
    chat: Any                     # OpenRouterChat or FakeChat
    sink: Any                     # ShadowPairingSink
    events: ListEventSink         # every GateEvent of this run (post-pairing)
    tracker: SavingsTracker
    thresholds: Thresholds
    session: PlaygroundSession
    mode: str                     # "live" | "simulated"
    recorder: ShadowRecorder
    flags: Dict[str, Any] = field(default_factory=dict)
    api_key: Optional[str] = None

    @property
    def spend(self) -> SpendGuard:
        return self.session.spend
