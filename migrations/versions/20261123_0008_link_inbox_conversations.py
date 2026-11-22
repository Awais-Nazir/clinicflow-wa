"""Link inbox messages to tenants and conversations.

Revision ID: 20261123_0008
Revises: 20261122_0007
Create Date: 2026-11-23
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20261123_0008"
down_revision: str | None = "20261122_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "inbox_messages",
        sa.Column("tenant_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "inbox_messages",
        sa.Column("conversation_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_inbox_messages_conversation_tenant",
        "inbox_messages",
        "conversations",
        ["conversation_id", "tenant_id"],
        ["id", "tenant_id"],
    )
    op.create_index(
        "ix_inbox_messages_tenant_id",
        "inbox_messages",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "ix_inbox_messages_conversation_id",
        "inbox_messages",
        ["conversation_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_inbox_messages_conversation_id",
        table_name="inbox_messages",
    )
    op.drop_index("ix_inbox_messages_tenant_id", table_name="inbox_messages")
    op.drop_constraint(
        "fk_inbox_messages_conversation_tenant",
        "inbox_messages",
        type_="foreignkey",
    )
    op.drop_column("inbox_messages", "conversation_id")
    op.drop_column("inbox_messages", "tenant_id")
