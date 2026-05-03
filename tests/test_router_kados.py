"""Integration tests for /protocols/kados/v1/methods/{name}/.

Standalone mode — no plugin, env-credential authentication. Exercises
both the router (envelope validation, X-API-Key, session header parse,
error mapping) and the method handlers (auth, content, sessions,
bookmarks, stubs).
"""

from __future__ import annotations

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
    import hummingbird.config as config
    import hummingbird.download as download
    import hummingbird.plugins as plugins
    import hummingbird.storage as storage
    importlib.reload(config)
    importlib.reload(storage)
    importlib.reload(download)
    importlib.reload(plugins)
    import hummingbird.protocols.kados.methods as kd_methods
    import hummingbird.protocols.kados.router as kd_router
    import hummingbird.protocols.hummingbird.router as hb_router
    importlib.reload(kd_methods)
    importlib.reload(kd_router)
    importlib.reload(hb_router)
    import hummingbird.main as main
    importlib.reload(main)
    return TestClient(main.app), kd_router


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


def test_session_header_with_wrong_prefix_treated_as_anon(client):
    """``Authorization: Bearer xyz`` is not "Session <token>" — the
    request is treated as unauthenticated."""
    r = _call(
        client, "contentList", {"list": "bookshelf"},
        headers={"Authorization": "Bearer notasessiontoken"},
    )
    # contentList with no user -> empty.
    assert r.status_code == 200
    assert r.json()["data"]["totalItems"] == 0


def test_session_header_unknown_token_treated_as_anon(client):
    r = _call(
        client, "contentList", {"list": "bookshelf"},
        headers={"Authorization": "Session not-a-real-token"},
    )
    assert r.status_code == 200
    assert r.json()["data"]["totalItems"] == 0


def test_stub_method_returns_501(client):
    """KADOS methods registered as stubs raise NotImplementedError ->
    the router maps that to 501 with a structured detail."""
    r = _call(client, "label")
    assert r.status_code == 501
    assert "label is not implemented" in r.json()["detail"]


def test_handler_unexpected_exception_returns_500(client, monkeypatch):
    """An unhandled exception in a handler bubbles to the router and
    becomes a 500 (not a 501 — that's reserved for the explicit
    NotImplementedError stub path)."""
    import hummingbird.protocols.kados.methods as kd_methods

    async def _boom(data, user, **_):
        raise RuntimeError("kaboom")

    monkeypatch.setitem(kd_methods._REGISTRY, "_boom_method", _boom)
    r = client.post(
        "/protocols/kados/v1/methods/_boom_method/",
        json={"method": "_boom_method", "data": {}},
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
    for name in ("bookshelf", "new"):
        r = _call(client, "contentListExists", {"list": name})
        assert r.status_code == 200
        assert r.json()["data"] is True


def test_content_list_exists_unknown(client):
    r = _call(client, "contentListExists", {"list": "fictional"})
    assert r.json()["data"] is False


def test_content_list_anon_returns_empty(client):
    r = _call(client, "contentList", {"list": "bookshelf"})
    assert r.json()["data"] == {"totalItems": 0, "contentItem": []}


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


def test_content_exists_anon_false(client):
    r = _call(client, "contentExists", {"contentId": 42})
    assert r.json()["data"] is False


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
    r = _call(client, "contentMetadata", {"contentId": 7})
    assert r.json()["data"]["metadata"]["dc:identifier"] == "7"


def test_content_metadata_missing_id(client):
    r = _call(client, "contentMetadata", {})
    assert r.json()["data"]["metadata"]["dc:identifier"] == ""


def test_content_resources_returns_empty_list(client):
    r = _call(client, "contentResources", {"contentId": 7})
    body = r.json()["data"]
    assert body["resources"] == []
    assert body["metadata"]["dc:identifier"] == "7"


# ---------------------------------------------------------------------------
# contentAddBookshelf / contentReturn
# ---------------------------------------------------------------------------


def test_content_add_bookshelf_anon_false(client):
    r = _call(client, "contentAddBookshelf", {"contentId": 42})
    assert r.json()["data"] is False


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


def test_content_return_anon_false(client):
    r = _call(client, "contentReturn", {"contentId": 42})
    assert r.json()["data"] is False


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


def test_start_session_false_when_anon(client):
    r = _call(client, "startSession", {})
    assert r.json()["data"] is False


def test_stop_session_always_true(client):
    r = _call(client, "stopSession", {})
    assert r.json()["data"] is True


def test_set_protocol_version_always_true(client):
    r = _call(client, "setProtocolVersion", {"version": "2.0"})
    assert r.json()["data"] is True


# ---------------------------------------------------------------------------
# bookmarks
# ---------------------------------------------------------------------------


def test_set_and_get_bookmarks_roundtrip(client):
    token = _authenticate(client)
    h = _session_headers(token)
    payload = {"contentId": 42, "bookmark": {"position": "smil-1#p3"}}
    r = _call(client, "setBookmarks", payload, headers=h)
    assert r.json()["data"] is True

    r = _call(client, "getBookmarks", {"contentId": 42}, headers=h)
    assert r.json()["data"] == {"position": "smil-1#p3"}


def test_set_bookmarks_anon_false(client):
    r = _call(client, "setBookmarks", {"contentId": 42, "bookmark": {}})
    assert r.json()["data"] is False


def test_set_bookmarks_no_content_id_false(client):
    token = _authenticate(client)
    r = _call(client, "setBookmarks", {"bookmark": {}}, headers=_session_headers(token))
    assert r.json()["data"] is False


def test_get_bookmarks_anon_empty(client):
    r = _call(client, "getBookmarks", {"contentId": 42})
    assert r.json()["data"] == {}


def test_get_bookmarks_no_content_id_empty(client):
    token = _authenticate(client)
    r = _call(client, "getBookmarks", {}, headers=_session_headers(token))
    assert r.json()["data"] == {}


def test_get_bookmarks_no_file_empty(client):
    token = _authenticate(client)
    r = _call(client, "getBookmarks", {"contentId": 999}, headers=_session_headers(token))
    assert r.json()["data"] == {}
