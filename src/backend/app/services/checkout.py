"""Checkout deadlines: what "today" means, and what counts as overdue.

A device is checked out until a date. Two things follow from that, and both
live here so the API, the background sweep and anything else agree on them:

* `today()` — the single definition of the current day. Stamping a checkout,
  warning about one, and deciding whether one is late all read it, so they
  cannot disagree by a day at the edges. It is deliberately timezone-aware:
  with a bare UTC date, a 7pm US-Eastern checkout stamps tomorrow.
* `overdue_clause()` — overdue is DERIVED, never stored. There is no sixth
  device status and no flag to fall out of step: a device is overdue exactly
  while it is checked out, has a due date, and that date has passed. Extend the
  date and the device stops being overdue the moment the row is saved.
"""

import logging
import threading
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import and_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ..config import settings
from ..db import SessionLocal, new_uuid, utcnow
from ..models import Device, Notification, User

logger = logging.getLogger(__name__)


def _resolve_tz() -> ZoneInfo:
    """The configured zone, or UTC if it does not name one.

    Resolved once at import: an unknown zone is a deployment mistake worth one
    loud line at startup, not an exception raised from inside every request
    that happens to touch a date.
    """
    try:
        return ZoneInfo(settings.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning(
            "TB_TIMEZONE=%r is not a known timezone; using UTC for checkout dates",
            settings.timezone,
        )
        return ZoneInfo("UTC")


TZ = _resolve_tz()


def today() -> date:
    """The current day, in the fleet's timezone."""
    return datetime.now(TZ).date()


def overdue_clause():
    """SQL for "this device is late".

    Used by GET /devices/overdue, by the `overdue` list filter (and so by the
    CSV/JSON export and the MCP device search that go through the same query),
    and by the sweep that sends the notifications.
    """
    return and_(
        Device.status == "checked_out",
        Device._data["checkout_due"].astext.isnot(None),
        Device._data["checkout_due"].astext < today().isoformat(),
    )


# ---------- notifications ----------
#
# The sweep writes nothing but notifications. It never changes a device: a
# device that is overdue is still checked out, because whoever has it still has
# it, and quietly marking it available would make the fleet lie about where it
# is. Red in the grid and a message in the bell are the whole intervention.


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _notify(
    db: Session,
    user_id: str,
    kind: str,
    title: str,
    body: str | None,
    dedupe_key: str,
    entity_type: str | None = None,
    entity_id: str | None = None,
) -> None:
    """Insert one notification, unless that exact one already exists.

    `dedupe_key` carries the identity — who, what, and which day's notice — so
    the sweep can run as often as it likes without repeating itself. The unique
    index does the deciding; nothing here has to remember what was sent.
    """
    db.execute(
        pg_insert(Notification)
        .values(
            id=new_uuid(),
            user_id=user_id,
            kind=kind,
            title=title,
            body=body,
            entity_type=entity_type,
            entity_id=entity_id,
            dedupe_key=dedupe_key,
            created_at=utcnow(),
        )
        .on_conflict_do_nothing(index_elements=["dedupe_key"])
    )


def _warn_holder(db: Session, device: Device, days: int) -> None:
    """One notice a day over the warning window, and on the due date itself."""
    when = "today" if days == 0 else f"in {_plural(days, 'day')}"
    _notify(
        db,
        user_id=device.checked_out_by,
        kind="checkout_due_soon",
        title=f"{device.unique_id} is due back {when}",
        body=(device.checkout_purpose or None),
        dedupe_key=f"checkout:{device.id}:{device.checkout_due}:d-{days}",
        entity_type="device",
        entity_id=device.id,
    )


def _tell_holder_overdue(db: Session, device: Device, days_over: int, stamp: date) -> None:
    """Once a day for as long as it stays out.

    The date is in the key rather than the day count, so a device that is
    returned and checked out again does not inherit yesterday's notice.
    """
    _notify(
        db,
        user_id=device.checked_out_by,
        kind="checkout_overdue",
        title=f"{device.unique_id} is {_plural(days_over, 'day')} overdue",
        body=(
            f"Due back {device.checkout_due}."
            + (f" Checked out for: {device.checkout_purpose}" if device.checkout_purpose else "")
        ),
        dedupe_key=f"checkout:{device.id}:{device.checkout_due}:overdue:{stamp}",
        entity_type="device",
        entity_id=device.id,
    )


def _digest_admins(db: Session, overdue: list[Device], stamp: date) -> None:
    """One notification per admin per day, naming the whole list.

    Deliberately not one per admin per device: a fleet with a bad week would
    generate n x m notifications, and a bell that arrives full is a bell people
    stop opening.
    """
    lines = [
        f"{d.unique_id} — {d.checked_out_user.username if d.checked_out_user else 'unknown'}"
        f", {_plural((stamp - d.checkout_due).days, 'day')} over"
        for d in overdue
    ]
    admins = db.scalars(
        select(User).where(User.role == "admin", User.is_active.is_(True))
    ).all()
    for admin in admins:
        _notify(
            db,
            user_id=admin.id,
            kind="checkout_overdue_digest",
            title=f"{_plural(len(overdue), 'device')} overdue",
            body="\n".join(lines),
            dedupe_key=f"overdue_digest:{admin.id}:{stamp}",
        )


def sweep(db: Session) -> dict:
    """Send the day's checkout notifications. Changes no devices.

    Returns a small summary so the caller (and the log line) can say what it
    did, which is the only way to tell a quiet day from a broken sweep.
    """
    stamp = today()
    devices = db.scalars(
        select(Device).where(
            Device.status == "checked_out",
            Device.checkout_due.isnot(None),
            # Nobody to tell. A checkout with no holder predates the stamp or
            # was made by a user who has since been deleted.
            Device.checked_out_by.isnot(None),
        )
    ).all()

    overdue: list[Device] = []
    warned = 0
    for device in devices:
        days = (device.checkout_due - stamp).days
        if days < 0:
            overdue.append(device)
            _tell_holder_overdue(db, device, -days, stamp)
        elif days <= settings.checkout_warn_days:
            _warn_holder(db, device, days)
            warned += 1

    if overdue:
        _digest_admins(db, overdue, stamp)

    db.commit()
    return {"checked": len(devices), "warned": warned, "overdue": len(overdue)}


def start_checkout_sweep_loop() -> None:
    """Run the sweep on a timer (no-op if the interval is <= 0).

    Same shape as the periodic scan in services/scan.py: a daemon thread on a
    sleep loop, started from the app lifespan. Safe to run more than once —
    every insert is deduplicated by the database — but this assumes the single
    backend replica the deployment ships with (k3s/04-backend.yaml).
    """
    minutes = settings.checkout_sweep_minutes
    if minutes <= 0:
        logger.info("Checkout deadline sweep disabled (TB_CHECKOUT_SWEEP_MINUTES=%s)", minutes)
        return

    def loop() -> None:
        while True:
            time.sleep(max(1, minutes) * 60)
            db = SessionLocal()
            try:
                result = sweep(db)
                if result["warned"] or result["overdue"]:
                    logger.info("Checkout sweep: %s", result)
            except Exception:  # noqa: BLE001
                db.rollback()
                logger.exception("Checkout deadline sweep failed")
            finally:
                db.close()

    threading.Thread(target=loop, name="checkout-sweep", daemon=True).start()
    logger.info("Checkout deadline sweep enabled: every %d minute(s)", minutes)
