"""Store the audit retention period in the database.

Revision ID: 0011_audit_retention
Revises: 0010_vendor_component_support
"""
from alembic import op
import sqlalchemy as sa

revision = "0011_audit_retention"
down_revision = "0010_vendor_component_support"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_retention",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("days", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint("id = 1", name="ck_audit_retention_singleton"),
        sa.CheckConstraint("days >= 0", name="ck_audit_retention_days"),
    )
    op.execute("INSERT INTO audit_retention (id, days) VALUES (1, 0)")


def downgrade() -> None:
    op.drop_table("audit_retention")
