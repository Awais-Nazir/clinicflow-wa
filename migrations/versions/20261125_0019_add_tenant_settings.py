"""Add tenant tone, staff authorization and encrypted WhatsApp credentials."""

from alembic import op
import sqlalchemy as sa

revision = "20261125_0019"
down_revision = "20261125_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("reply_tone", sa.String(20), server_default="standard", nullable=False))
    op.add_column("tenants", sa.Column("default_language", sa.String(8), server_default="roman_ur", nullable=False))
    op.add_column("tenants", sa.Column("staff_token_hash", sa.String(64)))
    op.create_unique_constraint("uq_tenants_staff_token_hash", "tenants", ["staff_token_hash"])
    op.create_check_constraint(
        "ck_tenants_reply_tone", "tenants", "reply_tone IN ('standard', 'warm', 'formal', 'concise')"
    )
    op.create_check_constraint(
        "ck_tenants_default_language", "tenants", "default_language IN ('en', 'ur', 'roman_ur')"
    )
    op.add_column("whatsapp_phone_numbers", sa.Column("access_token_ciphertext", sa.Text()))


def downgrade() -> None:
    op.drop_column("whatsapp_phone_numbers", "access_token_ciphertext")
    op.drop_constraint("ck_tenants_default_language", "tenants", type_="check")
    op.drop_constraint("ck_tenants_reply_tone", "tenants", type_="check")
    op.drop_constraint("uq_tenants_staff_token_hash", "tenants", type_="unique")
    op.drop_column("tenants", "staff_token_hash")
    op.drop_column("tenants", "default_language")
    op.drop_column("tenants", "reply_tone")
