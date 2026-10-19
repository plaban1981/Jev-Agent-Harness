"""Playground surfaces for the Jev decision hooks (everything except the
router, the cascade and the full agent run, which need extra measurement)."""

from __future__ import annotations

import base64
import os
import shutil
import tempfile
from typing import Any, Dict

from ..hooks import ORDER_LEDGER, SKILL_REGISTRY, evaluate_refund, evaluate_risk, judge, select_skill
from ..multimodal import check_listing_photo
from ..pricing import estimate_tokens
from ..rag.rerank import rerank
from ..rag.retrieve import KeywordRetriever, ScoredChunk
from .context import RunContext

MAX_IMAGE_BYTES = 8 * 1024 * 1024


def need(inputs: Dict[str, Any], key: str) -> str:
    value = str(inputs.get(key, "") or "").strip()
    if not value:
        raise ValueError(f"'{key}' is required")
    return value


def run_skills(inputs: Dict[str, Any], ctx: RunContext) -> Dict[str, Any]:
    request = need(inputs, "request")
    sel = select_skill(request, ctx.jev, ctx.sink, ctx.tracker)
    full = sum(estimate_tokens(text) for skills in SKILL_REGISTRY.values() for text in skills.values())
    injected = estimate_tokens(sel.instruction)
    return {
        "category": sel.category,
        "skill": sel.skill,
        "instruction": sel.instruction,
        "category_probabilities": sel.category_probabilities,
        "skill_probabilities": sel.skill_probabilities,
        "benefits": [{
            "kind": "prompt_tokens_avoided",
            "label": "Prompt tokens avoided (progressive disclosure)",
            "value": full - injected,
            "detail": {"full_registry_tokens": full, "injected_tokens": injected},
            "provenance": "estimated",
        }],
    }


def run_risk(inputs: Dict[str, Any], ctx: RunContext) -> Dict[str, Any]:
    t = ctx.thresholds
    v = evaluate_risk(
        need(inputs, "command"), project="jev_harness_poc", task=str(inputs.get("task", "")),
        jev=ctx.jev, approve_at=t.approve_at, block_at=t.block_at,
        event_sink=ctx.sink, tracker=ctx.tracker,
    )
    return {"outcome": v.outcome, "reason": v.reason, "checks": v.checks,
            "scores": v.jev_scores, "jev_error": v.jev_error}


def run_refund(inputs: Dict[str, Any], ctx: RunContext) -> Dict[str, Any]:
    t = ctx.thresholds
    ledger = {k: dict(v) for k, v in ORDER_LEDGER.items()}   # never mutate the shared ledger
    v = evaluate_refund(
        order_id=need(inputs, "order_id"), amount_cents=int(inputs.get("amount_cents") or 0),
        reason=need(inputs, "reason"), customer_asked_text=need(inputs, "customer_text"),
        jev=ctx.jev, approve_at=t.approve_at, block_at=t.block_at,
        event_sink=ctx.sink, tracker=ctx.tracker, ledger=ledger,
    )
    return {"outcome": v.outcome, "reason": v.reason, "checks": v.checks,
            "scores": v.jev_scores, "jev_error": v.jev_error}


def run_quality(inputs: Dict[str, Any], ctx: RunContext) -> Dict[str, Any]:
    v = judge(need(inputs, "question"), need(inputs, "answer"), need(inputs, "context"),
              ctx.jev, ctx.sink, ctx.tracker)
    accepted = v.verdict == "supported" and v.confidence >= ctx.thresholds.accept_confidence
    route = "send" if accepted else "handoff" if v.verdict == "declined" else "retry"
    return {"verdict": v.verdict, "confidence": v.confidence, "probabilities": v.probabilities,
            "accepted": accepted, "route": route,
            "note": "route 'retry' = QualityMiddleware would loop back to the model"}


def run_rag(inputs: Dict[str, Any], ctx: RunContext) -> Dict[str, Any]:
    query = need(inputs, "query")
    passages = [p.strip() for p in str(inputs.get("passages", "") or "").split("\n\n") if p.strip()]
    if passages:
        candidates = [ScoredChunk(f"p{i + 1}", p.split("\n", 1)[0][:60], p, 0.0)
                      for i, p in enumerate(passages)]
    else:
        candidates = KeywordRetriever().retrieve(query)
    t = ctx.thresholds
    if not candidates:
        return {"query": query, "candidates": 0, "kept": [], "benefits": []}
    kept = rerank(query, candidates, ctx.jev, top_k=t.rag_top_k, threshold=t.rag_threshold,
                  event_sink=ctx.sink, tracker=ctx.tracker)
    cand_tokens = sum(estimate_tokens(c.text) for c in candidates)
    kept_tokens = sum(estimate_tokens(c.text) for c in kept)
    return {
        "query": query,
        "candidates": len(candidates),
        "kept": [{"doc": c.doc_id, "title": c.title, "jev_score": c.jev_score} for c in kept],
        "benefits": [{
            "kind": "context_tokens_pruned",
            "label": "Context tokens pruned before the main LLM",
            "value": cand_tokens - kept_tokens,
            "detail": {"candidate_tokens": cand_tokens, "kept_tokens": kept_tokens},
            "provenance": "estimated",
        }],
    }


def run_multimodal(inputs: Dict[str, Any], ctx: RunContext) -> Dict[str, Any]:
    title = need(inputs, "title")
    b64 = need(inputs, "image_b64")
    name = str(inputs.get("image_name") or "upload.png")
    raw = base64.b64decode(b64.split(",", 1)[-1])
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("image is larger than 8 MB")
    ext = os.path.splitext(name)[1].lower() or ".png"
    tmp = tempfile.mkdtemp(prefix="jev_pg_")
    try:
        path = os.path.join(tmp, "upload" + ext)
        with open(path, "wb") as fh:
            fh.write(raw)
        result = check_listing_photo(path, title, ctx.jev, ctx.sink, ctx.tracker)
        return result.to_dict()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)   # Pillow may still hold the file on Windows
