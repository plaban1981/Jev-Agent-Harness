"""Intelligent threshold advisor.

Analyzes a scenario run (tracker records + emitted events) and produces
advice events (hook="advisor") using the cookbook's tuning guidance:

1. Many mid-band REVIEW outcomes on obviously-routine commands
   (static-list-clean + ``serves_task`` >= 0.8, nothing at/below BLOCK_AT)
   → suggest lowering APPROVE_AT toward 0.8.
2. Any APPROVED gate whose lowest score sat in [APPROVE_AT, APPROVE_AT+0.03]
   that was later refused/blocked downstream in the same run
   → suggest raising the threshold or extending the static risk list.
3. Always summarize the auto-resolution rate (decisions resolved without
   human/LLM involvement).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .config import APPROVE_AT, BLOCK_AT
from .events import HOOK_ADVISOR, GateEvent
from .metrics import SavingsTracker

# Margin (inclusive) above APPROVE_AT that counts as a "thin" approval.
THIN_APPROVE_MARGIN = 0.03
ROUTINE_SERVES_TASK = 0.8


def analyze(
    tracker: SavingsTracker,
    events: List[GateEvent],
) -> List[GateEvent]:
    """Return advice GateEvents for a completed scenario run."""
    advice: List[GateEvent] = []
    n_records = len(tracker.records)
    if n_records == 0:
        return advice

    # -- 1. mid-band reviews on routine commands --------------------------------
    routine_reviews = []
    for e in events:
        if e.hook != "hook3_risk_gate" or e.decision != "REVIEW":
            continue
        scores = (e.detail or {}).get("scores") or {}
        if not scores:
            continue
        serves = scores.get("serves_task")
        if serves is not None and serves >= ROUTINE_SERVES_TASK \
                and all(s > BLOCK_AT for s in scores.values()):
            routine_reviews.append(e)
    if routine_reviews:
        advice.append(GateEvent(
            hook=HOOK_ADVISOR,
            stage="threshold_advice",
            decision="LOWER_THRESHOLD",
            detail={
                "advice": (
                    f"{len(routine_reviews)} mid-band REVIEW outcome(s) had "
                    f"serves_task >= {ROUTINE_SERVES_TASK} and nothing near "
                    f"BLOCK_AT — these look routine. Consider lowering "
                    f"APPROVE_AT from {APPROVE_AT} toward 0.8 to cut human "
                    f"review load."
                ),
                "affected": len(routine_reviews),
                "suggested_approve_at": 0.8,
                "current_approve_at": APPROVE_AT,
            },
        ))

    # -- 2. thin approvals later blocked downstream ------------------------------
    blocked_later = any(
        e.decision in ("BLOCK", "BLOCKED", "HANDOFF")
        for e in events
    )
    thin_approvals = []
    for e in events:
        if e.hook != "hook3_risk_gate" or e.decision != "APPROVE":
            continue
        scores = (e.detail or {}).get("scores") or {}
        if scores:
            lowest = min(scores.values())
            if APPROVE_AT <= lowest <= APPROVE_AT + THIN_APPROVE_MARGIN:
                thin_approvals.append((e, lowest))
    if thin_approvals and blocked_later:
        advice.append(GateEvent(
            hook=HOOK_ADVISOR,
            stage="threshold_advice",
            decision="RAISE_THRESHOLD",
            detail={
                "advice": (
                    f"{len(thin_approvals)} approval(s) squeaked through with "
                    f"lowest score in [{APPROVE_AT}, {APPROVE_AT + THIN_APPROVE_MARGIN}] "
                    f"and this run also produced downstream BLOCK/HANDOFF "
                    f"outcomes. Consider raising APPROVE_AT or adding "
                    f"patterns to the static risk list."
                ),
                "affected": len(thin_approvals),
                "suggested_approve_at": round(APPROVE_AT + 0.02, 2),
                "current_approve_at": APPROVE_AT,
            },
        ))

    # -- 3. auto-resolution summary ----------------------------------------------
    summary = tracker.summary()
    auto = summary["auto_resolution"]
    advice.append(GateEvent(
        hook=HOOK_ADVISOR,
        stage="auto_resolution",
        decision="INFO",
        detail={
            "advice": (
                f"Auto-resolution rate: {auto['resolved']}/{auto['total']} "
                f"decisions ({auto['pct']}%) resolved by the decision model "
                f"with no human or generative-LLM involvement."
            ),
            **auto,
        },
    ))
    return advice


def advice_summary(advice: List[GateEvent]) -> Dict[str, Any]:
    """Compact digest of advice events for the control room."""
    return {
        "recommendations": [
            {"decision": a.decision, "advice": a.detail.get("advice", "")}
            for a in advice if a.stage == "threshold_advice"
        ],
        "auto_resolution": next(
            (a.detail for a in advice if a.stage == "auto_resolution"), None
        ),
    }
