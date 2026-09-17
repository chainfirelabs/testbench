"""remove redundant vendor-device vendor field

Revision ID: 0005_remove_vendor_device_vendor
Revises: 0004_vendor_device_schema
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_remove_vendor_device_vendor"
down_revision = "0004_vendor_device_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Removing vendor from identity can make formerly distinct legacy records
    # identical. Keep the most recently changed record in each new identity.
    op.execute("""
        WITH ranked AS (
            SELECT id, row_number() OVER (
                PARTITION BY software_id,
                    lower(trim(coalesce(make, ''))),
                    lower(trim(coalesce(model, ''))),
                    lower(trim(coalesce(firmware_version, ''))),
                    lower(trim(coalesce(hardware_version, ''))),
                    lower(trim(coalesce(architecture, '')))
                ORDER BY coalesce(updated_at, created_at) DESC, id DESC
            ) AS position
            FROM vendor_devices
        )
        DELETE FROM vendor_devices
        WHERE id IN (SELECT id FROM ranked WHERE position > 1)
    """)
    op.execute("""
        UPDATE vendor_devices SET match_key = concat_ws('|',
            lower(trim(coalesce(make, ''))),
            lower(trim(coalesce(model, ''))),
            lower(trim(coalesce(firmware_version, ''))),
            lower(trim(coalesce(hardware_version, ''))),
            lower(trim(coalesce(architecture, '')))
        )
    """)
    # Cascade removes per-software layout overrides for this catalog field.
    op.execute("DELETE FROM entity_fields WHERE entity = 'vendor_devices' AND key = 'vendor'")
    op.drop_index("ix_vendor_devices_vendor", table_name="vendor_devices")
    op.drop_column("vendor_devices", "vendor")


def downgrade() -> None:
    op.add_column("vendor_devices", sa.Column("vendor", sa.String(length=255), nullable=True))
    op.create_index("ix_vendor_devices_vendor", "vendor_devices", ["vendor"])
