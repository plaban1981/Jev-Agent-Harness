"""FastAPI control-room UI for the Jev-gated Agent Harness POC.

Run:
    python -m uvicorn ui.app:app --port 8000

Endpoints:
    GET  /                      -> single-page dashboard (static/index.html)
    GET  /api/health            -> {status, mode, openrouter_key_set}
    GET  /api/scenarios         -> list of demo scenario names
    POST /api/run/{scenario}    -> run synchronously, returns events + result
    WS   /ws                    -> send a scenario name, stream GateEvents as JSON
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from dataclasses import asdict

from fastapi import Body, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from jev_harness.config import MODEL_PRICES, ROUTER_POOL
from jev_harness.demo import SCENARIOS, control_room, run_scenario
from jev_harness.events import GateEvent
from jev_harness.hooks import ORDER_LEDGER, SKILL_REGISTRY, STATIC_DENY_PATTERNS
from jev_harness.jev import noul
from jev_harness.playground.coverage import COVERAGE_MATRIX
from jev_harness.playground.judge import SYSTEM_PROMPT, render_prompt
from jev_harness.playground.runner import ConfigError, execute
from jev_harness.playground.scoreboard import build_scoreboard, project
from jev_harness.playground.session import PlaygroundSession
from jev_harness.playground.spend import SpendCapExceeded
from jev_harness.playground.surfaces import SURFACES as PG_SURFACES
from jev_harness.playground.thresholds import Thresholds
from dotenv import load_dotenv
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Jev-gated Agent Harness — Control Room")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
SESSION = PlaygroundSession()


def _mode() -> str:
    if os.environ.get("JEV_HARNESS_OFFLINE", "1") in ("0", "false"):
        return "live"
    return "offline"


def _key_set() -> bool:
    return bool(os.environ.get("OPENROUTER_API_KEY"))


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "mode": _mode(),
        "openrouter_key_set": _key_set(),
        "scenarios": SCENARIOS,
    }


@app.get("/api/scenarios")
def scenarios() -> dict:
    return {"scenarios": SCENARIOS, "mode": _mode()}


@app.post("/api/run/{scenario}")
def run(scenario: str) -> dict:
    """Synchronous fallback: runs the scenario and returns all events.

    The payload includes the measured ``savings`` summary and a
    ``comparison`` of the Jev-gated harness vs the original full-LLM harness.
    """
    payload = run_scenario(scenario, mode=_mode(), with_baseline=True)
    payload.pop("tracker", None)  # not JSON-serializable
    payload["control_room"] = control_room(payload["events"])
    payload["control_room"].update({
        "tokens_saved": payload["savings"]["tokens_saved"],
        "cost_saved_usd": payload["savings"]["cost_saved_usd"],
        "latency_saved_ms": payload["savings"]["latency_saved_ms"],
        "latency_saved_pct": payload["savings"]["latency_saved_pct"],
        "auto_resolution_pct": payload["savings"]["auto_resolution"]["pct"],
    })
    return payload


class _WsSink:
    """EventSink that forwards GateEvents to a WebSocket (thread-safe-ish)."""

    def __init__(self, ws: WebSocket, loop: asyncio.AbstractEventLoop) -> None:
        self.ws = ws
        self.loop = loop

    def emit(self, event: GateEvent) -> None:
        self.loop.call_soon_threadsafe(
            lambda: asyncio.ensure_future(self.ws.send_json(event.to_dict()))
        )


@app.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    await websocket.accept()
    loop = asyncio.get_running_loop()
    try:
        while True:
            scenario = (await websocket.receive_text()).strip()
            if scenario not in SCENARIOS:
                await websocket.send_json(
                    {"error": f"unknown scenario '{scenario}'",
                     "scenarios": SCENARIOS}
                )
                continue
            sink = _WsSink(websocket, loop)

            def _run() -> dict:
                payload = run_scenario(scenario, mode=_mode(), with_baseline=True,
                                       event_sink=sink)
                payload.pop("tracker", None)
                return payload

            payload = await loop.run_in_executor(None, _run)
            await websocket.send_json(
                {
                    "type": "done",
                    "scenario": scenario,
                    "result": payload["result"],
                    "savings": payload["savings"],
                    "advice": payload["advice"],
                    "comparison": payload["comparison"],
                    "control_room": control_room(payload["events"]),
                }
            )
    except WebSocketDisconnect:
        return


# --- Playground ---------------------------------------------------------------
def _pg_default_mode() -> str:
    return "live" if (_key_set() and _mode() == "live") else "simulated"


def _pg_spend() -> dict:
    return {"cap_usd": SESSION.spend.cap_usd, "total_usd": SESSION.spend.total,
            "remaining_usd": SESSION.spend.remaining}


def _pg_run(body: dict, emit=None) -> dict:
    return execute(
        str(body.get("surface", "")), body.get("inputs") or {},
        mode=body.get("mode") or _pg_default_mode(), session=SESSION,
        thresholds=body.get("thresholds"), flags=body.get("flags"),
        emit=emit, api_key=os.environ.get("OPENROUTER_API_KEY"),
    )


def _pg_error_response(exc: Exception) -> JSONResponse:
    if isinstance(exc, KeyError):
        return JSONResponse({"error": str(exc.args[0])}, status_code=404)
    return JSONResponse({"error": str(exc)}, status_code=402 if isinstance(exc, SpendCapExceeded) else 400)


@app.get("/api/playground/config")
def pg_config() -> dict:
    return {
        "default_mode": _pg_default_mode(),
        "key_present": _key_set(),
        "thresholds": asdict(Thresholds()),
        "spend": _pg_spend(),
        "surfaces": list(PG_SURFACES),
        "models": {slug: asdict(p) for slug, p in MODEL_PRICES.items()},
        "router_pool": ROUTER_POOL,
        "orders": {k: {"item": v["item"], "balance_cents": v["balance_cents"]}
                   for k, v in ORDER_LEDGER.items()},
        "rules": {"deny_patterns": STATIC_DENY_PATTERNS,
                  "skills": {cat: list(skills) for cat, skills in SKILL_REGISTRY.items()}},
        "coverage": COVERAGE_MATRIX,
        "judge_prompt": {
            "system": SYSTEM_PROMPT,
            "example": render_prompt({"commands": "bun test", "task": "run the tests"},
                                     {"reversible": noul("Is every command reversible?")}),
        },
    }


@app.post("/api/playground/reset")
def pg_reset() -> dict:
    SESSION.reset()
    return {"status": "reset", "spend": _pg_spend()}


@app.get("/api/playground/projection")
def pg_projection(requests_per_day: float = 10000, decisions_per_request: float = 5) -> dict:
    sb = build_scoreboard(SESSION.shadows)
    return {"compared": sb["compared"], "claims_enabled": sb["claims_enabled"],
            "projection": project(sb["averages"], requests_per_day, decisions_per_request)}


@app.post("/api/playground")
def pg_run(body: dict = Body(...)):
    try:
        return _pg_run(body)
    except (KeyError, ValueError, ConfigError, SpendCapExceeded) as exc:
        return _pg_error_response(exc)


@app.websocket("/ws/playground")
async def pg_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    loop = asyncio.get_running_loop()
    try:
        while True:
            body = await websocket.receive_json()
            queue: asyncio.Queue = asyncio.Queue()

            def emit(msg: dict) -> None:
                loop.call_soon_threadsafe(queue.put_nowait, msg)

            fut = loop.run_in_executor(None, lambda: _pg_run(body, emit))
            while True:
                getter = asyncio.ensure_future(queue.get())
                done, _ = await asyncio.wait({getter, fut}, return_when=asyncio.FIRST_COMPLETED)
                if getter in done:
                    await websocket.send_json(getter.result())
                    continue
                getter.cancel()
                break
            while not queue.empty():
                await websocket.send_json(queue.get_nowait())
            try:
                out = fut.result()
            except Exception as exc:  # noqa: BLE001 - report every failure, keep the socket alive
                msg = exc.args[0] if isinstance(exc, KeyError) and exc.args else str(exc)
                await websocket.send_json({"type": "error", "error": str(msg)})
                continue
            out.pop("events", None)        # already streamed one by one
            await websocket.send_json({"type": "done", **out})
    except WebSocketDisconnect:
        return


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (BASE_DIR / "static" / "index.html").read_text(encoding="utf-8")


if __name__ == "__main__":
    import uvicorn

    print(f"mode={_mode()} openrouter_key_set={_key_set()}")
    uvicorn.run(app, host="127.0.0.1", port=8000)
