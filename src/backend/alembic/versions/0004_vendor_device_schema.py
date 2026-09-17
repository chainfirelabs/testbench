"""vendor device schema overrides

Revision ID: 0004_vendor_device_schema
Revises: 0003_software_bundles
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_vendor_device_schema"
down_revision = "0003_software_bundles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("entity_fields", sa.Column(
        "configuration_source", sa.String(length=10), nullable=False, server_default="system",
    ))
    op.add_column("entity_fields", sa.Column("source_revision", sa.String(length=100), nullable=True))
    op.create_table(
        "vendor_device_field_overrides",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("software_id", sa.String(length=36), nullable=False),
        sa.Column("field_id", sa.String(length=36), nullable=False),
        sa.Column("visible", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["field_id"], ["entity_fields.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["software_id"], ["software.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("software_id", "field_id", name="uq_vendor_device_field_override"),
    )
    op.create_index("ix_vendor_device_field_override_software", "vendor_device_field_overrides", ["software_id"])
    op.create_index("ix_vendor_device_field_override_field", "vendor_device_field_overrides", ["field_id"])


def downgrade() -> None:
    op.drop_index("ix_vendor_device_field_override_field", table_name="vendor_device_field_overrides")
    op.drop_index("ix_vendor_device_field_override_software", table_name="vendor_device_field_overrides")
    op.drop_table("vendor_device_field_overrides")
    op.drop_column("entity_fields", "source_revision")
    op.drop_column("entity_fields", "configuration_source")
