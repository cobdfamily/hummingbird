"""Plugin-active branches across both protocol surfaces.

The unit suite in test_router_hummingbird.py / test_router_kados.py
exercises the standalone (no-plugin) path. These tests inject a
deterministic ``FakePlugin`` into ``hummingbird.plugins._active``
so the corresponding plugin-active branches (delegate to plugin
on success, fall through to default storage on
``NotImplementedError``) get covered too.
"""

from __future__ import annotations

import base64
import importlib

import pytest
from fastapi.testclient import TestClient

from hummingbird.models import BookRecord, FormatEntry, SearchResult
from hummingbird.plugins import Plugin


# ---------------------------------------------------------------------------
# fake plugin
# ---------------------------------------------------------------------------


class FakePlugin(Plugin):
    """Deterministic test double. Attribute-driven so individual
    tests can flip a single hook to raise ``NotImplementedError``
    without subclassing — that's how the routes' "plugin doesn't
    answer -> fall back to default storage" branch is reached."""

    name = "fake"

    def __init__(self):
        self.authenticate_returns: bool | type = True
        self.list_bookshelf_returns: list | type = []
        self.add_to_bookshelf_returns: bool | type = True
        self.remove_from_bookshelf_returns: bool | type = True
        self.search_returns: SearchResult | type = SearchResult(
            query="", page=0, books=[],
        )
        self.set_bookmark_returns: bool | type = True
        self.get_bookmark_returns: dict | type = {}
        # Default: defer to the built-in cache + public-source path, so
        # tests that don't exercise the plugin download branch don't
        # have to stage a fake file.
        self.download_returns: object = NotImplementedError
        self.calls: list[tuple[str, tuple]] = []

    @staticmethod
    def _maybe_raise(value):
        if isinstance(value, type) and issubclass(value, BaseException):
            raise value()
        return value

    async def authenticate(self, username, password):
        self.calls.append(("authenticate", (username, password)))
        return self._maybe_raise(self.authenticate_returns)

    async def list_bookshelf(self, username):
        self.calls.append(("list_bookshelf", (username,)))
        return self._maybe_raise(self.list_bookshelf_returns)

    async def add_to_bookshelf(self, username, node_id):
        self.calls.append(("add_to_bookshelf", (username, node_id)))
        return self._maybe_raise(self.add_to_bookshelf_returns)

    async def remove_from_bookshelf(self, username, node_id):
        self.calls.append(("remove_from_bookshelf", (username, node_id)))
        return self._maybe_raise(self.remove_from_bookshelf_returns)

    async def search(self, username, query, formats, page):
        self.calls.append(("search", (username, query, formats, page)))
        return self._maybe_raise(self.search_returns)

    async def set_bookmark(self, username, content_id, bookmark):
        self.calls.append(("set_bookmark", (username, content_id, bookmark)))
        return self._maybe_raise(self.set_bookmark_returns)

    async def get_bookmark(self, username, content_id):
        self.calls.append(("get_bookmark", (username, content_id)))
        return self._maybe_raise(self.get_bookmark_returns)

    async def download(self, username, fmt, node_id, cache_dir):
        # Default behaviour: raise NotImplementedError so the default
        # public-source fallback path is exercised. Individual tests
        # flip ``download_returns`` to a Path (or NotImplementedError /
        # an exception class) to drive the plugin-hit branch.
        self.calls.append(("download", (username, fmt, node_id, cache_dir)))
        return self._maybe_raise(self.download_returns)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def app_with_plugin(tmp_path, monkeypatch):
    """Reload the whole module tree and stuff a FakePlugin into the
    plugin registry so every route sees it. Returns a tuple of
    (TestClient, plugin) so tests can flip behaviour per-call."""
    monkeypatch.setenv("HUMMINGBIRD_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("HUMMINGBIRD_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("HUMMINGBIRD_USERNAME", "alice")
    monkeypatch.setenv("HUMMINGBIRD_PASSWORD", "secret")
    monkeypatch.setenv("HUMMINGBIRD_PLUGIN", "")  # no entry-point lookup
    monkeypatch.delenv("HUMMINGBIRD_PUBLIC_CONTENT_URL", raising=False)
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

    fake = FakePlugin()
    # active_plugin() short-circuits on _loaded=True without re-running
    # the entry-point discovery — perfect injection point.
    plugins._active = fake
    plugins._loaded = True

    tc = TestClient(main.app)
    # Pre-populate the auth cache + default Basic header so existing
    # tests can hit REST routes; the dedicated auth test file exercises
    # the real dependency.
    token = base64.b64encode(b"alice:secret").decode()
    tc.headers.update({"Authorization": f"Basic {token}"})
    auth.remember_login("alice", "secret")
    return tc, fake


# ---------------------------------------------------------------------------
# /login — plugin-active branches
# ---------------------------------------------------------------------------


def test_login_plugin_authenticate_success(app_with_plugin):
    client, plugin = app_with_plugin
    plugin.authenticate_returns = True
    r = client.post(
        "/protocols/hummingbird/v1/login",
        json={"username": "u", "password": "p"},
    )
    assert r.status_code == 200
    assert r.json()["authenticated"] is True
    # And the plugin saw the right credentials.
    assert plugin.calls[-1] == ("authenticate", ("u", "p"))


def test_login_plugin_authenticate_failure(app_with_plugin):
    client, plugin = app_with_plugin
    plugin.authenticate_returns = False
    r = client.post(
        "/protocols/hummingbird/v1/login",
        json={"username": "u", "password": "p"},
    )
    assert r.status_code == 401


def test_login_plugin_not_implemented_falls_back_to_env(app_with_plugin):
    """Plugin raises NotImplementedError -> route falls through to the
    HUMMINGBIRD_USERNAME/PASSWORD env-credential check. With matching
    creds, login still succeeds."""
    client, plugin = app_with_plugin
    plugin.authenticate_returns = NotImplementedError
    # Env creds (alice/secret per fixture) match -> success.
    r = client.post(
        "/protocols/hummingbird/v1/login",
        json={"username": "alice", "password": "secret"},
    )
    assert r.status_code == 200
    # Wrong creds via the env fallback -> 401.
    r = client.post(
        "/protocols/hummingbird/v1/login",
        json={"username": "alice", "password": "nope"},
    )
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# /bookshelf — plugin-active branches
# ---------------------------------------------------------------------------


def _book(node_id: int, title: str, formats: list[tuple[int, str, str | None]]):
    return BookRecord(
        id=node_id, title=title,
        formats=[FormatEntry(id=fid, label=label, narrator=narr)
                 for fid, label, narr in formats],
    )


def test_bookshelf_list_via_plugin(app_with_plugin):
    client, plugin = app_with_plugin
    plugin.list_bookshelf_returns = [
        _book(42, "Moby Dick", [(4, "MP3", "Pat Bottoms")]),
        _book(99, "War and Peace", [(11, "DAISY 202 Audio", None)]),
    ]
    r = client.get("/protocols/hummingbird/v1/bookshelf/list?username=u")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2
    titles = [item["title"] for item in body["items"]]
    assert any("Moby Dick" in t and "Pat Bottoms" in t for t in titles)
    assert any("War and Peace" in t for t in titles)
    # Username comes from the Basic auth header (alice/secret in the
    # fixture), not from the ?username=u query param (which is ignored).
    assert plugin.calls[-1] == ("list_bookshelf", ("alice",))


def test_bookshelf_list_plugin_not_implemented_falls_back_to_storage(app_with_plugin):
    """Plugin raises NotImplementedError -> route reads from the
    JSON-backed default storage (which is empty here)."""
    client, plugin = app_with_plugin
    plugin.list_bookshelf_returns = NotImplementedError
    r = client.get("/protocols/hummingbird/v1/bookshelf/list?username=u")
    assert r.status_code == 200
    assert r.json()["count"] == 0


def test_bookshelf_add_via_plugin_returns_success_flag(app_with_plugin):
    client, plugin = app_with_plugin
    plugin.add_to_bookshelf_returns = True
    r = client.post("/protocols/hummingbird/v1/bookshelf/add/42?username=u")
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["action"] == "add"
    assert plugin.calls[-1] == ("add_to_bookshelf", ("alice", 42))


def test_bookshelf_add_plugin_not_implemented_falls_through(app_with_plugin):
    """Plugin -> NotImplementedError -> default storage handles add.
    Verify by then listing the shelf and seeing the entry."""
    client, plugin = app_with_plugin
    plugin.add_to_bookshelf_returns = NotImplementedError
    plugin.list_bookshelf_returns = NotImplementedError
    r = client.post(
        "/protocols/hummingbird/v1/bookshelf/add/42?username=u&format=4&title=X"
    )
    assert r.status_code == 200
    assert r.json()["success"] is True
    r = client.get("/protocols/hummingbird/v1/bookshelf/list?username=u")
    # Default storage now has the entry.
    assert r.json()["count"] == 1


def test_bookshelf_remove_via_plugin(app_with_plugin):
    client, plugin = app_with_plugin
    plugin.remove_from_bookshelf_returns = True
    r = client.post("/protocols/hummingbird/v1/bookshelf/remove/42?username=u")
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["action"] == "remove"
    assert plugin.calls[-1] == ("remove_from_bookshelf", ("alice", 42))


def test_bookshelf_remove_plugin_not_implemented_falls_through(app_with_plugin):
    """NotImplementedError -> default storage path. The shelf is empty,
    so storage.remove returns False (nothing to remove)."""
    client, plugin = app_with_plugin
    plugin.remove_from_bookshelf_returns = NotImplementedError
    r = client.post("/protocols/hummingbird/v1/bookshelf/remove/42?username=u")
    assert r.status_code == 200
    assert r.json()["success"] is False


# ---------------------------------------------------------------------------
# /search — plugin-active branches
# ---------------------------------------------------------------------------


def test_search_via_plugin_returns_books(app_with_plugin):
    client, plugin = app_with_plugin
    plugin.search_returns = SearchResult(
        query="moby", page=0,
        books=[_book(42, "Moby Dick", [(4, "MP3", None)])],
        total_pages=1, total_results=1,
    )
    r = client.get("/protocols/hummingbird/v1/search?q=moby&username=u")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    assert body["total_pages"] == 1
    assert body["total_results"] == 1
    assert plugin.calls[-1] == ("search", ("alice", "moby", None, 0))


def test_search_via_plugin_with_formats_filter_applies_in_route(app_with_plugin):
    """Route enforces a ``formats=`` filter even when the plugin
    didn't filter (a plugin is allowed to return all formats and
    let the route narrow). Books that have no remaining format
    after filtering drop out of the response."""
    client, plugin = app_with_plugin
    plugin.search_returns = SearchResult(
        query="x", page=0,
        books=[
            _book(1, "MP3 only", [(4, "MP3", None)]),
            _book(2, "BRF only", [(3, "BRF", None)]),
            _book(3, "Both",     [(4, "MP3", None), (3, "BRF", None)]),
        ],
        total_pages=1, total_results=3,
    )
    r = client.get(
        "/protocols/hummingbird/v1/search?q=x&username=u&formats=4"
    )
    assert r.status_code == 200
    titles = [item["title"] for item in r.json()["items"]]
    # MP3-only stays; BRF-only drops; Both keeps only the MP3 entry.
    assert any("MP3 only" in t for t in titles)
    assert not any("BRF only" in t for t in titles)


def test_search_plugin_not_implemented_returns_empty(app_with_plugin):
    """Plugin -> NotImplementedError -> route returns an empty
    SearchResult rather than 501-ing the caller."""
    client, plugin = app_with_plugin
    plugin.search_returns = NotImplementedError
    r = client.get("/protocols/hummingbird/v1/search?q=x&username=u")
    assert r.status_code == 200
    assert r.json()["count"] == 0


# ---------------------------------------------------------------------------
# kados methods — plugin-active branches
# ---------------------------------------------------------------------------


def _kados(client, name, data=None, *, headers=None):
    return client.post(
        f"/protocols/kados/v1/methods/{name}/",
        json={"method": name, "data": data or {}},
        headers=headers or {},
    )


def _login_kados(client) -> str:
    """Authenticate via kados and return the session token. Used to
    reach the user-scoped methods (contentList, contentAddBookshelf,
    contentReturn) which all gate on session_user()."""
    r = _kados(client, "authenticate", {"username": "alice", "password": "secret"})
    assert r.status_code == 200
    return r.json()["data"]["sessionToken"]


def test_kados_authenticate_via_plugin_success(app_with_plugin):
    """``authenticate`` delegates to plugin.authenticate first; only
    falls back to env creds when the plugin returns
    NotImplementedError. With the plugin returning True for any
    creds, even mismatched ones succeed."""
    client, plugin = app_with_plugin
    plugin.authenticate_returns = True
    r = _kados(client, "authenticate", {"username": "anyone", "password": "anything"})
    assert r.status_code == 200
    body = r.json()["data"]
    assert body["authenticated"] is True
    assert body["sessionToken"]


def test_kados_authenticate_plugin_falls_back_when_not_implemented(app_with_plugin):
    """Plugin doesn't implement authenticate -> falls through to the
    env-credential check (alice/secret)."""
    client, plugin = app_with_plugin
    plugin.authenticate_returns = NotImplementedError
    r = _kados(client, "authenticate", {"username": "alice", "password": "secret"})
    assert r.json()["data"]["authenticated"] is True


def test_kados_content_list_via_plugin(app_with_plugin):
    client, plugin = app_with_plugin
    plugin.list_bookshelf_returns = [
        _book(42, "Moby Dick", [(4, "MP3", None)]),
    ]
    token = _login_kados(client)
    r = _kados(
        client, "contentList", {"list": "bookshelf"},
        headers={"Authorization": f"Session {token}"},
    )
    body = r.json()["data"]
    assert body["totalItems"] == 1
    assert body["contentItem"][0]["id"] == "42"


def test_kados_content_list_plugin_not_implemented_falls_back(app_with_plugin):
    client, plugin = app_with_plugin
    plugin.list_bookshelf_returns = NotImplementedError
    token = _login_kados(client)
    r = _kados(
        client, "contentList", {"list": "bookshelf"},
        headers={"Authorization": f"Session {token}"},
    )
    body = r.json()["data"]
    # Default storage is empty.
    assert body["totalItems"] == 0


def test_kados_content_add_bookshelf_via_plugin(app_with_plugin):
    client, plugin = app_with_plugin
    plugin.add_to_bookshelf_returns = True
    token = _login_kados(client)
    r = _kados(
        client, "contentAddBookshelf", {"contentId": 42},
        headers={"Authorization": f"Session {token}"},
    )
    assert r.json()["data"] is True
    assert plugin.calls[-1] == ("add_to_bookshelf", ("alice", 42))


def test_kados_content_return_date_via_plugin(app_with_plugin):
    """Plugin overrides ``list_bookshelf`` to return a book with a
    due_date. ``contentReturnDate`` finds that book and returns the
    due_date (rather than falling through to storage)."""
    client, plugin = app_with_plugin
    plugin.list_bookshelf_returns = [
        BookRecord(
            id=42, title="Loan",
            formats=[FormatEntry(id=4, label="MP3")],
            due_date="2026-06-01T00:00:00+00:00",
        ),
    ]
    token = _login_kados(client)
    r = _kados(
        client, "contentReturnDate", {"contentId": 42},
        headers={"Authorization": f"Session {token}"},
    )
    assert r.json()["data"] == "2026-06-01T00:00:00+00:00"


def test_kados_content_return_via_plugin(app_with_plugin):
    client, plugin = app_with_plugin
    plugin.remove_from_bookshelf_returns = True
    token = _login_kados(client)
    r = _kados(
        client, "contentReturn", {"contentId": 42},
        headers={"Authorization": f"Session {token}"},
    )
    assert r.json()["data"] is True
    assert plugin.calls[-1] == ("remove_from_bookshelf", ("alice", 42))


def test_kados_content_add_plugin_not_implemented_falls_back(app_with_plugin):
    """Plugin raises NotImplementedError -> the kados handler falls
    through to the JSON-backed default storage. Storage's
    add_to_bookshelf returns True on a fresh shelf."""
    client, plugin = app_with_plugin
    plugin.add_to_bookshelf_returns = NotImplementedError
    token = _login_kados(client)
    r = _kados(
        client, "contentAddBookshelf", {"contentId": 42},
        headers={"Authorization": f"Session {token}"},
    )
    assert r.json()["data"] is True


def test_kados_content_return_plugin_not_implemented_falls_back(app_with_plugin):
    """Plugin -> NotImplementedError -> default storage path. Empty
    shelf so storage.remove returns False."""
    client, plugin = app_with_plugin
    plugin.remove_from_bookshelf_returns = NotImplementedError
    token = _login_kados(client)
    r = _kados(
        client, "contentReturn", {"contentId": 42},
        headers={"Authorization": f"Session {token}"},
    )
    assert r.json()["data"] is False


# ---------------------------------------------------------------------------
# kados router error mapping that the existing suite didn't reach
# ---------------------------------------------------------------------------


def test_kados_router_propagates_httpexception_from_handler(app_with_plugin):
    """A handler that raises HTTPException directly (eg. an
    auth-style handler that wants to return a specific status code)
    must NOT be repackaged as 500 -- the router re-raises so the
    handler's status code reaches the caller. Custom test methods
    aren't in the anonymous list so the call needs a Session token."""
    import hummingbird.protocols.kados.methods as kd_methods
    from fastapi import HTTPException

    async def _http_raiser(data, user, **_):
        raise HTTPException(status_code=418, detail="im a teapot")

    client, _ = app_with_plugin
    token = _login_kados(client)
    kd_methods._REGISTRY["_teapot"] = _http_raiser
    try:
        r = _kados(client, "_teapot", headers={"Authorization": f"Session {token}"})
        assert r.status_code == 418
        assert "teapot" in r.json()["detail"]
    finally:
        del kd_methods._REGISTRY["_teapot"]


def test_kados_router_maps_notimplementederror_to_501(app_with_plugin):
    """A handler that raises NotImplementedError (eg. a plugin
    extension that hasn't been wired up yet) becomes a 501 with a
    structured detail rather than a 500."""
    import hummingbird.protocols.kados.methods as kd_methods

    async def _ni(data, user, **_):
        raise NotImplementedError("not yet")

    client, _ = app_with_plugin
    token = _login_kados(client)
    kd_methods._REGISTRY["_ni_method"] = _ni
    try:
        r = _kados(client, "_ni_method", headers={"Authorization": f"Session {token}"})
        assert r.status_code == 501
        assert "not yet" in r.json()["detail"]
    finally:
        del kd_methods._REGISTRY["_ni_method"]


# ---------------------------------------------------------------------------
# /bookshelf/bookmark — plugin-active branches (REST + kados)
# ---------------------------------------------------------------------------


def test_rest_bookmark_set_via_plugin(app_with_plugin):
    client, plugin = app_with_plugin
    plugin.set_bookmark_returns = True
    r = client.post(
        "/protocols/hummingbird/v1/bookshelf/bookmark/42?username=u",
        json={"bookmark": {"currentTime": 12.5}},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["action"] == "set"
    assert plugin.calls[-1] == (
        "set_bookmark", ("alice", 42, {"currentTime": 12.5})
    )


def test_rest_bookmark_set_plugin_not_implemented_falls_through(app_with_plugin):
    """Plugin -> NotImplementedError -> default storage path. The
    bookmark then round-trips through GET against the same storage."""
    client, plugin = app_with_plugin
    plugin.set_bookmark_returns = NotImplementedError
    plugin.get_bookmark_returns = NotImplementedError
    r = client.post(
        "/protocols/hummingbird/v1/bookshelf/bookmark/42?username=u",
        json={"bookmark": {"currentTime": 9.0}},
    )
    assert r.status_code == 200
    assert r.json()["success"] is True
    r = client.get("/protocols/hummingbird/v1/bookshelf/bookmark/42?username=u")
    assert r.status_code == 200
    body = r.json()
    assert body["bookmark"]["currentTime"] == 9.0
    # Storage stamps updated_at on write.
    assert "updated_at" in body["bookmark"]


def test_rest_bookmark_get_via_plugin(app_with_plugin):
    client, plugin = app_with_plugin
    plugin.get_bookmark_returns = {"position": "smil-1#p3"}
    r = client.get("/protocols/hummingbird/v1/bookshelf/bookmark/42?username=u")
    assert r.status_code == 200
    body = r.json()
    assert body["bookmark"] == {"position": "smil-1#p3"}
    assert plugin.calls[-1] == ("get_bookmark", ("alice", 42))


def test_rest_bookmark_get_plugin_returns_none_normalizes_to_empty(app_with_plugin):
    """A plugin returning None must surface as ``{}`` in the response."""
    client, plugin = app_with_plugin
    plugin.get_bookmark_returns = None
    r = client.get("/protocols/hummingbird/v1/bookshelf/bookmark/42?username=u")
    assert r.json()["bookmark"] == {}


def test_kados_set_bookmarks_via_plugin(app_with_plugin):
    client, plugin = app_with_plugin
    plugin.set_bookmark_returns = True
    token = _login_kados(client)
    r = _kados(
        client, "setBookmarks",
        {"contentId": 42, "bookmark": {"position": "smil-1#p3"}},
        headers={"Authorization": f"Session {token}"},
    )
    assert r.json()["data"] is True
    assert plugin.calls[-1] == (
        "set_bookmark", ("alice", "42", {"position": "smil-1#p3"})
    )


def test_kados_set_bookmarks_plugin_not_implemented_falls_back(app_with_plugin):
    """Plugin -> NotImplementedError -> storage. setBookmarks then
    getBookmarks round-trips through the JSON-backed default."""
    client, plugin = app_with_plugin
    plugin.set_bookmark_returns = NotImplementedError
    plugin.get_bookmark_returns = NotImplementedError
    token = _login_kados(client)
    r = _kados(
        client, "setBookmarks",
        {"contentId": 42, "bookmark": {"position": "smil-9#x"}},
        headers={"Authorization": f"Session {token}"},
    )
    assert r.json()["data"] is True
    r = _kados(
        client, "getBookmarks", {"contentId": 42},
        headers={"Authorization": f"Session {token}"},
    )
    assert r.json()["data"]["position"] == "smil-9#x"


def test_kados_get_bookmarks_via_plugin(app_with_plugin):
    client, plugin = app_with_plugin
    plugin.get_bookmark_returns = {"position": "smil-1#p3"}
    token = _login_kados(client)
    r = _kados(
        client, "getBookmarks", {"contentId": 42},
        headers={"Authorization": f"Session {token}"},
    )
    assert r.json()["data"] == {"position": "smil-1#p3"}


# ---------------------------------------------------------------------------
# Small router gaps: _guess_mime extra mimes; download fetch 404
# ---------------------------------------------------------------------------


def test_guess_mime_uses_extra_mimes_table():
    """``.brf`` / ``.epub`` / etc aren't in the stdlib mimetypes
    table; the router's ``_EXTRA_MIMES`` dict supplies the
    application-specific types."""
    from hummingbird.protocols.hummingbird.router import _guess_mime

    assert _guess_mime("foo.brf") == "application/x-brf"
    assert _guess_mime("foo.epub") == "application/epub+zip"
    assert _guess_mime("foo.smil") == "application/smil+xml"


def test_flatten_to_items_skips_format_zero():
    """Format id 0 is the "unknown" sentinel from formats.yaml;
    it's not a real downloadable format, so the bookshelf list
    skips it rather than emitting a broken download URL."""
    from hummingbird.protocols.hummingbird.router import _flatten_to_items

    book = _book(1, "X", [(0, "Unknown", None), (4, "MP3", None)])
    items = _flatten_to_items([book], "https://x")
    # Only the MP3 (id=4) entry — the id=0 dropped.
    assert len(items) == 1
    assert "MP3" in items[0].title


def test_download_fetch_returns_503_then_404_for_missing_cache(app_with_plugin):
    """With a plugin loaded, cold-cache requests kick off an async
    prefetch and return 503 + Retry-After immediately. Once the
    prefetch task completes (the FakePlugin's download_returns
    defaults to NotImplementedError -> falls through to public-source
    which is unconfigured -> task completes with None), a subsequent
    poll sees MISSING and returns 404."""
    client, _ = app_with_plugin
    r = client.get("/protocols/hummingbird/v1/download/4/9999/missing.mp3")
    assert r.status_code == 503
    assert r.headers.get("Retry-After") == "10"
    # Allow the in-flight task a moment to drain.
    import time as _time
    _time.sleep(0.1)
    r = client.get("/protocols/hummingbird/v1/download/4/9999/missing.mp3")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# SessionExpired -> 401 across every plugin-touching route
# ---------------------------------------------------------------------------


def test_bookshelf_session_expired_returns_401(app_with_plugin):
    """An expired upstream cookie used to look like an empty bookshelf
    -- worst-possible UX (user thinks their books vanished). Plugin
    raising SessionExpired now surfaces as 401 + WWW-Authenticate so
    clients trigger a fresh sign-in."""
    from hummingbird.plugins import SessionExpired
    client, plugin = app_with_plugin
    plugin.list_bookshelf_returns = SessionExpired
    r = client.get("/protocols/hummingbird/v1/bookshelf/list")
    assert r.status_code == 401
    assert r.headers.get("WWW-Authenticate") == "Basic"


def test_search_session_expired_returns_401(app_with_plugin):
    from hummingbird.plugins import SessionExpired
    client, plugin = app_with_plugin
    plugin.search_returns = SessionExpired
    r = client.get("/protocols/hummingbird/v1/search?q=anything")
    assert r.status_code == 401


def test_bookshelf_add_session_expired_returns_401(app_with_plugin):
    from hummingbird.plugins import SessionExpired
    client, plugin = app_with_plugin
    plugin.add_to_bookshelf_returns = SessionExpired
    r = client.post("/protocols/hummingbird/v1/bookshelf/add/42")
    assert r.status_code == 401


def test_bookshelf_remove_session_expired_returns_401(app_with_plugin):
    from hummingbird.plugins import SessionExpired
    client, plugin = app_with_plugin
    plugin.remove_from_bookshelf_returns = SessionExpired
    r = client.post("/protocols/hummingbird/v1/bookshelf/remove/42")
    assert r.status_code == 401


def test_bookmark_get_session_expired_returns_401(app_with_plugin):
    from hummingbird.plugins import SessionExpired
    client, plugin = app_with_plugin
    plugin.get_bookmark_returns = SessionExpired
    r = client.get("/protocols/hummingbird/v1/bookshelf/bookmark/42")
    assert r.status_code == 401


def test_bookmark_set_session_expired_returns_401(app_with_plugin):
    from hummingbird.plugins import SessionExpired
    client, plugin = app_with_plugin
    plugin.set_bookmark_returns = SessionExpired
    r = client.post(
        "/protocols/hummingbird/v1/bookshelf/bookmark/42",
        json={"bookmark": {"position": 1}},
    )
    assert r.status_code == 401


def test_download_session_expired_returns_401(app_with_plugin):
    """SessionExpired raised from plugin.download propagates through
    the async-prefetch task; the second poll sees SESSION_EXPIRED
    state and returns 401 instead of a generic 404."""
    from hummingbird.plugins import SessionExpired
    client, plugin = app_with_plugin
    plugin.download_returns = SessionExpired
    # First call kicks off prefetch -> 503.
    r = client.get("/protocols/hummingbird/v1/download/4/9999/")
    assert r.status_code == 503
    import time as _time
    _time.sleep(0.1)
    # Second call drains the task; SessionExpired routed to 401.
    r = client.get("/protocols/hummingbird/v1/download/4/9999/")
    assert r.status_code == 401


def test_resources_session_expired_returns_401(app_with_plugin):
    from hummingbird.plugins import SessionExpired
    client, plugin = app_with_plugin
    plugin.download_returns = SessionExpired
    r = client.get("/protocols/hummingbird/v1/resources/4/9999")
    assert r.status_code == 503
    import time as _time
    _time.sleep(0.1)
    r = client.get("/protocols/hummingbird/v1/resources/4/9999")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# get_metadata hook -> contentMetadata via plugin
# ---------------------------------------------------------------------------


def test_kados_content_metadata_uses_plugin_when_available(app_with_plugin):
    """contentMetadata previously returned an empty {dc:identifier:..., dc:title:""}
    stub for every call. The plugin now gets the chance to supply real
    metadata (NNELS' 30-day cache, etc.) via the optional get_metadata
    hook. DODP clients see real titles + authors instead of blank fields."""
    client, plugin = app_with_plugin

    async def _real_metadata(user, content_id):
        return {
            "dc:identifier": str(content_id),
            "dc:title": "Moby-Dick",
            "dc:creator": "Herman Melville",
            "dc:format": "audio/mpeg",
        }
    # Plugin doesn't declare get_metadata in the abstract contract;
    # plugins opt in by overriding it. Inject at instance level here.
    plugin.get_metadata = _real_metadata

    token = _login_kados(client)
    r = _kados(
        client, "contentMetadata", {"contentId": 42},
        headers={"Authorization": f"Session {token}"},
    )
    assert r.status_code == 200
    meta = r.json()["data"]["metadata"]
    assert meta["dc:title"] == "Moby-Dick"
    assert meta["dc:creator"] == "Herman Melville"


def test_kados_content_metadata_falls_back_to_stub_when_plugin_not_impl(app_with_plugin):
    """Plugin's get_metadata raising NotImplementedError -> handler
    falls back to the minimal stub. Existing plugins that predate the
    hook (and use the base Plugin.get_metadata which raises) keep
    working."""
    client, plugin = app_with_plugin

    async def _ni(user, content_id):
        raise NotImplementedError
    plugin.get_metadata = _ni

    token = _login_kados(client)
    r = _kados(
        client, "contentMetadata", {"contentId": 42},
        headers={"Authorization": f"Session {token}"},
    )
    assert r.status_code == 200
    meta = r.json()["data"]["metadata"]
    assert meta["dc:identifier"] == "42"
    assert meta["dc:title"] == ""


# (No-plugin contentMetadata stub coverage lives in
#  test_router_kados.test_content_metadata_includes_identifier.)
