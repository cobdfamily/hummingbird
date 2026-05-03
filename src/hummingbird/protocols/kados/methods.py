"""KADOS method implementations.

Every handler has the signature:
    async def handler(data: dict, user: str | None, new_token_for: callable) -> Any

Unimplemented methods raise NotImplementedError and the router turns that
into HTTP 501. Phase 1/2 (auth, content, session, bookmarks, protocol)
are wired through to the Hummingbird plugin/storage layer. Everything
else is stubbed.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from ... import storage
from ...config import settings
from ...plugins import active_plugin


Handler = Callable[..., Awaitable[Any]]
_REGISTRY: dict[str, Handler] = {}


def method(name: str):
    def _dec(fn: Handler) -> Handler:
        _REGISTRY[name] = fn
        return fn
    return _dec


def get(name: str) -> Handler | None:
    return _REGISTRY.get(name)


# ==========================================================================
# Phase 1: core auth + content
# ==========================================================================


@method("authenticate")
async def _authenticate(data: dict, user: str | None, new_token_for) -> dict:
    username = data.get("username") or ""
    password = data.get("password") or ""
    if not username:
        return {"authenticated": False}

    plugin = active_plugin()
    ok = False
    if plugin is not None:
        try:
            ok = bool(await plugin.authenticate(username, password))
        except NotImplementedError:
            ok = None
    if ok is None or (plugin is None):
        # Fall back to Hummingbird .env credentials.
        ok = (username == settings.username and password == settings.password)

    if not ok:
        return {"authenticated": False}

    token = new_token_for(username)
    storage.write_session(username, via="kados")
    return {"authenticated": True, "sessionToken": token, "user": username}


@method("contentListExists")
async def _content_list_exists(data: dict, user: str | None, **_) -> bool:
    # Kados knows a few named lists; we support "bookshelf" and "new"
    # (new is an empty list in standalone mode).
    return data.get("list") in ("bookshelf", "new")


@method("contentList")
async def _content_list(data: dict, user: str | None, **_) -> dict:
    if not user:
        return {"totalItems": 0, "contentItem": []}
    listname = data.get("list", "bookshelf")
    if listname != "bookshelf":
        return {"totalItems": 0, "contentItem": []}

    plugin = active_plugin()
    if plugin is not None:
        try:
            books = await plugin.list_bookshelf(user)
        except NotImplementedError:
            books = storage.list_bookshelf(user)
    else:
        books = storage.list_bookshelf(user)

    return {
        "totalItems": len(books),
        "contentItem": [
            {"id": str(b.id), "lastModifiedDate": None} for b in books
        ],
    }


@method("contentExists")
async def _content_exists(data: dict, user: str | None, **_) -> bool:
    if not user:
        return False
    try:
        cid = int(data.get("contentId", 0))
    except (TypeError, ValueError):
        return False
    return any(b.id == cid for b in storage.list_bookshelf(user))


@method("contentMetadata")
async def _content_metadata(data: dict, user: str | None, **_) -> dict:
    # Minimal stub — real metadata lives in the NNELS plugin's 30-day cache;
    # the plugin is free to extend this handler, but the base returns enough
    # for KADOS to not crash.
    cid = data.get("contentId")
    return {
        "metadata": {
            "dc:identifier": str(cid) if cid is not None else "",
            "dc:title": "",
            "dc:format": "",
            "dc:creator": "",
        }
    }


@method("contentResources")
async def _content_resources(data: dict, user: str | None, **_) -> dict:
    cid = data.get("contentId")
    return {
        "returnBy": None,
        "resources": [],
        "metadata": {"dc:identifier": str(cid) if cid is not None else ""},
    }


@method("contentAddBookshelf")
async def _content_add_bookshelf(data: dict, user: str | None, **_) -> bool:
    if not user:
        return False
    try:
        cid = int(data.get("contentId", 0))
    except (TypeError, ValueError):
        return False
    plugin = active_plugin()
    if plugin is not None:
        try:
            return bool(await plugin.add_to_bookshelf(user, cid))
        except NotImplementedError:
            pass
    return storage.add_to_bookshelf(user, cid, format=0)


@method("contentReturn")
async def _content_return(data: dict, user: str | None, **_) -> bool:
    if not user:
        return False
    try:
        cid = int(data.get("contentId", 0))
    except (TypeError, ValueError):
        return False
    plugin = active_plugin()
    if plugin is not None:
        try:
            return bool(await plugin.remove_from_bookshelf(user, cid))
        except NotImplementedError:
            pass
    return storage.remove_from_bookshelf(user, cid, format=None)


# ==========================================================================
# Phase 2: session + bookmarks + protocol version
# ==========================================================================


@method("startSession")
async def _start_session(data: dict, user: str | None, **_) -> bool:
    return user is not None


@method("stopSession")
async def _stop_session(data: dict, user: str | None, **_) -> bool:
    return True


@method("setProtocolVersion")
async def _set_protocol_version(data: dict, user: str | None, **_) -> bool:
    return True


# These three are "fire-and-forget" hooks KADOS calls on every
# DODP session — when KADOS_LOG_LEVEL is INFO+ it pings
# logSoapRequestAndResponse for every request, and the logOn
# flow always pulls announcements + termsOfServiceAccepted. A
# 501 stub here crashes the SOAP server with an
# AdapterException, so they need to ack-with-default rather
# than raise. Plugins that want real behaviour can override by
# replacing the entry in _REGISTRY.


@method("logSoapRequestAndResponse")
async def _log_soap_request_and_response(data: dict, user: str | None, **_) -> None:
    return None


@method("announcements")
async def _announcements(data: dict, user: str | None, **_) -> list:
    return []


@method("termsOfServiceAccepted")
async def _terms_of_service_accepted(data: dict, user: str | None, **_) -> bool:
    return True


# Bookmarks: stored per-user under data_dir/bookmarks/{user}/{contentId}.json
# Simple JSON blob — protocol treats the payload as opaque.


def _bookmarks_dir(user: str):
    d = settings.data_dir / "bookmarks" / user
    d.mkdir(parents=True, exist_ok=True)
    return d


@method("setBookmarks")
async def _set_bookmarks(data: dict, user: str | None, **_) -> bool:
    if not user:
        return False
    cid = str(data.get("contentId", ""))
    if not cid:
        return False
    path = _bookmarks_dir(user) / f"{cid}.json"
    path.write_text(json.dumps(data.get("bookmark") or {}, indent=2))
    return True


@method("getBookmarks")
async def _get_bookmarks(data: dict, user: str | None, **_) -> dict:
    if not user:
        return {}
    cid = str(data.get("contentId", ""))
    if not cid:
        return {}
    path = _bookmarks_dir(user) / f"{cid}.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text())


# ==========================================================================
# Phase 3+: stubs — wire up when a real client exercises them.
# ==========================================================================
#
# Default for every stub is "return None". Matches the mock_backend's
# catch-all philosophy: PHP decodes null and the adapter caller falls
# back to whatever default makes sense for the return contract.
# Returning a 501 here would crash the openapi-kados adapter with an
# AdapterException — see the kados-fronting integration suite in
# cobdfamily/openapi-kados.
#
# Plugins that want real behaviour replace the entry in _REGISTRY at
# load time. The shape-typed helpers below cover the methods KADOS
# strictly requires a non-null response from during the standard
# logOn / list / read flow.

_STUBS = [
    "contentLastModifiedDate", "contentAccessDate", "contentAccessMethod",
    "contentAccessState", "contentSample", "contentCategory",
    "contentSubCategory", "contentReturnDate", "contentIssue",
    "announcementInfo", "announcementExists", "announcementRead",
    "menuDefault", "menuSearch", "menuBack", "menuNext", "menuContentQuestion",
    "requestedKey", "clientKey", "issuerInfo", "userCredentials",
    "termsOfService", "termsOfServiceAccept",
]


def _stub_factory(name: str) -> Handler:
    async def _stub(data: dict, user: str | None, **_) -> None:
        return None
    _stub.__name__ = f"_stub_{name}"
    return _stub


for _name in _STUBS:
    _REGISTRY[_name] = _stub_factory(_name)


# Methods KADOS calls during the default logOn / list / read flow that
# expect a non-null response of a specific shape. Each returns a
# minimal "no info" value so PHP can index into it without warnings.


@method("label")
async def _label(data: dict, user: str | None, **_) -> dict:
    """A DODP ``label`` is roughly ``{text, audio?, lang}``. No
    plugin loaded -> no real labels -> echo the requested id as
    the text. KADOS validates that ``label.text`` is non-empty
    when building responses (eg. ``serviceProvider.label.text``),
    so a blank default crashes its response builder."""
    label_id = str(data.get("id") or "label")
    return {"text": label_id, "audio": None, "lang": "en"}


@method("contentAccessible")
async def _content_accessible(data: dict, user: str | None, **_) -> bool:
    """Default-grant: anything on the bookshelf is accessible.
    Plugins with stricter access policies override."""
    return True


@method("contentReturnable")
async def _content_returnable(data: dict, user: str | None, **_) -> bool:
    """Default-allow: anything on the bookshelf can be returned.
    Plugins with one-way / time-windowed loans override."""
    return True


@method("contentIssuable")
async def _content_issuable(data: dict, user: str | None, **_) -> bool:
    """Standalone hummingbird has no loan ceremony — content is
    just on the shelf — so any contentId queried for issuability
    returns False (no issue needed)."""
    return False
