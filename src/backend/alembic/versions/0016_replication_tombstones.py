"""Durable deletion markers for instance replication.

Revision ID: 0016_replication_tombstones
Revises: 0015_dashboard_layout
"""
from alembic import op
import sqlalchemy as sa

revision = "0016_replication_tombstones"
down_revision = "0015_dashboard_layout"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "replication_tombstones",
        sa.Column("entity", sa.String(20), primary_key=True),
        sa.Column("identity", sa.String(700), primary_key=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("replication_tombstones")
