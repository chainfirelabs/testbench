"""version-specific software bundles

Revision ID: 0003_software_bundles
Revises: 0002_ai_profiles
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_software_bundles"
down_revision = "0002_ai_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "software_bundle_members",
        sa.Column("bundle_software_id", sa.String(length=36), nullable=False),
        sa.Column("component_software_id", sa.String(length=36), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["bundle_software_id"], ["software.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["component_software_id"], ["software.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("bundle_software_id", "component_software_id"),
        sa.UniqueConstraint("bundle_software_id", "component_software_id", name="uq_software_bundle_member"),
        sa.CheckConstraint("bundle_software_id <> component_software_id", name="ck_software_bundle_not_self"),
    )
    op.create_index("ix_software_bundle_component", "software_bundle_members", ["component_software_id"])


def downgrade() -> None:
    op.drop_index("ix_software_bundle_component", table_name="software_bundle_members")
    op.drop_table("software_bundle_members")
