# Jev-Agent-Harness


<img width="1621" height="872" alt="image" src="https://github.com/user-attachments/assets/cc2c917e-a8e6-4b93-b654-2c89b9b5dc5e" />

Jev-Agent-Harness


<img width="1387" height="352" alt="image" src="https://github.com/user-attachments/assets/df0240df-4958-4fb1-acde-cc657bb98178" />


# Jev-gated Agent Harness — Proof of Concept

A complete proof-of-concept showing how **TypesafeAI Jev decision models** (on
OpenRouter) plug into a **LangChain DeepAgents** agent harness. The harness
intercepts its agent loop at the four "decision hooks" from the source lesson
(`docs/Jev_Agent_Harness.docx`) and routes each hook's control-flow choice
through a fast, typed decision model instead of a heavy generative LLM call.

Everything runs **offline by default** (scripted fakes, zero network). Live
mode works by exporting `OPENROUTER_API_KEY`.

```
┌───────────────────────────┐
│      User Request         │
└────────────┬──────────────┘
             ▼
┌───────────────────────────┐   ┌──────────────────────────────┐
│ HOOK 1: Before prompt     │──▶│ Progressive Disclosure       │
│ hooks/skills.py           │   │ (two-pass Jev choice cascade)│
└────────────┬──────────────┘   └──────────────────────────────┘
             ▼
┌───────────────────────────┐   ┌──────────────────────────────┐
│ HOOK 2: Before model      │──▶│ Model Routing                │
│ hooks/router.py           │   │ (typesafe/jev-router probe)  │
└────────────┬──────────────┘   └──────────────────────────────┘
             ▼
      ┌────────────┐
      │ Model call │  ← generative LLM (claude-sonnet-5.5 in live mode)
      └─────┬──────┘
            ▼
┌───────────────────────────┐   ┌──────────────────────────────┐
│ HOOK 3: Before tool run   │──▶│ Risk Gating                  │
│ hooks/risk_gate.py        │   │ (static deny list + Jev nouls)│
│ hooks/refund_gate.py      │   │ (refund variant, cookbook)   │
└────────────┬──────────────┘   └──────────────────────────────┘
             ▼
       ┌───────────┐
       │ Execute   │  ← only if the gate APPROVES; BLOCK/REVIEW never run
       └─────┬─────┘
             ▼
┌───────────────────────────┐   ┌──────────────────────────────┐
│ HOOK 4: After result      │──▶│ Quality gate + Verified      │
│ hooks/quality.py          │   │ Cascade (Jev choice judge)   │
│ hooks/cascade.py          │   └──────────────────────────────┘
└───────────────────────────┘
```

## The four decision hooks → modules → middleware

| Docx lesson concept | Module | LangChain middleware hook | Jev question type |
|---|---|---|---|
| Hook 1 — Before prompt: Progressive Disclosure (filter & load skills) | `jev_harness/hooks/skills.py` | `SkillsMiddleware.wrap_model_call` (rewrites `system_message` via `request.override`) | `choice` ×2 (category → skill) |
| Hook 2 — Before model: Model Routing (fast vs reasoning model) | `jev_harness/hooks/router.py` | `RouterMiddleware.wrap_model_call` (rewrites `request.model`) | `typesafe/jev-router` plugin probe |
| Hook 3 — Before tool run: Risk Gating / safety verification | `jev_harness/hooks/risk_gate.py` (+ `refund_gate.py` variant) | `RiskGateMiddleware.wrap_tool_call` / `RefundGateMiddleware.wrap_tool_call` | `noul` (reversible, serves_task / customer_asked, right_order, policy_covers) |
| Hook 4 — After result: quality check, success or loop | `jev_harness/hooks/quality.py` (+ `cascade.py` demo) | `QualityMiddleware.after_model` (`Command(goto="model")` retry) | `choice` (supported / unsupported / declined) |

Supporting concepts: **RAG re-ranking** (`jev_harness/rag/` — keyword
retrieval + Jev `score` rubric, prune to top-K ≥ 2.0) and **multimodal
decisions** (`jev_harness/multimodal.py` — image validation/resizing to Jev
limits, then noul + choice on the base64 image).

## Layout

```
jev_harness_poc/
├── jev_harness/
│   ├── agent.py            # build_jev_agent() — DeepAgents + all 4 hooks + StubChatModel
│   ├── config.py           # thresholds (APPROVE_AT=0.9, BLOCK_AT=0.1, ACCEPT_CONFIDENCE=0.8), Settings
│   ├── demo.py             # run_scenario(name, mode) — powers examples AND the UI
│   ├── events.py           # GateEvent + EventSink protocol (UI streams these)
│   ├── llm.py              # ChatOpenAI(model="anthropic/claude-sonnet-5.5", OpenRouter) builder
│   ├── metrics.py          # SavingsTracker: measured Jev usage vs full-LLM baseline
│   ├── advisor.py          # threshold-tuning advice + auto-resolution stats
│   ├── multimodal.py       # image validate/resize → Jev noul + color choice
│   ├── jev/                # client.py (fail-closed, 8s timeout), fake.py, questions.py
│   ├── hooks/              # skills.py, router.py, risk_gate.py, refund_gate.py, quality.py, cascade.py
│   └── rag/                # corpus.py, retrieve.py (TF-IDF), rerank.py (Jev score)
├── ui/                     # FastAPI + WebSocket control room (light, colorful)
│   ├── app.py
│   └── static/index.html
├── examples/
│   ├── offline_demo.py     # all 7 scenarios, control-room JSON
│   └── live_demo.py        # needs OPENROUTER_API_KEY
├── tests/                  # 8 offline test modules, 66 tests, no network
├── docs/                   # source lesson (docx + extracted txt)
├── README.md / POC.md / requirements.txt / .env.example
```

