"""device fields can open a web page

Revision ID: 0008_field_opens_web_page
Revises: 0007_roles

Adds the per-field switch behind the address links in the device grid and on
the device page. Backfilled true for the fields carrying a scan address role,
so an installation that already has `wan_ip` / `lan_ip` gets the links without
having to go and tick anything; every other field starts off unlinked and is
opted in from the schema editor.
"""
from alembic import op
import sqlalchemy as sa

revision = "0008_field_opens_web_page"
down_revision = "0007_roles"
branch_labels = None
depends_on = None

# Duplicated from app/services/device_schema.py on purpose: a migration
# describes the data as it was at this revision and must not shift underneath
# an old database when that set changes.
WEB_ADDRESS_ROLES = ("scan_address_wan", "scan_address_lan")


def upgrade() -> None:
    op.add_column(
        "device_field_definitions",
        sa.Column("opens_web_page", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute(
        sa.text(
            "UPDATE device_field_definitions SET opens_web_page = true "
            "WHERE plugin_role IN :roles"
        ).bindparams(sa.bindparam("roles", value=WEB_ADDRESS_ROLES, expanding=True))
    )
    # The default did its job for the backfill; the application sets the column
    # explicitly from here on.
    op.alter_column("device_field_definitions", "opens_web_page", server_default=None)


def downgrade() -> None:
    op.drop_column("device_field_definitions", "opens_web_page")
