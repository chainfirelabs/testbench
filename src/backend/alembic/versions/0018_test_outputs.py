"""Store one viewable text output per test.

Revision ID: 0018_test_outputs
Revises: 0017_test_artifacts
"""
from alembic import op
import sqlalchemy as sa

revision = "0018_test_outputs"
down_revision = "0017_test_artifacts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "test_outputs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("test_id", sa.String(36), sa.ForeignKey("tests.id", ondelete="CASCADE"),
                  nullable=False, unique=True),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("bucket", sa.String(255), nullable=False),
        sa.Column("object_key", sa.String(1024), nullable=False),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime()),
    )


def downgrade() -> None:
    op.drop_table("test_outputs")
