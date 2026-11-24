"""Add an expiring claim for FAQ preparation.

Revision ID: 20261125_0013
Revises: 20261123_0012
"""

from alembic import op
import sqlalchemy as sa

revision = "20261125_0013"
down_revision = "20261123_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "inbox_messages",
        sa.Column("faq_claimed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("inbox_messages", "faq_claimed_at")
