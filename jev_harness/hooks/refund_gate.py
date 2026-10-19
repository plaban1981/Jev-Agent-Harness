"""Refund gate variant of Hook 3 — "gate tool calls with Jev" cookbook.

Deterministic pre-checks first (block without any Jev call), then three Jev
noul questions: ``customer_asked``, ``right_order``, ``policy_covers``.

Cookbook fixtures (reproduced in tests and the demo):
  * kettle, ORD-1234, late delivery, 1200c  -> APPROVE
  * espresso, ORD-7781, goodwill, 40000c    -> BLOCK (Jev)
  * crushed box, ORD-5520, 8900c            -> REVIEW (ambiguous band)
  * 8000c refund on a 6800c order           -> BLOCKED BY ARITHMETIC (no Jev)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

from ..events import HOOK_REFUND, EventSink, GateEvent, get_sink
from ..jev import FakeJevClient, JevClient, JevError, noul
from ..metrics import SavingsTracker

REFUND_POLICY = (
    "Refund policy: items may be refunded when (a) the customer actually "
    "asked for a refund in this conversation, (b) the refund is for the "
    "order on the ticket, and (c) the reason is covered by policy — late "
    "delivery, damaged/defective goods, or duplicate charges. Goodwill "
    "refunds beyond the order value require human approval."
)

CUSTOMER_ASKED = "The customer explicitly asked for a refund in this conversation."
RIGHT_ORDER = "The refund is being issued for the order on the support ticket."
POLICY_COVERS = (
    "The refund reason is covered by the refund policy (late delivery, "
    "damaged/defective goods, duplicate charge)."
)


@dataclass
class RefundVerdict:
    outcome: str                     # "approve" | "block" | "review"
    reason: str
    checks: List[str] = field(default_factory=list)
    jev_scores: Dict[str, float] = field(default_factory=dict)
    jev_error: Optional[str] = None

    @property
    def approved(self) -> bool:
        return self.outcome == "approve"


# --- In-memory order ledger (shared by tools, middleware and demo) -----------
ORDER_LEDGER: Dict[str, Dict[str, Any]] = {
    "ORD-1234": {"item": "kettle", "balance_cents": 3500, "status": "delivered_late"},
    "ORD-7781": {"item": "espresso machine", "balance_cents": 45000, "status": "delivered_ok"},
    "ORD-5520": {"item": "blender", "balance_cents": 12000, "status": "delivered_damaged"},
    "ORD-6800": {"item": "toaster", "balance_cents": 6800, "status": "delivered_ok"},
}


def evaluate_refund(
    *,
    order_id: str,
    amount_cents: int,
    reason: str,
    customer_asked_text: str,
    jev: JevClient | FakeJevClient,
    approve_at: float = 0.9,
    block_at: float = 0.1,
    event_sink: Optional[EventSink] = None,
    tracker: Optional[SavingsTracker] = None,
    ledger: Optional[Dict[str, Dict[str, Any]]] = None,
) -> RefundVerdict:
    """Deterministic pre-checks, then 3 Jev nouls. Emits a GateEvent."""
    sink = get_sink(event_sink)
    ledger = ledger if ledger is not None else ORDER_LEDGER
    order = ledger.get(order_id)
    checks: List[str] = []

    # Pre-check 1: order exists on the ticket.
    checks.append("order_on_ticket")
    if order is None:
        return _finish(sink, RefundVerdict(
            outcome="block",
            reason=f"no order '{order_id}' on ticket — blocked without Jev",
            checks=checks,
        ), order_id, amount_cents, reason)

    # Pre-check 2: arithmetic — refund must not exceed remaining balance.
    checks.append("amount_within_balance")
    if amount_cents > order["balance_cents"]:
        return _finish(sink, RefundVerdict(
            outcome="block",
            reason=(
                f"arithmetic: {amount_cents}c exceeds remaining balance "
                f"{order['balance_cents']}c — blocked without Jev"
            ),
            checks=checks,
        ), order_id, amount_cents, reason)

    # Jev questions.
    checks.append("jev")
    state = {
        "ticket": {
            "order_id": order_id,
            "item": order["item"],
            "reason": reason,
            "customer_asked": customer_asked_text,
        },
        "policy": REFUND_POLICY,
    }
    questions = {
        "customer_asked": noul(CUSTOMER_ASKED),
        "right_order": noul(RIGHT_ORDER),
        "policy_covers": noul(POLICY_COVERS),
    }
    try:
        result = jev.ask_with_usage(state, questions)
    except JevError as exc:
        return _finish(sink, RefundVerdict(
            outcome="review",
            reason=f"Jev unavailable — human review required (fail-closed): {exc}",
            checks=checks,
            jev_error=str(exc),
        ), order_id, amount_cents, reason)

    rec = (
        tracker.record_jev_call(HOOK_REFUND, questions, result.usage,
                                result.latency_ms, state_obj=state)
        if tracker is not None else None
    )
    answers = result.answers
    scores = {name: ans.noul for name, ans in answers.items()}
    if all(s >= approve_at for s in scores.values()):
        outcome, why = "approve", f"all scores >= {approve_at}"
    elif any(s <= block_at for s in scores.values()):
        outcome, why = "block", f"a score <= {block_at}"
    else:
        outcome, why = "review", "scores in the ambiguous band"
    return _finish(sink, RefundVerdict(
        outcome=outcome,
        reason=f"{why}: {scores}",
        checks=checks,
        jev_scores=scores,
    ), order_id, amount_cents, reason, rec)


def _finish(sink, verdict: RefundVerdict, order_id, amount_cents, reason, rec=None) -> RefundVerdict:
    detail: Dict[str, Any] = {
        "order_id": order_id,
        "amount_cents": amount_cents,
        "reason": reason,
        "checks": verdict.checks,
        "reason_text": verdict.reason,
    }
    if verdict.jev_scores:
        detail["scores"] = verdict.jev_scores
    if verdict.jev_error:
        detail["jev_error"] = verdict.jev_error
    kwargs = rec.event_kwargs() if rec is not None else {}
    sink.emit(GateEvent(hook=HOOK_REFUND, stage="refund_gate",
                        decision=verdict.outcome.upper(), detail=detail, **kwargs))
    return verdict


class RefundGateMiddleware(AgentMiddleware):
    """Gates the ``issue_refund`` tool with :func:`evaluate_refund`."""

    def __init__(
        self,
        jev: JevClient | FakeJevClient,
        *,
        approve_at: float = 0.9,
        block_at: float = 0.1,
        ledger: Optional[Dict[str, Dict[str, Any]]] = None,
        event_sink: Optional[EventSink] = None,
        tracker: Optional[SavingsTracker] = None,
    ) -> None:
        self.jev = jev
        self.approve_at = approve_at
        self.block_at = block_at
        self.ledger = ledger if ledger is not None else ORDER_LEDGER
        self.tracker = tracker
        self.sink = get_sink(event_sink)
        self.log: List[RefundVerdict] = []

    def wrap_tool_call(self, request, handler):
        tool_call = request.tool_call
        if tool_call.get("name") != "issue_refund":
            return handler(request)
        args = tool_call.get("args", {}) or {}
        verdict = evaluate_refund(
            order_id=str(args.get("order_id", "")),
            amount_cents=int(args.get("amount_cents", 0)),
            reason=str(args.get("reason", "")),
            customer_asked_text=str(args.get("customer_asked", "")),
            jev=self.jev,
            approve_at=self.approve_at,
            block_at=self.block_at,
            event_sink=self.sink,
            tracker=self.tracker,
            ledger=self.ledger,
        )
        self.log.append(verdict)
        if verdict.approved:
            order = self.ledger[args["order_id"]]
            order["balance_cents"] -= int(args["amount_cents"])
            return handler(request)
        return ToolMessage(
            content=(
                f"⛔ Refund NOT issued — outcome {verdict.outcome.upper()}. "
                f"Reason: {verdict.reason}"
            ),
            tool_call_id=tool_call.get("id", ""),
        )
