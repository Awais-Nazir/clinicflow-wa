"""PostgreSQL integration tests for tenant-isolated knowledge retrieval."""

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine, text

from clinicflow.embeddings import EMBEDDING_DIMENSIONS
from clinicflow.knowledge import (
    hybrid_search_kb,
    index_knowledge_embeddings,
    search_kb,
)
from clinicflow.tenants import seed_demo_clinic

DATABASE_URL = os.getenv("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    DATABASE_URL is None,
    reason="DATABASE_URL is required for PostgreSQL integration tests",
)


@pytest.fixture
def database_engine() -> Iterator[Engine]:
    if DATABASE_URL is None:
        pytest.skip("DATABASE_URL is required")

    engine = create_engine(DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE tenants RESTART IDENTITY CASCADE"))

    yield engine
    engine.dispose()


def test_demo_seed_is_idempotent_and_faq_is_searchable(
    database_engine: Engine,
) -> None:
    tenant = seed_demo_clinic("phone-456")
    seed_demo_clinic("phone-456")

    results = search_kb(tenant.id, "cancel appointment")

    assert len(results) == 1
    assert results[0].source_key == "appointment-changes"
    assert results[0].title == "Cancellation and rescheduling"
    assert results[0].score > 0
    with database_engine.connect() as connection:
        chunk_count = connection.execute(
            text("SELECT count(*) FROM knowledge_chunks")
        ).scalar_one()

    assert chunk_count == 4


def test_search_is_tenant_isolated(database_engine: Engine) -> None:
    first_tenant = seed_demo_clinic("phone-456")
    with database_engine.begin() as connection:
        second_tenant_id = connection.execute(
            text(
                """
                INSERT INTO tenants (slug, name, timezone)
                VALUES ('second-demo', 'Second Demo Clinic', 'Asia/Karachi')
                RETURNING id
                """
            )
        ).scalar_one()
        connection.execute(
            text(
                """
                INSERT INTO knowledge_chunks (
                    tenant_id, source_key, title, content, language, chunk_index
                )
                VALUES (
                    :tenant_id, 'private-faq', 'Private FAQ',
                    'secretword belongs only to the second clinic', 'en', 0
                )
                """
            ),
            {"tenant_id": second_tenant_id},
        )

    assert search_kb(first_tenant.id, "secretword") == []
    second_results = search_kb(second_tenant_id, "secretword")
    assert len(second_results) == 1
    assert second_results[0].source_key == "private-faq"


def test_blank_query_returns_no_results(database_engine: Engine) -> None:
    tenant = seed_demo_clinic("phone-456")

    assert search_kb(tenant.id, "   ") == []


def test_search_limit_is_bounded(database_engine: Engine) -> None:
    tenant = seed_demo_clinic("phone-456")

    with pytest.raises(ValueError, match="between 1 and 10"):
        search_kb(tenant.id, "clinic", limit=11)


class ControlledEmbedder:
    model_name = "controlled-test-v1"
    dimensions = EMBEDDING_DIMENSIONS

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        lowered = text.casefold()
        vector = [0.0] * self.dimensions
        vector[-1] = 0.01
        if any(
            phrase in lowered
            for phrase in ("cancel", "reschedule", "move my visit")
        ):
            vector[0] = 1.0
        elif any(word in lowered for word in ("cash", "payment", "debit")):
            vector[1] = 1.0
        elif "parking" in lowered:
            vector[2] = 1.0
        else:
            vector[3] = 1.0
        return vector


def test_hybrid_search_is_tenant_isolated(database_engine: Engine) -> None:
    first_tenant = seed_demo_clinic("phone-456")
    with database_engine.begin() as connection:
        second_tenant_id = connection.execute(
            text(
                """
                INSERT INTO tenants (slug, name, timezone)
                VALUES ('vector-demo', 'Vector Demo Clinic', 'Asia/Karachi')
                RETURNING id
                """
            )
        ).scalar_one()
        connection.execute(
            text(
                """
                INSERT INTO knowledge_chunks (
                    tenant_id, source_key, title, content, language, chunk_index
                )
                VALUES (
                    :tenant_id, 'vector-private', 'Vector Private',
                    'secret semantic policy', 'en', 0
                )
                """
            ),
            {"tenant_id": second_tenant_id},
        )

    embedder = ControlledEmbedder()
    assert index_knowledge_embeddings(second_tenant_id, embedder) == 1
    assert hybrid_search_kb(
        first_tenant.id,
        "secret semantic policy",
        embedder=embedder,
        min_similarity=0.5,
    ) == []
    second_results = hybrid_search_kb(
        second_tenant_id,
        "secret semantic policy",
        embedder=embedder,
        min_similarity=0.5,
    )
    assert second_results[0].source_key == "vector-private"


def test_hybrid_search_finds_a_vector_only_match(database_engine: Engine) -> None:
    tenant = seed_demo_clinic("phone-456")
    embedder = ControlledEmbedder()
    assert index_knowledge_embeddings(tenant.id, embedder) == 4
    assert search_kb(tenant.id, "move my visit") == []

    results = hybrid_search_kb(
        tenant.id,
        "move my visit",
        embedder=embedder,
        min_similarity=0.5,
    )

    assert results[0].source_key == "appointment-changes"


def test_default_hybrid_search_uses_seeded_local_embeddings(
    database_engine: Engine,
) -> None:
    tenant = seed_demo_clinic("phone-456")

    results = hybrid_search_kb(tenant.id, "cancel appointment")

    assert results[0].source_key == "appointment-changes"
