"""/protocols/kados/v1/methods/{name}/ RPC surface.

Wire-compatible with the KADOS OpenAPIAdapter:
- POST /protocols/kados/v1/methods/{name}/
- body:   {"method": "<name>", "data": {...args}}
- reply:  {"data": <value>}
- X-API-Key: app-level credential (optional; required if KADOS_API_KEY is set)
- Authorization: Session <token>: user-level credential
"""

from __future__ import annotations

import logging
import secrets

from fastapi import APIRouter, Header, HTTPException, Path
from pydantic import BaseModel, Field

from ...config import kados_settings
from ...plugins import SessionExpired
from . import methods

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/protocols/kados/v1")


class KadosEnvelope(BaseModel):
    method: str
    data: dict = Field(default_factory=dict)


class KadosReply(BaseModel):
    data: object


# In-memory session store for Kados. Simple and matches the spec: the
# adapter treats the session token as an opaque string issued by the
# backend at authenticate time.
_SESSIONS: dict[str, dict] = {}


def new_session_token(user: str) -> str:
    token = secrets.token_urlsafe(24)
    _SESSIONS[token] = {"user": user}
    return token


def session_user(token: str | None) -> str | None:
    if not token:
        return None
    rec = _SESSIONS.get(token)
    return rec["user"] if rec else None


def _require_api_key(x_api_key: str | None) -> None:
    configured = kados_settings.api_key
    if not configured:
        return  # no app-level auth enforced
    if x_api_key != configured:
        raise HTTPException(401, "invalid or missing X-API-Key")


def _parse_session(authorization: str | None) -> str | None:
    if not authorization:
        return None
    prefix = "Session "
    if authorization.startswith(prefix):
        return authorization[len(prefix):].strip() or None
    return None


# KADOS methods that are reachable WITHOUT a valid session token.
# Everything else returns HTTP 401 when called without one -- matches
# the OpenAPIAdapter contract: "Endpoints that require a session and
# receive no valid token MUST respond with an HTTP 401" (otherwise an
# expired token looks like a silent empty bookshelf to the SOAP
# layer). Each entry below is a method KADOS calls during startup or
# the logOn handshake -- they MUST stay reachable without a session
# or the entire login flow breaks.
_ANONYMOUS_METHODS = frozenset({
    # Establishes the session in the first place.
    "authenticate",
    # KADOS pulls labels for UI elements during service startup, before
    # any user has logged on.
    "label",
    # Capability negotiation + log hooks the SOAP server invokes for
    # every request; these run before authentication completes and
    # cannot be session-gated without breaking the handshake.
    "setProtocolVersion",
    "logSoapRequestAndResponse",
    # Spec-required pre-logOn responses (see v0.1.9 / v0.1.10).
    "announcements",
    "termsOfServiceAccepted",
    # Operator-key endpoints that participate in the PDTB2 key exchange
    # before the user identity is known.
    "requestedKey",
    "clientKey",
    "issuerInfo",
    "termsOfService",
    "termsOfServiceAccept",
})


@router.post("/methods/{name}/", response_model=KadosReply)
@router.post("/methods/{name}", response_model=KadosReply, include_in_schema=False)
async def kados_dispatch(
    envelope: KadosEnvelope,
    name: str = Path(..., description="KADOS method name"),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None),
) -> KadosReply:
    _require_api_key(x_api_key)

    if envelope.method != name:
        raise HTTPException(
            400, f"envelope method {envelope.method!r} != path name {name!r}"
        )

    token = _parse_session(authorization)
    user = session_user(token)

    handler = methods.get(name)
    if handler is None:
        raise HTTPException(404, f"unknown KADOS method: {name}")

    # OpenAPIAdapter contract: session-required endpoints MUST 401 when
    # no/invalid session token is presented. Without this, an expired
    # KADOS token surfaces as `{totalItems:0, contentItem:[]}` etc. --
    # the SOAP layer reads that as "the user just has an empty
    # bookshelf" rather than "re-trigger logOn", and the user gets
    # stuck looking at a phantom-empty library.
    if user is None and name not in _ANONYMOUS_METHODS:
        raise HTTPException(
            401, f"KADOS method {name} requires a valid Authorization: Session token"
        )

    try:
        result = await handler(
            data=envelope.data, user=user, new_token_for=new_session_token
        )
    except HTTPException:
        raise
    except SessionExpired as e:
        # Plugin's upstream session is no longer usable. Drop any
        # cached token tied to this caller so a follow-up authenticate
        # call mints a fresh one, then surface 401 -- the
        # OpenAPIAdapter doc says session-required endpoints MUST 401
        # so KADOS can re-trigger logOn.
        if token is not None:
            _SESSIONS.pop(token, None)
        raise HTTPException(401, str(e) or "upstream session expired") from e
    except NotImplementedError as e:
        raise HTTPException(501, f"KADOS method {name} is not implemented yet: {e}") from e
    except Exception as e:
        logger.exception("KADOS method %s failed", name)
        raise HTTPException(500, str(e)) from e

    # N2: stopSession invalidates the token server-side. The PHP
    # adapter clears its local copy AFTER the call, expecting the
    # backend to have already dropped its end. Doing it here after
    # the handler returns keeps the method handlers pure (the router
    # owns session lifecycle).
    if name == "stopSession" and token is not None:
        _SESSIONS.pop(token, None)

    return KadosReply(data=result)
