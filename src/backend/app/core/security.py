import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from ..config import settings
from ..models.api_key import ACCEPTED_KEY_PREFIXES, API_KEY_PREFIX, PREFIX_LENGTH


def _pw_bytes(password: str) -> bytes:
    """Encode a password for bcrypt, which hashes at most 72 bytes.

    Anything past 72 bytes never contributed to the hash, so truncating here
    reproduces the hashes we already have rather than changing them. bcrypt 5
    raises on an over-long password where it used to truncate silently, so the
    slice is what keeps a long password logging in instead of 500ing.

    Slice the *bytes*, not the str: `password[:72]` counts characters, and a
    non-ASCII password would then hash differently than it did before.
    """
    return password.encode()[:72]


# Hashed once at import so an unknown username can be made to cost the same as
# a known one; see dummy_verify.
_DUMMY_HASH = bcrypt.hashpw(_pw_bytes("tb-timing-equaliser"), bcrypt.gensalt())


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_pw_bytes(password), bcrypt.gensalt()).decode()


def dummy_verify(password: str) -> None:
    """Burn a bcrypt verification against a throwaway hash.

    A login for a username that does not exist would otherwise return without
    hashing anything, and the difference in response time tells an attacker
    which usernames are real.
    """
    bcrypt.checkpw(_pw_bytes(password), _DUMMY_HASH)


def verify_password(password: str, hash: str) -> bool:
    try:
        return bcrypt.checkpw(_pw_bytes(password), hash.encode())
    except ValueError:
        # A stored hash that is not a bcrypt hash at all; not a match.
        return False


def create_token(user_id: str, username: str, role: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "username": username,
        "role": role,
        "iat": now,
        "exp": now + timedelta(hours=settings.jwt_expires_hours),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])


# ---------------------------------------------------------------------------
# API keys
# ---------------------------------------------------------------------------
#
# Shape: `tb_<prefix>_<secret>`. The prefix is a public handle stored in the
# clear and used to find the row; only the secret is hashed. Splitting them
# means verification is one indexed lookup plus one comparison, instead of
# hashing the candidate against every key in the table.


def generate_api_key() -> tuple[str, str, str]:
    """Mint a key. Returns (plaintext, prefix, hash) — the plaintext is the
    only copy that will ever exist and is never written down by the caller."""
    prefix = secrets.token_hex(PREFIX_LENGTH // 2)
    secret = secrets.token_urlsafe(32)
    return f"{API_KEY_PREFIX}_{prefix}_{secret}", prefix, hash_api_key(secret)


def hash_api_key(secret: str) -> str:
    """SHA-256 of the secret half.

    Deliberately not bcrypt. bcrypt exists to make guessing a low-entropy
    human password expensive, at a cost of 50-300ms per verification. This
    secret is 256 bits from the system CSPRNG — there is nothing to guess —
    and it is verified on every single API request, not once per login.
    """
    return hashlib.sha256(secret.encode()).hexdigest()


def split_api_key(presented: str) -> tuple[str, str] | None:
    """Split a presented credential into (prefix, secret), or None if it is
    not shaped like one of our keys — in which case it is a JWT.

    Legacy `dm_` keys are still split here: the prefix is only a label on the
    plaintext, so an old key still finds its row and verifies against the same
    stored hash."""
    parts = presented.split("_", 2)
    if len(parts) != 3 or parts[0] not in ACCEPTED_KEY_PREFIXES:
        return None
    prefix, secret = parts[1], parts[2]
    if not prefix or not secret:
        return None
    return prefix, secret


def verify_api_key(secret: str, stored_hash: str) -> bool:
    # compare_digest, not ==: a plain comparison returns early on the first
    # differing byte, which leaks the hash one byte at a time under timing.
    return hmac.compare_digest(hash_api_key(secret), stored_hash)
