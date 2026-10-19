"""Registry: surface id -> function(inputs, ctx) -> JSON-ready result dict."""

from __future__ import annotations

from .agent_run import run_agent
from .lab_cascade import run_cascade_lab
from .lab_gates import run_multimodal, run_quality, run_rag, run_refund, run_risk, run_skills
from .lab_router import run_router

SURFACES = {
    "agent": run_agent,
    "skills": run_skills,
    "router": run_router,
    "risk": run_risk,
    "refund": run_refund,
    "quality": run_quality,
    "cascade": run_cascade_lab,
    "rag": run_rag,
    "multimodal": run_multimodal,
}
