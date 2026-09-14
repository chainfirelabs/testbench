from datetime import datetime

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, TimestampMixin, new_uuid

# Roles are a ladder, not a set: everything a tester may do, an admin may do
# too. The rank is what lets an API key be capped at its owner's role without
# spelling out every pair (see ApiKey and api/deps.py).
ROLE_RANK = {"readonly": 0, "tester": 1, "admin": 2}


def role_ceiling(role: str) -> list[str]:
    """The roles a holder of `role` may grant to a key they mint."""
    rank = ROLE_RANK.get(role)
    if rank is None:
        return []
    return [r for r, n in ROLE_RANK.items() if n <= rank]


def lower_role(a: str, b: str) -> str:
    """Whichever of the two roles grants less. Unknown roles grant nothing."""
    if a not in ROLE_RANK:
        return a if b not in ROLE_RANK else b
    if b not in ROLE_RANK:
        return a
    return a if ROLE_RANK[a] <= ROLE_RANK[b] else b


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    username: Mapped[str] = mapped_column(String(150), unique=True, nullable=False, index=True)
    email: Mapped[str | None] = mapped_column(String(255))
    auth_provider: Mapped[str] = mapped_column(String(20), default="local")
    authentik_sub: Mapped[str | None] = mapped_column(String(255), unique=True)
    role: Mapped[str] = mapped_column(String(20), default="readonly")  # admin|tester|readonly
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    last_login_at: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime]
