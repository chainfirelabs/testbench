"""In-app messages addressed to one user.

The app had no way to tell anyone anything: toasts live and die inside a single
view, and nothing survived a page load. Checkout deadlines need to reach a
person who is not looking at the device — so this is a small mailbox, read by
the bell in the header and by the archive on the profile page.

The one unusual column is `dedupe_key`. The sweep runs hourly and asks the same
question every time ("is dev-0042 overdue?"), so it would create the same
notification on every pass. Rather than remembering what it has sent, it builds
a key that encodes the notification's identity — the device, the due date, and
which day's notice this is — and inserts ON CONFLICT DO NOTHING. Sending once a
day is then a property of the schema, and it stays true across restarts,
backfills, and two sweeps somehow running at once.
"""

from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, new_uuid, utcnow

# What a notification is about. The bell renders an icon and a link per kind.
NOTIFICATION_KINDS = (
    "checkout_due_soon",  # to the holder, once a day over the warning window
    "checkout_overdue",  # to the holder, once a day while it stays out
    "checkout_overdue_digest",  # to each admin, once a day, naming the whole list
)


class Notification(Base):
    __tablename__ = "notifications"

    __table_args__ = (
        # The unread count and the panel both read "this user's, newest first".
        Index("ix_notifications_user_created", "user_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    # What it is about, so the panel can link to it. Not a foreign key: a
    # notification about a deleted device is still a true thing that was said.
    entity_type: Mapped[str | None] = mapped_column(String(50))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    dedupe_key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    read_at: Mapped[datetime | None]
