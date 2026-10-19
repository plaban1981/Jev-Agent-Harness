"""Shared demo module — powers examples/offline_demo.py AND the UI.

run_scenario(name, mode) returns {"scenario", "events", "result", "savings",
"advice", ["comparison" if with_baseline]} where events is the list of
streamed GateEvent dicts (now carrying measured Jev usage/latency and
baseline-savings fields).  Everything runs through ListEventSink so tests,
examples and the FastAPI UI see identical output.
"""

from __future__ import annotations

import os
import tempfile
from typing import Any, Dict, List, Optional

from PIL import Image

from . import hooks
from .advisor import advice_summary, analyze
from .agent import build_jev_agent
from .config import FRONTIER_MODEL, Settings
from .events import EventSink, GateEvent, ListEventSink
from .jev import FakeJevClient
from .metrics import SavingsTracker
from .multimodal import check_listing_photo
from .rag import search_docs

SCENARIOS = [
    "permissions",
    "refund",
    "routing",
    "cascade",
    "rag",
    "multimodal",
    "agent_loop",
]


def _emit_manual(sink: ListEventSink, hook: str, stage: str, decision: str, **detail: Any) -> None:
    sink.emit(GateEvent(hook=hook, stage=stage, decision=decision, detail=detail))


# --- scenario: permissions (Hook 3 cookbook) ---------------------------------
def scenario_permissions(sink: ListEventSink, mode: str, tracker: SavingsTracker) -> Dict[str, Any]:
    jev = FakeJevClient()

    def reversible(state, q):
        cmd = state.get("commands", "")
        if "cleanup" in cmd:
            return 0.85  # technically undoable but touches older files — unclear
        return 0.95

    def serves_task(state, q):
        cmd = state.get("commands", "")
        if "cleanup" in cmd:
            return 0.4  # vague — does it serve the task? unclear
        return 0.93

    jev.set_many({"reversible": reversible, "serves_task": serves_task})

    v1 = hooks.evaluate_risk(
        "bun test", project="jev_harness_poc",
        task="run the project tests", jev=jev, event_sink=sink, tracker=tracker,
    )
    v2 = hooks.evaluate_risk(
        "git push --force origin main", project="jev_harness_poc",
        task="run the project tests", jev=jev, event_sink=sink, tracker=tracker,
    )
    v3 = hooks.evaluate_risk(
        "python scripts/cleanup.py --older-than 90d",
        project="jev_harness_poc",
        task="run the project tests", jev=jev, event_sink=sink, tracker=tracker,
    )
    return {
        "bun_test": v1.outcome,
        "git_push_force": v2.outcome,
        "vague_cleanup": v3.outcome,
        "jev_calls": len(jev.calls),
    }


# --- scenario: refund (gate tool calls cookbook) ------------------------------
def scenario_refund(sink: ListEventSink, mode: str, tracker: SavingsTracker) -> Dict[str, Any]:
    def customer_asked(state, q):
        oid = state["ticket"]["order_id"]
        if oid == "ORD-7781":
            return {"type": "noul", "noul": 0.91}   # asked, but goodwill
        if oid == "ORD-5520":
            return {"type": "noul", "noul": 0.92}
        return {"type": "noul", "noul": 0.97}       # ORD-1234 kettle

    def right_order(state, q):
        return {"type": "noul", "noul": 0.96}

    def policy_covers(state, q):
        oid = state["ticket"]["order_id"]
        if oid == "ORD-7781":
            return {"type": "noul", "noul": 0.03}   # goodwill not covered -> BLOCK
        if oid == "ORD-5520":
            return {"type": "noul", "noul": 0.55}   # damaged? ambiguous -> REVIEW
        return {"type": "noul", "noul": 0.95}       # late delivery covered

    jev = FakeJevClient({
        "customer_asked": customer_asked,
        "right_order": right_order,
        "policy_covers": policy_covers,
    })

    cases = [
        ("ORD-1234", 1200, "late delivery"),
        ("ORD-7781", 40000, "goodwill"),
        ("ORD-5520", 8900, "crushed box on arrival"),
        ("ORD-6800", 8000, "changed my mind"),
    ]
    results = {}
    for oid, amount, reason in cases:
        verdict = hooks.evaluate_refund(
            order_id=oid, amount_cents=amount, reason=reason,
            customer_asked_text=f"customer asked for refund for {reason}",
            jev=jev, event_sink=sink, tracker=tracker,
        )
        results[f"{oid}_{amount}c"] = verdict.outcome
    return results


