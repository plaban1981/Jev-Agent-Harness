"""RAG re-ranking with Jev ``score`` questions (Context Judge pattern).

Broad retrieval returns candidate chunks; Jev rates each chunk 0.0-5.0
against a relevance rubric; low scorers are pruned and only the top K
survive.  Fully deterministic offline via FakeJevClient scripts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..config import RAG_THRESHOLD, RAG_TOP_K
from ..events import HOOK_RAG, EventSink, GateEvent, get_sink
from ..jev import FakeJevClient, JevClient, score as score_q
from ..metrics import SavingsTracker
from .retrieve import KeywordRetriever, ScoredChunk

RELEVANCE_LEVELS = [
    "0: irrelevant to the query",
    "1: barely related",
    "2: same product but does not address the query",
    "3: topically related and partially helpful",
    "4: mostly answers the query",
    "5: directly answers the query with specific facts",
]

RUBRIC = (
    "Rate how relevant this help-center excerpt is to the user query. "
    "5.0 = directly answers the query with specific facts; "
    "3.0-4.9 = topically related and partially helpful; "
    "1.0-2.9 = same product but does not address the query; "
    "0.0-0.9 = irrelevant."
)


@dataclass
class RerankedChunk:
    doc_id: str
    title: str
    text: str
    retrieval_score: float
    jev_score: float


def rerank(
    query: str,
    candidates: List[ScoredChunk],
    jev: JevClient | FakeJevClient,
    *,
    top_k: int = RAG_TOP_K,
    threshold: float = RAG_THRESHOLD,
    event_sink: Optional[EventSink] = None,
    tracker: Optional[SavingsTracker] = None,
) -> List[RerankedChunk]:
    """Score every candidate with Jev, prune to top_k above threshold."""
    sink = get_sink(event_sink)
    question = {"relevance": score_q(RUBRIC, 0.0, 5.0, RELEVANCE_LEVELS)}

    scored: List[RerankedChunk] = []
    for chunk in candidates:
        state = {"query": query, "chunk": chunk.text, "title": chunk.title}
        result = jev.ask_with_usage(state, question)
        rec = (
            tracker.record_jev_call(HOOK_RAG, question, result.usage,
                                    result.latency_ms, state_obj=state)
            if tracker is not None else None
        )
        answer = result.answers["relevance"]
        scored.append(
            RerankedChunk(
                doc_id=chunk.doc_id,
                title=chunk.title,
                text=chunk.text,
                retrieval_score=chunk.retrieval_score,
                jev_score=answer.value,
            )
        )
        # per-chunk score event so the UI can animate each judgment
        sink.emit(
            GateEvent(
                hook=HOOK_RAG,
                stage="score_chunk",
                decision=f"{answer.value:.1f}",
                detail={"doc": chunk.doc_id, "title": chunk.title,
                        "jev_score": answer.value, "query": query},
                **(rec.event_kwargs() if rec else {}),
            )
        )

    sink.emit(
        GateEvent(
            hook=HOOK_RAG,
            stage="rerank",
            decision="PRUNE",
            detail={
                "query": query,
                "num_candidates": len(candidates),
                "scores": {c.doc_id: c.jev_score for c in scored},
                "threshold": threshold,
                "top_k": top_k,
            },
        )
    )

    kept = [c for c in scored if c.jev_score >= threshold]
    kept.sort(key=lambda c: c.jev_score, reverse=True)
    return kept[:top_k]


def search_docs(
    query: str,
    jev: JevClient | FakeJevClient,
    *,
    retriever: Optional[KeywordRetriever] = None,
    top_k: int = RAG_TOP_K,
    threshold: float = RAG_THRESHOLD,
    event_sink: Optional[EventSink] = None,
    tracker: Optional[SavingsTracker] = None,
) -> List[RerankedChunk]:
    """Full pipeline: keyword retrieval -> Jev re-rank -> top-K chunks."""
    retriever = retriever or KeywordRetriever()
    candidates = retriever.retrieve(query)
    return rerank(
        query, candidates, jev, top_k=top_k, threshold=threshold,
        event_sink=event_sink, tracker=tracker,
    )
