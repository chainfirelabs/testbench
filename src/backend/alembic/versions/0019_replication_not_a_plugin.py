"""Drop plugin allowlist rows for replication, which is no longer a plugin.

Replication used to register its controller through plugin discovery, so it
could be listed, and allowlisted, under Device Schema → Plugins. It never had a
device action; those rows did nothing then and would now be reported as a
plugin that is "not installed".

Revision ID: 0019_replication_not_a_plugin
Revises: 0018_test_outputs
"""
from alembic import op

revision = "0019_replication_not_a_plugin"
down_revision = "0018_test_outputs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DELETE FROM device_type_plugins WHERE plugin_id = 'replication'")


def downgrade() -> None:
    # The rows granted nothing, so there is nothing to restore.
    pass
