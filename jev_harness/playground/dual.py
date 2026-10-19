"""DualClient: ask real Jev and the LLM judge the same question, in parallel.

The hook only ever sees Jev's result, so harness behaviour is unchanged. The
judge's answer is recorded as a ShadowRecord for the comparison UI.
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional

from ..jev.client import JevAnswer, JevCallResult, JevError
from ..pricing import estimate_tokens
from .agreement import answers_agree
from .thresholds import Thresholds


def answer_to_dict(a: JevAnswer) -> Dict[str, Any]:
    return {"type": a.type, "value": a.value,
            "probabilities": a.probabilities, "confidence": a.confidence}


@dataclass
class SideResult:
    answers: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    cost_source: str = "usage"
    model: str = ""
    error: Optional[str] = None

    @classmethod
    def from_result(cls, res: JevCallResult, model: str = "", cost_source: str = "usage") -> "SideResult":
        return cls(
            answers={n: answer_to_dict(a) for n, a in res.answers.items()},
            tokens_in=res.usage.input_tokens,
            tokens_out=res.usage.output_tokens,
            cost_usd=res.usage.cost_usd,
            latency_ms=res.latency_ms,
            cost_source=getattr(res, "cost_source", cost_source),
            model=getattr(res, "model", "") or model,
        )

    @classmethod
    def failed(cls, message: str) -> "SideResult":
        return cls(error=message)


def _redact(state: Any) -> Any:
    if isinstance(state, dict) and str(state.get("image", "")).startswith("data:image"):
        state = dict(state)
        state["image"] = "<image attached>"
    return state


@dataclass
class ShadowRecord:
    keys: List[str]
    state: Any
    questions: Dict[str, Dict]
    jev: SideResult
    judge: SideResult
    agree: Optional[Dict[str, bool]] = None

    def to_dict(self) -> Dict[str, Any]:
        full_state = json.dumps(_redact(self.state), default=str)
        qs = {}
        for name, q in self.questions.items():
            item = {"type": q.get("type"), "instructions": str(q.get("instructions", ""))[:300]}
            for extra in ("criteria", "min", "max"):
                if extra in q:
                    item[extra] = q[extra]
            qs[name] = item
        return {
            "keys": list(self.keys),
            "state_preview": full_state[:600],
            "state_tokens_est": estimate_tokens(full_state),
            "questions": qs,
            "jev": asdict(self.jev),
            "judge": asdict(self.judge),
            "agree": self.agree,
        }


class ShadowRecorder:
    """Thread-safe store of every shadow, plus a queue of not-yet-paired ones."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.all: List[ShadowRecord] = []
        self._pending: List[ShadowRecord] = []

    def add(self, rec: ShadowRecord) -> None:
        with self._lock:
            self.all.append(rec)
            self._pending.append(rec)

    def take_pending(self, predicate: Callable[[ShadowRecord], bool]) -> List[ShadowRecord]:
        with self._lock:
            taken = [r for r in self._pending if predicate(r)]
            self._pending = [r for r in self._pending if not predicate(r)]
        return taken


def _settle(fut) -> tuple:
    if fut is None:
        return None, "skipped: judge disabled"
    try:
        return fut.result(), None
    except Exception as exc:  # noqa: BLE001 - every failure is data here
        return None, str(exc) or exc.__class__.__name__


class DualClient:
    def __init__(
        self,
        jev: Any,
        judge: Any,
        recorder: ShadowRecorder,
        thresholds: Thresholds,
        *,
        force_jev_failure: bool = False,
        judge_enabled: bool = True,
    ) -> None:
        self.jev = jev
        self.judge = judge
        self.recorder = recorder
        self.thresholds = thresholds
        self.force_jev_failure = force_jev_failure
        self.judge_enabled = judge_enabled
        self.last_usage: Dict[str, Any] = {}

    def ask_with_usage(self, state: Any, questions: Mapping[str, Dict]) -> JevCallResult:
        qs = dict(questions)
        keys = list(qs)
        if self.force_jev_failure:
            msg = "forced Jev failure (demo)"
            self.recorder.add(ShadowRecord(keys, state, qs, SideResult.failed(msg),
                                           SideResult.failed("skipped: forced failure")))
            raise JevError(msg)

        with ThreadPoolExecutor(max_workers=2) as pool:
            jev_f = pool.submit(self.jev.ask_with_usage, state, qs)
            judge_f = pool.submit(self.judge.ask_with_usage, state, qs) if self.judge_enabled else None
            jev_res, jev_err = _settle(jev_f)
            judge_res, judge_err = _settle(judge_f)

        jev_side = (SideResult.failed(jev_err) if jev_err
                    else SideResult.from_result(jev_res, model=getattr(self.jev, "model", "")))
        judge_side = (SideResult.failed(judge_err) if judge_err
                      else SideResult.from_result(judge_res))
        agree = None
        if jev_res is not None and judge_res is not None:
            agree = answers_agree(jev_res.answers, judge_res.answers, self.thresholds)
        self.recorder.add(ShadowRecord(keys, state, qs, jev_side, judge_side, agree))

        if jev_err:
            raise JevError(jev_err)
        self.last_usage = dict(jev_res.usage.__dict__)
        return jev_res

    def ask(self, state: Any, questions: Mapping[str, Dict]) -> Dict[str, JevAnswer]:
        return self.ask_with_usage(state, questions).answers
