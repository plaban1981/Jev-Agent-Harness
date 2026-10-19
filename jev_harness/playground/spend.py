"""Per-session spend cap shared by every paid call the Playground makes."""

from __future__ import annotations

import threading
from typing import Optional


class SpendCapExceeded(RuntimeError):
    pass


class SpendGuard:
    def __init__(self, cap_usd: float) -> None:
        self.cap_usd = cap_usd
        self._total = 0.0
        self._lock = threading.Lock()

    @property
    def total(self) -> float:
        return self._total

    @property
    def remaining(self) -> float:
        return max(0.0, self.cap_usd - self._total)

    def would_exceed(self, estimate_usd: float) -> bool:
        return self._total + estimate_usd > self.cap_usd

    def check(self, estimate_usd: float) -> None:
        if self.would_exceed(estimate_usd):
            raise SpendCapExceeded(
                f"spend cap ${self.cap_usd:.2f} would be exceeded "
                f"(spent ${self._total:.4f}, next call ~${estimate_usd:.4f})"
            )

    def add(self, cost_usd: Optional[float]) -> None:
        if cost_usd:
            with self._lock:
                self._total += cost_usd

    def reset(self) -> None:
        with self._lock:
            self._total = 0.0
