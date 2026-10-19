"""Aggregate shadow records into the comparison scoreboard and projections."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

MIN_DECISIONS = 5          # no aggregate claim is shown below this many compared decisions
DAYS_PER_MONTH = 30


def _totals(shadows: List[Dict[str, Any]], side: str) -> Dict[str, float]:
    return {
        "tokens": sum(s[side]["tokens_in"] + s[side]["tokens_out"] for s in shadows),
        "cost_usd": sum(s[side]["cost_usd"] for s in shadows),
        "latency_ms": sum(s[side]["latency_ms"] for s in shadows),
    }


def _pct_saved(jev: float, judge: float) -> float:
    return round(100.0 * (judge - jev) / judge, 1) if judge else 0.0


def build_scoreboard(
    shadows: List[Dict[str, Any]],
    benefits: Optional[List[Dict[str, Any]]] = None,
    *,
    source: str = "measured",
) -> Dict[str, Any]:
    compared = [s for s in shadows if s["jev"]["error"] is None and s["judge"]["error"] is None]
    jev_failed = [s for s in shadows if s["jev"]["error"] is not None]
    judge_failed = [s for s in shadows if s["jev"]["error"] is None and s["judge"]["error"] is not None]
    jev, judge = _totals(compared, "jev"), _totals(compared, "judge")

    questions = agreed = 0
    disagreements: List[Dict[str, Any]] = []
    for s in compared:
        for name, ok in (s.get("agree") or {}).items():
            questions += 1
            agreed += 1 if ok else 0
            if not ok:
                disagreements.append({"question": name,
                                      "jev": s["jev"]["answers"].get(name),
                                      "judge": s["judge"]["answers"].get(name)})
    n = len(compared)
    return {
        "decisions": len(shadows),
        "compared": n,
        "jev_failures": len(jev_failed),
        "judge_failures": len(judge_failed),
        "jev": jev,
        "judge": judge,
        "pct_saved": {
            "cost": _pct_saved(jev["cost_usd"], judge["cost_usd"]),
            "latency": _pct_saved(jev["latency_ms"], judge["latency_ms"]),
            "tokens": _pct_saved(jev["tokens"], judge["tokens"]),
        },
        "agreement": {"questions": questions, "agreed": agreed,
                      "rate": round(100.0 * agreed / questions, 1) if questions else None},
        "disagreements": disagreements,
        "claims_enabled": n >= MIN_DECISIONS,
        "min_decisions": MIN_DECISIONS,
        "averages": ({"jev": {k: v / n for k, v in jev.items()},
                      "judge": {k: v / n for k, v in judge.items()}} if n else None),
        "benefits": benefits or [],
        "max_state_tokens": max((s.get("state_tokens_est", 0) for s in shadows), default=0),
        "source": source,
    }


def project(
    averages: Optional[Dict[str, Dict[str, float]]],
    requests_per_day: float,
    decisions_per_request: float,
) -> Optional[Dict[str, float]]:
    """Scale measured per-decision averages to a daily/monthly workload."""
    if not averages:
        return None
    n = requests_per_day * decisions_per_request
    jev, judge = averages["jev"], averages["judge"]
    jev_cost, judge_cost = jev["cost_usd"] * n, judge["cost_usd"] * n
    jev_wait, judge_wait = jev["latency_ms"] * n / 3_600_000, judge["latency_ms"] * n / 3_600_000
    return {
        "decisions_per_day": n,
        "jev_cost_day": round(jev_cost, 6),
        "judge_cost_day": round(judge_cost, 6),
        "saved_cost_day": round(judge_cost - jev_cost, 6),
        "saved_cost_month": round((judge_cost - jev_cost) * DAYS_PER_MONTH, 6),
        "jev_wait_hours_day": round(jev_wait, 6),
        "judge_wait_hours_day": round(judge_wait, 6),
        "saved_wait_hours_day": round(judge_wait - jev_wait, 6),
    }
