"""Create tenant-scoped providers, availability and appointments.

Revision ID: 20261125_0014
Revises: 20261125_0013
"""

from alembic import op
import sqlalchemy as sa

revision = "20261125_0014"
down_revision = "20261125_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_clinic_services_id_tenant", "clinic_services", ["id", "tenant_id"]
    )
    op.create_table(
        "providers",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("tenant_id", sa.BigInteger(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.UniqueConstraint("id", "tenant_id", name="uq_providers_id_tenant"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_providers_tenant_name"),
    )
    op.create_table(
        "provider_hours",
        sa.Column("provider_id", sa.BigInteger(), primary_key=True),
        sa.Column("weekday", sa.SmallInteger(), primary_key=True),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("opens_at", sa.Time(), nullable=False),
        sa.Column("closes_at", sa.Time(), nullable=False),
        sa.ForeignKeyConstraint(
            ["provider_id", "tenant_id"], ["providers.id", "providers.tenant_id"],
            name="fk_provider_hours_provider_tenant",
        ),
        sa.CheckConstraint("weekday BETWEEN 0 AND 6", name="ck_provider_hours_weekday"),
        sa.CheckConstraint("opens_at < closes_at", name="ck_provider_hours_interval"),
    )
    op.create_table(
        "provider_services",
        sa.Column("tenant_id", sa.BigInteger(), primary_key=True),
        sa.Column("provider_id", sa.BigInteger(), primary_key=True),
        sa.Column("service_id", sa.BigInteger(), primary_key=True),
        sa.ForeignKeyConstraint(
            ["provider_id", "tenant_id"], ["providers.id", "providers.tenant_id"],
            name="fk_provider_services_provider_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["service_id", "tenant_id"], ["clinic_services.id", "clinic_services.tenant_id"],
            name="fk_provider_services_service_tenant",
        ),
    )
    op.create_table(
        "appointments",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("conversation_id", sa.BigInteger(), nullable=False),
        sa.Column("provider_id", sa.BigInteger(), nullable=False),
        sa.Column("service_id", sa.BigInteger(), nullable=False),
        sa.Column("customer_name", sa.String(160), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), server_default="booked", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "provider_id", "service_id"],
            ["provider_services.tenant_id", "provider_services.provider_id", "provider_services.service_id"],
            name="fk_appointments_offering",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id", "tenant_id"], ["conversations.id", "conversations.tenant_id"],
            name="fk_appointments_conversation_tenant",
        ),
        sa.CheckConstraint("starts_at < ends_at", name="ck_appointments_interval"),
        sa.CheckConstraint("status IN ('booked', 'cancelled')", name="ck_appointments_status"),
    )
    op.create_index(
        "uq_appointments_booked_provider_start", "appointments", ["provider_id", "starts_at"],
        unique=True, postgresql_where=sa.text("status = 'booked'"),
    )
    op.create_index(
        "ix_appointments_conversation_start", "appointments", ["conversation_id", "starts_at"]
    )


def downgrade() -> None:
    op.drop_table("appointments")
    op.drop_table("provider_services")
    op.drop_table("provider_hours")
    op.drop_table("providers")
    op.drop_constraint("uq_clinic_services_id_tenant", "clinic_services", type_="unique")