# --- scenario: routing (Hook 2 / jev-router) ----------------------------------
def scenario_routing(sink: ListEventSink, mode: str, tracker: SavingsTracker) -> Dict[str, Any]:
    if mode == "live":
        settings = Settings.from_env()
        router = hooks.JevRouter(settings.openrouter_api_key)
    else:
        router = hooks.FakeRouter(
            mapping={
                "trivial": "openai/gpt-4o-mini",
                "greeting": "openai/gpt-4o-mini",
                "architecture": FRONTIER_MODEL,
            },
            classifier=lambda text: (
                "architecture"
                if any(w in text for w in ("design", "architecture", "tradeoff"))
                else "greeting"
                if any(w in text for w in ("hello", "hey"))
                else "trivial"
            ),
        )
    probes = {
        "trivial_lookup": "what time is my standup tomorrow",
        "greeting": "hello there!",
        "architecture": "help me design the architecture for a multi-tenant sync engine",
    }
    results = {}
    for name, text in probes.items():
        probe = [{"role": "user", "content": f"{text}\n\nReply with ok"}]
        decision = router.resolve(probe)
        results[name] = decision.chosen_model
        _emit_manual(
            sink, "hook2_router", "route", decision.chosen_model,
            chosen_model=decision.chosen_model,
            cost_tier=decision.cost_tier,
            reason=decision.reason,
            probe=text,
        )
    return results


# --- scenario: cascade (verified-cascade cookbook) ----------------------------
def scenario_cascade(sink: ListEventSink, mode: str, tracker: SavingsTracker) -> Dict[str, Any]:
    excerpts = (
        "Workspace plans: Starter includes 5 seats and 10GB storage; Pro "
        "includes 25 seats, 100GB storage and SSO; Enterprise is unlimited "
        "seats with audit logs. Seat changes are prorated to the day."
    )
    q1 = "How many seats does the Pro plan include?"
    draft1 = "The Pro plan includes 25 seats."

    def support_answer(state, q):
        if "How many seats" in state["objective"]:
            return {"type": "choice", "choice": "supported",
                    "probabilities": {"supported": 0.97, "unsupported": 0.02, "declined": 0.01},
                    "confidence": 0.97}
        if "Jira" in state["objective"]:
            return {"type": "choice", "choice": "declined",
                    "probabilities": {"supported": 0.01, "unsupported": 0.04, "declined": 0.95},
                    "confidence": 0.95}
        if "audit logs" in state["objective"] and "audit logs" in state["answer"]:
            # Grounded frontier answer — supported after escalation.
            return {"type": "choice", "choice": "supported",
                    "probabilities": {"supported": 0.9, "unsupported": 0.06, "declined": 0.04},
                    "confidence": 0.9}
        return {"type": "choice", "choice": "unsupported",
                "probabilities": {"supported": 0.1, "unsupported": 0.85, "declined": 0.05},
                "confidence": 0.85}

    jev = FakeJevClient({"support": support_answer})
    r1 = hooks.run_cascade(
        q1, excerpts, jev,
        draft_answer=draft1,
        frontier_answer="Pro includes 25 seats with 100GB storage and SSO.",
        event_sink=sink, tracker=tracker,
    )

    q2 = "How do I connect the Jira integration?"
    jev2 = FakeJevClient({"support": support_answer})
    r2 = hooks.run_cascade(
        q2, excerpts, jev2,
        draft_answer="You can connect Jira under Settings > Integrations.",
        frontier_answer="Jira is connected via Settings > Integrations using OAuth.",
        event_sink=sink, tracker=tracker,
    )

    q3 = "Do Enterprise seats include audit logs?"
    jev3 = FakeJevClient({"support": support_answer})
    r3 = hooks.run_cascade(
        q3, excerpts, jev3,
        draft_answer="The Pro plan is the best plan for most customers.",
        frontier_answer="Enterprise includes audit logs.",
        event_sink=sink, tracker=tracker,
    )

    return {
        "answerable": r1.to_dict(),
        "jira_declined": r2.to_dict(),
        "escalated": r3.to_dict(),
    }


# --- scenario: rag ------------------------------------------------------------
def scenario_rag(sink: ListEventSink, mode: str, tracker: SavingsTracker) -> Dict[str, Any]:
    def relevance(state, q):
        chunk = state.get("chunk", "")
        if "webhook" in chunk:
            return {"type": "score", "score": 4.8}
        if "rate limit" in chunk or "Retry-After" in chunk:
            return {"type": "score", "score": 2.6}
        if "Slack integration" in chunk:
            return {"type": "score", "score": 1.4}
        return {"type": "score", "score": 0.6}

    jev = FakeJevClient({"relevance": relevance})
    query = "Why are my webhook requests failing with an error?"
    chunks = search_docs(query, jev, event_sink=sink, tracker=tracker)
    return {
        "query": query,
        "top_chunks": [
            {"doc": c.doc_id, "title": c.title, "jev_score": c.jev_score}
            for c in chunks
        ],
    }


# --- scenario: multimodal ------------------------------------------------------
def scenario_multimodal(sink: ListEventSink, mode: str, tracker: SavingsTracker) -> Dict[str, Any]:
    jev = FakeJevClient({
        "shows_item": {"type": "noul", "noul": 0.94},
        "color": {"type": "choice", "choice": "red",
                  "probabilities": {"red": 0.91, "green": 0.06, "blue": 0.03},
                  "confidence": 0.91},
    })
    tmpdir = tempfile.mkdtemp(prefix="jev_mm_")
    path = os.path.join(tmpdir, "listing.png")
    # Oversized photo (3000x2000) — the pipeline must downscale to Jev limits.
    Image.new("RGB", (3000, 2000), (200, 30, 30)).save(path, "PNG")
    result = check_listing_photo(path, "Vintage red enamel kettle", jev, sink, tracker)
    return result.to_dict()


