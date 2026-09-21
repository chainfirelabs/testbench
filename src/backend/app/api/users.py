from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import settings
from ..core.security import hash_password
from ..db import as_utc, get_db, utcnow
from ..models import Role, User
from ..schemas import Page, PasswordReset, UserCreate, UserOut, UserUpdate
from ..services.audit import field_diff, log_action
from ..services.list_filters import exclude_clause, excluded_values, include_clause
from .deps import require_users_manage, require_users_manage_session
from ..services.permissions import USERS_MANAGE, role_permissions


def _known_role(db: Session, slug: str) -> None:
    """Refuse a role this installation has not defined.

    Roles are rows now, so "readonly, tester or admin" is no longer the answer —
    but neither is "anything". A user carries a role by slug, and one naming a
    role that does not exist grants nothing at all: an account that cannot use
    the app, created without a word of complaint.
    """
    if db.scalar(select(Role.id).where(Role.slug == slug)):
        return
    available = sorted(db.scalars(select(Role.slug)))
    raise HTTPException(
        status_code=422,
        detail=f"No role '{slug}'. Available roles: {', '.join(available)}.",
    )


def _guard_last_manager(db: Session, target: User, new_role: str) -> None:
    """Refuse a change that would leave nobody able to manage accounts.

    The mirror of the rule in `api/roles.py`: that one stops the permission
    being edited off the last role that has it, this one stops the last account
    holding such a role being moved off it. Either way the failure is the same
    — a deployment whose only way back in is a database console.
    """
    if USERS_MANAGE not in role_permissions(db, target.role):
        return
    if USERS_MANAGE in role_permissions(db, new_role):
        return
    managing = {
        slug for slug in db.scalars(select(Role.slug))
        if USERS_MANAGE in role_permissions(db, slug)
    }
    others = db.scalar(
        select(func.count(User.id)).where(
            User.role.in_(managing), User.is_active.is_(True), User.id != target.id,
        )
    ) or 0
    if not others:
        raise HTTPException(
            status_code=409,
            detail=(
                f"'{target.username}' is the only active account that can manage "
                "users and roles. Give another account such a role first."
            ),
        )

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db), user: User = Depends(require_users_manage)):
    users = db.scalars(select(User).order_by(User.username)).all()
    now = utcnow()
    ttl = timedelta(hours=settings.jwt_expires_hours)
    out = []
    for u in users:
        o = UserOut.model_validate(u)
        o.is_online = u.last_login_at is not None and (now - as_utc(u.last_login_at)) < ttl
        out.append(o)
    return out


