"""Record handoff cases and support staff messages while the bot is paused."""

from alembic import op
import sqlalchemy as sa

revision = "20261125_0017"
down_revision = "20261125_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("human_until", sa.DateTime(timezone=True)))
    op.add_column(
        "outbox_messages",
        sa.Column("allow_during_handoff", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.create_table(
        "handoff_cases",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("conversation_id", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["conversation_id", "tenant_id"], ["conversations.id", "conversations.tenant_id"],
            name="fk_handoff_conversation_tenant",
        ),
    )
    op.create_index("ix_handoff_tenant_open", "handoff_cases", ["tenant_id", "closed_at"])
    op.create_index(
        "uq_handoff_open_conversation", "handoff_cases", ["conversation_id"],
        unique=True, postgresql_where=sa.text("closed_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_table("handoff_cases")
    op.drop_column("outbox_messages", "allow_during_handoff")
    op.drop_column("conversations", "human_until")
