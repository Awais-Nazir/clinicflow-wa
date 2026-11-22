"""Tenant-isolated lexical retrieval for clinic FAQ and policy chunks."""

from dataclasses import dataclass

from sqlalchemy import func, select

from clinicflow.database import session_scope
from clinicflow.models import KnowledgeChunk


@dataclass(frozen=True)
class KnowledgeResult:
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
            source_key=row.source_key,
            title=row.title,
            content=row.content,
            language=row.language,
            score=float(row.score),
        )
        for row in rows
    ]
