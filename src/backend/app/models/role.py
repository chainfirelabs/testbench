from sqlalchemy import Boolean, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, TimestampMixin, new_uuid


class Role(TimestampMixin, Base):
    """A named set of permissions a user can be given.

    Roles used to be a fixed ladder of three — readonly, tester, admin — with
    every guard in the API asking "is this an admin?" or "may this one write?".
    That answers two questions and no others: an installation that wants
    somebody who can read the audit log without also being able to reset
    passwords had to make them an admin, which grants both.

    So a role is a row now, and what it grants is its `permissions` list.
    The three built-in roles still exist and still mean what they meant — they
    are seeded with the permission sets that reproduce the old ladder exactly,
    so an upgrade changes nobody's access — but they are rows like any other,
    and an installation can add its own beside them.

    `slug` rather than `id` is what a user carries (`users.role`), because that
    is what the column already held: the three built-in slugs are the three old
    role names, so every existing row, every existing API key and every OIDC
    group mapping keeps working untouched.
    """

    __tablename__ = "roles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    # Referenced by `users.role` and `api_keys.role`. Deliberately not a foreign
    # key: those columns predate this table and hold values (a role removed from
    # an OIDC mapping, say) that must not stop a user row from loading.
    slug: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    permissions: Mapped[list] = mapped_column(JSONB, default=list)
    # The three that ship. They cannot be deleted and their slugs cannot be
    # changed — `admin` is named in the OIDC default-role setting, in the seeded
    # admin account and in the API-key ceiling — but their permissions can be
    # edited, because an installation's idea of what a tester may do is its own.
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
