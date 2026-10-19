"""Savings tracking: Jev decision calls vs the original full-LLM harness.

The original harness answered every control-flow question ("is this safe?",
"which skill?", "is this good enough?") with a full generative call to a
frontier model. This module measures what each Jev Decisions call actually
cost (tokens / USD / latency) and derives the delta against that baseline.

All baseline numbers are configurable estimates — see
:class:`jev_harness.config.BaselineLLMConfig`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .config import BaselineLLMConfig


@dataclass
class TokenUsage:
    """Measured (or canned) usage of one Jev Decisions call."""

    input_tokens: int
    output_tokens: int
    cost_usd: float


@dataclass
class JevCallMeasurement:
    """Raw measurement of one Jev Decisions call."""

    hook: str
    questions: Dict[str, Dict]
    usage: TokenUsage
    latency_ms: float
    state_obj: Any = None
    auto_resolved: bool = True   # resolved without human/LLM involvement

    @property
    def question_count(self) -> int:
        return len(self.questions)


@dataclass
class SavingsRecord:
    """One Jev call plus its full-LLM baseline equivalent and the delta."""

    hook: str
    question_count: int
    # Jev side (measured)
    jev_tokens_in: int
    jev_tokens_out: int
    jev_cost_usd: float
    jev_latency_ms: float
    # Baseline side (estimated from BaselineLLMConfig)
    baseline_tokens_in: int
    baseline_tokens_out: int
    baseline_cost_usd: float
    baseline_latency_ms: float
    auto_resolved: bool = True
    # Derived deltas
    tokens_saved: int = 0
    cost_saved_usd: float = 0.0
    latency_saved_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "hook": self.hook,
            "question_count": self.question_count,
            "jev_tokens_in": self.jev_tokens_in,
            "jev_tokens_out": self.jev_tokens_out,
            "jev_cost_usd": round(self.jev_cost_usd, 8),
            "jev_latency_ms": round(self.jev_latency_ms, 2),
            "baseline_tokens_in": self.baseline_tokens_in,
            "baseline_tokens_out": self.baseline_tokens_out,
            "baseline_cost_usd": round(self.baseline_cost_usd, 8),
            "baseline_latency_ms": self.baseline_latency_ms,
            "tokens_saved": self.tokens_saved,
            "cost_saved_usd": round(self.cost_saved_usd, 8),
            "latency_saved_ms": round(self.latency_saved_ms, 2),
            "auto_resolved": self.auto_resolved,
        }

    def event_kwargs(self) -> Dict[str, Any]:
        """Optional GateEvent fields for this record."""
        return {
            "tokens_in": self.jev_tokens_in,
            "tokens_out": self.jev_tokens_out,
            "cost_usd": self.jev_cost_usd,
            "latency_ms": self.jev_latency_ms,
            "baseline_tokens_in": self.baseline_tokens_in,
            "baseline_cost_usd": self.baseline_cost_usd,
            "baseline_latency_ms": self.baseline_latency_ms,
            "tokens_saved": self.tokens_saved,
            "cost_saved_usd": self.cost_saved_usd,
            "latency_saved_ms": self.latency_saved_ms,
        }


class SavingsTracker:
    """Collects :class:`SavingsRecord`s across a scenario run."""

    def __init__(self, baseline: Optional[BaselineLLMConfig] = None) -> None:
        self.baseline = baseline or BaselineLLMConfig()
        self.records: List[SavingsRecord] = []
        self.static_blocks: int = 0  # static-list catches (free, even vs baseline)

    # -- recording -------------------------------------------------------------
    def estimate_baseline_tokens_in(self, measurement: JevCallMeasurement) -> int:
        """Estimate the original harness's input tokens for this check.

        len(serialized state+questions)/4 + scaffolding, floored at
        ``default_input_tokens`` — a real generative call also drags the
        system prompt and every tool schema into context, so tiny decision
        states never produce a tiny baseline.
        """
        if measurement.state_obj is None:
            return self.baseline.default_input_tokens
        try:
            serialized = json.dumps(
                {"state": measurement.state_obj, "questions": measurement.questions},
                default=str,
            )
        except (TypeError, ValueError):
            return self.baseline.default_input_tokens
        estimate = int(len(serialized) / self.baseline.chars_per_token) + self.baseline.scaffolding_tokens
        return max(estimate, self.baseline.default_input_tokens)

    def record_jev_call(
        self,
        hook: str,
        questions: Dict[str, Dict],
        usage: TokenUsage,
        latency_ms: float,
        *,
        state_obj: Any = None,
        auto_resolved: bool = True,
    ) -> SavingsRecord:
        """Record one measured Jev call and derive its baseline equivalent."""
        base_in = self.estimate_baseline_tokens_in(
            JevCallMeasurement(hook, questions, usage, latency_ms, state_obj)
        )
        base_out = self.baseline.output_tokens
        base_cost = (
            base_in * self.baseline.price_input_per_token
            + base_out * self.baseline.price_output_per_token
        )
        rec = SavingsRecord(
            hook=hook,
            question_count=len(questions),
            jev_tokens_in=usage.input_tokens,
            jev_tokens_out=usage.output_tokens,
            jev_cost_usd=usage.cost_usd,
            jev_latency_ms=latency_ms,
            baseline_tokens_in=base_in,
            baseline_tokens_out=base_out,
            baseline_cost_usd=base_cost,
            baseline_latency_ms=self.baseline.latency_ms,
            auto_resolved=auto_resolved,
            tokens_saved=(base_in + base_out) - (usage.input_tokens + usage.output_tokens),
            cost_saved_usd=base_cost - usage.cost_usd,
            latency_saved_ms=self.baseline.latency_ms - latency_ms,
        )
        self.records.append(rec)
        return rec

    def note_static_block(self) -> None:
        """A static deny-list catch — free here, a full LLM call in the original harness."""
        self.static_blocks += 1

    # -- summaries ---------------------------------------------------------------
    def summary(self) -> Dict[str, Any]:
        """Jev-side totals plus per-hook breakdown and auto-resolution stats."""
        total_lat = sum(r.jev_latency_ms for r in self.records)
        base_lat = (
            sum(r.baseline_latency_ms for r in self.records)
            + self.static_blocks * self.baseline.latency_ms
        )
        per_hook: Dict[str, Dict[str, Any]] = {}
        for r in self.records:
            h = per_hook.setdefault(r.hook, {
                "jev_calls": 0, "tokens_saved": 0, "cost_saved_usd": 0.0,
                "latency_saved_ms": 0.0, "jev_tokens": 0, "baseline_tokens": 0,
            })
            h["jev_calls"] += 1
            h["tokens_saved"] += r.tokens_saved
            h["cost_saved_usd"] += r.cost_saved_usd
            h["latency_saved_ms"] += r.latency_saved_ms
            h["jev_tokens"] += r.jev_tokens_in + r.jev_tokens_out
            h["baseline_tokens"] += r.baseline_tokens_in + r.baseline_tokens_out
        auto = [r for r in self.records if r.auto_resolved]
        latency_saved_ms = base_lat - total_lat
        return {
            "total_jev_calls": len(self.records),
            "static_list_blocks": self.static_blocks,
            "jev_tokens": sum(r.jev_tokens_in + r.jev_tokens_out for r in self.records),
            "jev_cost_usd": round(sum(r.jev_cost_usd for r in self.records), 8),
            "jev_latency_ms": round(total_lat, 2),
            "baseline_tokens": sum(r.baseline_tokens_in + r.baseline_tokens_out for r in self.records),
            "baseline_cost_usd": round(sum(r.baseline_cost_usd for r in self.records), 8),
            "baseline_latency_ms": round(base_lat, 2),
            "tokens_saved": sum(r.tokens_saved for r in self.records),
            "cost_saved_usd": round(sum(r.cost_saved_usd for r in self.records), 8),
            "latency_saved_ms": round(latency_saved_ms, 2),
            "latency_saved_pct": round(100 * latency_saved_ms / base_lat, 1) if base_lat else 0.0,
            "auto_resolution": {
                "resolved": len(auto),
                "total": len(self.records),
                "pct": round(100 * len(auto) / len(self.records), 1) if self.records else 0.0,
            },
            "per_hook": per_hook,
        }

    def baseline_comparison(self) -> Dict[str, Any]:
        """Side-by-side totals for the ORIGINAL harness vs the Jev-gated one.

        The original harness ran one full baseline LLM call per gate check —
        including checks our static deny list now catches for free.
        """
        s = self.summary()
        baseline_calls = s["total_jev_calls"] + self.static_blocks
        baseline_cost = s["baseline_cost_usd"] + sum(
            (self.baseline.default_input_tokens * self.baseline.price_input_per_token
             + self.baseline.output_tokens * self.baseline.price_output_per_token)
            for _ in range(self.static_blocks)
        )
        baseline_tokens = s["baseline_tokens"] + self.static_blocks * (
            self.baseline.default_input_tokens + self.baseline.output_tokens
        )
        baseline_latency = s["baseline_latency_ms"]
        jev_cost = s["jev_cost_usd"]
        jev_tokens = s["jev_tokens"]
        jev_latency = s["jev_latency_ms"]
        return {
            "original": {
                "gate_checks": baseline_calls,
                "model": self.baseline.model,
                "tokens": baseline_tokens,
                "cost_usd": round(baseline_cost, 8),
                "latency_ms": round(baseline_latency, 2),
            },
            "jev_gated": {
                "gate_checks": baseline_calls,  # same decisions, cheaper mechanism
                "tokens": jev_tokens,
                "cost_usd": round(jev_cost, 8),
                "latency_ms": round(jev_latency, 2),
            },
            "tokens_saved": baseline_tokens - jev_tokens,
            "cost_saved_usd": round(baseline_cost - jev_cost, 8),
            "latency_saved_ms": round(baseline_latency - jev_latency, 2),
            "cost_savings_pct": round(100 * (baseline_cost - jev_cost) / baseline_cost, 1) if baseline_cost else 0.0,
        }
