"""Cascade surface: cheap draft -> Jev verify -> (escalate) with a real reference.

The reference is a frontier completion of the same question. It is started at
most once per (question, excerpts) via the session ReferenceCache, so an
escalation *is* the reference (no extra call) and repeat runs reuse it.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from ..config import DRAFT_MODEL, FRONTIER_MODEL
from ..hooks import judge, run_cascade
from ..openrouter import OpenRouterError
from .context import RunContext
from .lab_gates import need
from .lab_router import CompletionSide, estimate_cost

ANSWER_SYSTEM = (
    "Answer the question using ONLY the excerpts. If the excerpts do not cover it, "
    "say the excerpts do not cover it. Be concise."
)
ANSWER_MAX_TOKENS = 300


def _messages(question: str, excerpts: str):
    return [{"role": "system", "content": ANSWER_SYSTEM},
            {"role": "user", "content": f"Excerpts:\n{excerpts}\n\nQuestion: {question}"}]


def _pct(saved: float, base: float) -> float:
    return round(100.0 * saved / base, 1) if base else 0.0


def run_cascade_lab(inputs: Dict[str, Any], ctx: RunContext) -> Dict[str, Any]:
    question = need(inputs, "question")
    excerpts = need(inputs, "excerpts")
    user_draft = str(inputs.get("draft_answer", "") or "").strip()
    msgs = _messages(question, excerpts)
    spend = ctx.spend
    refs = ctx.session.refs
    key = (question, excerpts, FRONTIER_MODEL)
    ref_est = estimate_cost(FRONTIER_MODEL, msgs, ANSWER_MAX_TOKENS)
    note = ""

    def make_reference():
        res = ctx.chat.complete(FRONTIER_MODEL, msgs, max_tokens=ANSWER_MAX_TOKENS, temperature=0.0)
        spend.add(res.cost_usd)
        return res

    # 1. Start the reference up front (parallel with the draft + verify) when allowed.
    ref_future, created = None, False
    if ctx.flags.get("reference_run", True):
        if refs.has(key) or not spend.would_exceed(ref_est):
            ref_future, created = refs.get_or_start(key, make_reference)
        else:
            note = "spend cap reached: reference run skipped"

    # 2. Draft with the cheap model (measured), unless the user pasted one.
    draft_side: Optional[CompletionSide] = None
    if user_draft:
        draft_text = user_draft
        note = (note + "; " if note else "") + "draft provided by user: its cost is not comparable"
    else:
        spend.check(estimate_cost(DRAFT_MODEL, msgs, ANSWER_MAX_TOKENS))
        res = ctx.chat.complete(DRAFT_MODEL, msgs, max_tokens=ANSWER_MAX_TOKENS, temperature=0.0)
        spend.add(res.cost_usd)
        draft_side = CompletionSide.from_chat(res)
        draft_text = res.content

    # 3. The real cascade. Escalation asks the cache for the same reference.
    escalated = {"used": False}

    def frontier() -> str:
        if not refs.has(key):
            spend.check(ref_est)          # an escalation is a functional need, but still capped
        fut, _ = refs.get_or_start(key, make_reference)
        escalated["used"] = True
        return fut.result().content

    start = len(ctx.tracker.records)
    result = run_cascade(
        question, excerpts, ctx.jev, draft_answer=draft_text, frontier_answer=frontier,
        cheap_model=DRAFT_MODEL, accept_confidence=ctx.thresholds.accept_confidence,
        event_sink=ctx.sink, tracker=ctx.tracker,
    )
    verify = ctx.tracker.records[start:]
    verify_cost = sum(r.jev_cost_usd for r in verify)
    verify_latency = sum(r.jev_latency_ms for r in verify)

    # 4. Gather the reference (cached/started, or the escalation answer).
    reference: Optional[CompletionSide] = None
    cached = False
    fut = ref_future
    if fut is None and escalated["used"]:
        fut, _ = refs.get_or_start(key, make_reference)
    if fut is not None:
        try:
            reference = CompletionSide.from_chat(fut.result())
            cached = not created and not escalated["used"]
        except OpenRouterError as exc:
            note = (note + "; " if note else "") + f"reference failed: {exc}"

    reference_verdict = None
    if reference is not None and not escalated["used"]:
        reference_verdict = judge(question, reference.content, excerpts, ctx.plain_jev, None, None).verdict

    # 5. Savings = reference cost/latency - cascade cost/latency (can be negative).
    savings = None
    if (reference is not None and draft_side is not None
            and reference.cost_usd is not None and draft_side.cost_usd is not None):
        cascade_cost = draft_side.cost_usd + verify_cost + (reference.cost_usd if escalated["used"] else 0.0)
        cascade_latency = (draft_side.latency_ms + verify_latency
                           + (reference.latency_ms if escalated["used"] else 0.0))
        sources = {draft_side.cost_source, reference.cost_source}
        savings = {
            "cost_usd": reference.cost_usd - cascade_cost,
            "cost_pct": _pct(reference.cost_usd - cascade_cost, reference.cost_usd),
            "latency_ms": reference.latency_ms - cascade_latency,
            "latency_pct": _pct(reference.latency_ms - cascade_latency, reference.latency_ms),
            "cascade_cost_usd": cascade_cost,
            "cascade_latency_ms": cascade_latency,
            "provenance": "measured" if ctx.mode == "live" else "simulated",
            "cost_source": "usage" if sources == {"usage"} else (
                "simulated" if "simulated" in sources else "list_price"),
        }
    elif reference is None and not note:
        note = "reference run off: savings not measured"

    benefit = {
        "kind": "cascade_vs_reference",
        "label": "Cascade vs always-frontier (measured)",
        "value": savings["cost_usd"] if savings else None,
        "detail": savings or {"note": note},
        "provenance": savings["provenance"] if savings else "not measured",
    }
    return {
        "cascade": result.to_dict(),
        "escalated": escalated["used"],
        "draft": draft_side.to_dict() if draft_side else {"provided_by_user": True},
        "reference": reference.to_dict() if reference else None,
        "reference_cached": cached,
        "reference_verdict": reference_verdict,
        "verify_cost_usd": verify_cost,
        "savings": savings,
        "status": savings["provenance"] if savings else "not measured",
        "note": note,
        "benefits": [benefit],
    }
