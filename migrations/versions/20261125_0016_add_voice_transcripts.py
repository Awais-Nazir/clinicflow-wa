"""Store voice transcripts on inbound conversation messages.

Revision ID: 20261125_0016
Revises: 20261125_0015
"""

from alembic import op
import sqlalchemy as sa

revision = "20261125_0016"
down_revision = "20261125_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("conversation_messages", sa.Column("transcript", sa.Text()))


def downgrade() -> None:
    op.drop_column("conversation_messages", "transcript")
