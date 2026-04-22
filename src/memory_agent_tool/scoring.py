from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections import Counter
from typing import Any

from memory_agent_tool.logging import get_logger

logger = get_logger("scoring")


def normalize_text(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


class RecallScorer(ABC):
    @abstractmethod
    def score(self, query: str, text: str) -> float:
        ...

    @abstractmethod
    def name(self) -> str:
        ...


class KeywordOverlapScorer(RecallScorer):
    def score(self, query: str, text: str) -> float:
        query_tokens = [token for token in normalize_text(query).split() if token]
        if not query_tokens:
            return 0.0
        haystack = normalize_text(text)
        matched = sum(1 for token in query_tokens if token in haystack)
        return matched / max(len(query_tokens), 1)

    def name(self) -> str:
        return "keyword_overlap"


class TfidfScorer(RecallScorer):
    def __init__(self) -> None:
        self._doc_freq: Counter[str] = Counter()
        self._doc_count: int = 0

    def feed_corpus(self, documents: list[str]) -> None:
        for doc in documents:
            tokens = set(normalize_text(doc).split())
            for token in tokens:
                self._doc_freq[token] += 1
            self._doc_count += 1

    def score(self, query: str, text: str) -> float:
        if self._doc_count == 0:
            return KeywordOverlapScorer().score(query, text)
        query_tokens = normalize_text(query).split()
        text_tokens = normalize_text(text).split()
        if not query_tokens or not text_tokens:
            return 0.0
        text_counter = Counter(text_tokens)
        score = 0.0
        for token in query_tokens:
            if token not in text_counter:
                continue
            tf = text_counter[token] / len(text_tokens)
            df = self._doc_freq.get(token, 0)
            idf = math.log((self._doc_count + 1) / (df + 1)) + 1.0
            score += tf * idf
        max_possible = sum(
            math.log((self._doc_count + 1) / (self._doc_freq.get(t, 0) + 1)) + 1.0
            for t in query_tokens
            if t in text_counter
        )
        return score / max_possible if max_possible > 0 else 0.0

    def name(self) -> str:
        return "tfidf"


class SemanticScorer(RecallScorer):
    def __init__(self, fallback: RecallScorer | None = None) -> None:
        self._fallback = fallback or KeywordOverlapScorer()
        self._embedder: Any = None
        self._embedder_available: bool | None = None

    def _try_load_embedder(self) -> bool:
        if self._embedder_available is not None:
            return self._embedder_available
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore[import-untyped]

            self._embedder = SentenceTransformer("all-MiniLM-L6-v2")
            self._embedder_available = True
            logger.info("sentence_transformers loaded for semantic scoring")
        except ImportError:
            self._embedder_available = False
            logger.info("sentence_transformers not available, falling back to keyword scoring")
        except Exception as exc:
            self._embedder_available = False
            logger.warning("failed to load sentence_transformers: %s", exc)
        return self._embedder_available

    def score(self, query: str, text: str) -> float:
        if not self._try_load_embedder():
            return self._fallback.score(query, text)
        try:
            import numpy as np  # type: ignore[import-untyped]

            q_emb = self._embedder.encode([query], normalize_embeddings=True)
            t_emb = self._embedder.encode([text], normalize_embeddings=True)
            similarity = float(np.dot(q_emb[0], t_emb[0]))
            return max(0.0, min(1.0, (similarity + 1.0) / 2.0))
        except Exception as exc:
            logger.warning("semantic scoring failed, fallback: %s", exc)
            return self._fallback.score(query, text)

    def name(self) -> str:
        return "semantic" if self._embedder_available else self._fallback.name()


class CompositeScorer(RecallScorer):
    def __init__(
        self,
        scorers: list[tuple[RecallScorer, float]] | None = None,
    ) -> None:
        if scorers is None:
            self._scorers: list[tuple[RecallScorer, float]] = [
                (KeywordOverlapScorer(), 0.6),
                (TfidfScorer(), 0.4),
            ]
        else:
            self._scorers = scorers
        total = sum(w for _, w in self._scorers)
        if total > 0:
            self._scorers = [(s, w / total) for s, w in self._scorers]

    def score(self, query: str, text: str) -> float:
        total = 0.0
        for scorer, weight in self._scorers:
            total += scorer.score(query, text) * weight
        return round(total, 4)

    def name(self) -> str:
        parts = [f"{s.name()}@{w:.2f}" for s, w in self._scorers]
        return f"composite({'+'.join(parts)})"


def create_scorer(strategy: str = "composite") -> RecallScorer:
    if strategy == "keyword":
        return KeywordOverlapScorer()
    if strategy == "tfidf":
        return TfidfScorer()
    if strategy == "semantic":
        return SemanticScorer(fallback=CompositeScorer())
    return CompositeScorer()
