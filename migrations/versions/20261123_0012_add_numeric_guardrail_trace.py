"""Record numeric grounding guardrail outcomes.

Revision ID: 20261123_0012
Revises: 20261123_0011
Create Date: 2026-11-23
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20261123_0012"
down_revision: str | None = "20261123_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("guardrail_reason", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column(
            "unsupported_numbers",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_runs", "unsupported_numbers")
    op.drop_column("agent_runs", "guardrail_reason")
