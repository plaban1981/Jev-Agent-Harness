"""List-price helpers for the model catalog in ``config.MODEL_PRICES``."""

from __future__ import annotations

from typing import Optional

from .config import MODEL_PRICES, ModelPrice


def price_for(model: str) -> Optional[ModelPrice]:
    """Exact slug match, else the longest catalog slug that prefixes ``model``.

    OpenRouter may report dated revisions (``...-20260901``); those price like
    their base slug. Unknown models return None (callers flag them "unpriced").
    """
    if model in MODEL_PRICES:
        return MODEL_PRICES[model]
    best: Optional[str] = None
    for slug in MODEL_PRICES:
        if model.startswith(slug) and (best is None or len(slug) > len(best)):
            best = slug
    return MODEL_PRICES[best] if best else None


def list_cost(model: str, tokens_in: int, tokens_out: int) -> Optional[float]:
    p = price_for(model)
    if p is None:
        return None
    return (tokens_in * p.input_per_m + tokens_out * p.output_per_m) / 1_000_000


def estimate_tokens(text: str) -> int:
    """chars/4 estimate (the same convention metrics.py uses), minimum 1."""
    return max(1, len(text) // 4)
