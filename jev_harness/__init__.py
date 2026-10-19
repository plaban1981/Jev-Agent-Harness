"""Jev-gated Agent Harness — proof of concept.

Decision models (TypesafeAI Jev on OpenRouter) plugged into a LangChain
DeepAgents harness via four AgentMiddleware decision hooks.
"""

from .config import Settings
from .events import GateEvent, ListEventSink, NullEventSink
from .jev import FakeJevClient, JevClient, JevError

__all__ = [
    "Settings",
    "GateEvent",
    "ListEventSink",
    "NullEventSink",
    "FakeJevClient",
    "JevClient",
    "JevError",
]

__version__ = "0.1.0"
