import secrets

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import as_utc, get_db, utcnow
from ..models import ApiKey, User
from ..models.user import lower_role
from ..core.security import decode_token, hash_api_key, split_api_key, verify_api_key
from ..config import settings

bearer_scheme = HTTPBearer(auto_error=False)

ROLES = ("admin", "tester", "readonly")

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
    effective = lower_role(key.role, user.role)

    # Detached on purpose. Every role check downstream reads `user.role`, so the
    # cheapest correct way to apply the cap is to hand back a User carrying the
    # effective role — but mutating a session-attached row would mean the next
    # `db.commit()` in the request writes the downgrade back to the users table.
    # Expunging first makes the object a local copy that no flush can reach.
    db.expunge(user)
    user.role = effective
    return user


def _from_mcp_internal(request: Request) -> User:
    """A chart-bundled, readonly service identity with no database bootstrap."""
    now = utcnow()
    request.state.auth_method = "mcp_internal"
    return User(
        id="testbench-mcp-internal",
        username="testbench-mcp",
        email=None,
        auth_provider="internal",
        role="readonly",
        is_active=True,
        created_at=now,
        last_login_at=None,
    )


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


def require_write(user: User = Depends(get_current_user)) -> User:
    if user.role not in ("admin", "tester"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role for write access")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required")
    return user


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


def require_admin_session(
    request: Request, user: User = Depends(require_admin)
) -> User:
    """Admin role, held by a real login rather than an API key.

    Same reasoning as `require_session`, one step further out: a local account is
    a credential too, and a longer-lived one than a key. A key that can create an
    admin or reset an admin's password outlives its own revocation — revoke it
    and the account it left behind still signs in.
    """
    _reject_non_session(request, "manage local accounts")
    return user
