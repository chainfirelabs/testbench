import secrets

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import as_utc, get_db, utcnow
from ..models import ApiKey, User
from ..core.security import decode_token, hash_api_key, split_api_key, verify_api_key
from ..config import settings
from ..services.permissions import (
    AUDIT_VIEW,
    DEVICES_EDIT,
    PLUGINS_MANAGE,
    SCHEMA_MANAGE,
    SETTINGS_MANAGE,
    SOFTWARE_EDIT,
    TESTS_EDIT,
    USERS_MANAGE,
    VIEWS_SAVE,
    caller_permissions,
    role_permissions,
)

bearer_scheme = HTTPBearer(auto_error=False)

# How stale `api_keys.last_used_at` is allowed to get. Stamping it on every
# request would turn every read into a write; a minute's resolution is enough
# to answer "is this key still in use?" without that cost.
LAST_USED_RESOLUTION_SECONDS = 60

# A hash of nothing in particular, compared against when the presented prefix
# matches no row. Without it, an unknown prefix returns before hashing and the
# response time says whether a prefix exists.
_DUMMY_KEY_HASH = hash_api_key("tb-timing-equaliser")


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


def _active_user(db: Session, user_id: str | None) -> User:
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise _unauthorized("User not found or inactive")
    return user


def _from_jwt(db: Session, token: str) -> User:
    try:
        payload = decode_token(token)
    except Exception:
        raise _unauthorized("Invalid or expired token")
    return _active_user(db, payload.get("sub"))


def _from_api_key(request: Request, db: Session, prefix: str, secret: str) -> User:
    key = db.scalar(select(ApiKey).where(ApiKey.prefix == prefix))
    # Verified even when the prefix matched nothing, so the two cases cost the same.
    valid = verify_api_key(secret, key.key_hash if key is not None else _DUMMY_KEY_HASH)
    if key is None or not valid:
        raise _unauthorized("Invalid API key")

    now = utcnow()
    if key.revoked_at is not None:
        raise _unauthorized("This API key has been revoked")
    if not key.is_usable(now):
        raise _unauthorized("This API key has expired")

    user = _active_user(db, key.user_id)

    if key.last_used_at is None or (now - as_utc(key.last_used_at)).total_seconds() > LAST_USED_RESOLUTION_SECONDS:
        key.last_used_at = now
        db.commit()

    # Recorded for the audit log, so a row written by a key says which one.
    request.state.api_key_id = key.id
    request.state.api_key_label = key.label
    request.state.auth_method = "api_key"

    # The key's role is capped against the owner's *current* role, not the one
    # they held when they minted it: demoting someone has to weaken the keys
    # they already handed out, and a stored grant alone would not do that.
    #
    # The cap is the intersection of the two permission sets, which is what a
    # ladder's "whichever grants less" meant when there were only three roles
    # and each was a superset of the one below. With roles an installation
    # defines, two of them need not be comparable at all — a key carrying
    # "auditor" held by a "tester" grants what both grant, which is nothing
    # either of them could not already do.
    owned = role_permissions(db, user.role)
    effective = role_permissions(db, key.role) & owned

    # Detached on purpose. The permission set is carried on the User object for
    # the rest of the request, and mutating a session-attached row would mean
    # the next `db.commit()` writes the downgrade back to the users table.
    # Expunging first makes the object a local copy that no flush can reach.
    db.expunge(user)
    user.granted_permissions = effective
    # Kept honest for anything that reads the name rather than the set — the
    # 403 messages, for one. A key granting less than its owner is not acting
    # as its owner's role, so it should not say that it is.
    if effective != owned:
        user.role = key.role
    return user


