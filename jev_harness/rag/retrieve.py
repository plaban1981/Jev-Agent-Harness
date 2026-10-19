"""Broad keyword retrieval (BM25-ish, no embeddings — fully offline)."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List

from .corpus import CORPUS

_TOKEN = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> List[str]:
    return _TOKEN.findall(text.lower())


@dataclass
class ScoredChunk:
    doc_id: str
    title: str
    text: str
    retrieval_score: float


class KeywordRetriever:
    """Tiny TF-IDF scorer over the in-memory corpus."""

    def __init__(self, corpus: Iterable[Dict[str, str]] = CORPUS) -> None:
        self.corpus = list(corpus)
        self._docs = [
            {"id": c["id"], "title": c["title"], "text": c["text"],
             "tokens": tokenize(c["title"] + " " + c["text"])}
            for c in self.corpus
        ]
        df: Dict[str, int] = {}
        for d in self._docs:
            for t in set(d["tokens"]):
                df[t] = df.get(t, 0) + 1
        n = len(self._docs)
        self._idf = {t: math.log(1 + n / c) for t, c in df.items()}

    def retrieve(self, query: str, top_n: int | None = None) -> List[ScoredChunk]:
        qtokens = tokenize(query)
        scored: List[ScoredChunk] = []
        for d in self._docs:
            tf: Dict[str, int] = {}
            for t in d["tokens"]:
                tf[t] = tf.get(t, 0) + 1
            score = 0.0
            for t in qtokens:
                if t in tf:
                    score += (1 + math.log(tf[t])) * self._idf.get(t, 0.0)
            if score > 0:
                scored.append(ScoredChunk(d["id"], d["title"], d["text"], score))
        scored.sort(key=lambda c: c.retrieval_score, reverse=True)
        return scored if top_n is None else scored[:top_n]
