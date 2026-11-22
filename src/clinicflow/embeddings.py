"""Swappable, zero-cost baseline embeddings for knowledge retrieval."""

from dataclasses import dataclass
import hashlib
import math
import re
from typing import Protocol

EMBEDDING_DIMENSIONS = 384


class Embedder(Protocol):
    """The small interface required by indexing and hybrid retrieval."""

    model_name: str
    dimensions: int

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one fixed-size vector for every input text."""


@dataclass(frozen=True)
class HashingEmbedder:
    """A deterministic character n-gram baseline with no API or model cost."""

    model_name: str = "local-char-ngram-v1"
    dimensions: int = EMBEDDING_DIMENSIONS

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        normalized = re.sub(r"\s+", " ", text.casefold()).strip()
        vector = [0.0] * self.dimensions
        if not normalized:
            return vector

        padded = f"  {normalized}  "
        for index in range(len(padded) - 2):
            ngram = padded[index : index + 3].encode("utf-8")
            digest = hashlib.blake2b(ngram, digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[bucket] += sign

        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return vector
        return [value / norm for value in vector]


def validate_embeddings(
    embedder: Embedder,
    texts: list[str],
    vectors: list[list[float]],
) -> None:
    """Reject provider output that cannot fit the configured vector column."""

    if embedder.dimensions != EMBEDDING_DIMENSIONS:
        raise ValueError(
            f"embedder must use {EMBEDDING_DIMENSIONS} dimensions"
        )
    if len(vectors) != len(texts):
        raise ValueError("embedder returned the wrong number of vectors")
    if any(len(vector) != EMBEDDING_DIMENSIONS for vector in vectors):
        raise ValueError(
            f"every embedding must have {EMBEDDING_DIMENSIONS} dimensions"
        )
