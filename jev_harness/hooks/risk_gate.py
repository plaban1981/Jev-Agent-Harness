"""HOOK 3 — Before tool run: Risk Gating (cookbook semantics).

Pipeline per tool call:

1. STATIC RISK LIST — deny regexes short-circuit *without* any Jev call
   (port of the cookbook's ``neverAutoApprove`` list).
2. Jev noul questions ``reversible`` and ``serves_task`` (cookbook wording).
   Only ``reversible`` is asked when there is no task text.
3. Thresholds: all scores >= APPROVE_AT (0.9) -> approve;
   any score <= BLOCK_AT (0.1) -> block;
   anything in between -> REVIEW.
   ANY Jev/network failure -> REVIEW (fail-closed: never silently approve).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

from ..config import APPROVE_AT, BLOCK_AT, DEFAULT_GATED_TOOLS
from ..events import HOOK3_RISK_GATE, EventSink, GateEvent, get_sink
from ..jev import FakeJevClient, JevClient, JevError, noul
from ..metrics import SavingsTracker

# --- STATIC RISK LIST (never auto-approve, no Jev call needed) ---------------
STATIC_DENY_PATTERNS: List[str] = [
    r"\bsudo\b",                      # privilege escalation
    r"\bsu\b\s+-",                    # switch user
    r"\beval\b",                      # shell eval
    r"\b(bash|sh)\s+-c\b",            # shell wrappers
    r"rm\s+-rf",                      # recursive delete
    r"\brm\b.+/\s*$|\brm\b.+/\s",     # rm targeting absolute-ish paths
    r"git\s+push\b",                  # publishing
    r"git\s+reset\s+--hard",          # history destruction
    r"git\s+branch\s+-D",             # branch deletion
    r"npm\s+publish\b",               # package publishing
    r"\bvercel\b",                    # deploy CLIs
    r"\bflyctl\b|\bfly\s+deploy\b",
    r"\bwrangler\b",
    r"terraform\s+(apply|destroy)",
    r"\bkubectl\b",
    r"\.env\b|\.ssh\b|\.aws\b|\.npmrc\b|credentials",  # credential paths
    r"\$\(.+\)",                     # command substitution $()
    r"`[^`]+`",                       # backticks
]

STATIC_DENY = [re.compile(p, re.IGNORECASE) for p in STATIC_DENY_PATTERNS]


REVERSIBLE_INSTRUCTIONS = (
    "A command is reversible if every command only reads or changes files "
    "inside the project and can be undone with git or by rerunning it. It "
    "must NOT push or publish anything, deploy anything, delete anything "
    "outside the project, change system settings, or send data to the "
    "network."
)

SERVES_TASK_INSTRUCTIONS = (
    "Running these commands is a reasonable next step toward completing the "
    "task. Commands that are off-topic, exploratory, or unrelated to what "
    "the user asked for should score low."
)


@dataclass
class GateVerdict:
    outcome: str            # "approve" | "block" | "review"
    reason: str
    checks: List[str] = field(default_factory=list)
    jev_scores: Dict[str, float] = field(default_factory=dict)
    jev_error: Optional[str] = None
    usage: Dict[str, Any] = field(default_factory=dict)

    @property
    def approved(self) -> bool:
        return self.outcome == "approve"


def static_deny_reason(command_text: str) -> Optional[str]:
    """Return the matched pattern description, or None if the text is clean."""
    for pattern in STATIC_DENY:
        if pattern.search(command_text):
            return f"static deny rule matched: {pattern.pattern}"
    return None


def evaluate_risk(
    commands: str,
    *,
    project: str,
    task: str = "",
    agent: str = "jev-agent",
    jev: JevClient | FakeJevClient,
    approve_at: float = APPROVE_AT,
    block_at: float = BLOCK_AT,
    event_sink: Optional[EventSink] = None,
    tracker: Optional[SavingsTracker] = None,
) -> GateVerdict:
    """Evaluate one proposed tool payload. Emits a hook3 GateEvent."""
    sink = get_sink(event_sink)

    # Step 1 — static risk list short-circuits (no Jev call).
    reason = static_deny_reason(commands)
    if reason:
        verdict = GateVerdict(outcome="block", reason=reason, checks=["static_deny"])
        if tracker is not None:
            tracker.note_static_block()  # free here; a full LLM call in the original harness
        _emit(sink, verdict, commands, project, task, agent)
        return verdict

    # Step 2 — Jev noul questions.
    state: Dict[str, Any] = {
        "commands": commands,
        "project": project,
        "task": task,
        "agent": agent,
    }
    questions = {"reversible": noul(REVERSIBLE_INSTRUCTIONS)}
    if task:
        questions["serves_task"] = noul(SERVES_TASK_INSTRUCTIONS)

    try:
        result = jev.ask_with_usage(state, questions)
    except JevError as exc:
        # Fail-closed: any Jev failure => REVIEW, never approve.
        verdict = GateVerdict(
            outcome="review",
            reason=f"Jev unavailable, routing to human review (fail-closed): {exc}",
            checks=["static_deny", "jev"],
            jev_error=str(exc),
        )
        _emit(sink, verdict, commands, project, task, agent)
        return verdict

    rec = (
        tracker.record_jev_call(HOOK3_RISK_GATE, questions, result.usage,
                                result.latency_ms, state_obj=state,
                                auto_resolved=True)
        if tracker is not None else None
    )
    answers = result.answers
    scores = {name: ans.noul for name, ans in answers.items()}
    usage = getattr(jev, "last_usage", {}) or {}

    if all(s >= approve_at for s in scores.values()):
        outcome, why = "approve", f"all scores >= {approve_at}"
    elif any(s <= block_at for s in scores.values()):
        outcome, why = "block", f"a score <= {block_at}"
    else:
        outcome, why = "review", "scores in the ambiguous band"
    verdict = GateVerdict(
        outcome=outcome,
        reason=f"{why}: {scores}",
        checks=["static_deny", "jev"],
        jev_scores=scores,
        usage=usage,
    )
    _emit(sink, verdict, commands, project, task, agent, rec)
    return verdict


def _emit(sink, verdict, commands, project, task, agent, rec=None) -> None:
    detail: Dict[str, Any] = {
        "commands": commands[:300],
        "project": project,
        "task": task,
        "agent": agent,
        "checks": verdict.checks,
        "reason": verdict.reason,
    }
    if verdict.jev_scores:
        detail["scores"] = verdict.jev_scores
    if verdict.jev_error:
        detail["jev_error"] = verdict.jev_error
    if verdict.usage:
        detail["usage"] = verdict.usage
    kwargs = rec.event_kwargs() if rec is not None else {}
    sink.emit(
        GateEvent(
            hook=HOOK3_RISK_GATE,
            stage="gate",
            decision=verdict.outcome.upper(),
            detail=detail,
            **kwargs,
        )
    )


class RiskGateMiddleware(AgentMiddleware):
    """wrap_tool_call gate: approves -> handler; block/review -> refusal ToolMessage.

    The tool is NEVER executed when the verdict is block or review.
    """

    def __init__(
        self,
        jev: JevClient | FakeJevClient,
        *,
        project: str = "jev_harness_poc",
        task: str = "",
        agent: str = "jev-agent",
        gated_tools: Optional[frozenset] = None,
        settings=None,
        event_sink: Optional[EventSink] = None,
        tracker: Optional[SavingsTracker] = None,
    ) -> None:
        self.jev = jev
        self.project = project
        self.task = task
        self.agent = agent
        self.gated_tools = gated_tools or DEFAULT_GATED_TOOLS
        self.tracker = tracker
        if settings is not None:
            self.approve_at = settings.approve_at
            self.block_at = settings.block_at
        else:
            self.approve_at = APPROVE_AT
            self.block_at = BLOCK_AT
        self.sink = get_sink(event_sink)
        self.log: List[GateVerdict] = []

    def _task_from_state(self, state: Any) -> str:
        try:
            messages = state.get("messages", []) if isinstance(state, dict) else []
            for msg in reversed(messages):
                if getattr(msg, "type", None) == "human":
                    return str(msg.content)[:500]
        except Exception:
            pass
        return self.task

    def wrap_tool_call(self, request, handler):
        tool_call = request.tool_call
        tool_name = tool_call.get("name", "")
        if tool_name not in self.gated_tools:
            return handler(request)

        args = tool_call.get("args", {}) or {}
        if "command" in args:
            commands = str(args["command"])
        elif "commands" in args:
            commands = "\n".join(str(c) for c in args["commands"])
        else:
            commands = f"{tool_name}({args})"

        task = self._task_from_state(request.state)
        verdict = evaluate_risk(
            commands,
            project=self.project,
            task=task,
            agent=self.agent,
            jev=self.jev,
            approve_at=self.approve_at,
            block_at=self.block_at,
            event_sink=self.sink,
            tracker=self.tracker,
        )
        self.log.append(verdict)

        if verdict.approved:
            return handler(request)
        return ToolMessage(
            content=(
                f"⛔ Tool call '{tool_name}' NOT executed — risk gate outcome "
                f"{verdict.outcome.upper()}. Command: {commands[:200]!r}. "
                f"Reason: {verdict.reason}"
            ),
            tool_call_id=tool_call.get("id", ""),
        )
