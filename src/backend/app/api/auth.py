import base64
import hashlib
import json
import logging
import secrets
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import jwt as pyjwt
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db, utcnow
from ..models import ApiKey, Role, User
from ..schemas import ApiKeyCreate, ApiKeyCreated, ApiKeyOut, LoginIn, LoginOut, UserOut
from ..core.security import create_token, dummy_verify, generate_api_key, verify_password
from ..services.audit import log_action
from ..services.login_guard import check_login_allowed
from .deps import get_current_user, require_session
from ..services.permissions import caller_permissions, grantable_roles

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# Ties the state token to the browser that started the flow. Without it anyone
# can mint a state, hand the victim a callback URL carrying their own code, and
# have the victim silently signed in as them (login CSRF).
STATE_COOKIE = "tb_oidc_state"


def _oidc_enabled() -> bool:
    return bool(settings.authentik_issuer and settings.authentik_client_id)


def _oidc_callback_url() -> str:
    """Return the public callback registered with the OIDC provider.

    The request reaching Uvicorn is often HTTP after TLS terminates at an
    ingress and passes through the frontend proxy. The configured frontend URL
    is the authoritative external origin and avoids generating an incorrect
    http:// redirect URI from that internal request.
    """
    return f"{settings.frontend_url.rstrip('/')}/api/v1/auth/oidc/callback"


# Discovery documents are fetched once per issuer and kept for the process
# lifetime; the endpoints in them do not move while the app is running.
_discovery_lock = threading.Lock()
_discovery_cache: dict[str, dict[str, str]] = {}


def _issuer_urls() -> dict[str, str]:
    """Endpoint URLs read from the issuer's OIDC discovery document.

    They cannot be built by appending to the issuer. Authentik publishes a
    per-application issuer (``/application/o/<slug>/``) but serves the
    authorization, token and userinfo endpoints from shared paths one level up
    (``/application/o/authorize/``), so a constructed URL 404s at the IdP.
    """
    issuer = settings.authentik_issuer.rstrip("/")
    with _discovery_lock:
        cached = _discovery_cache.get(issuer)
    if cached is not None:
        return cached

    url = f"{issuer}/.well-known/openid-configuration"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            doc = json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        logger.error("OIDC discovery failed for %s: %s", url, exc)
        raise HTTPException(
            status_code=502,
            detail="Could not read the OIDC discovery document; check TB_AUTHENTIK_ISSUER",
        )

    urls = {
        "authorization": doc.get("authorization_endpoint"),
        "token": doc.get("token_endpoint"),
        # Only needed as the fallback path for group lookup, so a provider that
        # omits it is not an error here.
        "userinfo": doc.get("userinfo_endpoint"),
    }
    missing = [k for k in ("authorization", "token") if not urls[k]]
    if missing:
        logger.error("OIDC discovery document at %s is missing: %s", url, missing)
        raise HTTPException(status_code=502, detail="OIDC discovery document is incomplete")

    with _discovery_lock:
        _discovery_cache[issuer] = urls
    return urls


def _resolve_role(db: Session, groups: list[str]) -> str:
    """The role an Authentik login lands on, from its groups.

    Checked against the roles this installation actually has rather than
    against a fixed list of three, so a group can be mapped to a role the
    installation defined itself. A mapping naming a role that has since been
    deleted is skipped rather than honoured — an unknown role grants nothing,
    and silently signing somebody in with no permissions at all is a worse
    answer than the configured default.
    """
    known = set(db.scalars(select(Role.slug)))
    mapping = settings.group_role_map
    for group in groups:
        role = mapping.get(group)
        if role in known:
            return role
    fallback = settings.authentik_default_role
    return fallback if fallback in known else "readonly"


@router.post("/login", response_model=LoginOut)
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    # Before the password is touched: a guessing run should cost an index scan,
    # not a bcrypt verification each.
    check_login_allowed(db, body.username, request)
    user = db.query(User).filter(User.username == body.username, User.auth_provider == "local").first()
    if user is None or user.password_hash is None:
        # Spend the same time hashing as a real check would, so an unknown
        # username is not distinguishable from a wrong password by timing.
        dummy_verify(body.password)
        password_ok = False
    else:
        password_ok = verify_password(body.password, user.password_hash)
    if not password_ok:
        log_action(db, user, "auth.login_failed", "auth", body.username, {"username": body.username}, request)
        db.commit()
        raise HTTPException(status_code=401, detail="Invalid username or password")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="User is deactivated")
    user.last_login_at = utcnow()
    token = create_token(user.id, user.username, user.role)
    log_action(db, user, "auth.login", "auth", user.id, {"provider": "local"}, request)
    db.commit()
    return LoginOut(access_token=token, user=_self(db, user))


def _self(db: Session, user: User) -> UserOut:
    """The signed-in user's own record, carrying what they may do.

    Sent on login and from `/me` so the UI can hide the buttons the API would
    refuse. It is resolved rather than stored in the token: a role edited while
    someone is signed in takes effect on their next page load, not at their
    next login.
    """
    out = UserOut.model_validate(user)
    out.permissions = sorted(caller_permissions(db, user))
    return out


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _self(db, user)


