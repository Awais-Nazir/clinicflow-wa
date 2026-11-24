"""Persist the booking draft and durable WhatsApp reply buttons.

Revision ID: 20261125_0015
Revises: 20261125_0014
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261125_0015"
down_revision = "20261125_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("booking_draft", postgresql.JSONB()))
    op.add_column("outbox_messages", sa.Column("reply_buttons", postgresql.JSONB()))


def downgrade() -> None:
    op.drop_column("outbox_messages", "reply_buttons")
    op.drop_column("conversations", "booking_draft")
