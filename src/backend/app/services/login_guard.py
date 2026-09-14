"""Login throttling.

bcrypt makes one guess expensive; nothing here made a million of them slow.
This counts recent failures straight out of the audit log — which already
records every one, with the attempted username and the client address — so
there is no second store to keep in step, nothing to clear on restart, and a
throttle that holds across replicas because the state is in the database
rather than in one process's memory.

The window slides rather than locking for a fixed term: an attempt is refused
while too many failures sit inside the last WINDOW, and the refusal lifts by
itself as the oldest of them ages out. Retry-After says exactly when.

Two counters, because either alone has a hole. Per-username alone lets someone
spray one guess each across every account from one address; per-address alone
lets a botnet grind one account. The username limit is the tighter of the two
— an office behind a single NAT address is a legitimate source of several
people's typos, so the address limit has to be loose enough not to lock the
office out over them.

Both counters restart from the account's (or address's) last SUCCESSFUL login:
failures before that are the typos of someone who then got in, not an attack
in progress.
"""

from datetime import datetime, timedelta

from fastapi import HTTPException, Request
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from ..db import as_utc, utcnow
from ..models import AuditLog

FAILED = "auth.login_failed"
SUCCESS = "auth.login"

#: How far back failures count.
WINDOW = timedelta(minutes=15)
#: Failures against one username inside the window before it stops answering.
MAX_PER_USERNAME = 10
#: Failures from one address inside the window before it stops being answered.
MAX_PER_IP = 30


def _client_ip(request: Request | None) -> str | None:
    return request.client.host if request and request.client else None


def _last_success(db: Session, criterion) -> datetime | None:
    ts = db.scalar(select(func.max(AuditLog.timestamp)).where(AuditLog.action == SUCCESS, criterion))
    return as_utc(ts) if ts else None


def _failures_since(db: Session, criterion, since: datetime) -> tuple[int, datetime | None]:
    """How many failures match, and when the oldest of them was."""
    count, oldest = db.execute(
        select(func.count(), func.min(AuditLog.timestamp))
        .select_from(AuditLog)
        .where(AuditLog.action == FAILED, AuditLog.timestamp >= since, criterion)
    ).one()
    return count or 0, as_utc(oldest) if oldest else None


def check_login_allowed(db: Session, username: str, request: Request | None) -> None:
    """Refuse an attempt that is one too many recent failures. Raises 429.

    Called before the password is looked at, so a refused attempt costs a
    couple of indexed counts instead of a bcrypt verification — otherwise the
    throttle would stop the guessing but not the CPU burn behind it.

    The answer is the same whether or not the username exists: failures are
    recorded for unknown usernames too, so a 429 here reveals nothing that a
    401 did not.
    """
    now = utcnow()
    window_start = now - WINDOW
    ip = _client_ip(request)

    checks = [
        # Failed logins record the attempted username as the audit *entity*:
        # the account usually does not exist by then, so the row's `username`
        # column is "system" and only `entity_id` says what was tried. Paired
        # with its entity_type, which `action` already implies, so the lookup
        # can use the (entity_type, entity_id) index that was already there.
        (
            and_(AuditLog.entity_type == "auth", AuditLog.entity_id == username),
            AuditLog.username == username,
            MAX_PER_USERNAME,
        ),
    ]
    if ip:
        checks.append((AuditLog.ip_address == ip, AuditLog.ip_address == ip, MAX_PER_IP))

    for failed_by, success_by, limit in checks:
        last_ok = _last_success(db, success_by)
        since = max(window_start, last_ok) if last_ok else window_start
        count, oldest = _failures_since(db, failed_by, since)
        if count >= limit and oldest is not None:
            retry_after = max(int((oldest + WINDOW - now).total_seconds()) + 1, 1)
            raise HTTPException(
                status_code=429,
                detail="Too many failed sign-in attempts. Try again shortly.",
                headers={"Retry-After": str(retry_after)},
            )
