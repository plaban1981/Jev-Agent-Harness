"""Live-mode LLM builder — ChatOpenAI pointed at OpenRouter.

Used by examples/live_demo.py and any live path that needs a frontier
generative model.  Offline code paths never import this.
"""

from __future__ import annotations

import os

from langchain_openai import ChatOpenAI

from .config import FRONTIER_MODEL, OPENROUTER_BASE_URL


def build_chat_model(
    model: str = FRONTIER_MODEL,
    api_key: str | None = None,
    **kwargs,
) -> ChatOpenAI:
    """Build a ChatOpenAI instance routed through OpenRouter."""
    key = api_key or os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError(
            "OPENROUTER_API_KEY is required for live mode. "
            "Copy .env.example to .env or export the key."
        )
    return ChatOpenAI(
        model=model,
        base_url=OPENROUTER_BASE_URL,
        api_key=key,
        **kwargs,
    )
