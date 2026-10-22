"""Group tests into runs, with files shared by the whole run.

Revision ID: 0020_test_runs
Revises: 0019_replication_not_a_plugin
"""
from alembic import op
import sqlalchemy as sa

revision = "0020_test_runs"
down_revision = "0019_replication_not_a_plugin"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "test_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("external_id", sa.String(255), unique=True),
        sa.Column("url", sa.String(2048)),
        sa.Column("started_at", sa.DateTime()),
        sa.Column("finished_at", sa.DateTime()),
        sa.Column("notes", sa.Text()),
        sa.Column("storage_folder", sa.String(512), nullable=False),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime()),
    )
    op.create_table(
        "test_run_artifacts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("test_run_id", sa.String(36), sa.ForeignKey("test_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("bucket", sa.String(255), nullable=False),
        sa.Column("object_key", sa.String(1024), nullable=False, unique=True),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime()),
    )
    op.create_index("ix_test_run_artifacts_test_run_id", "test_run_artifacts", ["test_run_id"])
    op.add_column("tests", sa.Column(
        "test_run_id", sa.String(36), sa.ForeignKey("test_runs.id", ondelete="SET NULL"), nullable=True,
    ))
    op.create_index("ix_tests_test_run_id", "tests", ["test_run_id"])


def downgrade() -> None:
    op.drop_index("ix_tests_test_run_id", table_name="tests")
    op.drop_column("tests", "test_run_id")
    op.drop_table("test_run_artifacts")
    op.drop_table("test_runs")
