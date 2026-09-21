"""Roles: what a role is called, and what it lets someone do.

Guarded by `users.manage` — the same permission that assigns a role to an
account, because being able to edit what a role grants and being able to hand
it out are the same power one step apart.

Two rules stop this from being a way to lock an installation out of itself:

* A built-in role cannot be deleted and its slug cannot change. `admin` in
  particular is named by the seeded first account and by the OIDC default-role
  setting; a deployment where it does not resolve has no way back in.
* `users.manage` cannot be taken off the last role that has it. Without that,
  one save of the admin role ends with nobody able to edit roles, and the only
  fix is a database console.

A role in use cannot be deleted either. Users carry a role by slug rather than
by foreign key (see models/role.py), so deleting one out from under them would
not fail — it would quietly leave them holding a role that grants nothing.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db, utcnow
from ..models import Role, User
from ..schemas import PermissionOut, RoleCreate, RoleOut, RoleUpdate
from ..services.audit import field_diff, log_action
from ..services.permissions import (
    PERMISSIONS,
    USERS_MANAGE,
    valid_permissions,
)
from .deps import get_current_user, require_users_manage

router = APIRouter(prefix="/roles", tags=["roles"])


def _user_counts(db: Session) -> dict[str, int]:
    """How many accounts hold each role, in one query."""
    rows = db.execute(select(User.role, func.count(User.id)).group_by(User.role)).all()
    return {role: count for role, count in rows}


def _out(role: Role, counts: dict[str, int]) -> RoleOut:
    item = RoleOut.model_validate(role)
    item.permissions = valid_permissions(role.permissions)
    item.user_count = counts.get(role.slug, 0)
    return item


def _get_role(db: Session, key: str) -> Role:
    """Resolve by slug or id — the UI holds ids, a script will reach for slugs."""
    role = db.scalar(select(Role).where(Role.slug == key)) or db.get(Role, key)
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")
    return role


def _guard_last_manager(db: Session, role: Role, permissions: list[str]) -> None:
    """Refuse a change that would leave nobody able to manage roles."""
    if USERS_MANAGE in permissions or USERS_MANAGE not in valid_permissions(role.permissions):
        return
    others = [
        other for other in db.scalars(select(Role).where(Role.id != role.id))
        if USERS_MANAGE in valid_permissions(other.permissions)
    ]
    if not others:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This is the only role that can manage users and roles. "
                "Grant that permission to another role first."
            ),
        )


# Declared before `/{role_id}`, which would otherwise match "permissions".
@router.get("/permissions", response_model=list[PermissionOut])
def list_permissions(user: User = Depends(get_current_user)):
    """Everything a role can grant, with what each one means.

    A static description of this build rather than of this installation, but
    still behind a login like everything else: it is the list the role editor
    renders its checkboxes from, and there is no reason for it to be the one
    endpoint an anonymous caller can read.
    """
    return [PermissionOut(**vars(item)) for item in PERMISSIONS]


@router.get("", response_model=list[RoleOut])
def list_roles(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Every role, built-in first, then the installation's own alphabetically.

    Readable by any signed-in user, not just by someone who can edit them: the
    API-key dialog offers the roles the holder may mint a key with, and the
    device pages explain why an action is unavailable by naming a role. Neither
    is privileged information — the list says what roles exist, which the login
    screen's own account already implies.
    """
    counts = _user_counts(db)
    roles = sorted(
        db.scalars(select(Role)).all(),
        key=lambda role: (not role.is_builtin, role.name.lower()),
    )
    return [_out(role, counts) for role in roles]


@router.get("/{role_id}", response_model=RoleOut)
def get_role(
    role_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return _out(_get_role(db, role_id), _user_counts(db))


@router.post("", response_model=RoleOut, status_code=201)
def create_role(
    body: RoleCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_users_manage),
):
    if db.scalar(select(Role.id).where(Role.slug == body.slug)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A role with the slug '{body.slug}' already exists",
        )
    role = Role(
        slug=body.slug,
        name=body.name.strip(),
        description=(body.description or "").strip() or None,
        permissions=valid_permissions(body.permissions),
        is_builtin=False,
    )
    db.add(role)
    db.flush()
    log_action(db, user, "role.create", "role", role.id, {
        "slug": role.slug, "name": role.name, "permissions": role.permissions,
    }, request)
    db.commit()
    db.refresh(role)
    return _out(role, _user_counts(db))


@router.patch("/{role_id}", response_model=RoleOut)
def update_role(
    role_id: str,
    body: RoleUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_users_manage),
):
    """Rename a role or change what it grants.

    A built-in role's permissions are editable — what a tester may do is an
    installation's business — but its slug is not; see the module docstring.
    """
    role = _get_role(db, role_id)
    before = {
        "name": role.name,
        "description": role.description,
        "permissions": valid_permissions(role.permissions),
    }
    values = body.model_dump(exclude_unset=True)
    if "permissions" in values:
        permissions = valid_permissions(values["permissions"])
        _guard_last_manager(db, role, permissions)
        role.permissions = permissions
    if "name" in values and values["name"]:
        role.name = values["name"].strip()
    if "description" in values:
        role.description = (values["description"] or "").strip() or None
    role.updated_at = utcnow()
    after = {
        "name": role.name,
        "description": role.description,
        "permissions": valid_permissions(role.permissions),
    }
    log_action(db, user, "role.update", "role", role.id, {
        "slug": role.slug, "diff": field_diff(before, after),
    }, request)
    db.commit()
    db.refresh(role)
    return _out(role, _user_counts(db))


@router.delete("/{role_id}", status_code=204)
def delete_role(
    role_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_users_manage),
):
    role = _get_role(db, role_id)
    if role.is_builtin:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"'{role.name}' is a built-in role and cannot be deleted.",
        )
    holders = _user_counts(db).get(role.slug, 0)
    if holders:
        # Users carry a role by slug, so this would succeed silently and leave
        # them holding a role that grants nothing. Refused instead, naming the
        # number so the fix ("move them first") is obvious.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"{holders} account{'s' if holders != 1 else ''} still "
                f"{'have' if holders != 1 else 'has'} the '{role.name}' role. "
                "Move them to another role first."
            ),
        )
    _guard_last_manager(db, role, [])
    log_action(db, user, "role.delete", "role", role.id, {
        "slug": role.slug, "name": role.name,
        "permissions": valid_permissions(role.permissions),
    }, request)
    db.delete(role)
    db.commit()
    return None
