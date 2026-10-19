"""User-adjustable decision thresholds (defaults come from config.py)."""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, Dict, Optional

from ..config import ACCEPT_CONFIDENCE, APPROVE_AT, BLOCK_AT, RAG_THRESHOLD, RAG_TOP_K


@dataclass(frozen=True)
class Thresholds:
    approve_at: float = APPROVE_AT
    block_at: float = BLOCK_AT
    accept_confidence: float = ACCEPT_CONFIDENCE
    rag_threshold: float = RAG_THRESHOLD
    rag_top_k: int = RAG_TOP_K

    def validate(self) -> None:
        if not (0.0 <= self.block_at < self.approve_at <= 1.0):
            raise ValueError("thresholds need 0 <= block_at < approve_at <= 1")
        if not (0.0 <= self.accept_confidence <= 1.0):
            raise ValueError("accept_confidence must be within 0..1")
        if not (0.0 <= self.rag_threshold <= 5.0):
            raise ValueError("rag_threshold must be within 0..5")
        if self.rag_top_k < 1:
            raise ValueError("rag_top_k must be >= 1")

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "Thresholds":
        data = data or {}
        kwargs: Dict[str, Any] = {}
        for f in fields(cls):
            raw = data.get(f.name)
            if raw is None or raw == "":
                continue
            kwargs[f.name] = int(raw) if f.name == "rag_top_k" else float(raw)
        t = cls(**kwargs)
        t.validate()
        return t
