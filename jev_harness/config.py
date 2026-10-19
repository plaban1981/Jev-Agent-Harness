"""Central configuration for the Jev-gated Agent Harness POC.

All decision thresholds, model slugs and runtime flags live here so that
tests, examples and the UI all share one source of truth.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def load_env_file(path: str | Path | None = None) -> None:
    """Minimal .env loader (no external dependency).

    Reads KEY=VALUE lines from the project-root .env into os.environ without
    overriding variables that are already set. Called at import time below so
    the UI server, examples and tests all see the key.
    """
    env_path = Path(path) if path else Path(__file__).resolve().parent.parent / ".env"
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except OSError:
        pass  # no .env present — live mode will report the key as missing


load_env_file()


# --- Model slugs -------------------------------------------------------------
JEV_MODEL = "typesafe/jev-1.13"
JEV_ROUTER_MODEL = "typesafe/jev-router"
FRONTIER_MODEL = "anthropic/claude-sonnet-5.5"
CHEAP_MODEL = "openai/gpt-4o-mini"
DRAFT_MODEL = "anthropic/claude-haiku-5.5"   # cheap drafter for the cascade lab
JUDGE_MODEL = FRONTIER_MODEL                 # generative baseline judge


@dataclass(frozen=True)
class ModelPrice:
    """USD per 1M tokens plus context window (user-supplied OpenRouter figures)."""

    input_per_m: float
    output_per_m: float
    context_tokens: int


MODEL_PRICES: dict[str, ModelPrice] = {
    "anthropic/claude-sonnet-5.5": ModelPrice(4.0, 20.0, 1_000_000),
    "anthropic/claude-haiku-5.5": ModelPrice(0.10, 0.50, 1_000_000),
    "anthropic/claude-opus-5.5": ModelPrice(4.0, 20.0, 1_000_000),
    "deepseek/deepseek-v4-pro-0813": ModelPrice(0.12, 8.0, 1_000_000),
    "deepseek/deepseek-chat-v3-0324": ModelPrice(0.25, 1.0, 164_000),
    "google/gemini-2.5-flash": ModelPrice(0.30, 2.50, 1_000_000),
}
ROUTER_POOL = list(MODEL_PRICES)             # default `models` list for jev-router

PLAYGROUND_SPEND_CAP_USD = float(os.environ.get("PLAYGROUND_SPEND_CAP_USD", "0.50"))

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"

# --- Risk gate thresholds (cookbook: auto-approve permission prompts) --------
APPROVE_AT = 0.9
BLOCK_AT = 0.1

# --- Quality gate thresholds (verified-cascade cookbook) ---------------------
ACCEPT_CONFIDENCE = 0.8

# --- RAG re-ranking -----------------------------------------------------------
RAG_TOP_K = 3
RAG_THRESHOLD = 2.0

# --- Jev image limits (multimodal decisions) ---------------------------------
IMG_MIN_SIDE = 256
IMG_MAX_SIDE = 1568
IMG_MIN_PIXELS = 78400        # 280x280
IMG_MAX_PIXELS = 2458624      # ~1568x1568
IMG_FORMATS = ("png", "jpeg", "jpg")

# --- Default gated tool set (Hook 3) ------------------------------------------
DEFAULT_GATED_TOOLS = frozenset({"run_shell_command", "write_file"})

# --- Jev pricing / sizing (OpenRouter cookbook captured runs) -----------------
# Estimated defaults from captured Decisions API runs; update if OpenRouter
# publishes different numbers.
JEV_INPUT_TOKENS = 400          # typical decision-state input size
JEV_OUTPUT_TOKENS = 40          # typed answers are tiny
JEV_COST_PER_CALL = 0.0000168   # ~$16.8 per 1M decision calls
JEV_LATENCY_MS = 18.0           # observed decision latency (fake uses this)


@dataclass
class BaselineLLMConfig:
    """What the ORIGINAL harness would have spent per gate check.

    The pre-Jev harness ran a full generative call (claude-sonnet-5.5) for
    every control-flow decision. These numbers are CONFIGURABLE ESTIMATES —
    update ``price_*`` to current OpenRouter pricing before quoting real money.
    """

    model: str = FRONTIER_MODEL
    # Priced from MODEL_PRICES (user-supplied OpenRouter figures).
    price_input_per_token: float = MODEL_PRICES[FRONTIER_MODEL].input_per_m / 1_000_000
    price_output_per_token: float = MODEL_PRICES[FRONTIER_MODEL].output_per_m / 1_000_000
    scaffolding_tokens: int = 250   # fixed instruction scaffolding per check
    output_tokens: int = 80         # JSON-ish reasoning output per check
    latency_ms: float = 2000.0      # one generative round-trip
    chars_per_token: float = 4.0    # len(serialized state)/4 token estimate
    default_input_tokens: int = 700  # fallback when no state is available


@dataclass
class Settings:
    """Runtime settings. ``offline=True`` is the default everywhere."""

    offline: bool = True
    openrouter_api_key: str | None = None
    jev_model: str = JEV_MODEL
    router_model: str = JEV_ROUTER_MODEL
    frontier_model: str = FRONTIER_MODEL
    cheap_model: str = CHEAP_MODEL
    timeout: float = 8.0
    approve_at: float = APPROVE_AT
    block_at: float = BLOCK_AT
    accept_confidence: float = ACCEPT_CONFIDENCE
    rag_top_k: int = RAG_TOP_K
    rag_threshold: float = RAG_THRESHOLD
    gated_tools: frozenset = DEFAULT_GATED_TOOLS
    project: str = "jev_harness_poc"
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "Settings":
        api_key = os.environ.get("OPENROUTER_API_KEY") or None
        offline = os.environ.get("JEV_HARNESS_OFFLINE", "1") not in ("0", "false")
        if api_key and os.environ.get("JEV_HARNESS_OFFLINE") in ("0", "false"):
            offline = False
        return cls(offline=offline, openrouter_api_key=api_key)
