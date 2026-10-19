"""HOOK 2 — Before model: Model Routing via the Jev Router.

Faithful to the OpenRouter "Routers / Jev Router" guide: we send a tiny probe
chat completion with ``model="typesafe/jev-router"`` and the ``jev-router``
plugin enabled, then read the ``model`` field of the response to learn which
model served it, and rewrite the real ``ModelRequest`` to use that model via
``request.override(model=...)``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import httpx
from langchain.agents.middleware import AgentMiddleware

from ..config import OPENROUTER_BASE_URL
from ..events import HOOK2_ROUTER, EventSink, GateEvent, get_sink
from ..jev import FakeJevClient, JevClient
from ..openrouter import OpenRouterChat


VALID_TIERS = ("low", "medium", "high")


@dataclass
class RouterDecision:
    chosen_model: str
    cost_tier: str
    reason: str
    probe_usage: Dict[str, Any] = field(default_factory=dict)
    # --- measured by JevRouter (left at defaults for FakeRouter) -------------
    latency_ms: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: Optional[float] = None
    cost_source: str = ""
    route_meta: Dict[str, Any] = field(default_factory=dict)

    def measured_detail(self) -> Dict[str, Any]:
        """Event-detail fields; empty for routers that did not measure."""
        if not self.cost_source:
            return {}
        return {
            "latency_ms": self.latency_ms,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "cost_usd": self.cost_usd,
            "cost_source": self.cost_source,
            "route_meta": self.route_meta,
        }


class JevRouter:
    """Live router: probes OpenRouter chat completions with the jev-router plugin.

    The docs (openrouter.ai/docs/guides/routing/routers/jev-router) say Jev
    *selects* the model and the selected model *serves* the completion; the
    response ``model`` field names it. ``X-OpenRouter-Metadata: enabled`` adds
    ``openrouter_metadata`` with the router's reason and candidate estimates.
    """

    def __init__(
        self,
        api_key: str,
        *,
        cost_tier: str = "medium",
        base_url: str = OPENROUTER_BASE_URL,
        timeout: float = 8.0,
        models: Optional[List[str]] = None,
        excluded_models: Optional[List[str]] = None,
        want_metadata: bool = False,
        chat: Optional[OpenRouterChat] = None,
    ) -> None:
        if cost_tier not in VALID_TIERS:
            raise ValueError(f"cost_tier must be one of {VALID_TIERS}, got {cost_tier!r}")
        self.api_key = api_key
        self.cost_tier = cost_tier
        self.base_url = base_url
        self.timeout = timeout
        self.models = list(models or [])
        self.excluded_models = list(excluded_models or [])
        self.want_metadata = want_metadata
        self._chat = chat or OpenRouterChat(api_key, base_url=base_url, timeout=timeout)

    def plugin(self) -> Dict[str, Any]:
        plugin: Dict[str, Any] = {"id": "jev-router", "cost_tier": self.cost_tier}
        if self.models:
            plugin["models"] = self.models
        if self.excluded_models:
            plugin["excluded_models"] = self.excluded_models
        return plugin

    def resolve(self, probe_messages: List[Dict[str, str]]) -> RouterDecision:
        res = self._chat.complete(
            "typesafe/jev-router", probe_messages,
            max_tokens=8, temperature=None,
            extra_body={"plugins": [self.plugin()]},
            want_metadata=self.want_metadata,
        )
        if not res.served_model or res.served_model == "typesafe/jev-router":
            raise RuntimeError("jev-router response missing 'model'")
        meta = res.router_meta()
        return RouterDecision(
            chosen_model=res.served_model,
            cost_tier=self.cost_tier,
            reason=str(meta.get("reason") or "served by jev-router probe"),
            probe_usage=res.raw_usage,
            latency_ms=res.latency_ms,
            tokens_in=res.tokens_in,
            tokens_out=res.tokens_out,
            cost_usd=res.cost_usd,
            cost_source=res.cost_source,
            route_meta=meta,
        )


class FakeRouter:
    """Offline router mapping scripted request signatures -> model slugs."""

    def __init__(
        self,
        mapping: Optional[Dict[str, str]] = None,
        default_model: str = "openai/gpt-4o-mini",
        classifier: Optional[Callable[[str], str]] = None,
    ) -> None:
        self.mapping = dict(mapping or {})
        self.default_model = default_model
        self.classifier = classifier or (lambda text: "default")
        self.probes: List[List[Dict[str, str]]] = []

    def resolve(self, probe_messages: List[Dict[str, str]]) -> RouterDecision:
        self.probes.append(probe_messages)
        text = " ".join(m.get("content", "") for m in probe_messages).lower()
        sig = self.classifier(text)
        model = self.mapping.get(sig, self.default_model)
        return RouterDecision(
            chosen_model=model,
            cost_tier="offline",
            reason=f"fake router signature '{sig}'",
        )


def build_probe(messages: List[Any]) -> List[Dict[str, str]]:
    """Lightweight probe derived from the current request."""
    last_user = ""
    for msg in reversed(messages):
        if getattr(msg, "type", None) == "human":
            last_user = str(msg.content)
            break
    snippet = last_user[:200] if last_user else "(empty request)"
    return [
        {"role": "system", "content": "You are a routing probe. Reply with ok."},
        {"role": "user", "content": f"Request summary: {snippet}\n\nReply with ok"},
    ]


class RouterMiddleware(AgentMiddleware):
    """Rewrites the ModelRequest to the router-resolved model before the call."""

    def __init__(
        self,
        router: JevRouter | FakeRouter,
        model_factory: Optional[Callable[[str], Any]] = None,
        event_sink: Optional[EventSink] = None,
        pin_model: bool = True,
    ) -> None:
        self.router = router
        self.model_factory = model_factory
        self.sink = get_sink(event_sink)
        # The router draws a model per probe, so without pinning one run can
        # switch models mid-flight (and land on a costly one). Pinned: route
        # once, reuse that decision for every later call of this middleware.
        self.pin_model = pin_model
        self._pinned: Optional[RouterDecision] = None

    def wrap_model_call(self, request, handler):
        pinned = self.pin_model and self._pinned is not None
        if pinned:
            first = self._pinned
            decision = RouterDecision(
                chosen_model=first.chosen_model,
                cost_tier=first.cost_tier,
                reason="pinned: reusing this run's first routing decision",
            )
        else:
            decision = self.router.resolve(build_probe(request.messages))
            if self.pin_model:
                self._pinned = decision
        new_request = request
        if self.model_factory is not None:
            resolved = self.model_factory(decision.chosen_model)
            if resolved is not None:
                new_request = request.override(model=resolved)
        detail: Dict[str, Any] = {
            "chosen_model": decision.chosen_model,
            "cost_tier": decision.cost_tier,
            "reason": decision.reason,
        }
        if pinned:
            detail["pinned"] = True      # no probe was sent, so no probe cost
        if decision.probe_usage:
            detail["probe_usage"] = decision.probe_usage
        detail.update(decision.measured_detail())
        self.sink.emit(
            GateEvent(
                hook=HOOK2_ROUTER,
                stage="route",
                decision=decision.chosen_model,
                detail=detail,
            )
        )
        return handler(new_request)


def jev_router_direct_demo(api_key: str, user_text: str) -> Dict[str, Any]:
    """Demo the alternate pattern: use ``typesafe/jev-router`` as the model itself.

    The response ``model`` field tells you which downstream model served it.
    """
    body = {
        "model": "typesafe/jev-router",
        "plugins": [{"id": "jev-router", "cost_tier": "medium"}],
        "messages": [{"role": "user", "content": user_text}],
    }
    with httpx.Client(timeout=8.0) as client:
        resp = client.post(
            f"{OPENROUTER_BASE_URL}/chat/completions",
            json=body,
            headers={"Authorization": f"Bearer {api_key}"},
        )
    resp.raise_for_status()
    payload = resp.json()
    return {
        "requested_model": "typesafe/jev-router",
        "served_model": payload.get("model"),
        "content": (payload.get("choices") or [{}])[0].get("message", {}).get("content"),
        "usage": payload.get("usage"),
    }