# --- scenario: agent_loop (the showpiece) --------------------------------------
def scenario_agent_loop(sink: ListEventSink, mode: str, tracker: SavingsTracker) -> Dict[str, Any]:
    jev = FakeJevClient()
    jev.set_many({
        "category": {"type": "choice", "choice": "coding",
                     "probabilities": {"coding": 0.94, "security": 0.03,
                                       "devops": 0.02, "billing_support": 0.01},
                     "confidence": 0.94},
        "skill": {"type": "choice", "choice": "run_tests",
                  "probabilities": {"run_tests": 0.93, "refactor_module": 0.07},
                  "confidence": 0.93},
        "reversible": lambda state, q: {"type": "noul", "noul": 0.95},
        "serves_task": lambda state, q: {"type": "noul", "noul": 0.93},
        "support": {"type": "choice", "choice": "supported",
                    "probabilities": {"supported": 0.95, "unsupported": 0.03,
                                      "declined": 0.02},
                    "confidence": 0.95},
    })
    model_script = [
        {"tool_calls": [{"name": "run_shell_command", "args": {"command": "bun test"}}]},
        {"tool_calls": [{"name": "run_shell_command", "args": {"command": "rm -rf /data"}}]},
        {"tool_calls": [{"name": "write_file", "args": {"path": "src/app.ts",
                                                        "content": "export const x = 1;"}}]},
        {"content": "All done: tests pass (bun test), src/app.ts written; "
                    "the destructive `rm -rf /data` was refused by the risk gate."},
    ]
    built = build_jev_agent(
        mode="offline", event_sink=sink,
        jev=jev, model_script=model_script, tracker=tracker,
    )
    final = built["agent"].invoke(
        {"messages": [("user", "Run the project tests, then write src/app.ts.")]}
    )
    tool_msgs = [
        m for m in final["messages"] if getattr(m, "type", None) == "tool"
    ]
    return {
        "hooks_fired": sink.hooks_fired(),
        "final_answer": str(final["messages"][-1].content),
        "tool_results": [str(m.content)[:120] for m in tool_msgs],
    }


SCENARIO_FUNCS = {
    "permissions": scenario_permissions,
    "refund": scenario_refund,
    "routing": scenario_routing,
    "cascade": scenario_cascade,
    "rag": scenario_rag,
    "multimodal": scenario_multimodal,
    "agent_loop": scenario_agent_loop,
}


def run_scenario(
    name: str,
    mode: str = "offline",
    event_sink: Optional[EventSink] = None,
    *,
    tracker: Optional[SavingsTracker] = None,
    with_baseline: bool = False,
) -> Dict[str, Any]:
    """Run one demo scenario; returns events + result + savings payload.

    with_baseline=True additionally simulates the ORIGINAL harness path (one
    full baseline LLM call per gate check, including static-list catches) so
    the UI can show the side-by-side comparison.
    """
    if name not in SCENARIO_FUNCS:
        raise KeyError(f"unknown scenario '{name}'; choose from {SCENARIOS}")
    own_tracker = tracker is None
    tracker = tracker or SavingsTracker()
    sink = ListEventSink()
    result = SCENARIO_FUNCS[name](sink, mode, tracker)

    # Intelligent advisor: threshold tuning + auto-resolution stats.
    advice = analyze(tracker, sink.events)
    for a in advice:
        sink.emit(a)

    if event_sink is not None:
        for e in sink.events:
            event_sink.emit(e)

    payload: Dict[str, Any] = {
        "scenario": name,
        "mode": mode,
        "events": [e.to_dict() for e in sink.events],
        "result": result,
        "savings": tracker.summary(),
        "advice": advice_summary(advice),
    }
    if with_baseline:
        payload["comparison"] = tracker.baseline_comparison()
    if own_tracker:
        payload["tracker"] = tracker
    return payload


def control_room(events: List[Dict[str, Any]], tracker: Optional[SavingsTracker] = None) -> Dict[str, Any]:
    """Aggregate stats for the UI control-room strip (decisions + savings)."""
    by_decision: Dict[str, int] = {}
    for e in events:
        by_decision[e["decision"]] = by_decision.get(e["decision"], 0) + 1
    room: Dict[str, Any] = {
        "events": len(events),
        "by_decision": by_decision,
    }
    if tracker is not None:
        s = tracker.summary()
        room.update({
            "jev_decision_calls": s["total_jev_calls"],
            "tokens_saved": s["tokens_saved"],
            "cost_saved_usd": s["cost_saved_usd"],
            "latency_saved_ms": s["latency_saved_ms"],
            "latency_saved_pct": s["latency_saved_pct"],
            "auto_resolution_pct": s["auto_resolution"]["pct"],
        })
    else:
        jev_calls = sum(
            1 for e in events
            if e.get("cost_usd") is not None
        )
        room.update({
            "jev_decision_calls": jev_calls,
            "est_cost_usd": round(jev_calls * 0.0000168, 6),
        })
    return room
