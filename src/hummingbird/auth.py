"""HTTP Basic auth for the v1 REST surface.

The REST routes (``/protocols/hummingbird/v1/...``) previously gated on a
``?username=`` query param with no password check, which let any caller
who knew a username read or mutate that user's bookshelf. This module
adds a FastAPI dependency that:

  - Parses ``Authorization: Basic <base64(user:pw)>`` from the request.
  - Validates the credentials against the active plugin (or, in
    standalone mode, the env-defined HUMMINGBIRD_USERNAME / PASSWORD).
  - Caches valid (user, password) pairs for a short TTL so we don't
    spawn a full Playwright login on every REST hit -- the NNELS plugin
    takes seconds per `authenticate()` call.

Routes that want auth use ``user: str = Depends(current_user)`` and get
the validated username; missing or wrong creds return 401.

The cache uses constant-time comparison on a SHA-256 hash of the
password rather than the password itself; clearing it is a process
restart.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import time

from fastapi import Header, HTTPException

from .config import settings
from .plugins import active_plugin


# user -> (sha256_hex_of_password, expires_at_monotonic)
_VALIDATED: dict[str, tuple[str, float]] = {}

# How long a successful validation is trusted before we re-check with
# the plugin. 15 minutes matches a typical "I'm actively listening"
# session and bounds the worst-case staleness if the upstream NNELS
# password is rotated.
TTL_SECONDS = 15 * 60


def _hash(pw: str) -> str:
    return hashlib.sha256(pw.encode("utf-8")).hexdigest()


def _cache_valid(user: str, pw: str) -> bool:
    record = _VALIDATED.get(user)
    if record is None:
        return False
    pw_hash, expires_at = record
    if time.monotonic() > expires_at:
        _VALIDATED.pop(user, None)
        return False
    return hmac.compare_digest(pw_hash, _hash(pw))


def _remember(user: str, pw: str) -> None:
    _VALIDATED[user] = (_hash(pw), time.monotonic() + TTL_SECONDS)


def _forget(user: str) -> None:
    _VALIDATED.pop(user, None)


async def validate_credentials(user: str, pw: str) -> bool:
    """Return True if (user, pw) is valid for the active plugin or for
    standalone env credentials. Caches positive results for ``TTL_SECONDS``."""
    if not user or not pw:
        return False
    if _cache_valid(user, pw):
        return True

    plugin = active_plugin()
    if plugin is not None:
        try:
            ok = bool(await plugin.authenticate(user, pw))
        except NotImplementedError:
            ok = None
        if ok is not None:
            if ok:
                _remember(user, pw)
            return ok

    # Standalone fallback: match env-defined credentials in constant time.
    env_user = settings.username
    env_pw = settings.password
    if env_user and env_pw:
        users_match = hmac.compare_digest(user, env_user)
        pws_match = hmac.compare_digest(pw, env_pw)
        if users_match and pws_match:
            _remember(user, pw)
            return True

    return False


def remember_login(user: str, pw: str) -> None:
    """Record an already-validated (user, pw) pair so subsequent REST
    calls within ``TTL_SECONDS`` don't re-trigger the plugin's expensive
    `authenticate()` (Playwright login, network round-trip).
    Called by ``/login`` after a successful sign-in."""
    _remember(user, pw)


def forget_login(user: str) -> None:
    _forget(user)


def _parse_basic(authorization: str | None) -> tuple[str, str] | None:
    if not authorization:
        return None
    prefix = "Basic "
    if not authorization.startswith(prefix):
        return None
    encoded = authorization[len(prefix):].strip()
    try:
        decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None
    if ":" not in decoded:
        return None
    user, pw = decoded.split(":", 1)
    return user, pw


async def current_user(
    authorization: str | None = Header(default=None),
) -> str:
    """FastAPI dependency: returns the authenticated username, or 401.

    REST routes wire this in as ``user: str = Depends(current_user)`` and
    use ``user`` as the canonical identity for the request. Any
    ``?username=`` query param is ignored -- the password-validated
    username is authoritative.
    """
    creds = _parse_basic(authorization)
    if creds is None:
        raise HTTPException(
            status_code=401,
            detail="missing or malformed Authorization: Basic header",
            headers={"WWW-Authenticate": "Basic"},
        )
    user, pw = creds
    if not await validate_credentials(user, pw):
        # Constant-time-ish: don't leak whether the user exists vs the
        # password was wrong. Always the same response.
        raise HTTPException(
            status_code=401,
            detail="authentication failed",
            headers={"WWW-Authenticate": "Basic"},
        )
    return user
