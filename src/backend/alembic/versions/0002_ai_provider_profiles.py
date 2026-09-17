"""admin-managed AI provider profiles

Revision ID: 0002_ai_profiles
Revises: 0001_initial
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002_ai_profiles"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_provider_profiles",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("provider_type", sa.String(length=30), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("encrypted_api_key", sa.Text(), nullable=True),
        sa.Column("custom_headers", postgresql.JSONB(), nullable=False),
        sa.Column("manual_models", postgresql.JSONB(), nullable=False),
        sa.Column("discovered_models", postgresql.JSONB(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_refreshed_at", sa.String(length=40), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "ai_plugin_defaults",
        sa.Column("plugin_id", sa.String(length=100), nullable=False),
        sa.Column("profile_id", sa.String(length=36), nullable=True),
        sa.Column("model", sa.String(length=255), nullable=True),
        sa.Column("repeat_model", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["profile_id"], ["ai_provider_profiles.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("plugin_id"),
    )
    op.create_index("ix_ai_plugin_defaults_profile_id", "ai_plugin_defaults", ["profile_id"])


def downgrade() -> None:
    op.drop_index("ix_ai_plugin_defaults_profile_id", table_name="ai_plugin_defaults")
    op.drop_table("ai_plugin_defaults")
    op.drop_table("ai_provider_profiles")
