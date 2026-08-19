"""Embedding providers.

``deterministic-hash`` is a real, reproducible embedder: hashed character
n-grams and word unigrams projected into a fixed-dimension space with sublinear
term weighting and L2 normalisation. It behaves like a lexical-semantic vector
space — near-duplicates and paraphrases with shared vocabulary land close
together — and it needs no network, no model download and no GPU, so retrieval
works identically in development, CI and air-gapped deployments.

It is not a neural embedding and does not claim to capture synonymy. Where
higher recall on paraphrase matters, configure a real embedding provider; the
retrieval layer is written against the interface, not the implementation.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

from agentic_os.core.config import get_settings
from agentic_os.core.errors import UpstreamUnavailable, ValidationError

_WORD = re.compile(r"[a-z0-9']+")


class EmbeddingProvider(Protocol):
    name: str
    dimensions: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def _tokens(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def _char_ngrams(word: str, n: int = 4) -> list[str]:
    padded = f"^{word}$"
    if len(padded) <= n:
        return [padded]
    return [padded[i : i + n] for i in range(len(padded) - n + 1)]


class DeterministicHashEmbedder:
    """Hashed n-gram embedder. Reproducible across processes and machines."""

    name = "deterministic-hash"

    def __init__(self, dimensions: int = 384) -> None:
        if dimensions <= 0:
            raise ValidationError("embedding dimensions must be positive")
        self.dimensions = dimensions

    def _bucket(self, feature: str) -> tuple[int, float]:
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        value = int.from_bytes(digest, "big")
        # Signed hashing keeps collisions from systematically inflating a bucket.
        return value % self.dimensions, 1.0 if (value >> 63) & 1 else -1.0

    def embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        words = _tokens(text)
        if not words:
            return vector

        counts: dict[str, int] = {}
        for word in words:
            counts[word] = counts.get(word, 0) + 1

        for word, count in counts.items():
            # Sublinear term frequency: a word repeated 100 times is not 100x
            # more informative than one seen once.
            weight = 1.0 + math.log(count)
            index, sign = self._bucket(f"w:{word}")
            vector[index] += sign * weight
            # Character n-grams give partial credit for morphological variants
            # ("escalator" / "escalators") and typos.
            for gram in _char_ngrams(word):
                gi, gs = self._bucket(f"g:{gram}")
                vector[gi] += gs * weight * 0.35

        # Adjacent-word bigrams carry a little order information.
        for left, right in zip(words, words[1:], strict=False):
            index, sign = self._bucket(f"b:{left}_{right}")
            vector[index] += sign * 0.5

        norm = math.sqrt(sum(v * v for v in vector))
        if norm == 0:
            return vector
        return [v / norm for v in vector]

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_one(t) for t in texts]


class HttpEmbedder:
    """OpenAI-compatible /embeddings adapter for a real embedding model."""

    name = "http"

    def __init__(self, model: str, base_url: str, api_key: str, dimensions: int) -> None:
        self.model = model
        self.base_url = base_url
        self.api_key = api_key
        self.dimensions = dimensions

    def embed(self, texts: list[str]) -> list[list[float]]:
        import httpx

        settings = get_settings()
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"
        try:
            with httpx.Client(
                base_url=self.base_url,
                headers=headers,
                timeout=settings.model_request_timeout_seconds,
            ) as client:
                response = client.post("/embeddings", json={"model": self.model, "input": texts})
                response.raise_for_status()
                data = response.json()
        except Exception as exc:  # pragma: no cover - network path
            raise UpstreamUnavailable(f"embedding provider failed: {exc}") from exc

        vectors = [item["embedding"] for item in data.get("data", [])]
        for vector in vectors:
            if len(vector) != self.dimensions:
                raise ValidationError(
                    "embedding provider returned an unexpected dimension",
                    details={"expected": self.dimensions, "received": len(vector)},
                )
        return vectors


_cached: EmbeddingProvider | None = None


def get_embedder() -> EmbeddingProvider:
    global _cached
    if _cached is not None:
        return _cached
    settings = get_settings()
    if settings.embedding_provider == "deterministic-hash":
        _cached = DeterministicHashEmbedder(settings.embedding_dimensions)
    else:
        _cached = HttpEmbedder(
            model=settings.embedding_provider,
            base_url=settings.openai_base_url or settings.local_model_base_url,
            api_key=settings.openai_api_key,
            dimensions=settings.embedding_dimensions,
        )
    return _cached


def reset_embedder_cache() -> None:
    global _cached
    _cached = None


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        raise ValidationError("cannot compare vectors of different dimensions")
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
