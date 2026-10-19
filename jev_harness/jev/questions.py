"""Typed question builders for the Jev Decisions API.

Each helper returns a plain dict matching the Decisions API ``questions``
shape.  Keeping them as typed helpers means the rest of the codebase never
hand-writes question JSON.
"""

from __future__ import annotations

from typing import Dict, List, Optional


def noul(instructions: str) -> Dict:
    """A yes/no question answered with a probability (noul = null-prob)."""
    return {"type": "noul", "instructions": instructions}


def choice(instructions: str, criteria: List[str]) -> Dict:
    """A categorical question answered with per-option probabilities."""
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


def score(
    instructions: str,
    minimum: float,
    maximum: float,
    criteria: Optional[List[str]] = None,
) -> Dict:
    """A numeric-rating question answered with a float in [min, max].

    The Decisions API has no min/max: a score question takes ``criteria``, an
    ordered list of level descriptions, and answers with a position in that
    list. ``criteria`` defaults to one generic level per integer in the range;
    pass real descriptions for a meaningful rubric. ``min``/``max`` stay in the
    dict for local use (judge prompt, fakes) and are dropped on the wire.
    """
    levels = criteria or [f"Level {i}" for i in range(int(minimum), int(maximum) + 1)]
    return {"type": "score", "instructions": instructions, "min": minimum,
            "max": maximum, "criteria": list(levels)}
