"""Tables backing the installation-defined device schema.

Four related things live here:

* `DeviceFieldDefinition` — one reusable field, defined once and assigned
  wherever it is wanted. Definitions carry meaning (type, choices, validation,
  semantic plugin role); they say nothing about where the field appears.
* `DeviceFieldAssignment` — where a definition appears, and how. A row with no
  `device_type_id` is global and is inherited by every type, present and
  future; a row with one either adds the field to that type or overrides
  properties of the global assignment.
* `DeviceTypePlugin` — the per-type plugin allowlist. Nothing is available to a
  device type until a row here says so.
* `DeviceSchemaRevision` / `DeviceFieldIndex` — the published-revision counter
  every cache keys off, and the managed PostgreSQL expression indexes the
  catalog asks for.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, TimestampMixin, new_uuid


class DeviceFieldDefinition(TimestampMixin, Base):
    """One reusable device field, independent of the types that use it."""

    __tablename__ = "device_field_definitions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    # Stable, immutable, and what every URL, import column, API payload and
    # plugin contract names. Labels are presentation and may change freely.
    key: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    label: Mapped[str] = mapped_column(String(150), nullable=False)
    field_type: Mapped[str] = mapped_column(String(20), nullable=False, default="text")
    description: Mapped[str | None] = mapped_column(Text)
    options: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # Grows without a migration: minimum/maximum, pattern, min/max length,
    # whether blank is accepted. See services/device_schema.py for the readers.
    validation: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    default_value: Mapped[dict | None] = mapped_column(JSONB)
    sensitive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    indexed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    unique_value: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # The semantic name a plugin asks for, so plugins depend on meaning rather
    # than on one installation's choice of field names.
    plugin_role: Mapped[str | None] = mapped_column(String(50), index=True)
    # Created by the application. Cannot be deleted, and its key, type and role
    # cannot change; visibility is still the administrator's to set except
    # where application behaviour depends on it.
    protected_system_field: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    configuration_source: Mapped[str] = mapped_column(String(10), nullable=False, default="gui")
    source_revision: Mapped[str | None] = mapped_column(String(100))


class DeviceFieldAssignment(TimestampMixin, Base):
    """Where one definition appears: globally, or on a single device type.

    The override columns are nullable on purpose. Null means "inherit" — from
    the global assignment when this row is a type-specific override of one, and
    from the built-in default otherwise. A non-null value is a deliberate
    decision an administrator made and is what an editor shows as set.
    """

    __tablename__ = "device_field_assignments"
    __table_args__ = (
        UniqueConstraint("field_definition_id", "device_type_id", name="uq_device_field_assignment_scope"),
        Index("ix_device_field_assignments_type", "device_type_id", "position"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    field_definition_id: Mapped[str] = mapped_column(
        ForeignKey("device_field_definitions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Null is the global scope, inherited by every device type.
    device_type_id: Mapped[str | None] = mapped_column(ForeignKey("device_types.id", ondelete="CASCADE"))
    visible: Mapped[bool | None] = mapped_column(Boolean)
    # Presentation-only: false omits this field from inventory grids but
    # leaves it available on device details, forms, exports and to plugins.
    list_visible: Mapped[bool | None] = mapped_column(Boolean)
    required: Mapped[bool | None] = mapped_column(Boolean)
    writable: Mapped[bool | None] = mapped_column(Boolean)
    position: Mapped[int | None] = mapped_column(Integer)
    label_override: Mapped[str | None] = mapped_column(String(150))
    description_override: Mapped[str | None] = mapped_column(Text)
    validation_override: Mapped[dict | None] = mapped_column(JSONB)
    # An advanced override: take a global field back off this one type. Never
    # permitted for protected system fields, and off by default so that
    # "global" keeps meaning global.
    excluded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    configuration_source: Mapped[str] = mapped_column(String(10), nullable=False, default="gui")
    source_revision: Mapped[str | None] = mapped_column(String(100))


class DeviceTypePlugin(TimestampMixin, Base):
    """An allowlist entry: this plugin may act on this device type.

    `plugin_id` names an installed manifest rather than a database row, because
    plugins come and go with the deployment. An assignment naming a plugin that
    is not installed right now is kept and reported as unavailable, so removing
    a plugin for an afternoon does not destroy its configuration.
    """

    __tablename__ = "device_type_plugins"
    __table_args__ = (
        UniqueConstraint("device_type_id", "plugin_id", name="uq_device_type_plugin"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    # Null means every device type. Refused for manifests that set
    # allow_global_assignment=false, which is how a disruptive action such as
    # Reboot is kept to types somebody explicitly authorised.
    device_type_id: Mapped[str | None] = mapped_column(ForeignKey("device_types.id", ondelete="CASCADE"), index=True)
    plugin_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    configuration: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    configuration_source: Mapped[str] = mapped_column(String(10), nullable=False, default="gui")
    source_revision: Mapped[str | None] = mapped_column(String(100))


class DeviceSchemaRevision(Base):
    """One published schema. The highest id is what every reader resolves.

    Publishing is the only thing that changes the effective schema, so a single
    monotonic number is enough to key caches on and enough for an operator to
    tell two configurations apart.
    """

    __tablename__ = "device_schema_revisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    source: Mapped[str] = mapped_column(String(10), nullable=False, default="gui")
    note: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class DeviceFieldIndex(Base):
    """Desired and applied state for one managed PostgreSQL expression index.

    Kept in a table rather than derived on the fly so a CREATE INDEX that fails
    — a duplicate value where a unique index was asked for, most often — is
    visible to an administrator and retryable, instead of disappearing into a
    log line nobody reads.
    """

    __tablename__ = "device_field_indexes"

    name: Mapped[str] = mapped_column(String(63), primary_key=True)
    field_key: Mapped[str] = mapped_column(String(100), nullable=False)
    expression: Mapped[str] = mapped_column(Text, nullable=False)
    unique_index: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # False marks an index the catalog no longer wants; the applier drops it.
    desired: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    state: Mapped[str] = mapped_column(String(10), nullable=False, default="pending")
    error: Mapped[str | None] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime)
