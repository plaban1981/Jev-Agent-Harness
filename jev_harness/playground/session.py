"""Session state: spend cap, cached frontier references, shadow history."""

from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, Dict, Hashable, List, Optional, Tuple

from ..config import PLAYGROUND_SPEND_CAP_USD
from .spend import SpendGuard


class ReferenceCache:
    """Starts each (question, excerpts)-keyed reference call at most once.

    Both the up-front reference run and a cascade escalation ask for the same
    key, so an escalation reuses the in-flight/finished call: no extra spend.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._futures: Dict[Hashable, Future] = {}
        self._pool = ThreadPoolExecutor(max_workers=4)

    def get_or_start(self, key: Hashable, fn: Callable[[], Any]) -> Tuple[Future, bool]:
        with self._lock:
            fut = self._futures.get(key)
            if fut is not None:
                return fut, False
            fut = self._pool.submit(fn)
            self._futures[key] = fut
            return fut, True

    def has(self, key: Hashable) -> bool:
        with self._lock:
            return key in self._futures

    def clear(self) -> None:
        with self._lock:
            self._futures.clear()


class PlaygroundSession:
    def __init__(self, cap_usd: Optional[float] = None) -> None:
        self.spend = SpendGuard(PLAYGROUND_SPEND_CAP_USD if cap_usd is None else cap_usd)
        self.refs = ReferenceCache()
        self.shadows: List[Dict[str, Any]] = []
        self._lock = threading.Lock()

    def add_shadows(self, items: List[Dict[str, Any]]) -> None:
        with self._lock:
            self.shadows.extend(items)

    def reset(self) -> None:
        self.spend.reset()
        self.refs.clear()
        with self._lock:
            self.shadows.clear()