@router.get("/paged", response_model=Page[UserOut])
def list_users_paged(
    request: Request,
    search: str | None = None,
    sort: str = "username",
    order: str = "asc",
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
    user: User = Depends(require_users_manage),
):
    q = select(User)
    if search:
        like = f"%{search}%"
        q = q.where(or_(User.username.ilike(like), User.email.ilike(like),
                        User.role.ilike(like), User.auth_provider.ilike(like)))
    filter_columns = {
        "username": User.username, "email": User.email, "role": User.role,
        "auth_provider": User.auth_provider, "created_at": User.created_at,
        "last_login_at": User.last_login_at,
    }
    for key, value in request.query_params.items():
        if not key.startswith(("exclude__", "include__")):
            continue
        keeping = key.startswith("include__")
        field = key.removeprefix("include__" if keeping else "exclude__")
        values = excluded_values(value)
        if field == "is_online":
            # Two values, so the ticked and unticked sides say the same thing
            # from opposite ends: what is kept is what is not dropped.
            dropped = {str(item).lower() for item in values}
            if keeping:
                dropped = {"true", "false"} - dropped
            cutoff = utcnow() - timedelta(hours=settings.jwt_expires_hours)
            if "true" in dropped and "false" in dropped:
                q = q.where(User.id.is_(None))
            elif "true" in dropped:
                q = q.where(or_(User.last_login_at.is_(None), User.last_login_at < cutoff))
            elif "false" in dropped:
                q = q.where(User.last_login_at >= cutoff)
            continue
        expression = filter_columns.get(field)
        pick = include_clause if keeping else exclude_clause
        clause = pick(expression, values) if expression is not None else None
        if clause is not None:
            q = q.where(clause)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    columns = {
        "username": User.username, "email": User.email, "role": User.role,
        "auth_provider": User.auth_provider, "created_at": User.created_at,
        "last_login_at": User.last_login_at,
    }
    column = columns.get(sort, User.username)
    q = q.order_by(column.desc() if order == "desc" else column.asc(), User.id)
    users = db.scalars(q.offset((page - 1) * page_size).limit(page_size)).all()
    now = utcnow()
    ttl = timedelta(hours=settings.jwt_expires_hours)
    items = []
    for item in users:
        output = UserOut.model_validate(item)
        output.is_online = item.last_login_at is not None and (now - as_utc(item.last_login_at)) < ttl
        items.append(output)
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.post("", response_model=UserOut, status_code=201)
def create_user(
    body: UserCreate,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_users_manage_session),
):
    """Create a local account.

    For people who have no Authentik identity — a contractor, a lab machine's
    operator, a break-glass admin for when SSO itself is what is broken. SSO
    users are not created here; they appear on first login.
    """
    # Checked case-insensitively even though the unique index is exact: two
    # accounts differing only in case are a login footgun, not a feature. The
    # IntegrityError below still guards the race between this and the insert.
    taken = db.scalar(
        select(User.id).where(func.lower(User.username) == body.username.lower())
    )
    if taken:
        raise HTTPException(status_code=409, detail=f"Username '{body.username}' is already taken")
    _known_role(db, body.role)

    user = User(
        username=body.username,
        email=body.email,
        auth_provider="local",
        role=body.role,
        is_active=True,
        password_hash=hash_password(body.password),
    )
    db.add(user)
    try:
        db.flush()  # assign the id before the audit row references it
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"Username '{body.username}' is already taken")

    # Username, role and email — never the password, and never its hash.
    log_action(
        db, actor, "user.create", "user", user.id,
        {"username": user.username, "role": user.role, "email": user.email}, request,
    )
    db.commit()
    db.refresh(user)
    return UserOut.model_validate(user)


@router.patch("/{user_id}", response_model=UserOut)
def update_user(
    user_id: str,
    body: UserUpdate,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_users_manage_session),
):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    if target.id == actor.id and body.is_active is False:
        raise HTTPException(status_code=400, detail="Cannot deactivate yourself")
    if body.is_active is False:
        # Deactivating the last account that can manage roles locks the
        # installation out just as thoroughly as demoting it.
        _guard_last_manager(db, target, "")

    updates = body.model_dump(exclude_unset=True)
    if "role" in updates and updates["role"] != target.role:
        if target.auth_provider != "local":
            raise HTTPException(
                status_code=400,
                detail="An SSO user's role comes from their Authentik groups and is "
                       "re-resolved at every login — change it in the IdP.",
            )
        if target.id == actor.id:
            # The same trap as deactivating yourself, one step quieter: demote
            # your own account and the page you did it from is now forbidden.
            raise HTTPException(status_code=400, detail="Cannot change your own role")
        _known_role(db, updates["role"])
        _guard_last_manager(db, target, updates["role"])

    tracked = ("is_active", "email", "role")
    old = {f: getattr(target, f) for f in tracked}
    for field, value in updates.items():
        setattr(target, field, value)
    new = {f: getattr(target, f) for f in tracked}
    log_action(db, actor, "user.update", "user", target.id, {"diff": field_diff(old, new)}, request)
    db.commit()
    db.refresh(target)
    return UserOut.model_validate(target)


@router.post("/{user_id}/password", response_model=UserOut)
def reset_password(
    user_id: str,
    body: PasswordReset,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_users_manage_session),
):
    """Set a local account's password, without needing the old one.

    Existing sessions are deliberately left alone: tokens are stateless JWTs, so
    there is nothing to revoke short of rotating the signing secret for
    everybody. Deactivate the account if the point is to cut someone off now —
    `api/deps.py` rejects an inactive user on the very next request.
    """
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    if target.auth_provider != "local":
        raise HTTPException(
            status_code=400,
            detail=f"'{target.username}' signs in through SSO and has no password here.",
        )
    target.password_hash = hash_password(body.password)
    # No password, no hash, no length — only that it happened, and to whom.
    log_action(
        db, actor, "user.password_reset", "user", target.id,
        {"username": target.username, "self": target.id == actor.id}, request,
    )
    db.commit()
    db.refresh(target)
    return UserOut.model_validate(target)
