"""per-field link scheme and per-device link overrides

Revision ID: 0009_device_link_scheme
Revises: 0008_field_opens_web_page

The address links added in 0008 were always `http://`. Two columns on the field
definition make that a choice rather than a rule — the installation's default
for that field — and a JSONB column on the device overrides it for the one box
that is not like the others.

The override lives on `devices` rather than in the device's document on
purpose. A document key would collide with an installation-defined field name,
and it would be fed to the plugins that read this device's addresses: the value
of a `scan_address_lan` field goes to `socket.create_connection` and to the
reboot job's SSH target unparsed, so a scheme or a port stored *in* the value
takes the device offline. The link's opinion about a scheme belongs next to the
device, not inside the address.

Backfilled to today's behaviour exactly: `http`, no port, no overrides.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0009_device_link_scheme"
down_revision = "0008_field_opens_web_page"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "device_field_definitions",
        sa.Column("link_scheme", sa.String(length=5), nullable=False, server_default="http"),
    )
    op.add_column(
        "device_field_definitions",
        sa.Column("link_port", sa.Integer(), nullable=True),
    )
    op.add_column(
        "devices",
        sa.Column(
            "link_overrides",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    # The defaults did their job for the backfill; the application sets both
    # columns explicitly from here on.
    op.alter_column("device_field_definitions", "link_scheme", server_default=None)


def downgrade() -> None:
    op.drop_column("devices", "link_overrides")
    op.drop_column("device_field_definitions", "link_port")
    op.drop_column("device_field_definitions", "link_scheme")
