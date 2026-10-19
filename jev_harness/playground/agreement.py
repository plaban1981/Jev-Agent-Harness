"""When do Jev and the LLM judge "agree"? Rules depend on the question type."""

from __future__ import annotations

from typing import Dict, Mapping

from ..jev.client import JevAnswer
from .thresholds import Thresholds

MULTIMODAL_MATCH_AT = 0.8   # multimodal.py hard-codes MATCH at shows_item >= 0.8


def band(value: float, t: Thresholds) -> str:
    if value >= t.approve_at:
        return "approve"
    if value <= t.block_at:
        return "block"
    return "review"


def answers_agree(
    jev: Mapping[str, JevAnswer],
    judge: Mapping[str, JevAnswer],
    t: Thresholds,
) -> Dict[str, bool]:
    out: Dict[str, bool] = {}
    for name, ja in jev.items():
        da = judge.get(name)
        if da is None:
            continue
        if ja.type == "noul":
            if name == "shows_item":
                out[name] = (ja.noul >= MULTIMODAL_MATCH_AT) == (da.noul >= MULTIMODAL_MATCH_AT)
            else:
                out[name] = band(ja.noul, t) == band(da.noul, t)
        elif ja.type == "choice":
            out[name] = ja.choice_value == da.choice_value
        else:  # score
            out[name] = (ja.value >= t.rag_threshold) == (da.value >= t.rag_threshold)
    return out