# ---------------------------------------------------------------------------
# API keys
# ---------------------------------------------------------------------------
#
# A user mints these for themselves under their profile and uses them exactly
# like a login token: `Authorization: Bearer tb_<prefix>_<secret>` against any
# `/api/v1` endpoint. Resolution happens in `api/deps.py`, which is why no
# router below needed changing to accept one.


def _key_out(key: ApiKey, now: datetime) -> ApiKeyOut:
    out = ApiKeyOut.model_validate(key)
    out.is_active = key.is_usable(now)
    return out


@router.get("/api-keys", response_model=list[ApiKeyOut])
def list_api_keys(db: Session = Depends(get_db), user: User = Depends(require_session)):
    """The caller's own keys. Revoked ones stay listed so the history is visible."""
    keys = db.scalars(
        select(ApiKey).where(ApiKey.user_id == user.id).order_by(ApiKey.created_at.desc())
    ).all()
    now = utcnow()
    return [_key_out(k, now) for k in keys]


@router.get("/api-key-roles", response_model=list[str])
def list_api_key_roles(
    db: Session = Depends(get_db), user: User = Depends(require_session),
):
    """The roles this user may put on a key they mint.

    Offered to the key dialog so it never shows a choice the POST below would
    refuse. Not a static list any more: which roles exist is an installation's
    business, and which of them a given user may grant depends on what they
    hold themselves (see `grantable_roles`).
    """
    return grantable_roles(db, caller_permissions(db, user))


@router.post("/api-keys", response_model=ApiKeyCreated, status_code=201)
def create_api_key(
    body: ApiKeyCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_session),
):
    """Mint a key. The plaintext is in this response and nowhere else, ever."""
    allowed = grantable_roles(db, caller_permissions(db, user))
    if body.role not in allowed:
        # Named explicitly rather than a bare 403: the UI offers only the
        # allowed roles, so anything reaching here is a direct API call whose
        # author benefits from being told what the ceiling actually is.
        raise HTTPException(
            status_code=403,
            detail=f"A {user.role} cannot create a {body.role} key. "
                   f"Allowed: {', '.join(sorted(allowed))}.",
        )

    plaintext, prefix, key_hash = generate_api_key()
    key = ApiKey(
        user_id=user.id,
        label=body.label,
        prefix=prefix,
        key_hash=key_hash,
        role=body.role,
        expires_at=utcnow() + timedelta(days=body.expires_in_days) if body.expires_in_days else None,
    )
    db.add(key)
    db.flush()  # assign the id before the audit row references it

    # `prefix` only. Writing the plaintext into the audit log would defeat the
    # point of hashing it in the first place.
    log_action(
        db, user, "api_key.create", "api_key", key.id,
        {"label": key.label, "role": key.role, "prefix": key.prefix,
         "expires_at": key.expires_at.isoformat() if key.expires_at else None},
        request,
    )
    db.commit()
    db.refresh(key)
    return ApiKeyCreated(key=_key_out(key, utcnow()), plaintext=plaintext)


@router.delete("/api-keys/{key_id}", status_code=204)
def revoke_api_key(
    key_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_session),
):
    """Revoke one of the caller's keys. The row stays so the audit log still
    resolves; `revoked_at` is what `api/deps.py` refuses on."""
    key = db.get(ApiKey, key_id)
    if key is None or key.user_id != user.id:
        # Same answer for "no such key" and "someone else's key": otherwise this
        # endpoint reports whether an id exists.
        raise HTTPException(status_code=404, detail="API key not found")
    if key.revoked_at is None:
        key.revoked_at = utcnow()
        log_action(
            db, user, "api_key.revoke", "api_key", key.id,
            {"label": key.label, "role": key.role, "prefix": key.prefix}, request,
        )
        db.commit()
    return None


# ---------------------------------------------------------------------------
# Authentik OIDC (Authorization Code + PKCE)
# ---------------------------------------------------------------------------


@router.get("/oidc/config")
def oidc_config():
    """Frontend uses this to decide whether to show the SSO button."""
    return {"enabled": _oidc_enabled()}


