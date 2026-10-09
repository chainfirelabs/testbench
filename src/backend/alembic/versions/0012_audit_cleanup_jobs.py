"""Track manual audit cleanup across requests and restarts.

Revision ID: 0012_audit_cleanup_jobs
Revises: 0011_audit_retention
"""
from alembic import op
import sqlalchemy as sa

revision = "0012_audit_cleanup_jobs"
down_revision = "0011_audit_retention"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_cleanup_jobs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("before", sa.DateTime(), nullable=False),
        sa.Column("delete_device_changelogs", sa.Boolean(), nullable=False),
        sa.Column("requested_by", sa.String(length=150), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("deleted", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime()),
    )
    op.create_index("ix_audit_cleanup_jobs_state", "audit_cleanup_jobs", ["state"])


def downgrade() -> None:
    op.drop_index("ix_audit_cleanup_jobs_state", table_name="audit_cleanup_jobs")
    op.drop_table("audit_cleanup_jobs")
