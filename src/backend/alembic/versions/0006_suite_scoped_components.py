"""make software components suite-scoped

Revision ID: 0006_suite_scoped_components
Revises: 0005_remove_vendor_device_vendor
"""
from alembic import op
import sqlalchemy as sa

revision = "0006_suite_scoped_components"
down_revision = "0005_remove_vendor_device_vendor"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "software_components",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("software_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("version", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["software_id"], ["software.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("software_id", "name", "version", name="uq_software_component_identity"),
    )
    op.create_index("ix_software_components_software_id", "software_components", ["software_id"])
    # Preserve bundle definitions as suite-owned names. Historical tests remain
    # on their original Software rows because their intended parent is unknowable.
    op.execute("""
        INSERT INTO software_components
            (id, software_id, name, version, required, position, created_at, updated_at)
        SELECT md5(m.bundle_software_id || ':' || m.component_software_id),
               m.bundle_software_id, c.data->>'name', coalesce(c.data->>'version', ''),
               m.required, m.position, m.created_at, m.updated_at
        FROM software_bundle_members m
        JOIN software c ON c.id = m.component_software_id
    """)
    op.add_column("tests", sa.Column("component_id", sa.String(length=36), nullable=True))
    op.create_foreign_key(
        "fk_tests_component_id", "tests", "software_components", ["component_id"], ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_tests_component_id", "tests", ["component_id"])
    # Old component tests pointed at the component's standalone Software row.
    # When it had one parent, move that evidence beneath the parent suite. A
    # component shared by several suites is intentionally left untouched: the
    # migration cannot know which suite a historical run meant.
    op.execute("""
        WITH mappings AS (
            SELECT component_software_id,
                   min(bundle_software_id) AS bundle_software_id
            FROM software_bundle_members
            GROUP BY component_software_id
            HAVING count(DISTINCT bundle_software_id) = 1
        )
        UPDATE tests t
        SET software_id = m.bundle_software_id,
            component_id = md5(m.bundle_software_id || ':' || m.component_software_id)
        FROM mappings m
        WHERE t.software_id = m.component_software_id
    """)
    op.drop_table("software_bundle_members")


def downgrade() -> None:
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
    )
    op.create_index("ix_software_bundle_component", "software_bundle_members", ["component_software_id"])
    # Reconstruct old-style memberships and test ownership only when a matching
    # standalone Software row still exists.
    op.execute("""
        INSERT INTO software_bundle_members
            (bundle_software_id, component_software_id, required, position, created_at, updated_at)
        SELECT c.software_id, s.id, c.required, c.position, c.created_at, c.updated_at
        FROM software_components c
        JOIN software s
          ON lower(s.data->>'name') = lower(c.name)
         AND coalesce(s.data->>'version', '') = c.version
        ON CONFLICT DO NOTHING
    """)
    op.execute("""
        UPDATE tests t
        SET software_id = m.component_software_id,
            component_id = NULL
        FROM software_bundle_members m
        WHERE t.component_id = md5(m.bundle_software_id || ':' || m.component_software_id)
          AND t.software_id = m.bundle_software_id
    """)
    op.drop_index("ix_tests_component_id", table_name="tests")
    op.drop_constraint("fk_tests_component_id", "tests", type_="foreignkey")
    op.drop_column("tests", "component_id")
    op.drop_index("ix_software_components_software_id", table_name="software_components")
    op.drop_table("software_components")