def _from_mcp_internal(request: Request) -> User:
    """A chart-bundled, readonly service identity with no database bootstrap."""
    now = utcnow()
    request.state.auth_method = "mcp_internal"
    user = User(
        id="testbench-mcp-internal",
        username="testbench-mcp",
        email=None,
        auth_provider="internal",
        role="readonly",
        is_active=True,
        created_at=now,
        last_login_at=None,
    )
    # Read-only by construction rather than by looking up what `readonly`
    # currently grants: this identity exists without a database bootstrap, and
    # an installation that gave its readonly role a write permission must not
    # thereby hand one to a service account it never created.
    user.granted_permissions = frozenset()
    return user


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Resolve the caller from a bearer credential.

    Interactive credentials land on a real `User`, while the optional bundled
    MCP credential lands on a synthetic readonly identity:

    * a login JWT, short-lived, minted by `/auth/login` or the OIDC callback
    * an API key, long-lived, minted by the user under their profile
    * the Helm-generated MCP internal token, when configured

    They are told apart by shape — an API key is `tb_<prefix>_<secret>` — not by
    trying to decode one as the other.
    """
    if credentials is None:
        raise _unauthorized("Not authenticated")
    presented = credentials.credentials
    if settings.mcp_internal_token and secrets.compare_digest(
        presented, settings.mcp_internal_token
    ):
        return _from_mcp_internal(request)
    parts = split_api_key(presented)
    if parts is not None:
        return _from_api_key(request, db, *parts)
    return _from_jwt(db, presented)


def require_permission(permission: str, what: str):
    """A dependency that admits a caller holding `permission`.

    `what` completes "…is not allowed to <what>", so the 403 says which
    capability is missing rather than which role the caller is not.
    """

    def guard(
        user: User = Depends(get_current_user), db: Session = Depends(get_db),
    ) -> User:
        if permission not in caller_permissions(db, user):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Your role ({user.role}) is not allowed to {what}.",
            )
        return user

    return guard


require_devices_edit = require_permission(DEVICES_EDIT, "edit devices")
require_software_edit = require_permission(SOFTWARE_EDIT, "edit software")
require_tests_edit = require_permission(TESTS_EDIT, "record tests")
require_views_save = require_permission(VIEWS_SAVE, "save views")
require_audit_view = require_permission(AUDIT_VIEW, "read the audit log")
require_users_manage = require_permission(USERS_MANAGE, "manage users and roles")
require_schema_manage = require_permission(SCHEMA_MANAGE, "manage the schema")
require_plugins_manage = require_permission(PLUGINS_MANAGE, "manage plugins")
require_settings_manage = require_permission(SETTINGS_MANAGE, "manage settings")


def holds(db: Session, user: User, permission: str) -> bool:
    """Whether the caller has a permission, asked rather than enforced.

    For the places deciding how much of a record to show rather than whether to
    allow an action — the device changelog includes the raw audit entry for a
    caller who could read it in the audit log anyway, and hides it from everyone
    else.
    """
    return permission in caller_permissions(db, user)


def _reject_non_session(request: Request, what: str) -> None:
    """Refuse a request that arrived on a non-session credential.

    For the endpoints that manage credentials. A key that can mint credentials
    is a key that cannot be revoked: cut off the one you know about and it has
    already issued its replacement. Minting stays tied to an interactive login.
    """
    if getattr(request.state, "auth_method", None) in {"api_key", "mcp_internal"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Service and API-key credentials cannot {what} — sign in to the web app to do that.",
        )


def require_session(
    request: Request, user: User = Depends(get_current_user)
) -> User:
    """Reject service/API-key credentials, accept a signed-in session."""
    _reject_non_session(request, "manage API keys")
    return user


def require_users_manage_session(
    request: Request, user: User = Depends(require_users_manage)
) -> User:
    """Permission to manage accounts, held by a real login rather than a key.

    Same reasoning as `require_session`, one step further out: a local account is
    a credential too, and a longer-lived one than a key. A key that can create an
    admin or reset an admin's password outlives its own revocation — revoke it
    and the account it left behind still signs in.
    """
    _reject_non_session(request, "manage local accounts")
    return user
