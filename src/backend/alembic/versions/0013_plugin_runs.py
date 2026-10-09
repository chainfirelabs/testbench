"""Persist safe plugin run status snapshots.

Revision ID: 0013_plugin_runs
Revises: 0012_audit_cleanup_jobs
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0013_plugin_runs"
down_revision = "0012_audit_cleanup_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plugin_runs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("plugin_id", sa.String(length=100), nullable=False),
        sa.Column("run_id", sa.String(length=100), nullable=False),
        sa.Column("state", sa.String(length=30), nullable=False),
        sa.Column("status", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime()),
        sa.UniqueConstraint("plugin_id", "run_id", name="uq_plugin_run_identity"),
    )
    op.create_index("ix_plugin_runs_state", "plugin_runs", ["state"])


def downgrade() -> None:
    op.drop_index("ix_plugin_runs_state", table_name="plugin_runs")
    op.drop_table("plugin_runs")
