from datetime import datetime

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, TimestampMixin, new_uuid

# Roles used to be a ladder of three defined right here — readonly, tester,
# admin — with a rank apiece, so "which of these two grants less?" could be
# answered by comparing integers. They are rows now (models/role.py) and what
# one grants is a set of permissions, so the same questions are answered by set
# containment in services/permissions.py: `grantable_roles` replaces the
# ceiling, and an intersection replaces the comparison. A ladder cannot
# describe two roles that simply overlap, which installation-defined roles do.


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    username: Mapped[str] = mapped_column(String(150), unique=True, nullable=False, index=True)
    email: Mapped[str | None] = mapped_column(String(255))
    auth_provider: Mapped[str] = mapped_column(String(20), default="local")
    authentik_sub: Mapped[str | None] = mapped_column(String(255), unique=True)
    # A role's `slug` (models/role.py), not a foreign key: this column predates
    # the roles table, and a user whose role was deleted or was mapped from an
    # OIDC group this installation never defined must still load — they simply
    # hold a role that grants nothing.
    role: Mapped[str] = mapped_column(String(20), default="readonly")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    last_login_at: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime]
