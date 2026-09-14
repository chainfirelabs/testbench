from datetime import datetime

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, TimestampMixin, as_utc, new_uuid

# The literal prefix every key carries. It is what lets `get_current_user`
# tell an API key from a JWT without trying to decode one as the other, and it
# is what secret scanners key on when a key is pasted somewhere public.
API_KEY_PREFIX = "tb"

# Prefixes accepted on a presented key. New keys are minted with
# `API_KEY_PREFIX`; "dm" is the name this project used before TestBench and is
# still carried by keys minted back then, which are otherwise perfectly valid
# rows — only the plaintext shape changed, not the stored hash.
ACCEPTED_KEY_PREFIXES = (API_KEY_PREFIX, "dm")

# How many characters of the key are the public lookup handle. Long enough to
# be unique in any realistic keyring, short enough to show in a UI column.
PREFIX_LENGTH = 12


class ApiKey(TimestampMixin, Base):
    """A long-lived credential a user mints for themselves.

    Presented as `Authorization: Bearer tb_<prefix>_<secret>`, it authenticates
    exactly like a login token but does not expire in eight hours, which is what
    makes it usable by scripts and by the MCP server.

    A key carries its own `role`, which may be lower than its owner's but never
    higher — a tester can mint a readonly key to hand to a script that should
    not be able to write. The ceiling is enforced twice: once here at creation,
    and again on every request, where the effective role is the *lower* of the
    key's role and the owner's current role. The second check is the one that
    matters: demoting a user has to weaken the keys they already hold, and a
    role frozen into the row at creation time would not do that.

    Only `key_hash` is stored. The plaintext is shown once, at creation, and is
    unrecoverable afterwards.
    """

    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # What the user called it, so they can tell two keys apart when revoking.
    label: Mapped[str] = mapped_column(String(100), nullable=False)

    # The non-secret half, used to find the row before verifying the secret.
    # Unique so the lookup is a single indexed read rather than a scan-and-compare
    # over every key in the table.
    prefix: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)

    # SHA-256 of the secret half, hex. Not bcrypt: this is verified on *every*
    # request rather than once per login, and the secret is 256 bits of CSPRNG
    # output, not a human-chosen password — there is no dictionary to slow down,
    # only latency to add. Passwords keep bcrypt (see core/security.py).
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    role: Mapped[str] = mapped_column(String(20), nullable=False, default="readonly")

    # NULL means the key does not expire on its own.
    expires_at: Mapped[datetime | None] = mapped_column(default=None)
    last_used_at: Mapped[datetime | None] = mapped_column(default=None)
    # Set instead of deleting the row, so an audit entry naming a revoked key
    # can still be resolved back to who owned it and what it was called.
    revoked_at: Mapped[datetime | None] = mapped_column(default=None)

    def is_usable(self, now: datetime) -> bool:
        if self.revoked_at is not None:
            return False
        return self.expires_at is None or as_utc(self.expires_at) > as_utc(now)
