"""Router surface: measure the path not taken by really running it.

Direct pattern  : A = messages -> typesafe/jev-router (the selected model serves it).
Probe pattern   : A = tiny probe -> router, then the real completion on the resolved
                  model (what RouterMiddleware does); probe cost is reported as overhead.
Reference (B)   : the same messages to the pinned frontier model, run concurrently.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from ..config import FRONTIER_MODEL, ROUTER_POOL
from ..events import HOOK2_ROUTER, GateEvent
from ..hooks.router import VALID_TIERS, JevRouter
from ..openrouter import ChatResult, OpenRouterError
from ..pricing import estimate_tokens, list_cost
from .context import RunContext
from .lab_gates import need
from .spend import SpendGuard


@dataclass
class CompletionSide:
    model: str
    content: str
    tokens_in: int
    tokens_out: int
    cost_usd: Optional[float]
    cost_source: str
    latency_ms: float
    meta: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_chat(cls, res: ChatResult, meta: Optional[Dict[str, Any]] = None) -> "CompletionSide":
        return cls(res.served_model, res.content, res.tokens_in, res.tokens_out,
                   res.cost_usd, res.cost_source, res.latency_ms, meta or {})

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _combine(first: CompletionSide, second: CompletionSide) -> CompletionSide:
    """probe + completion: sequential, so costs and latencies add."""
    both_priced = first.cost_usd is not None and second.cost_usd is not None
    sources = {first.cost_source, second.cost_source}
    source = "usage" if sources == {"usage"} else (
        "simulated" if "simulated" in sources else "list_price")
    return CompletionSide(
        model=second.model, content=second.content,
        tokens_in=first.tokens_in + second.tokens_in,
        tokens_out=first.tokens_out + second.tokens_out,
        cost_usd=(first.cost_usd + second.cost_usd) if both_priced else None,
        cost_source=source if both_priced else "unpriced",
        latency_ms=first.latency_ms + second.latency_ms,
        meta=first.meta,
    )


def estimate_cost(model: str, messages: List[Dict[str, Any]], max_tokens: int) -> float:
    tin = sum(estimate_tokens(str(m.get("content", ""))) for m in messages)
    return list_cost(model, tin, max_tokens) or 0.0


def probe_messages(text: str) -> List[Dict[str, str]]:
    return [
        {"role": "system", "content": "You are a routing probe. Reply with ok."},
        {"role": "user", "content": f"Request summary: {text[:200]}\n\nReply with ok"},
    ]


def _pct(saved: float, base: float) -> float:
    return round(100.0 * saved / base, 1) if base else 0.0


def run_router_pair(
    chat: Any,
    messages: List[Dict[str, Any]],
    *,
    pattern: str,
    cost_tier: str,
    models: Optional[List[str]],
    excluded_models: Optional[List[str]],
    max_tokens: int,
    reference_on: bool,
    spend: SpendGuard,
    provenance: str = "measured",
) -> Dict[str, Any]:
    if cost_tier not in VALID_TIERS:
        raise ValueError(f"cost_tier must be one of {VALID_TIERS}, got {cost_tier!r}")
    if pattern not in ("direct", "probe"):
        raise ValueError("pattern must be 'direct' or 'probe'")
    router = JevRouter("unused", cost_tier=cost_tier, models=models,
                       excluded_models=excluded_models, want_metadata=True, chat=chat)
    plugin = router.plugin()

    est = estimate_cost(FRONTIER_MODEL, messages, max_tokens)   # routed worst case <= frontier
    spend.check(est)
    note = ""
    run_ref = reference_on
    if run_ref and spend.would_exceed(2 * est):
        run_ref = False
        note = "spend cap reached: reference run skipped"

    pool = ThreadPoolExecutor(max_workers=1)
    ref_future = (pool.submit(chat.complete, FRONTIER_MODEL, messages,
                              max_tokens=max_tokens, temperature=0.0) if run_ref else None)
    try:
        overhead: Optional[CompletionSide] = None
        if pattern == "direct":
            res = chat.complete("typesafe/jev-router", messages, max_tokens=max_tokens,
                                temperature=None, extra_body={"plugins": [plugin]}, want_metadata=True)
            routed = CompletionSide.from_chat(res, meta=res.router_meta())
        else:
            text = " ".join(str(m.get("content", "")) for m in messages if m.get("role") == "user")
            decision = router.resolve(probe_messages(text))
            overhead = CompletionSide(
                decision.chosen_model, "ok", decision.tokens_in, decision.tokens_out,
                decision.cost_usd, decision.cost_source, decision.latency_ms, decision.route_meta)
            res2 = chat.complete(decision.chosen_model, messages, max_tokens=max_tokens, temperature=0.0)
            routed = _combine(overhead, CompletionSide.from_chat(res2))
        spend.add(routed.cost_usd)

        reference: Optional[CompletionSide] = None
        if ref_future is not None:
            try:
                reference = CompletionSide.from_chat(ref_future.result())
                spend.add(reference.cost_usd)
            except OpenRouterError as exc:
                note = f"reference failed: {exc}"
    finally:
        pool.shutdown(wait=True)

    savings = None
    if reference and routed.cost_usd is not None and reference.cost_usd is not None:
        sources = {routed.cost_source, reference.cost_source}
        savings = {
            "cost_usd": reference.cost_usd - routed.cost_usd,
            "cost_pct": _pct(reference.cost_usd - routed.cost_usd, reference.cost_usd),
            "latency_ms": reference.latency_ms - routed.latency_ms,
            "latency_pct": _pct(reference.latency_ms - routed.latency_ms, reference.latency_ms),
            "provenance": provenance,
            "cost_source": "usage" if sources == {"usage"} else (
                "simulated" if "simulated" in sources else "list_price"),
        }
    return {
        "pattern": pattern,
        "cost_tier": cost_tier,
        "plugin": plugin,
        "routed": routed.to_dict(),
        "overhead": overhead.to_dict() if overhead else None,
        "reference": reference.to_dict() if reference else None,
        "savings": savings,
        "status": provenance if savings else "not measured",
        "note": note,
    }


def run_router(inputs: Dict[str, Any], ctx: RunContext) -> Dict[str, Any]:
    request = need(inputs, "request")
    models = ROUTER_POOL if inputs.get("use_pool") else [
        m.strip() for m in str(inputs.get("models", "") or "").split(",") if m.strip()]
    excluded = [m.strip() for m in str(inputs.get("excluded_models", "") or "").split(",") if m.strip()]
    messages = [{"role": "user", "content": request}]
    out = run_router_pair(
        ctx.chat, messages,
        pattern=str(inputs.get("pattern") or "direct"),
        cost_tier=str(inputs.get("cost_tier") or "medium"),
        models=models or None, excluded_models=excluded or None,
        max_tokens=int(inputs.get("max_tokens") or 512),
        reference_on=bool(ctx.flags.get("reference_run", True)),
        spend=ctx.spend,
        provenance="measured" if ctx.mode == "live" else "simulated",
    )
    ctx.sink.emit(GateEvent(
        hook=HOOK2_ROUTER, stage="route", decision=out["routed"]["model"],
        detail={"chosen_model": out["routed"]["model"], "cost_tier": out["cost_tier"],
                "reason": (out["routed"]["meta"] or {}).get("reason", ""), "pair": out},
    ))
    if out["savings"]:
        out["benefits"] = [{
            "kind": "router_vs_reference",
            "label": "Routed vs pinned-frontier completion (end to end)",
            "value": out["savings"]["cost_usd"],
            "detail": out["savings"],
            "provenance": out["savings"]["provenance"],
        }]
    else:
        out["benefits"] = [{
            "kind": "router_vs_reference", "label": "Routed vs pinned-frontier completion (end to end)",
            "value": None, "detail": {"note": out["note"] or "reference run off"},
            "provenance": "not measured",
        }]
    return out
