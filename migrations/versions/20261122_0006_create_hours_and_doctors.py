"""Create clinic opening hours and doctors.

Revision ID: 20261122_0006
Revises: 20261122_0005
Create Date: 2026-11-22
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20261122_0006"
down_revision: str | None = "20261122_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "clinic_opening_hours",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("weekday", sa.SmallInteger(), nullable=False),
        sa.Column("is_closed", sa.Boolean(), nullable=False),
        sa.Column("opens_at", sa.Time(), nullable=True),
        sa.Column("closes_at", sa.Time(), nullable=True),
        sa.CheckConstraint(
            "weekday >= 0 AND weekday <= 6",
            name="ck_clinic_opening_hours_weekday",
        ),
        sa.CheckConstraint(
            "(is_closed AND opens_at IS NULL AND closes_at IS NULL) OR "
            "(NOT is_closed AND opens_at IS NOT NULL AND "
            "closes_at IS NOT NULL AND opens_at < closes_at)",
            name="ck_clinic_opening_hours_valid_interval",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "weekday",
            name="uq_clinic_opening_hours_tenant_weekday",
        ),
    )
    op.create_index(
        "ix_clinic_opening_hours_tenant_id",
        "clinic_opening_hours",
        ["tenant_id"],
        unique=False,
    )
    op.create_table(
        "doctors",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("specialty", sa.String(length=160), nullable=False),
        sa.Column("bio", sa.Text(), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "name",
            name="uq_doctors_tenant_name",
        ),
    )
    op.create_index(
        "ix_doctors_tenant_id",
        "doctors",
        ["tenant_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_doctors_tenant_id", table_name="doctors")
    op.drop_table("doctors")
    op.drop_index(
        "ix_clinic_opening_hours_tenant_id",
        table_name="clinic_opening_hours",
    )
    op.drop_table("clinic_opening_hours")
