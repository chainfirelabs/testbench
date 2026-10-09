"""Index global substring search prefilters and vendor claim fields.

Revision ID: 0014_search_trigrams
Revises: 0013_plugin_runs
"""
from alembic import op

revision = "0014_search_trigrams"
down_revision = "0013_plugin_runs"
branch_labels = None
depends_on = None

_INDEXES = [
    ("ix_devices_search_data_trgm", "devices", "(data::text)"),
    ("ix_software_search_data_trgm", "software", "(data::text)"),
    ("ix_tests_search_data_trgm", "tests", "(data::text)"),
    ("ix_devices_unique_id_trgm", "devices", "unique_id"),
    ("ix_vendor_make_trgm", "vendor_devices", "make"),
    ("ix_vendor_model_trgm", "vendor_devices", "model"),
    ("ix_vendor_firmware_trgm", "vendor_devices", "firmware_version"),
    ("ix_vendor_hardware_trgm", "vendor_devices", "hardware_version"),
    ("ix_vendor_architecture_trgm", "vendor_devices", "architecture"),
    ("ix_vendor_source_trgm", "vendor_devices", "source"),
    ("ix_vendor_notes_trgm", "vendor_devices", "notes"),
    ("ix_software_name_trgm", "software", "(data->>'name')"),
]


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    for name, table, expression in _INDEXES:
        op.execute(f"CREATE INDEX {name} ON {table} USING gin ({expression} gin_trgm_ops)")


def downgrade() -> None:
    for name, _table, _expression in reversed(_INDEXES):
        op.drop_index(name)