## Run it

```bash
cd jev_harness_poc
python -m venv .venv && source .venv/bin/activate    # or your preferred venv
pip install -r requirements.txt

# 1. Offline tests (no network, no API key)
python -m pytest tests/ -q

# 2. Offline demo (scripted fakes)
python examples/offline_demo.py

# 3. Control-room UI
python -m uvicorn ui.app:app --port 8000
# open http://127.0.0.1:8000  — click a scenario, watch gate events stream over /ws

# 4. Live mode (optional)
export OPENROUTER_API_KEY=sk-or-...        # Git Bash; Windows: setx or $env:
export JEV_HARNESS_OFFLINE=0
python examples/live_demo.py permissions
```

## Key semantics (from the cookbooks)

- **Risk gate** — static deny list (sudo, `rm -rf`, `git push`,
  `terraform destroy`, credential paths, `$(...)`/backticks…) short-circuits
  *without* a Jev call. Then Jev `noul` questions `reversible` and
  `serves_task` (only `reversible` when no task text). All scores ≥ 0.9 →
  approve; any ≤ 0.1 → block; in-between **or any Jev/network failure →
  REVIEW (fail-closed)**. Blocked/reviewed tool calls never execute.
- **Refund gate** — deterministic pre-checks first (order on ticket, amount ≤
  remaining balance — the 8000c-on-6800c case blocks *without* Jev), then
  three nouls. Cookbook fixtures: kettle/ORD-1234/1200c → approve;
  espresso/ORD-7781/40000c goodwill → block; crushed box/ORD-5520/8900c →
  review; 8000c on 6800c → arithmetic block.
- **Quality gate / cascade** — `choice` judge with the cookbook's exact
  supported/unsupported/declined criteria; accept at confidence ≥ 0.8.
  Cascade: cheap draft → verify → send / declined→handoff / else escalate to
  `anthropic/claude-sonnet-5.5` and re-verify; double failure → handoff.
- **Routing** — probe chat completion with `model="typesafe/jev-router"` +
  plugin `[{"id":"jev-router","cost_tier":"..."}]`; the response `model`
  field resolves the real model; the `ModelRequest` is rewritten via
  `request.override(model=...)`.

See `POC.md` for acceptance criteria, the runbook, hardening checklist, and
industry-benefit analysis.

## Savings methodology (Jev vs the original full-LLM harness)

The original harness answered every control-flow question with a full
generative call to `anthropic/claude-sonnet-5.5`. `jev_harness/metrics.py`
measures every real Jev Decisions call and derives the delta:

| Quantity | Jev side (measured) | Baseline side (estimated) |
|---|---|---|
| Input tokens | response `usage` (or `len(serialized)/4`) | `len(state+questions)/4` + 250 scaffolding, floored at 700 (a generative call drags the system prompt + tool schemas along) |
| Output tokens | response `usage` (or 40/question-set) | 80 (JSON reasoning) |
| Latency | measured around the HTTP call (offline fake: fixed 18 ms) | 2000 ms per generative round-trip |
| Price | response `usage.cost` (or $0.0000168/call, per captured cookbook runs) | $4/M input + $20/M output (claude-sonnet-5.5; full catalog in `config.py:MODEL_PRICES`) |

Static deny-list catches cost *zero* here but count as full baseline calls in
`baseline_comparison()` — the original harness had no such short-circuit.
Offline runs use canned usage (400 in / 40 out / $0.0000168 / 18 ms), so
savings percentages are estimates; live runs measure real usage. The
distinction is "measured vs estimated" per field, and the advisor
(`jev_harness/advisor.py`) turns run logs into tuning advice: routine mid-band
REVIEWs suggest lowering `APPROVE_AT` toward 0.8; thin approvals (lowest score
in `[0.90, 0.93]`) in runs that also produced downstream BLOCK/HANDOFF
outcomes suggest raising the threshold or extending the static risk list; and
the auto-resolution rate (decisions settled with no human/LLM) is always
summarized.

## Playground — interactive, measured decisions

Open the control room and use the **Playground** card (first card). Paste your own
input and watch every decision system decide, with **real Jev and a real generative
LLM judge answering the same question in parallel**; latency, tokens, cost and
agreement are measured on both sides.

| Tab | Decision system |
|---|---|
| Agent Run | the real DeepAgents harness with all middleware hooks |
| Skills | Hook 1 progressive disclosure (two-pass cascade) |
| Router | Hook 2: `typesafe/jev-router` vs the pinned frontier model (end to end) |
| Risk gate | Hook 3a: static deny list, then Jev nouls |
| Refund gate | Hook 3b: pre-checks, then three nouls |
| Quality gate | Hook 4a: supported / unsupported / declined |
| Cascade | Hook 4b: cheap draft, verify, escalate, vs a real frontier reference |
| RAG re-rank | per-chunk relevance scores, pruning |
| Multimodal | image resize to Jev limits, noul + choice |

Modes: **live** (needs `OPENROUTER_API_KEY`; spends real money, capped by
`PLAYGROUND_SPEND_CAP_USD`, default `0.50`) and **simulated** (offline heuristics; every
number is labelled `simulated` and no savings claim is made). Router and cascade
savings are measured by really running the path not taken; with the reference-run
toggle off they show "not measured". No aggregate claim is shown below 5 compared
decisions. Opt-in live tests: `pytest tests/test_playground_live.py -m live`.
