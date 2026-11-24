"""Persist daily customer and model usage counters."""

from alembic import op
import sqlalchemy as sa

revision = "20261125_0018"
down_revision = "20261125_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("inbox_messages", sa.Column("quota_day", sa.Date()))
    op.create_table(
        "usage_counters",
        sa.Column("tenant_id", sa.BigInteger(), sa.ForeignKey("tenants.id"), primary_key=True),
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("subject", sa.String(80), primary_key=True),
        sa.Column("units", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.CheckConstraint("units >= 0", name="ck_usage_counters_units"),
    )


def downgrade() -> None:
    op.drop_table("usage_counters")
    op.drop_column("inbox_messages", "quota_day")
