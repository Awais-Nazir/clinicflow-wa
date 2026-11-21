"""Create the durable inbox table.

Revision ID: 20261121_0001
Revises:
Create Date: 2026-11-21
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20261121_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "inbox_messages",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("message_id", sa.Text(), nullable=False),
        sa.Column("phone_number_id", sa.Text(), nullable=False),
        sa.Column("sender_id", sa.Text(), nullable=False),
        sa.Column("message_type", sa.String(length=32), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("message_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "message_id",
            name="uq_inbox_messages_message_id",
        ),
    )
    op.create_index(
        "ix_inbox_messages_status",
        "inbox_messages",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_inbox_messages_status", table_name="inbox_messages")
    op.drop_table("inbox_messages")
