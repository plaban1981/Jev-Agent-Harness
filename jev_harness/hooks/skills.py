"""HOOK 1 — Before prompt: Progressive Disclosure (two-pass skill cascade).

Hierarchical skill registry (categories -> skills with instruction text).
Two Jev ``choice`` questions pick the category, then the specific skill,
and only the selected skill's instruction text is injected into the system
prompt — keeping the context window lightweight per the lesson's
"Progressive Disclosure" pattern.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage, SystemMessage

from ..events import HOOK1_SKILLS, EventSink, GateEvent, get_sink
from ..jev import FakeJevClient, JevClient, choice
from ..metrics import SavingsTracker

# --- Hierarchical skill registry ---------------------------------------------
SKILL_REGISTRY: Dict[str, Dict[str, str]] = {
    "security": {
        "audit_logs": (
            "## Skill: audit_logs\nPull authentication and access logs for the "
            "requested time window, summarise anomalies, and never modify data."
        ),
        "investigate_alert": (
            "## Skill: investigate_alert\nTriage a security alert: identify the "
            "indicator, check related tickets, and draft a containment note. "
            "Do not take disruptive action without human approval."
        ),
        "reset_password": (
            "## Skill: reset_password\nVerify identity, then trigger a password "
            "reset and notify the user out-of-band."
        ),
    },
    "coding": {
        "run_tests": (
            "## Skill: run_tests\nPrefer the project's test runner (bun test / "
            "pytest). Run the smallest relevant test file first, report failures "
            "with file:line. Never use --force or skip flags."
        ),
        "refactor_module": (
            "## Skill: refactor_module\nMake the smallest behaviour-preserving "
            "change, keep public APIs stable, and run tests afterwards."
        ),
    },
    "devops": {
        "deploy_check": (
            "## Skill: deploy_check\nRead deployment status, recent releases and "
            "health checks. Never trigger deploys or rollbacks yourself."
        ),
        "incident_triage": (
            "## Skill: incident_triage\nClassify severity, page the on-call owner "
            "for SEV-1/2, and draft a timeline from logs."
        ),
    },
    "billing_support": {
        "issue_refund": (
            "## Skill: issue_refund\nCheck the order on the ticket, verify the "
            "refund amount against the remaining balance, confirm the policy "
            "covers the reason, then issue the refund. Escalate anything "
            "ambiguous to a human."
        ),
        "prorate_plan": (
            "## Skill: prorate_plan\nCompute proration for mid-cycle plan "
            "changes and explain the line items to the customer."
        ),
    },
}

CATEGORIES = list(SKILL_REGISTRY.keys())


@dataclass
class SkillSelection:
    category: str
    skill: str
    instruction: str
    category_probabilities: Dict[str, float] = field(default_factory=dict)
    skill_probabilities: Dict[str, float] = field(default_factory=dict)

    @property
    def category_confidence(self) -> float:
        return self.category_probabilities.get(self.category, 0.0)

    @property
    def skill_confidence(self) -> float:
        return self.skill_probabilities.get(self.skill, 0.0)


def _user_text(messages: List[Any]) -> str:
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            return str(msg.content)
    return ""


def select_skill(
    user_request: str,
    jev: JevClient | FakeJevClient,
    event_sink: Optional[EventSink] = None,
    tracker: Optional[SavingsTracker] = None,
) -> SkillSelection:
    """Two-pass cascade: category choice, then skill choice. Emits a GateEvent."""
    sink = get_sink(event_sink)

    # Pass 1 — pick the category.
    cat_qs = {
        "category": choice(
            "Given the user request, which skill category should handle it?",
            CATEGORIES,
        )
    }
    cat_result = jev.ask_with_usage({"request": user_request}, cat_qs)
    cat_rec = (
        tracker.record_jev_call(HOOK1_SKILLS, cat_qs, cat_result.usage,
                                cat_result.latency_ms,
                                state_obj={"request": user_request})
        if tracker is not None else None
    )
    cat_answers = cat_result.answers
    category = cat_answers["category"].choice_value
    if category not in SKILL_REGISTRY:
        category = CATEGORIES[0]

    # Pass 2 — pick the specific skill inside the category.
    skills = SKILL_REGISTRY[category]
    skill_qs = {
        "skill": choice(
            f"Within category '{category}', which specific skill applies?",
            list(skills.keys()),
        )
    }
    skill_result = jev.ask_with_usage({"request": user_request, "category": category}, skill_qs)
    skill_rec = (
        tracker.record_jev_call(HOOK1_SKILLS, skill_qs, skill_result.usage,
                                skill_result.latency_ms,
                                state_obj={"request": user_request, "category": category})
        if tracker is not None else None
    )
    skill_answers = skill_result.answers
    skill = skill_answers["skill"].choice_value
    if skill not in skills:
        skill = next(iter(skills))

    selection = SkillSelection(
        category=category,
        skill=skill,
        instruction=skills[skill],
        category_probabilities=cat_answers["category"].probabilities,
        skill_probabilities=skill_answers["skill"].probabilities,
    )
    sink.emit(
        GateEvent(
            hook=HOOK1_SKILLS,
            stage="two_pass_cascade",
            decision=f"{category}/{skill}",
            detail={
                "category": category,
                "skill": skill,
                "category_confidence": selection.category_confidence,
                "skill_confidence": selection.skill_confidence,
                "category_probabilities": selection.category_probabilities,
                "skill_probabilities": selection.skill_probabilities,
            },
            **(cat_rec.event_kwargs() if cat_rec else {}),
        )
    )
    return selection


class SkillsMiddleware(AgentMiddleware):
    """Injects only the selected skill's instructions into the system prompt.

    Implemented as ``wrap_model_call`` so the system message is rewritten
    deterministically before the model sees the request (verified safe with
    langchain 1.4.6 — ``request.override(system_message=...)``).
    """

    def __init__(
        self,
        jev: JevClient | FakeJevClient,
        event_sink: Optional[EventSink] = None,
        tracker: Optional[SavingsTracker] = None,
    ) -> None:
        self.jev = jev
        self.sink = get_sink(event_sink)
        self.tracker = tracker
        self._cache_key: Optional[str] = None
        self._cached: Optional[SkillSelection] = None

    def wrap_model_call(self, request, handler):
        user_text = _user_text(request.messages)
        if self._cache_key != user_text:
            self._cached = select_skill(user_text, self.jev, self.sink, self.tracker)
            self._cache_key = user_text
        selection = self._cached
        base = request.system_message.text if request.system_message else ""
        marker = "## Skill:"
        if marker not in base:
            new_system = SystemMessage(
                content=base + "\n\n" + selection.instruction
            )
            request = request.override(system_message=new_system)
        return handler(request)
