"""roles as rows, with permission sets

Revision ID: 0007_roles
Revises: 0006_suite_scoped_components

The three-rung ladder (readonly < tester < admin) becomes a table, and what a
role grants becomes a list of permissions on the row. The three built-ins are
seeded with exactly the sets that reproduce the old guards — admin held every
one of them, tester passed `require_write`, readonly passed neither — so an
upgrade changes nobody's access. `users.role` and `api_keys.role` are left
alone: they already hold the three slugs this seeds.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0007_roles"
down_revision = "0006_suite_scoped_components"
branch_labels = None
depends_on = None

# Duplicated from app/services/permissions.py on purpose: a migration has to
# describe the data as it was at this revision, and must not shift underneath
# an old database when the catalogue grows.
TESTER = ["devices.edit", "software.edit", "tests.edit", "views.save"]
ADMIN = TESTER + [
    "audit.view", "users.manage", "schema.manage", "plugins.manage", "settings.manage",
]


def upgrade() -> None:
    op.create_table(
        "roles",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("slug", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("permissions", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("is_builtin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_roles_slug"),
    )
    op.create_index("ix_roles_slug", "roles", ["slug"])

    roles = sa.table(
        "roles",
        sa.column("id", sa.String),
        sa.column("slug", sa.String),
        sa.column("name", sa.String),
        sa.column("description", sa.Text),
        sa.column("permissions", postgresql.JSONB),
        sa.column("is_builtin", sa.Boolean),
    )
    op.bulk_insert(roles, [
        {
            "id": "00000000-0000-0000-0000-000000000001",
            "slug": "readonly",
            "name": "Read only",
            "description": "Can see the fleet, the software catalogue and test "
                           "results, and change nothing.",
            "permissions": [],
            "is_builtin": True,
        },
        {
            "id": "00000000-0000-0000-0000-000000000002",
            "slug": "tester",
            "name": "Tester",
            "description": "Can edit the inventory, the software catalogue and "
                           "test results.",
            "permissions": TESTER,
            "is_builtin": True,
        },
        {
            "id": "00000000-0000-0000-0000-000000000003",
            "slug": "admin",
            "name": "Administrator",
            "description": "Full access, including user accounts, the schema and "
                           "the audit log.",
            "permissions": ADMIN,
            "is_builtin": True,
        },
    ])


def downgrade() -> None:
    # Roles an installation defined are lost with the table, and every user
    # holding one is left with a slug the old ladder does not know — which it
    # already treated as granting nothing.
    op.drop_index("ix_roles_slug", table_name="roles")
    op.drop_table("roles")
