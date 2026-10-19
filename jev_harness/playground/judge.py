"""Generative LLM judge: the "original harness" way of answering a decision.

Receives the SAME state and question definitions Jev receives and must return
the same typed JSON shapes, so the two sides are compared like for like.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple

from ..config import JUDGE_MODEL
from ..jev.client import JevAnswer, JevCallResult, parse_answer
from ..metrics import TokenUsage
from ..openrouter import OpenRouterChat, OpenRouterError
from ..pricing import estimate_tokens, list_cost
from .spend import SpendGuard

FENCE = "`" * 3

SYSTEM_PROMPT = (
    "You are a decision judge inside an agent harness. You are given a STATE and "
    "QUESTIONS. Answer every question using ONLY the state. Reply with a single "
    "JSON object and nothing else: no prose, no code fences."
)

_SHAPES = {
    "noul": '{"type":"noul","noul":<probability 0..1 that the statement is TRUE>}',
    "choice": ('{"type":"choice","choice":"<exactly one criterion>",'
               '"probabilities":{"<criterion>":<p>,...},"confidence":<p of chosen>}'),
    "score": '{"type":"score","score":<number inside the range>}',
}


class JudgeError(RuntimeError):
    """Judge call failed or returned an unusable answer."""


@dataclass
class JudgeCallResult(JevCallResult):
    model: str = ""
    cost_source: str = "usage"


def _split_image(state: Any) -> Tuple[Any, Optional[str]]:
    """Pull a base64 data-URI image out of the state; leave a placeholder."""
    if isinstance(state, dict) and str(state.get("image", "")).startswith("data:image"):
        clean = dict(state)
        image = clean["image"]
        clean["image"] = "<image attached>"
        return clean, image
    return state, None


def render_prompt(state: Any, questions: Mapping[str, Dict]) -> str:
    clean, _ = _split_image(state)
    lines = ["STATE:", json.dumps(clean, default=str, indent=2), "",
             "QUESTIONS (answer every one):"]
    for name, q in questions.items():
        typ = q.get("type", "")
        line = f'- "{name}" ({typ}): {q.get("instructions", "")}'
        if typ == "choice":
            line += f' Criteria: {q.get("criteria")}.'
        if typ == "score":
            line += f' Range: {q.get("min")} to {q.get("max")}.'
        line += f" Answer shape: {_SHAPES.get(typ, '{}')}"
        lines.append(line)
    lines += ["", 'Reply with ONE JSON object: {"<question name>": <answer>, ...}']
    return "\n".join(lines)


def _strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(rf"^{FENCE}(?:json)?\s*", "", text)
    return re.sub(rf"\s*{FENCE}$", "", text).strip()


class LLMJudge:
    def __init__(
        self,
        chat: OpenRouterChat,
        *,
        model: str = JUDGE_MODEL,
        spend: Optional[SpendGuard] = None,
        max_tokens: int = 300,
    ) -> None:
        self.chat = chat
        self.model = model
        self.spend = spend
        self.max_tokens = max_tokens

    def ask_with_usage(self, state: Any, questions: Mapping[str, Dict]) -> JudgeCallResult:
        _, image = _split_image(state)
        prompt = render_prompt(state, questions)
        user_content: Any = prompt
        if image:
            user_content = [{"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": image}}]
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_content}]
        if self.spend is not None:
            est = list_cost(self.model, estimate_tokens(prompt) + 50, self.max_tokens) or 0.0
            self.spend.check(est)
        try:
            res = self.chat.complete(self.model, messages,
                                     max_tokens=self.max_tokens, temperature=0.0)
        except OpenRouterError as exc:
            raise JudgeError(str(exc)) from exc
        if self.spend is not None:
            self.spend.add(res.cost_usd)
        answers = self._parse(res.content, questions)
        return JudgeCallResult(
            answers=answers,
            usage=TokenUsage(res.tokens_in, res.tokens_out, res.cost_usd or 0.0),
            latency_ms=res.latency_ms,
            model=res.served_model,
            cost_source=res.cost_source,
        )

    @staticmethod
    def _parse(content: str, questions: Mapping[str, Dict]) -> Dict[str, JevAnswer]:
        try:
            obj = json.loads(_strip_fences(content))
        except ValueError as exc:
            raise JudgeError(f"judge reply is not JSON: {exc}") from exc
        if not isinstance(obj, dict):
            raise JudgeError("judge reply is not a JSON object")
        answers: Dict[str, JevAnswer] = {}
        for name, q in questions.items():
            if name not in obj:
                raise JudgeError(f"judge reply missing answer for '{name}'")
            ans = parse_answer(name, obj[name])
            if ans is None:
                raise JudgeError(f"judge answer for '{name}' is malformed: {obj[name]!r}")
            if ans.type == "choice" and ans.choice_value not in (q.get("criteria") or []):
                raise JudgeError(f"judge chose '{ans.choice_value}' for '{name}', not in criteria")
            answers[name] = ans
        return answers
