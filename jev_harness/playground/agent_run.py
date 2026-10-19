"""Agent Run surface: the real build_jev_agent harness on the user's request.

Every middleware hook fires naturally. Live mode wires the real model and
router; simulated mode scripts the model like the existing agent_loop demo.
Tools stay simulated either way (see agent.make_tools).
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional

from langchain_core.callbacks import BaseCallbackHandler

from ..agent import build_jev_agent
from ..config import FRONTIER_MODEL, ROUTER_POOL, Settings
from ..hooks import ORDER_LEDGER, JevRouter
from ..hooks.refund_gate import RefundGateMiddleware
from ..pricing import list_cost
from .context import RunContext
from .lab_gates import need
from .spend import SpendGuard

SIM_TESTS_SCRIPT = [
    {"tool_calls": [{"name": "run_shell_command", "args": {"command": "bun test"}}]},
    {"tool_calls": [{"name": "run_shell_command", "args": {"command": "rm -rf /data"}}]},
    {"tool_calls": [{"name": "write_file", "args": {"path": "src/app.ts", "content": "export const x = 1;"}}]},
    {"content": "All done: tests pass (bun test), src/app.ts written; the destructive "
                "`rm -rf /data` was refused by the risk gate."},
]
SIM_REFUND_SCRIPT = [
    {"tool_calls": [{"name": "issue_refund", "args": {
        "order_id": "ORD-1234", "amount_cents": 1200, "reason": "late delivery",
        "customer_asked": "customer asked for a refund for late delivery"}}]},
    {"content": "Refund of 1200 cents for ORD-1234 (late delivery) was issued after the refund gate approved it."},
]


class MainModelMeter(BaseCallbackHandler):
    """Measures every main-model call (latency, tokens, cost) and enforces the cap."""

    raise_error = True      # let SpendCapExceeded abort the run

    def __init__(self, spend: SpendGuard, *, default_model: str = FRONTIER_MODEL) -> None:
        self.spend = spend
        self.default_model = default_model
        self.calls: List[Dict[str, Any]] = []
        self._starts: Dict[Any, float] = {}
        self._lock = threading.Lock()

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs) -> None:
        self.spend.check(0.0)                       # stop once the cap has been passed
        self._starts[run_id] = time.perf_counter()

    def on_llm_end(self, response, *, run_id, **kwargs) -> None:
        started = self._starts.pop(run_id, None)
        latency = (time.perf_counter() - started) * 1000.0 if started else 0.0
        out = response.llm_output or {}
        usage = out.get("token_usage") or {}
        model = out.get("model_name") or self.default_model
        tin = tout = 0
        try:
            um = getattr(response.generations[0][0].message, "usage_metadata", None) or {}
            tin, tout = int(um.get("input_tokens", 0)), int(um.get("output_tokens", 0))
        except (IndexError, AttributeError):
            pass
        tin = tin or int(usage.get("prompt_tokens", 0))
        tout = tout or int(usage.get("completion_tokens", 0))
        if usage.get("cost") is not None:
            cost, source = float(usage["cost"]), "usage"
        else:
            cost = list_cost(model, tin, tout)
            source = "list_price" if cost is not None else "unpriced"
        with self._lock:
            self.calls.append({"model": model, "tokens_in": tin, "tokens_out": tout,
                               "cost_usd": cost, "cost_source": source, "latency_ms": latency})
        self.spend.add(cost)

    def summary(self) -> Dict[str, Any]:
        return {
            "calls": len(self.calls),
            "tokens_in": sum(c["tokens_in"] for c in self.calls),
            "tokens_out": sum(c["tokens_out"] for c in self.calls),
            "cost_usd": sum(c["cost_usd"] or 0.0 for c in self.calls),
            "latency_ms": sum(c["latency_ms"] for c in self.calls),
            "models": sorted({c["model"] for c in self.calls}),
            "detail": list(self.calls),
        }


def _pct(part: float, total: float) -> float:
    return round(100.0 * part / total, 1) if total else 0.0


def run_agent(inputs: Dict[str, Any], ctx: RunContext) -> Dict[str, Any]:
    request = need(inputs, "request")
    t = ctx.thresholds
    settings = Settings(offline=ctx.mode != "live", openrouter_api_key=ctx.api_key,
                        approve_at=t.approve_at, block_at=t.block_at,
                        accept_confidence=t.accept_confidence)
    snapshot = {k: dict(v) for k, v in ORDER_LEDGER.items()}
    meter = MainModelMeter(ctx.spend)
    try:
        if ctx.mode == "live":
            models = ROUTER_POOL if inputs.get("use_pool") else None
            router = JevRouter(ctx.api_key, cost_tier=str(inputs.get("cost_tier") or "medium"),
                               models=models, want_metadata=True, chat=ctx.chat)
            built = build_jev_agent(mode="live", event_sink=ctx.sink, settings=settings,
                                    jev=ctx.jev, tracker=ctx.tracker, router=router)
        else:
            script = SIM_REFUND_SCRIPT if "refund" in request.lower() else SIM_TESTS_SCRIPT
            built = build_jev_agent(mode="offline", event_sink=ctx.sink, settings=settings,
                                    jev=ctx.jev, tracker=ctx.tracker, model_script=script)
        for mw in built["middleware"]:
            if isinstance(mw, RefundGateMiddleware):
                mw.approve_at, mw.block_at = t.approve_at, t.block_at
        final = built["agent"].invoke({"messages": [("user", request)]},
                                      config={"callbacks": [meter]})
    finally:
        ORDER_LEDGER.clear()
        ORDER_LEDGER.update(snapshot)               # simulated tools must not leak state

    messages = final["messages"]
    tool_results = [str(m.content)[:300] for m in messages if getattr(m, "type", None) == "tool"]
    gate = ctx.tracker.summary()
    router_events = [e for e in ctx.events.events if e.hook == "hook2_router"]
    router_cost = sum(e.detail.get("cost_usd") or 0.0 for e in router_events)
    router_latency = sum(e.detail.get("latency_ms") or 0.0 for e in router_events)
    main = meter.summary()
    decision_cost = gate["jev_cost_usd"] + router_cost
    decision_latency = gate["jev_latency_ms"] + router_latency
    total_cost = decision_cost + main["cost_usd"]
    total_latency = decision_latency + main["latency_ms"]
    share = {
        "decision_cost_usd": decision_cost,
        "decision_latency_ms": decision_latency,
        "main_cost_usd": main["cost_usd"],
        "main_latency_ms": main["latency_ms"],
        "cost_pct": _pct(decision_cost, total_cost),
        "latency_pct": _pct(decision_latency, total_latency),
    }
    return {
        "final_answer": str(messages[-1].content),
        "tool_results": tool_results,
        "hooks_fired": list(ctx.sink.hooks_seen),
        "main_model": main,
        "gate_share": share,
        "benefits": [{
            "kind": "gate_share",
            "label": "Decision overhead as a share of the whole run",
            "value": share["cost_pct"],
            "detail": share,
            "provenance": "measured" if ctx.mode == "live" else "simulated",
        }],
    }
