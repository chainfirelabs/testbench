"""Optional support status per software component and vendor device.

Revision ID: 0010_vendor_component_support
Revises: 0009_device_link_scheme
"""
from alembic import op
import sqlalchemy as sa

revision = "0010_vendor_component_support"
down_revision = "0009_device_link_scheme"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vendor_device_component_support",
        sa.Column("vendor_device_id", sa.String(length=36), nullable=False),
        sa.Column("component_id", sa.String(length=36), nullable=False),
        sa.Column("support_status", sa.String(length=20), nullable=False),
        sa.ForeignKeyConstraint(["vendor_device_id"], ["vendor_devices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["component_id"], ["software_components.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("vendor_device_id", "component_id"),
    )
    op.create_index(
        "ix_vendor_device_component_support_component_id",
        "vendor_device_component_support", ["component_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_vendor_device_component_support_component_id", table_name="vendor_device_component_support")
    op.drop_table("vendor_device_component_support")
