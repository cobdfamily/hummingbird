"""KADOS method implementations.

Every handler has the signature:
    async def handler(data: dict, user: str | None, new_token_for: callable) -> Any

Unimplemented methods raise NotImplementedError and the router turns that
into HTTP 501. Phase 1/2 (auth, content, session, bookmarks, protocol)
are wired through to the Hummingbird plugin/storage layer. Everything
else is stubbed.
"""

from __future__ import annotations

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
    # ``user`` is guaranteed non-None here -- the router gates every
    # user-scoped method with a 401 when no valid session token is
    # presented. We just route to the right backend.
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
    try:
        cid = int(data.get("contentId", 0))
    except (TypeError, ValueError):
        return False
    return any(b.id == cid for b in storage.list_bookshelf(user))


@method("contentMetadata")
async def _content_metadata(data: dict, user: str | None, **_) -> dict:
    """DODP getContentMetadata. Returns a DC-shaped metadata dict.
    If the active plugin exposes ``get_metadata(user, content_id)``
    we use its richer shape (NNELS' 30-day metadata cache has real
    title / authors / narrator); otherwise we emit the minimum
    KADOS won't crash on."""
    cid = data.get("contentId")

    plugin = active_plugin()
    if plugin is not None and cid is not None:
        get_meta = getattr(plugin, "get_metadata", None)
        if get_meta is not None:
            try:
                meta = await get_meta(user, cid)
            except NotImplementedError:
                meta = None
            if meta:
                # Caller hands back a {dc:identifier, dc:title, dc:creator,
                # dc:format, ...} dict already shaped for DODP.
                return {"metadata": meta}

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
    """DODP-style ``getContentResources``: returns the list of resources
    (audio, SMIL, NCC, etc) the client should fetch to assemble the
    content item locally. Each resource is ``{uri, mimeType, size,
    localURI}`` -- the same shape the Hummingbird REST
    ``/resources/{fmt}/{node_id}`` route returns. Both go through the
    same ``download.list_resources`` helper.

    ``contentId`` here is the NNELS node_id. The PHP adapter sends
    ``accessMethod`` (the DODP-spec key); legacy hummingbird-aware
    clients used ``format``. We try ``accessMethod`` first, fall back
    to ``format``, and finally default to DAISY 202 Audio (fmt 11) so
    DAISY clients get the full structure.
    """
    from ...download import ensure_cached, list_resources

    cid_raw = data.get("contentId")
    if cid_raw is None:
        return {
            "returnBy": None,
            "resources": [],
            "metadata": {"dc:identifier": ""},
        }
    try:
        cid = int(cid_raw)
    except (TypeError, ValueError):
        return {
            "returnBy": None,
            "resources": [],
            "metadata": {"dc:identifier": str(cid_raw)},
        }

    # Prefer the DODP-spec ``accessMethod`` (what the PHP adapter
    # forwards via ``contentResources($contentId, $accessMethod)``).
    # Fall back to legacy ``format`` for hummingbird-native clients
    # that predate the rename. Default to DAISY 202 Audio (fmt 11).
    fmt_raw = data.get("accessMethod")
    if fmt_raw is None:
        fmt_raw = data.get("format")
    try:
        fmt = int(fmt_raw) if fmt_raw is not None else 11
    except (TypeError, ValueError):
        fmt = 11

    cache = await ensure_cached(fmt, cid, username=user)
    # KADOS clients don't carry an HTTP request, so we don't know the
    # public base URL -- emit relative URIs. Clients prepend their
    # base. (The REST endpoint that knows the base URL emits absolute
    # URIs.)
    base_url = settings.public_base_url
    if cache is None:
        return {
            "returnBy": None,
            "resources": [],
            "metadata": {"dc:identifier": str(cid)},
        }
    resources = list_resources(cache, fmt, cid, base_url)
    return {
        "returnBy": None,
        "resources": resources,
        "metadata": {"dc:identifier": str(cid)},
    }


@method("contentAddBookshelf")
async def _content_add_bookshelf(data: dict, user: str | None, **_) -> bool:
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
# Simple JSON blob — protocol treats the payload as opaque. Plugins
# with an upstream sync API can override via set_bookmark / get_bookmark;
# raising NotImplementedError falls back to the JSON storage layer.


@method("setBookmarks")
async def _set_bookmarks(data: dict, user: str | None, **_) -> bool:
    cid = str(data.get("contentId", ""))
    if not cid:
        return False
    bookmark = data.get("bookmark") or {}
    plugin = active_plugin()
    if plugin is not None:
        try:
            return bool(await plugin.set_bookmark(user, cid, bookmark))
        except NotImplementedError:
            pass
    return storage.write_bookmark(user, cid, bookmark)


@method("getBookmarks")
async def _get_bookmarks(data: dict, user: str | None, **_) -> dict:
    cid = str(data.get("contentId", ""))
    if not cid:
        return {}
    plugin = active_plugin()
    if plugin is not None:
        try:
            return await plugin.get_bookmark(user, cid) or {}
        except NotImplementedError:
            pass
    return storage.read_bookmark(user, cid)


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
    "contentSubCategory", "contentIssue",
    "announcementInfo", "announcementExists", "announcementRead",
    "menuDefault", "menuSearch", "menuBack", "menuNext", "menuContentQuestion",
    "requestedKey", "clientKey", "issuerInfo", "userCredentials",
    "termsOfService", "termsOfServiceAccept",
]


@method("contentReturnDate")
async def _content_return_date(data: dict, user: str | None, **_) -> str | None:
    """Return the ISO-8601 due date for a (user, book) pair, or None if
    the library has no loan period (NNELS) or the book isn't on the
    user's shelf. Drives client-side auto-return on expiry."""
    try:
        cid = int(data.get("contentId", 0))
    except (TypeError, ValueError):
        return None
    plugin = active_plugin()
    if plugin is not None:
        try:
            for book in await plugin.list_bookshelf(user):
                if book.id == cid:
                    return book.due_date
            return None
        except NotImplementedError:
            pass
    return storage.get_due_date(user, cid)


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
