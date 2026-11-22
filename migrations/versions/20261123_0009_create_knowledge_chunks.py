"""Create tenant-scoped searchable knowledge chunks.

Revision ID: 20261123_0009
Revises: 20261123_0008
Create Date: 2026-11-23
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20261123_0009"
down_revision: str | None = "20261123_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("source_key", sa.String(length=160), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "language",
            sa.String(length=16),
            server_default="en",
            nullable=False,
        ),
        sa.Column(
            "chunk_index",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed(
                "to_tsvector('simple'::regconfig, COALESCE(content, ''))",
                persisted=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "source_key",
            "chunk_index",
            name="uq_knowledge_chunks_tenant_source_chunk",
        ),
    )
    op.create_index(
        "ix_knowledge_chunks_tenant_id",
        "knowledge_chunks",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_chunks_search_vector",
        "knowledge_chunks",
        ["search_vector"],
        unique=False,
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_knowledge_chunks_search_vector",
        table_name="knowledge_chunks",
    )
    op.drop_index(
        "ix_knowledge_chunks_tenant_id",
        table_name="knowledge_chunks",
    )
    op.drop_table("knowledge_chunks")
