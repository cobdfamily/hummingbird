"""Integration tests for /protocols/kados/v1/methods/{name}/.

Standalone mode — no plugin, env-credential authentication. Exercises
both the router (envelope validation, X-API-Key, session header parse,
error mapping) and the method handlers (auth, content, sessions,
bookmarks, stubs).
"""

from __future__ import annotations

import base64
import importlib

import pytest
from fastapi.testclient import TestClient


def _build_client(tmp_path, monkeypatch, *, api_key: str = ""):
    monkeypatch.setenv("HUMMINGBIRD_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("HUMMINGBIRD_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("HUMMINGBIRD_USERNAME", "alice")
    monkeypatch.setenv("HUMMINGBIRD_PASSWORD", "secret")
    monkeypatch.setenv("HUMMINGBIRD_PLUGIN", "")
    if api_key:
        monkeypatch.setenv("KADOS_API_KEY", api_key)
    else:
        monkeypatch.delenv("KADOS_API_KEY", raising=False)
    import hummingbird.auth as auth
    import hummingbird.config as config
    import hummingbird.download as download
    import hummingbird.plugins as plugins
    import hummingbird.storage as storage
    importlib.reload(config)
    importlib.reload(storage)
    importlib.reload(download)
    importlib.reload(plugins)
    importlib.reload(auth)
    import hummingbird.protocols.kados.methods as kd_methods
    import hummingbird.protocols.kados.router as kd_router
    import hummingbird.protocols.hummingbird.router as hb_router
    importlib.reload(kd_methods)
    importlib.reload(kd_router)
    importlib.reload(hb_router)
    import hummingbird.main as main
    importlib.reload(main)
    tc = TestClient(main.app)
    # Pre-populate the auth cache + default Basic header so setup calls
    # via the hummingbird REST surface still work for these KADOS tests.
    token = base64.b64encode(b"alice:secret").decode()
    tc.headers.update({"Authorization": f"Basic {token}"})
    auth.remember_login("alice", "secret")
    return tc, kd_router


@pytest.fixture
def client(tmp_path, monkeypatch):
    c, _ = _build_client(tmp_path, monkeypatch)
    return c


@pytest.fixture
def client_with_apikey(tmp_path, monkeypatch):
    c, _ = _build_client(tmp_path, monkeypatch, api_key="topsecret")
    return c


def _call(client, name, data=None, *, headers=None):
    body = {"method": name, "data": data or {}}
    return client.post(
        f"/protocols/kados/v1/methods/{name}/",
        json=body,
        headers=headers or {},
    )


def _authenticate(client) -> str:
    r = _call(client, "authenticate", {"username": "alice", "password": "secret"})
    assert r.status_code == 200
    token = r.json()["data"]["sessionToken"]
    assert token
    return token


def _session_headers(token: str) -> dict:
    return {"Authorization": f"Session {token}"}


# ---------------------------------------------------------------------------
# Router-level: envelope, api key, session header
# ---------------------------------------------------------------------------


def test_envelope_method_must_match_path(client):
    r = client.post(
        "/protocols/kados/v1/methods/authenticate/",
        json={"method": "wrong", "data": {}},
    )
    assert r.status_code == 400
    assert "envelope method" in r.json()["detail"]


def test_unknown_method_returns_404(client):
    r = _call(client, "totally-unknown-method")
    assert r.status_code == 404


def test_api_key_required_when_configured(client_with_apikey):
    r = _call(client_with_apikey, "authenticate", {"username": "alice", "password": "secret"})
    assert r.status_code == 401


def test_api_key_accepted_when_match(client_with_apikey):
    r = _call(
        client_with_apikey, "authenticate",
        {"username": "alice", "password": "secret"},
        headers={"X-API-Key": "topsecret"},
    )
    assert r.status_code == 200


def test_session_header_with_wrong_prefix_returns_401(client):
    """``Authorization: Bearer xyz`` is not "Session <token>" -- the
    request is unauthenticated, which the router now turns into HTTP
    401 instead of silently returning empty data. The PHP adapter
    contract says session-required endpoints MUST 401 so KADOS can
    re-trigger logOn; previously expired tokens looked like empty
    bookshelves to the SOAP layer."""
    r = _call(
        client, "contentList", {"list": "bookshelf"},
        headers={"Authorization": "Bearer notasessiontoken"},
    )
    assert r.status_code == 401


def test_session_header_unknown_token_returns_401(client):
    r = _call(
        client, "contentList", {"list": "bookshelf"},
        headers={"Authorization": "Session not-a-real-token"},
    )
    assert r.status_code == 401


def test_stub_method_returns_null(client):
    """KADOS stub methods now return ``{"data": null}`` rather than
    raising 501 -- KADOS' adapter treats any non-200 as fatal, so a
    501 from a stub crashes every DODP request once log level is
    INFO+. The mock_backend has always done this; hummingbird now
    matches. Most stubs are session-required (per-content scope),
    so authenticate first."""
    token = _authenticate(client)
    r = _call(client, "menuBack", headers=_session_headers(token))
    assert r.status_code == 200
    assert r.json() == {"data": None}


def test_handler_unexpected_exception_returns_500(client, monkeypatch):
    """An unhandled exception in a handler bubbles to the router and
    becomes a 500 (not a 501 -- that's reserved for the explicit
    NotImplementedError stub path). Custom test method isn't on the
    anonymous-allowed list so authenticate first."""
    import hummingbird.protocols.kados.methods as kd_methods

    async def _boom(data, user, **_):
        raise RuntimeError("kaboom")

    monkeypatch.setitem(kd_methods._REGISTRY, "_boom_method", _boom)
    token = _authenticate(client)
    r = client.post(
        "/protocols/kados/v1/methods/_boom_method/",
        json={"method": "_boom_method", "data": {}},
        headers=_session_headers(token),
    )
    assert r.status_code == 500


# ---------------------------------------------------------------------------
# authenticate
# ---------------------------------------------------------------------------


def test_authenticate_returns_token_for_valid_creds(client):
    r = _call(client, "authenticate", {"username": "alice", "password": "secret"})
    assert r.status_code == 200
    body = r.json()["data"]
    assert body["authenticated"] is True
    assert body["user"] == "alice"
    assert body["sessionToken"]


def test_authenticate_rejects_empty_username(client):
    r = _call(client, "authenticate", {"username": "", "password": "x"})
    assert r.status_code == 200
    assert r.json()["data"] == {"authenticated": False}


def test_authenticate_rejects_wrong_password(client):
    r = _call(client, "authenticate", {"username": "alice", "password": "wrong"})
    assert r.status_code == 200
    assert r.json()["data"] == {"authenticated": False}


# ---------------------------------------------------------------------------
# contentListExists / contentList
# ---------------------------------------------------------------------------


def test_content_list_exists_known_lists(client):
    token = _authenticate(client)
    for name in ("bookshelf", "new"):
        r = _call(client, "contentListExists", {"list": name}, headers=_session_headers(token))
        assert r.status_code == 200
        assert r.json()["data"] is True


def test_content_list_exists_unknown(client):
    token = _authenticate(client)
    r = _call(client, "contentListExists", {"list": "fictional"}, headers=_session_headers(token))
    assert r.json()["data"] is False


def test_content_list_anon_returns_401(client):
    r = _call(client, "contentList", {"list": "bookshelf"})
    assert r.status_code == 401


def test_content_list_non_bookshelf_returns_empty(client):
    token = _authenticate(client)
    r = _call(client, "contentList", {"list": "new"}, headers=_session_headers(token))
    assert r.json()["data"] == {"totalItems": 0, "contentItem": []}


def test_content_list_returns_bookshelf(client):
    token = _authenticate(client)
    # Add via the hummingbird REST surface so storage is populated.
    client.post(
        "/protocols/hummingbird/v1/bookshelf/add/42?username=alice&format=4&title=X"
    )
    r = _call(client, "contentList", {"list": "bookshelf"}, headers=_session_headers(token))
    body = r.json()["data"]
    assert body["totalItems"] == 1
    assert body["contentItem"][0]["id"] == "42"


# ---------------------------------------------------------------------------
# contentExists / contentMetadata / contentResources
# ---------------------------------------------------------------------------


def test_content_exists_anon_returns_401(client):
    r = _call(client, "contentExists", {"contentId": 42})
    assert r.status_code == 401


def test_content_exists_invalid_id_false(client):
    token = _authenticate(client)
    r = _call(client, "contentExists", {"contentId": "not-an-int"}, headers=_session_headers(token))
    assert r.json()["data"] is False


def test_content_exists_true_when_on_shelf(client):
    token = _authenticate(client)
    client.post("/protocols/hummingbird/v1/bookshelf/add/99?username=alice&format=4&title=X")
    r = _call(client, "contentExists", {"contentId": 99}, headers=_session_headers(token))
    assert r.json()["data"] is True


def test_content_metadata_includes_identifier(client):
    token = _authenticate(client)
    r = _call(client, "contentMetadata", {"contentId": 7}, headers=_session_headers(token))
    assert r.json()["data"]["metadata"]["dc:identifier"] == "7"


def test_content_metadata_missing_id(client):
    token = _authenticate(client)
    r = _call(client, "contentMetadata", {}, headers=_session_headers(token))
    assert r.json()["data"]["metadata"]["dc:identifier"] == ""


def test_content_resources_returns_empty_list(client):
    token = _authenticate(client)
    r = _call(client, "contentResources", {"contentId": 7}, headers=_session_headers(token))
    body = r.json()["data"]
    assert body["resources"] == []
    assert body["metadata"]["dc:identifier"] == "7"


# ---------------------------------------------------------------------------
# contentAddBookshelf / contentReturn
# ---------------------------------------------------------------------------


def test_content_add_bookshelf_anon_returns_401(client):
    r = _call(client, "contentAddBookshelf", {"contentId": 42})
    assert r.status_code == 401


def test_content_add_bookshelf_invalid_id_false(client):
    token = _authenticate(client)
    r = _call(client, "contentAddBookshelf", {"contentId": "abc"}, headers=_session_headers(token))
    assert r.json()["data"] is False


def test_content_add_bookshelf_success(client):
    token = _authenticate(client)
    r = _call(client, "contentAddBookshelf", {"contentId": 42}, headers=_session_headers(token))
    assert r.json()["data"] is True
    # Now exists on the shelf.
    r = _call(client, "contentExists", {"contentId": 42}, headers=_session_headers(token))
    assert r.json()["data"] is True


def test_content_return_anon_returns_401(client):
    r = _call(client, "contentReturn", {"contentId": 42})
    assert r.status_code == 401


def test_content_return_invalid_id_false(client):
    token = _authenticate(client)
    r = _call(client, "contentReturn", {"contentId": "abc"}, headers=_session_headers(token))
    assert r.json()["data"] is False


def test_content_return_drops_book(client):
    token = _authenticate(client)
    client.post("/protocols/hummingbird/v1/bookshelf/add/42?username=alice&format=4&title=X")
    r = _call(client, "contentReturn", {"contentId": 42}, headers=_session_headers(token))
    assert r.json()["data"] is True


# ---------------------------------------------------------------------------
# session + protocol version
# ---------------------------------------------------------------------------


def test_start_session_true_when_user(client):
    token = _authenticate(client)
    r = _call(client, "startSession", {}, headers=_session_headers(token))
    assert r.json()["data"] is True


def test_start_session_anon_returns_401(client):
    """startSession is a session-required method per the OpenAPIAdapter
    contract: the client just authenticated, has a token, and
    startSession is called to validate it. Calling without a token
    means the caller skipped authenticate -- return 401 so KADOS
    re-runs logOn."""
    r = _call(client, "startSession", {})
    assert r.status_code == 401


def test_stop_session_succeeds_with_session(client):
    """stopSession requires a session token (the contract is to
    INVALIDATE that token). Calling without one is now a 401."""
    token = _authenticate(client)
    r = _call(client, "stopSession", {}, headers=_session_headers(token))
    assert r.status_code == 200
    assert r.json()["data"] is True


def test_stop_session_drops_token_from_sessions(tmp_path, monkeypatch):
    """stopSession must remove the token from the server-side _SESSIONS
    map; the PHP adapter clears its local copy AFTER the call, so the
    backend MUST already be done with the token. Previously stopSession
    returned True without touching _SESSIONS -- the token lived forever
    server-side, a memory leak + contract violation."""
    client, kd_router = _build_client(tmp_path, monkeypatch)
    token = _authenticate(client)
    assert token in kd_router._SESSIONS

    r = _call(client, "stopSession", {}, headers=_session_headers(token))
    assert r.status_code == 200
    assert token not in kd_router._SESSIONS

    # Re-using the dropped token now returns 401 (no longer valid).
    r2 = _call(client, "contentList", {"list": "bookshelf"}, headers=_session_headers(token))
    assert r2.status_code == 401


def test_set_protocol_version_always_true(client):
    r = _call(client, "setProtocolVersion", {"version": "2.0"})
    assert r.json()["data"] is True


# ---------------------------------------------------------------------------
# fire-and-forget hooks — must ack rather than 501, otherwise KADOS
# (with default log level) blows up on every DODP request.
# ---------------------------------------------------------------------------


def test_log_soap_request_and_response_is_a_noop(client):
    """KADOS pings this for every SOAP request when log level is
    INFO+. A 501 here crashed the openapi-kados adapter with an
    AdapterException — caught by the kados-fronting integration
    tests."""
    r = _call(client, "logSoapRequestAndResponse", {"request": "...", "response": "..."})
    assert r.status_code == 200
    assert r.json() == {"data": None}


def test_announcements_returns_empty_list(client):
    """Standalone hummingbird has no service announcements; the
    handler returns ``[]`` so KADOS' logon flow doesn't crash on
    a 501."""
    r = _call(client, "announcements")
    assert r.status_code == 200
    assert r.json() == {"data": []}


def test_terms_of_service_accepted_is_true(client):
    """No ToS gating in standalone — always-accepted is the right
    default, and the call must not 501."""
    r = _call(client, "termsOfServiceAccepted")
    assert r.status_code == 200
    assert r.json() == {"data": True}


def test_label_echoes_id_as_text(client):
    """KADOS validates that ``label.text`` is non-empty when
    building responses (it crashes ``logOnResponse`` builder if
    ``serviceProvider.label.text`` is blank). Default echoes the
    id as the text."""
    r = _call(client, "label", {"id": "OpenAPI", "type": "serviceProvider"})
    assert r.status_code == 200
    body = r.json()["data"]
    assert body["text"] == "OpenAPI"
    assert body["audio"] is None
    assert body["lang"] == "en"


def test_label_falls_back_to_constant_when_id_missing(client):
    r = _call(client, "label", {"type": "serviceProvider"})
    body = r.json()["data"]
    assert body["text"] == "label"  # non-empty fallback
    assert body["lang"] == "en"


def test_content_accessible_default_true(client):
    """Anything on the bookshelf is accessible to its owner --
    plugins with stricter policies override. Per-content gates are
    session-required so authenticate first."""
    token = _authenticate(client)
    r = _call(client, "contentAccessible", {"contentId": 42}, headers=_session_headers(token))
    assert r.json()["data"] is True


def test_content_returnable_default_true(client):
    token = _authenticate(client)
    r = _call(client, "contentReturnable", {"contentId": 42}, headers=_session_headers(token))
    assert r.json()["data"] is True


def test_content_issuable_default_false(client):
    """Standalone has no loan ceremony -- nothing needs issuing."""
    token = _authenticate(client)
    r = _call(client, "contentIssuable", {"contentId": 42}, headers=_session_headers(token))
    assert r.json()["data"] is False


# ---------------------------------------------------------------------------
# bookmarks
# ---------------------------------------------------------------------------


def test_content_resources_returns_archive_entries(client, tmp_path, monkeypatch):
    """KADOS getContentResources returns the same DODP-shaped resource
    list as the REST /resources endpoint -- one entry per file in the
    cached archive, each with uri/mimeType/size/localURI."""
    monkeypatch.setenv("HUMMINGBIRD_PUBLIC_BASE_URL", "https://hummingbird.example.com")
    # Force a config reload + handlers + main reload.
    import importlib
    import hummingbird.config as config
    importlib.reload(config)
    import hummingbird.download as download
    importlib.reload(download)
    import hummingbird.protocols.kados.methods as kd_methods
    importlib.reload(kd_methods)
    import hummingbird.protocols.kados.router as kd_router
    importlib.reload(kd_router)
    import hummingbird.protocols.hummingbird.router as hb_router
    importlib.reload(hb_router)
    import hummingbird.main as main
    importlib.reload(main)
    fresh = TestClient(main.app)
    fresh.headers.update({"Authorization": "Basic YWxpY2U6c2VjcmV0"})

    # Stage a zip into the cache for fmt=11 node=300.
    import zipfile
    from io import BytesIO
    cache_dir = download.cache_dir_for(11, 300)
    cache_dir.mkdir(parents=True, exist_ok=True)
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("ncc.html", "<html/>")
        z.writestr("audio/01.mp3", b"audio")
    (cache_dir / "book.zip").write_bytes(buf.getvalue())

    # KADOS authentication first (gets a session token).
    token = _authenticate(fresh)
    r = _call(
        fresh, "contentResources",
        {"contentId": 300, "format": 11},
        headers=_session_headers(token),
    )
    body = r.json()["data"]
    paths = {res["localURI"] for res in body["resources"]}
    assert paths == {"ncc.html", "audio/01.mp3"}
    for res in body["resources"]:
        assert res["uri"].startswith("https://hummingbird.example.com/")


def test_content_resources_anon_returns_401(client):
    r = _call(client, "contentResources", {"contentId": 300})
    assert r.status_code == 401


def test_content_resources_bad_content_id_returns_empty(client):
    token = _authenticate(client)
    r = _call(
        client, "contentResources", {"contentId": "not-a-number"},
        headers=_session_headers(token),
    )
    assert r.json()["data"]["resources"] == []


def test_content_return_date_via_storage(client):
    """contentReturnDate reads the per-book due_date persisted in
    storage. None for books with no loan period."""
    token = _authenticate(client)
    h = _session_headers(token)
    # Stage two books on the shelf: one with a due_date, one without.
    import hummingbird.storage as storage
    storage.add_to_bookshelf(
        "alice", 42, format=4, title="X",
        due_date="2026-06-01T00:00:00+00:00",
    )
    storage.add_to_bookshelf("alice", 43, format=4, title="Y")
    r = _call(client, "contentReturnDate", {"contentId": 42}, headers=h)
    assert r.json()["data"] == "2026-06-01T00:00:00+00:00"
    r = _call(client, "contentReturnDate", {"contentId": 43}, headers=h)
    assert r.json()["data"] is None


def test_content_return_date_anon_returns_401(client):
    r = _call(client, "contentReturnDate", {"contentId": 42})
    assert r.status_code == 401


def test_content_return_date_invalid_content_id_returns_none(client):
    token = _authenticate(client)
    r = _call(
        client, "contentReturnDate", {"contentId": "not-a-number"},
        headers=_session_headers(token),
    )
    assert r.json()["data"] is None


def test_set_and_get_bookmarks_roundtrip(client):
    token = _authenticate(client)
    h = _session_headers(token)
    payload = {"contentId": 42, "bookmark": {"position": "smil-1#p3"}}
    r = _call(client, "setBookmarks", payload, headers=h)
    assert r.json()["data"] is True

    r = _call(client, "getBookmarks", {"contentId": 42}, headers=h)
    body = r.json()["data"]
    assert body["position"] == "smil-1#p3"
    # Storage stamps a server-side write timestamp.
    assert "updated_at" in body


def test_set_bookmarks_anon_returns_401(client):
    r = _call(client, "setBookmarks", {"contentId": 42, "bookmark": {}})
    assert r.status_code == 401


def test_set_bookmarks_no_content_id_false(client):
    token = _authenticate(client)
    r = _call(client, "setBookmarks", {"bookmark": {}}, headers=_session_headers(token))
    assert r.json()["data"] is False


def test_get_bookmarks_anon_returns_401(client):
    r = _call(client, "getBookmarks", {"contentId": 42})
    assert r.status_code == 401


def test_get_bookmarks_no_content_id_empty(client):
    token = _authenticate(client)
    r = _call(client, "getBookmarks", {}, headers=_session_headers(token))
    assert r.json()["data"] == {}


def test_get_bookmarks_no_file_empty(client):
    token = _authenticate(client)
    r = _call(client, "getBookmarks", {"contentId": 999}, headers=_session_headers(token))
    assert r.json()["data"] == {}


# ---------------------------------------------------------------------------
# SessionExpired -> 401 + session-token drop
# ---------------------------------------------------------------------------


def test_kados_session_expired_returns_401_and_drops_token(tmp_path, monkeypatch):
    """Plugin raises SessionExpired -> router maps to HTTP 401 and
    removes the caller's token from _SESSIONS so the next authenticate
    mints a fresh one. The PHP adapter doc says session-required
    endpoints MUST 401 so KADOS can re-trigger logOn."""
    monkeypatch.setenv("HUMMINGBIRD_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("HUMMINGBIRD_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("HUMMINGBIRD_USERNAME", "alice")
    monkeypatch.setenv("HUMMINGBIRD_PASSWORD", "secret")
    monkeypatch.setenv("HUMMINGBIRD_PLUGIN", "")
    monkeypatch.delenv("KADOS_API_KEY", raising=False)

    import hummingbird.auth as auth
    import hummingbird.config as config
    import hummingbird.download as download
    import hummingbird.plugins as plugins
    import hummingbird.storage as storage
    importlib.reload(config)
    importlib.reload(storage)
    importlib.reload(download)
    importlib.reload(plugins)
    importlib.reload(auth)
    import hummingbird.protocols.kados.methods as kd_methods
    import hummingbird.protocols.kados.router as kd_router
    import hummingbird.protocols.hummingbird.router as hb_router
    importlib.reload(kd_methods)
    importlib.reload(kd_router)
    importlib.reload(hb_router)
    import hummingbird.main as main
    importlib.reload(main)
    # Import AFTER the reloads so class identity matches what the
    # reloaded router catches in `except SessionExpired`.
    from hummingbird.plugins import Plugin, SessionExpired

    class _Plugin(Plugin):
        async def authenticate(self, u, p): return True
        async def list_bookshelf(self, u): raise SessionExpired("nnels cookie gone")
        async def add_to_bookshelf(self, u, n): raise NotImplementedError
        async def remove_from_bookshelf(self, u, n): raise NotImplementedError
        async def search(self, u, q, f, p): raise NotImplementedError
        async def set_bookmark(self, u, c, b): raise NotImplementedError
        async def get_bookmark(self, u, c): raise NotImplementedError
        async def download(self, u, f, n, d): raise NotImplementedError

    plugins._active = _Plugin()
    plugins._loaded = True

    tc = TestClient(main.app)

    # authenticate -> get a token.
    r = tc.post(
        "/protocols/kados/v1/methods/authenticate/",
        json={"method": "authenticate", "data": {"username": "alice", "password": "secret"}},
    )
    assert r.status_code == 200
    token = r.json()["data"]["sessionToken"]
    assert token in kd_router._SESSIONS

    # contentList -> plugin raises SessionExpired -> 401 AND token dropped.
    r = tc.post(
        "/protocols/kados/v1/methods/contentList/",
        json={"method": "contentList", "data": {"list": "bookshelf"}},
        headers={"Authorization": f"Session {token}"},
    )
    assert r.status_code == 401
    assert token not in kd_router._SESSIONS


# ---------------------------------------------------------------------------
# N3: contentResources reads accessMethod (the PHP adapter sends this);
# legacy `format` still works for hummingbird-native clients.
# ---------------------------------------------------------------------------


def test_content_resources_reads_access_method(client):
    """The OpenAPIAdapter forwards `contentResources($cid, $accessMethod)`
    which lands on the wire as ``data["accessMethod"]``, not
    ``data["format"]``. Previously hummingbird only read ``format``
    and silently fell back to its DAISY-202 default for every PHP-
    adapter call -- a real format-selection bug masked by the
    default. Now ``accessMethod`` is honoured.

    With no cache populated the response is empty regardless of
    format, but the metadata.dc:identifier echoes back so we can
    confirm the call reached the handler."""
    token = _authenticate(client)
    r = _call(
        client, "contentResources",
        {"contentId": 99, "accessMethod": 4},
        headers=_session_headers(token),
    )
    assert r.status_code == 200
    assert r.json()["data"]["metadata"]["dc:identifier"] == "99"


def test_content_resources_legacy_format_key_still_accepted(client):
    """Hummingbird-native clients that predate the rename can keep
    sending ``format`` -- accessMethod takes precedence when both are
    present, otherwise format is read."""
    token = _authenticate(client)
    r = _call(
        client, "contentResources",
        {"contentId": 99, "format": 4},
        headers=_session_headers(token),
    )
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Anonymous methods stay reachable without a session (logon-flow + UI labels).
# ---------------------------------------------------------------------------


def test_label_reachable_without_session(client):
    """KADOS calls label() for UI element labels during service startup,
    BEFORE the user logs on. It must stay anonymous."""
    r = _call(client, "label", {"id": "service-provider"})
    assert r.status_code == 200
    assert r.json()["data"]["text"] == "service-provider"


def test_announcements_reachable_without_session(client):
    r = _call(client, "announcements", {})
    assert r.status_code == 200
    assert r.json()["data"] == []


def test_terms_of_service_accepted_reachable_without_session(client):
    r = _call(client, "termsOfServiceAccepted", {})
    assert r.status_code == 200
    assert r.json()["data"] is True


def test_set_protocol_version_reachable_without_session(client):
    r = _call(client, "setProtocolVersion", {"version": 2})
    assert r.status_code == 200


def test_log_soap_request_and_response_reachable_without_session(client):
    """KADOS calls this on EVERY request when log level is INFO+, including
    well before logOn completes. Must stay anonymous."""
    r = _call(client, "logSoapRequestAndResponse", {"request": "x", "response": "y"})
    assert r.status_code == 200
