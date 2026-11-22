"""Enable pgvector and add knowledge embeddings.

Revision ID: 20261123_0010
Revises: 20261123_0009
Create Date: 2026-11-23
"""

from collections.abc import Sequence

from pgvector.sqlalchemy import VECTOR
import sqlalchemy as sa

from alembic import op

revision: str = "20261123_0010"
down_revision: str | None = "20261123_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.add_column(
        "knowledge_chunks",
        sa.Column("embedding", VECTOR(384), nullable=True),
    )
    op.add_column(
        "knowledge_chunks",
        sa.Column("embedding_model", sa.String(length=160), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("knowledge_chunks", "embedding_model")
    op.drop_column("knowledge_chunks", "embedding")
