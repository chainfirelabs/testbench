"""Store downloadable test artifact metadata.

Revision ID: 0017_test_artifacts
Revises: 0016_replication_tombstones
"""
from alembic import op
import sqlalchemy as sa

revision = "0017_test_artifacts"
down_revision = "0016_replication_tombstones"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "test_artifacts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("test_id", sa.String(36), sa.ForeignKey("tests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("bucket", sa.String(255), nullable=False),
        sa.Column("object_key", sa.String(1024), nullable=False, unique=True),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime()),
    )
    op.create_index("ix_test_artifacts_test_id", "test_artifacts", ["test_id"])


def downgrade() -> None:
    op.drop_table("test_artifacts")
