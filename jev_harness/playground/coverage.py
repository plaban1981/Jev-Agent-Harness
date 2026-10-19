"""Which decision system lives where. The coverage test keeps this honest."""

from __future__ import annotations

from typing import Dict, List, Tuple

# Jev question key -> the hook(s) whose GateEvent follows that question.
QUESTION_KEY_HOOKS: Dict[str, Tuple[str, ...]] = {
    "category": ("hook1_skills",),
    "skill": ("hook1_skills",),
    "reversible": ("hook3_risk_gate",),
    "serves_task": ("hook3_risk_gate",),
    "customer_asked": ("hook3_refund_gate",),
    "right_order": ("hook3_refund_gate",),
    "policy_covers": ("hook3_refund_gate",),
    "support": ("hook4_quality",),
    "relevance": ("rag",),
    "shows_item": ("multimodal",),
    "color": ("multimodal",),
}

# events.py HOOK_* value -> Playground surface id.
HOOK_SURFACES: Dict[str, str] = {
    "hook1_skills": "skills",
    "hook2_router": "router",
    "hook3_risk_gate": "risk",
    "hook3_refund_gate": "refund",
    "hook4_quality": "quality",
    "cascade": "cascade",
    "rag": "rag",
    "multimodal": "multimodal",
    "advisor": "advisor",
}

# Hooks whose GateEvent can carry shadows, and stages that are not decisions.
PAIRABLE_HOOKS = frozenset({
    "hook1_skills", "hook3_risk_gate", "hook3_refund_gate",
    "hook4_quality", "rag", "multimodal",
})
NON_DECISION_STAGES = frozenset({"rerank"})

COVERAGE_MATRIX: List[Dict[str, str]] = [
    {"id": "1", "name": "Hook 1 Progressive disclosure", "module": "hooks/skills.py",
     "surface": "skills", "baseline": "judge answers the same two choices",
     "benefit": "prompt tokens avoided (full registry vs injected skill)"},
    {"id": "2", "name": "Hook 2 Model routing", "module": "hooks/router.py",
     "surface": "router", "baseline": "same messages to the pinned frontier model",
     "benefit": "measured end-to-end cost and latency of routed vs reference completion"},
    {"id": "3", "name": "Hook 3a Risk gate", "module": "hooks/risk_gate.py",
     "surface": "risk", "baseline": "judge answers the same nouls",
     "benefit": "free decisions (static deny list = 0 model calls)"},
    {"id": "4", "name": "Hook 3b Refund gate", "module": "hooks/refund_gate.py",
     "surface": "refund", "baseline": "judge answers the same nouls",
     "benefit": "free decisions (pre-check blocks = 0 model calls)"},
    {"id": "5", "name": "Hook 4a Quality gate + retry", "module": "hooks/quality.py",
     "surface": "quality", "baseline": "judge answers the same choice",
     "benefit": "retry loop visibility"},
    {"id": "6", "name": "Hook 4b Verified cascade", "module": "hooks/cascade.py",
     "surface": "cascade", "baseline": "real frontier completion of the same question",
     "benefit": "measured generation cost avoided"},
    {"id": "7", "name": "RAG re-ranking", "module": "rag/rerank.py",
     "surface": "rag", "baseline": "judge scores each chunk",
     "benefit": "context tokens pruned"},
    {"id": "8", "name": "Multimodal decisions", "module": "multimodal.py",
     "surface": "multimodal", "baseline": "vision judge answers the same questions",
     "benefit": "image resized to Jev limits"},
    {"id": "9", "name": "Fail-closed behaviour", "module": "hooks/risk_gate.py, refund_gate.py",
     "surface": "risk", "baseline": "-", "benefit": "forced Jev failure shows REVIEW"},
    {"id": "10", "name": "Threshold advisor", "module": "advisor.py",
     "surface": "advisor", "baseline": "-", "benefit": "LOWER/RAISE advice, auto-resolution %"},
    {"id": "11", "name": "Full agent loop", "module": "agent.py",
     "surface": "agent", "baseline": "all of the above",
     "benefit": "gate cost as a share of total run cost"},
]
