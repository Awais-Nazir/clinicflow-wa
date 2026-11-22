"""Unit tests for the zero-cost local embedding baseline."""

import math

import pytest

from clinicflow.embeddings import EMBEDDING_DIMENSIONS, HashingEmbedder


def cosine_similarity(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


def test_hashing_embeddings_are_deterministic_and_normalized() -> None:
    embedder = HashingEmbedder()

    first, repeated = embedder.embed(["Cancel appointment", "Cancel appointment"])

    assert first == repeated
    assert len(first) == EMBEDDING_DIMENSIONS
    norm = math.sqrt(sum(value * value for value in first))
    assert norm == pytest.approx(1.0)


def test_hashing_embeddings_reward_similar_spelling() -> None:
    embedder = HashingEmbedder()
    original, typo, unrelated = embedder.embed(
        ["appointment cancellation", "apointment cancelation", "visitor parking"]
    )

    assert cosine_similarity(original, typo) > cosine_similarity(
        original,
        unrelated,
    )
