"""Tenant-isolated lexical retrieval for clinic FAQ and policy chunks."""

from dataclasses import dataclass
import math

from sqlalchemy import func, select, update

from clinicflow.database import session_scope
from clinicflow.embeddings import (
    Embedder,
    HashingEmbedder,
    validate_embeddings,
)
from clinicflow.models import KnowledgeChunk

RRF_K = 60


@dataclass(frozen=True)
class KnowledgeResult:
    chunk_id: int
    source_key: str
    title: str
    content: str
    language: str
    score: float


def search_kb(
    tenant_id: int,
    query: str,
    *,
    limit: int = 5,
) -> list[KnowledgeResult]:
    """Search one tenant's knowledge chunks using PostgreSQL full-text rank."""

    normalized_query = query.strip()
    if not normalized_query:
        return []
    if not 1 <= limit <= 10:
        raise ValueError("limit must be between 1 and 10")

    ts_query = func.plainto_tsquery("simple", normalized_query)
    rank = func.ts_rank_cd(KnowledgeChunk.search_vector, ts_query)
    statement = (
        select(
            KnowledgeChunk.id,
            KnowledgeChunk.source_key,
            KnowledgeChunk.title,
            KnowledgeChunk.content,
            KnowledgeChunk.language,
            rank.label("score"),
        )
        .where(
            KnowledgeChunk.tenant_id == tenant_id,
            KnowledgeChunk.search_vector.op("@@")(ts_query),
        )
        .order_by(rank.desc(), KnowledgeChunk.id)
        .limit(limit)
    )

    with session_scope() as session:
        rows = session.execute(statement).all()

    return [
        KnowledgeResult(
            chunk_id=row.id,
            source_key=row.source_key,
            title=row.title,
            content=row.content,
            language=row.language,
            score=float(row.score),
        )
        for row in rows
    ]


def index_knowledge_embeddings(
    tenant_id: int,
    embedder: Embedder | None = None,
) -> int:
    """Generate and store embeddings for all chunks belonging to one tenant."""

    active_embedder = embedder or HashingEmbedder()
    with session_scope() as session:
        rows = session.execute(
            select(KnowledgeChunk.id, KnowledgeChunk.content)
            .where(KnowledgeChunk.tenant_id == tenant_id)
            .order_by(KnowledgeChunk.id)
        ).all()
        texts = [row.content for row in rows]
        vectors = active_embedder.embed(texts)
        validate_embeddings(active_embedder, texts, vectors)

        for row, vector in zip(rows, vectors, strict=True):
            session.execute(
                update(KnowledgeChunk)
                .where(
                    KnowledgeChunk.id == row.id,
                    KnowledgeChunk.tenant_id == tenant_id,
                )
                .values(
                    embedding=vector,
                    embedding_model=active_embedder.model_name,
                )
            )

    return len(rows)


def hybrid_search_kb(
    tenant_id: int,
    query: str,
    *,
    embedder: Embedder | None = None,
    limit: int = 5,
    min_similarity: float = 0.1,
) -> list[KnowledgeResult]:
    """Fuse lexical and cosine-similarity ranks for one tenant."""

    normalized_query = query.strip()
    if not normalized_query:
        return []
    if not 1 <= limit <= 10:
        raise ValueError("limit must be between 1 and 10")
    if not -1.0 <= min_similarity <= 1.0:
        raise ValueError("min_similarity must be between -1 and 1")

    active_embedder = embedder or HashingEmbedder()
    query_vectors = active_embedder.embed([normalized_query])
    validate_embeddings(active_embedder, [normalized_query], query_vectors)
    query_vector = query_vectors[0]
    candidate_limit = min(40, max(20, limit * 4))

    lexical_results = search_kb(
        tenant_id,
        normalized_query,
        limit=min(10, candidate_limit),
    )
    distance = KnowledgeChunk.embedding.cosine_distance(query_vector)
    semantic_statement = (
        select(
            KnowledgeChunk.id,
            KnowledgeChunk.source_key,
            KnowledgeChunk.title,
            KnowledgeChunk.content,
            KnowledgeChunk.language,
            (1.0 - distance).label("similarity"),
        )
        .where(
            KnowledgeChunk.tenant_id == tenant_id,
            KnowledgeChunk.embedding.is_not(None),
            KnowledgeChunk.embedding_model == active_embedder.model_name,
        )
        .order_by(distance, KnowledgeChunk.id)
        .limit(candidate_limit)
    )

    with session_scope() as session:
        semantic_rows = session.execute(semantic_statement).all()

    fused_scores: dict[int, float] = {}
    results_by_id: dict[int, KnowledgeResult] = {}
    for rank, result in enumerate(lexical_results, start=1):
        fused_scores[result.chunk_id] = 1.0 / (RRF_K + rank)
        results_by_id[result.chunk_id] = result

    semantic_rank = 0
    for row in semantic_rows:
        if row.similarity is None:
            continue
        similarity = float(row.similarity)
        if not math.isfinite(similarity) or similarity < min_similarity:
            continue
        semantic_rank += 1
        fused_scores[row.id] = fused_scores.get(row.id, 0.0) + 1.0 / (
            RRF_K + semantic_rank
        )
        results_by_id.setdefault(
            row.id,
            KnowledgeResult(
                chunk_id=row.id,
                source_key=row.source_key,
                title=row.title,
                content=row.content,
                language=row.language,
                score=similarity,
            ),
        )

    ranked_ids = sorted(
        fused_scores,
        key=lambda chunk_id: (-fused_scores[chunk_id], chunk_id),
    )[:limit]
    return [
        KnowledgeResult(
            chunk_id=chunk_id,
            source_key=results_by_id[chunk_id].source_key,
            title=results_by_id[chunk_id].title,
            content=results_by_id[chunk_id].content,
            language=results_by_id[chunk_id].language,
            score=fused_scores[chunk_id],
        )
        for chunk_id in ranked_ids
    ]