@router.get("/oidc/redirect")
def oidc_redirect():
    if not _oidc_enabled():
        raise HTTPException(status_code=400, detail="SSO is not configured")
    urls = _issuer_urls()
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    nonce = secrets.token_urlsafe(16)
    # The state parameter carries a short-lived signed token holding the PKCE
    # verifier so the callback can complete the exchange statelessly.
    state_token = pyjwt.encode(
        {
            "state": state,
            "nonce": nonce,
            "code_verifier": verifier,
            "exp": datetime.now(timezone.utc) + timedelta(minutes=10),
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    params = {
        "client_id": settings.authentik_client_id,
        "redirect_uri": _oidc_callback_url(),
        "response_type": "code",
        "scope": "openid profile email",
        "state": state_token,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    response = RedirectResponse(urls["authorization"] + "?" + urlencode(params))
    # SameSite=Lax so the cookie still rides the top-level GET the IdP sends the
    # browser back on, but not a cross-site POST or subresource request.
    response.set_cookie(
        STATE_COOKIE,
        state,
        max_age=600,
        httponly=True,
        samesite="lax",
        secure=settings.frontend_url.startswith("https://"),
    )
    return response


def _extract_groups(claims: dict, access_token: str | None) -> list[str]:
    """Groups come from the id_token `groups` claim when the Authentik
    application has that claim enabled; fall back to the userinfo endpoint."""
    groups = claims.get("groups")
    if isinstance(groups, list):
        return [str(g) for g in groups]
    userinfo = _issuer_urls().get("userinfo")
    if access_token and userinfo:
        try:
            req = urllib.request.Request(
                userinfo,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    info = json.loads(resp.read())
                    g = info.get("groups")
                    if isinstance(g, list):
                        return [str(x) for x in g]
        except Exception:  # noqa: BLE001
            logger.warning("OIDC userinfo group lookup failed", exc_info=True)
    return []


@router.get("/oidc/callback")
def oidc_callback(
    code: str,
    state: str,
    request: Request,
    db: Session = Depends(get_db),
    tb_oidc_state: str | None = Cookie(default=None),
):
    if not _oidc_enabled():
        raise HTTPException(status_code=400, detail="SSO is not configured")
    try:
        state_payload = pyjwt.decode(state, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="Invalid or expired OIDC state")
    # The signature only proves we minted this state, not that this browser is
    # the one we minted it for. The cookie is what proves that.
    if not tb_oidc_state or not secrets.compare_digest(tb_oidc_state, state_payload.get("state", "")):
        raise HTTPException(status_code=400, detail="OIDC state does not match this browser session")

    urls = _issuer_urls()
    body = urlencode(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": _oidc_callback_url(),
            "client_id": settings.authentik_client_id,
            "client_secret": settings.authentik_client_secret,
            "code_verifier": state_payload["code_verifier"],
        }
    ).encode()
    req = urllib.request.Request(
        urls["token"], data=body, headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            token_data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        logger.error("OIDC token exchange failed: %s %s", exc.code, exc.read()[:300])
        raise HTTPException(status_code=502, detail="OIDC token exchange failed")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=502, detail=f"OIDC token exchange failed: {exc}")
    if token_data.get("error"):
        logger.error("OIDC token exchange error: %s", token_data)
        raise HTTPException(status_code=502, detail=f"OIDC token exchange failed: {token_data.get('error')}")
    id_token = token_data.get("id_token")
    if not id_token:
        raise HTTPException(status_code=502, detail="OIDC response missing id_token")

    # The token came straight back from the issuer's token endpoint over TLS,
    # which OIDC Core 3.1.3.7 accepts in place of verifying the signature here
    # (no JWKS fetch). The claims that bind it to *this* request still have to
    # be checked by hand, since verify_signature=False disables those too.
    claims = pyjwt.decode(id_token, options={"verify_signature": False})
    audience = claims.get("aud")
    audiences = audience if isinstance(audience, list) else [audience]
    if settings.authentik_client_id not in audiences:
        raise HTTPException(status_code=502, detail="OIDC id_token was issued for another client")
    issuer = (claims.get("iss") or "").rstrip("/")
    if issuer and issuer != settings.authentik_issuer.rstrip("/"):
        raise HTTPException(status_code=502, detail="OIDC id_token came from an unexpected issuer")
    # Replay protection: this id_token must answer the authorization request we
    # actually made, not one captured from an earlier flow.
    if claims.get("nonce") != state_payload.get("nonce"):
        raise HTTPException(status_code=502, detail="OIDC id_token nonce does not match")
    sub = claims.get("sub")
    if not sub:
        raise HTTPException(status_code=502, detail="OIDC id_token missing sub")
    username = claims.get("preferred_username") or claims.get("username") or sub
    email = claims.get("email")
    groups = _extract_groups(claims, token_data.get("access_token"))
    role = _resolve_role(db, groups)

    user = db.scalar(select(User).where(User.authentik_sub == sub))
    if user is None:
        # Keep usernames unique: suffix with the sub if the name is taken.
        uname = str(username)
        if db.scalar(select(User.id).where(User.username == uname)):
            uname = f"{uname}-{str(sub)[:8]}"
        user = User(
            username=uname,
            email=email,
            auth_provider="authentik",
            authentik_sub=sub,
            role=role,
            is_active=True,
        )
        db.add(user)
    else:
        if not user.is_active:
            raise HTTPException(status_code=403, detail="User is deactivated")
        # Role is re-resolved on every login so Authentik group changes apply.
        user.email = email or user.email
        user.role = role
    user.last_login_at = utcnow()
    db.flush()

    token = create_token(user.id, user.username, user.role)
    log_action(db, user, "auth.login", "auth", user.id, {"provider": "authentik", "groups": groups}, request)
    db.commit()
    response = RedirectResponse(f"{settings.frontend_url.rstrip('/')}/login#token={token}")
    response.delete_cookie(STATE_COOKIE)
    return response
