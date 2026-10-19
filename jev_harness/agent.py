"""DeepAgents harness wiring: build_jev_agent(mode, event_sink).

Offline mode uses a scripted StubChatModel and FakeJevClient /
FakeRouter — zero network.  Live mode wires ChatOpenAI through OpenRouter
plus the real JevClient / JevRouter (requires OPENROUTER_API_KEY).
"""

from __future__ import annotations

import json
import os
from typing import Any, Callable, Dict, List, Optional, Sequence

from deepagents import create_deep_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, TodoListMiddleware
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import PrivateAttr

from . import hooks
from .config import FRONTIER_MODEL, Settings
from .events import EventSink
from .hooks.refund_gate import ORDER_LEDGER
from .jev import FakeJevClient, JevClient
from .metrics import SavingsTracker
from .rag import search_docs


# --- Stub chat model for offline runs ----------------------------------------
class StubChatModel(BaseChatModel):
    """Scripted chat model: cycles through canned responses.

    Each script entry may be an AIMessage or a dict describing one:
    ``{"tool_calls": [{"name": ..., "args": {...}}]}`` or
    ``{"content": "..."}``.  DeepAgents calls ``bind_tools`` (unimplemented
    on BaseChatModel), so we implement it here; all mutable state lives in
    ``__init__`` because tool nodes run outside ``run()``.
    """

    _script: List[AIMessage] = PrivateAttr(default_factory=list)
    _cursor: int = PrivateAttr(default=0)
    _calls: List[List[BaseMessage]] = PrivateAttr(default_factory=list)

    def __init__(self, script: Sequence[Any] = (), **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._script = [self._coerce(s) for s in script]
        self._cursor = 0
        self._calls = []

    @property
    def calls(self) -> List[List[BaseMessage]]:
        return self._calls

    @staticmethod
    def _coerce(entry: Any) -> AIMessage:
        if isinstance(entry, AIMessage):
            return entry
        if isinstance(entry, dict) and "tool_calls" in entry:
            return AIMessage(
                content="",
                tool_calls=[
                    {"name": tc["name"], "args": tc["args"], "id": tc.get("id", f"call_{i+1}")}
                    for i, tc in enumerate(entry["tool_calls"])
                ],
            )
        if isinstance(entry, dict):
            return AIMessage(content=str(entry.get("content", "")))
        return AIMessage(content=str(entry))

    def _next(self) -> AIMessage:
        if not self._script:
            return AIMessage(content="(stub: no scripted response)")
        msg = self._script[min(self._cursor, len(self._script) - 1)]
        if self._cursor < len(self._script) - 1:
            self._cursor += 1
        return msg

    def _generate(self, messages, stop=None, **kwargs: Any) -> ChatResult:
        self._calls.append(list(messages))
        return ChatResult(generations=[ChatGeneration(message=self._next())])

    def bind_tools(self, tools, **kwargs: Any) -> "StubChatModel":
        return self

    @property
    def _llm_type(self) -> str:
        return "stub"


# --- Tools (simulated even in live mode — this is a harness POC) -------------
def make_tools(
    jev: FakeJevClient | JevClient,
    event_sink: Optional[EventSink],
    tracker: Optional[SavingsTracker] = None,
) -> List[Callable]:
    def run_shell_command(command: str) -> str:
        """Run a shell command in the project workspace (SIMULATED execution)."""
        return json.dumps({
            "simulated": True,
            "command": command,
            "exit_code": 0,
            "stdout": f"[simulated] ok: {command}",
        })

    def write_file(path: str, content: str) -> str:
        """Write a file inside the project (SIMULATED — nothing touches disk)."""
        return json.dumps({"simulated": True, "path": path, "bytes": len(content)})

    def issue_refund(order_id: str, amount_cents: int, reason: str, customer_asked: str) -> str:
        """Issue a refund against the in-memory order ledger (SIMULATED)."""
        order = ORDER_LEDGER.get(order_id)
        if order is None:
            return json.dumps({"error": f"unknown order {order_id}"})
        order["balance_cents"] -= amount_cents
        return json.dumps({
            "simulated": True,
            "order_id": order_id,
            "refunded_cents": amount_cents,
            "remaining_cents": order["balance_cents"],
        })

    def search_docs_tool(query: str) -> str:
        """Search the help-center docs (keyword retrieval + Jev re-ranking)."""
        chunks = search_docs(query, jev, event_sink=event_sink, tracker=tracker)
        return json.dumps([
            {"doc": c.doc_id, "title": c.title, "jev_score": c.jev_score, "text": c.text}
            for c in chunks
        ])

    return [run_shell_command, write_file, issue_refund, search_docs_tool]


def build_jev_agent(
    mode: str = "offline",
    event_sink: Optional[EventSink] = None,
    *,
    settings: Optional[Settings] = None,
    model_script: Optional[Sequence[Any]] = None,
    jev: Optional[FakeJevClient | JevClient] = None,
    tracker: Optional[SavingsTracker] = None,
    router: Optional[Any] = None,
) -> Dict[str, Any]:
    """Build the DeepAgents harness with all four Jev gate hooks installed.

    Returns a dict with the compiled agent and the wired components so tests
    and demos can inspect fakes, logs and the event sink.
    """
    settings = settings or Settings.from_env()
    if mode == "live":
        if not settings.openrouter_api_key:
            raise RuntimeError(
                "live mode requires OPENROUTER_API_KEY — use mode='offline'"
            )
        from langchain_openai import ChatOpenAI

        model: BaseChatModel = ChatOpenAI(
            model=FRONTIER_MODEL,
            base_url="https://openrouter.ai/api/v1",
            api_key=settings.openrouter_api_key,
            extra_body={"usage": {"include": True}},
        )
        if jev is None:
            jev = JevClient(settings.openrouter_api_key, timeout=settings.timeout)
        router = router or hooks.JevRouter(settings.openrouter_api_key, timeout=settings.timeout)

        def model_factory(slug: str) -> Any:
            return ChatOpenAI(
                model=slug,
                base_url="https://openrouter.ai/api/v1",
                api_key=settings.openrouter_api_key,
                extra_body={"usage": {"include": True}},
            )
    else:
        script = list(model_script) if model_script else [
            {"content": "(offline stub) nothing scripted — done."}
        ]
        model = StubChatModel(script)
        if jev is None:
            jev = FakeJevClient()
        router = router or hooks.FakeRouter(
            mapping={
                "trivial": "openai/gpt-4o-mini",
                "greeting": "openai/gpt-4o-mini",
                "architecture": FRONTIER_MODEL,
            },
            classifier=lambda text: (
                "architecture" if any(w in text for w in ("design", "architecture", "system")) else
                "greeting" if any(w in text for w in ("hello", "hi", "hey")) else
                "trivial"
            ),
        )
        model_factory = None  # offline: keep the stub model, just log routing

    tools = make_tools(jev, event_sink, tracker)

    middleware = [
        hooks.SkillsMiddleware(jev, event_sink, tracker),
        hooks.RouterMiddleware(router, model_factory, event_sink),
        hooks.RiskGateMiddleware(
            jev, project=settings.project, settings=settings,
            event_sink=event_sink, tracker=tracker,
        ),
        hooks.RefundGateMiddleware(jev, event_sink=event_sink, tracker=tracker),
        hooks.QualityMiddleware(jev, event_sink=event_sink, tracker=tracker),
        TodoListMiddleware(),
        ModelCallLimitMiddleware(run_limit=8, exit_behavior="end"),
    ]

    agent = create_deep_agent(
        model=model,
        tools=tools,
        system_prompt=(
            "You are the Jev-gated harness agent. Propose tool calls to make "
            "progress; every gated tool call passes a risk gate first. Tools "
            "are simulated and there is no real project on disk, so do not "
            "explore or plan with a todo list: call the tools the request "
            "names directly, then give a short final answer. If a tool call is "
            "blocked or needs review, say so instead of retrying it."
        ),
        middleware=middleware,
    )

    return {
        "agent": agent,
        "model": model,
        "jev": jev,
        "router": router,
        "middleware": middleware,
        "tools": tools,
        "tracker": tracker,
        "mode": mode,
    }
