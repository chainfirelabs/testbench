"""Store the shared home dashboard layout.

Revision ID: 0015_dashboard_layout
Revises: 0014_search_trigrams
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0015_dashboard_layout"
down_revision = "0014_search_trigrams"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dashboard_layouts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("widgets", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime()),
        sa.CheckConstraint("id = 1", name="ck_dashboard_layout_singleton"),
        sa.CheckConstraint("revision >= 0", name="ck_dashboard_layout_revision"),
    )


def downgrade() -> None:
    op.drop_table("dashboard_layouts")
