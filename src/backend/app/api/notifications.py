"""Reading your own notifications, and marking them read.

Every endpoint here is scoped to the calling user by the query itself, never by
an id in the path: there is no way to name someone else's notification, so
there is nothing to get wrong later. Notifications are created by the checkout
sweep (services/checkout.py), never by a request, so there is no POST to make
one.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db, utcnow
from ..models import Notification, User
from ..schemas import NotificationOut, Page, UnreadCountOut
from .deps import get_current_user

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=Page[NotificationOut])
def list_notifications(
    unread_only: bool = False,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """This user's notifications, newest first.

    Paged rather than capped: the bell only ever wants the first page, but the
    profile archive pages back through everything.
    """
    q = select(Notification).where(Notification.user_id == user.id)
    if unread_only:
        q = q.where(Notification.read_at.is_(None))
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    items = db.scalars(
        q.order_by(Notification.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return Page(
        items=[NotificationOut.model_validate(n) for n in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/unread_count", response_model=UnreadCountOut)
def unread_count(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Just the badge number — polled every minute, so it stays this cheap."""
    count = db.scalar(
        select(func.count())
        .select_from(Notification)
        .where(Notification.user_id == user.id, Notification.read_at.is_(None))
    )
    return UnreadCountOut(unread=count or 0)


@router.post("/{notification_id}/read", response_model=NotificationOut)
def mark_read(
    notification_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    notification = db.scalar(
        select(Notification).where(
            Notification.id == notification_id, Notification.user_id == user.id
        )
    )
    if notification is None:
        raise HTTPException(status_code=404, detail="Notification not found")
    # Already-read stays at the time it was first read: re-reading a
    # notification is not an event, and moving the stamp would reorder nothing
    # but confuse anyone looking at it.
    if notification.read_at is None:
        notification.read_at = utcnow()
        db.commit()
        db.refresh(notification)
    return NotificationOut.model_validate(notification)


@router.post("/read_all", response_model=UnreadCountOut)
def mark_all_read(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Clear the badge. Returns the new count, which is always zero."""
    stamp = utcnow()
    for notification in db.scalars(
        select(Notification).where(
            Notification.user_id == user.id, Notification.read_at.is_(None)
        )
    ).all():
        notification.read_at = stamp
    db.commit()
    return UnreadCountOut(unread=0)
