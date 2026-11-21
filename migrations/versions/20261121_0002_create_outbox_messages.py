"""Create the durable outbox table.

Revision ID: 20261121_0002
Revises: 20261121_0001
Create Date: 2026-11-21
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20261121_0002"
down_revision: str | None = "20261121_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "outbox_messages",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("inbox_message_id", sa.BigInteger(), nullable=False),
        sa.Column("phone_number_id", sa.Text(), nullable=False),
        sa.Column("recipient_id", sa.Text(), nullable=False),
        sa.Column("message_type", sa.String(length=32), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("provider_message_id", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["inbox_message_id"],
            ["inbox_messages.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "inbox_message_id",
            name="uq_outbox_messages_inbox_message_id",
        ),
    )
    op.create_index(
        "ix_outbox_messages_status",
        "outbox_messages",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_outbox_messages_status", table_name="outbox_messages")
    op.drop_table("outbox_messages")
