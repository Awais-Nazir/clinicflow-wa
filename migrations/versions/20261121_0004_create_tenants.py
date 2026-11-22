"""Create tenants and WhatsApp phone-number mappings.

Revision ID: 20261121_0004
Revises: 20261121_0003
Create Date: 2026-11-21
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20261121_0004"
down_revision: str | None = "20261121_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_tenants_slug"),
    )
    op.create_table(
        "whatsapp_phone_numbers",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("phone_number_id", sa.Text(), nullable=False),
        sa.Column("display_phone_number", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "phone_number_id",
            name="uq_whatsapp_phone_numbers_phone_number_id",
        ),
    )
    op.create_index(
        "ix_whatsapp_phone_numbers_tenant_id",
        "whatsapp_phone_numbers",
        ["tenant_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_whatsapp_phone_numbers_tenant_id",
        table_name="whatsapp_phone_numbers",
    )
    op.drop_table("whatsapp_phone_numbers")
    op.drop_table("tenants")
