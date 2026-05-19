"""Integration tests for /protocols/hummingbird/v1 (login, bookshelf,
search, download). Standalone mode — no plugin loaded, env-credential
authentication, JSON-backed default storage."""

from __future__ import annotations

import base64
import importlib
import zipfile
from io import BytesIO

import pytest
from fastapi.testclient import TestClient


def _basic_auth(user: str, pw: str) -> dict[str, str]:
    token = base64.b64encode(f"{user}:{pw}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


# Default Basic auth for env-defined credentials in the standalone fixture.
AUTH = _basic_auth("alice", "secret")


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Reload config + the whole module tree so settings.{data,cache}_dir
    point at a per-test tmp dir. Pre-populates the auth cache with
    alice/secret so existing tests can hit REST routes without having to
    build a Basic auth header per request -- a dedicated test file
    exercises the real auth dependency."""
    monkeypatch.setenv("HUMMINGBIRD_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("HUMMINGBIRD_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("HUMMINGBIRD_USERNAME", "alice")
    monkeypatch.setenv("HUMMINGBIRD_PASSWORD", "secret")
    monkeypatch.setenv("HUMMINGBIRD_PLUGIN", "")
    monkeypatch.delenv("HUMMINGBIRD_PUBLIC_CONTENT_URL", raising=False)
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
    import hummingbird.protocols.hummingbird.router as hb_router
    import hummingbird.protocols.kados.router as kd_router
    importlib.reload(hb_router)
    importlib.reload(kd_router)
    import hummingbird.main as main
    importlib.reload(main)
    tc = TestClient(main.app)
    tc.headers.update(AUTH)
    auth.remember_login("alice", "secret")
    return tc


# ---------------------------------------------------------------------------
# /login
# ---------------------------------------------------------------------------


def test_login_succeeds_with_env_credentials(client):
    r = client.post(
        "/protocols/hummingbird/v1/login",
        json={"username": "alice", "password": "secret"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["authenticated"] is True
    assert body["username"] == "alice"


def test_login_uses_env_defaults_when_no_body_args(client):
    r = client.post("/protocols/hummingbird/v1/login", json={})
    assert r.status_code == 200
    assert r.json()["authenticated"] is True


def test_login_rejects_wrong_password(client):
    r = client.post(
        "/protocols/hummingbird/v1/login",
        json={"username": "alice", "password": "wrong"},
    )
    assert r.status_code == 401


def test_login_400_when_no_credentials(client, monkeypatch):
    monkeypatch.delenv("HUMMINGBIRD_USERNAME", raising=False)
    monkeypatch.delenv("HUMMINGBIRD_PASSWORD", raising=False)
    import hummingbird.config as config
    import hummingbird.protocols.hummingbird.router as hb_router
    importlib.reload(config)
    importlib.reload(hb_router)
    import hummingbird.main as main
    importlib.reload(main)
    fresh = TestClient(main.app)
    r = fresh.post("/protocols/hummingbird/v1/login", json={})
    assert r.status_code == 400


def test_login_query_string_credentials_no_longer_accepted(client):
    """The old ?username=&password= shape used to work; it now fails
    because credentials in the URL get captured by every access log
    in the request path (uvicorn, reverse proxies, CDN, the user's
    own Console.app). Body-only is the supported shape -- FastAPI
    returns 422 when the body is absent."""
    r = client.post(
        "/protocols/hummingbird/v1/login?username=alice&password=secret"
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# /bookshelf
# ---------------------------------------------------------------------------


def test_bookshelf_list_empty_when_no_books(client):
    r = client.get("/protocols/hummingbird/v1/bookshelf/list?username=alice")
    assert r.status_code == 200
    body = r.json()
    assert body["username"] == "alice"
    assert body["items"] == []
    assert body["count"] == 0


def test_bookshelf_add_then_list(client):
    r = client.post(
        "/protocols/hummingbird/v1/bookshelf/add/42"
        "?username=alice&format=4&title=Moby+Dick"
    )
    assert r.status_code == 200
    assert r.json()["success"] is True

    r = client.get("/protocols/hummingbird/v1/bookshelf/list?username=alice")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    item = body["items"][0]
    assert item["id"] == 42
    assert "Moby Dick" in item["title"]
    assert item["url"].endswith("/protocols/hummingbird/v1/download/4/42/")


def test_bookshelf_remove(client):
    client.post(
        "/protocols/hummingbird/v1/bookshelf/add/42?username=alice&format=4&title=X"
    )
    r = client.post(
        "/protocols/hummingbird/v1/bookshelf/remove/42?username=alice&format=4"
    )
    assert r.status_code == 200
    assert r.json()["success"] is True
    r = client.get("/protocols/hummingbird/v1/bookshelf/list?username=alice")
    assert r.json()["count"] == 0


def test_bookshelf_remove_no_format_drops_all(client):
    client.post(
        "/protocols/hummingbird/v1/bookshelf/add/42?username=alice&format=4&title=X"
    )
    client.post(
        "/protocols/hummingbird/v1/bookshelf/add/42?username=alice&format=11&title=X"
    )
    r = client.post(
        "/protocols/hummingbird/v1/bookshelf/remove/42?username=alice"
    )
    assert r.json()["success"] is True
    r = client.get("/protocols/hummingbird/v1/bookshelf/list?username=alice")
    assert r.json()["count"] == 0


def test_bookshelf_username_comes_from_basic_auth(client):
    # Basic auth carries alice/secret per the fixture; ?username= is
    # ignored. The route resolves the user from the Authorization header.
    client.post(
        "/protocols/hummingbird/v1/bookshelf/add/7?format=4&title=Y"
    )
    r = client.get("/protocols/hummingbird/v1/bookshelf/list")
    assert r.status_code == 200
    assert r.json()["username"] == "alice"
    assert r.json()["count"] == 1


def test_bookshelf_401_when_no_auth(client):
    """Stripping the Authorization header -> the dependency rejects with
    401 + WWW-Authenticate: Basic. (The old behaviour was 400-when-no-
    username; that path is gone now that the username comes from auth.)"""
    fresh = TestClient(client.app)  # no default headers
    r = fresh.get("/protocols/hummingbird/v1/bookshelf/list")
    assert r.status_code == 401
    assert r.headers.get("WWW-Authenticate") == "Basic"


def test_bookshelf_list_exposes_due_date(client):
    """Standalone storage carries due_date end-to-end so a client (eg.
    BookPlayer) can auto-return expired loans."""
    import hummingbird.storage as storage
    storage.add_to_bookshelf(
        "alice", 42, format=4, title="X",
        due_date="2026-06-01T00:00:00+00:00",
    )
    r = client.get("/protocols/hummingbird/v1/bookshelf/list")
    item = r.json()["items"][0]
    assert item["due_date"] == "2026-06-01T00:00:00+00:00"


def test_bookshelf_list_due_date_null_for_libraries_without_loans(client):
    """NNELS-style: no loan period -> due_date is null in the response."""
    import hummingbird.storage as storage
    storage.add_to_bookshelf("alice", 42, format=4, title="X")
    r = client.get("/protocols/hummingbird/v1/bookshelf/list")
    item = r.json()["items"][0]
    assert item["due_date"] is None


# ---------------------------------------------------------------------------
# /bookshelf/bookmark (standalone, no plugin -> file-backed storage)
# ---------------------------------------------------------------------------


def test_bookmark_get_empty_when_unset(client):
    r = client.get("/protocols/hummingbird/v1/bookshelf/bookmark/42?username=alice")
    assert r.status_code == 200
    body = r.json()
    assert body["bookmark"] == {}
    assert body["node_id"] == 42
    assert body["username"] == "alice"


def test_bookmark_set_then_get_roundtrips(client):
    r = client.post(
        "/protocols/hummingbird/v1/bookshelf/bookmark/42?username=alice",
        json={"bookmark": {"currentTime": 12.5, "duration": 60.0, "isFinished": False}},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["action"] == "set"

    r = client.get("/protocols/hummingbird/v1/bookshelf/bookmark/42?username=alice")
    bookmark = r.json()["bookmark"]
    assert bookmark["currentTime"] == 12.5
    assert bookmark["duration"] == 60.0
    assert bookmark["isFinished"] is False


def test_bookmark_set_with_no_payload_field_still_persists(client):
    """Body without ``bookmark`` field treated as empty -- useful so the
    client can stamp "opened" without a position yet."""
    r = client.post(
        "/protocols/hummingbird/v1/bookshelf/bookmark/42?username=alice",
        json={},
    )
    assert r.status_code == 200
    assert r.json()["success"] is True


def test_bookmark_set_401_when_no_auth(client):
    fresh = TestClient(client.app)  # no default headers
    r = fresh.post(
        "/protocols/hummingbird/v1/bookshelf/bookmark/42",
        json={"bookmark": {"currentTime": 1.0}},
    )
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# /search
# ---------------------------------------------------------------------------


def test_search_empty_in_standalone_mode(client):
    """No plugin loaded -> standalone returns empty results."""
    r = client.get("/protocols/hummingbird/v1/search?q=anything&username=alice")
    assert r.status_code == 200
    body = r.json()
    assert body["query"] == "anything"
    assert body["count"] == 0
    assert body["items"] == []


def test_search_rejects_empty_query(client):
    r = client.get("/protocols/hummingbird/v1/search?q=&username=alice")
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# /download
# ---------------------------------------------------------------------------


def _drop_into_cache(tmp_path, fmt: int, node_id: int, name: str, content: bytes):
    d = tmp_path / "cache" / str(fmt) / str(node_id)
    d.mkdir(parents=True)
    (d / name).write_bytes(content)
    return d / name


def test_download_file_404_when_no_cache(client):
    r = client.get("/protocols/hummingbird/v1/download/4/999/")
    assert r.status_code == 404


def test_download_file_returns_bytes_for_single_file(client, tmp_path):
    """The trailing-slash URL serves the actual cached file -- this is
    what BookItem.url points to so clients can do a single blind GET
    and get audio bytes (or a DAISY zip)."""
    _drop_into_cache(tmp_path, 4, 100, "song.mp3", b"AUDIO-DATA")
    r = client.get("/protocols/hummingbird/v1/download/4/100/")
    assert r.status_code == 200
    assert r.content == b"AUDIO-DATA"
    assert "json" not in r.headers.get("content-type", "")


def test_download_file_returns_zip_archive(client, tmp_path):
    """Archive-format books: the trailing-slash URL serves the whole
    .zip back as-is. DAISY-aware clients extract locally."""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("ncc.html", "<html></html>")
        z.writestr("audio/01.mp3", b"audio1")
    _drop_into_cache(tmp_path, 11, 200, "book.zip", buf.getvalue())
    r = client.get("/protocols/hummingbird/v1/download/11/200/")
    assert r.status_code == 200
    # We get the .zip back as bytes, not a JSON listing.
    assert r.content[:2] == b"PK"  # zip magic


def test_download_info_single_file(client, tmp_path):
    """Moved listing JSON lives at /_info now -- DAISY clients can hit
    it to inspect archive contents before fetching individual entries."""
    _drop_into_cache(tmp_path, 4, 100, "song.mp3", b"AUDIO")
    r = client.get("/protocols/hummingbird/v1/download/4/100/_info")
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "single"
    assert body["filename"] == "song.mp3"
    assert body["files"] == ["song.mp3"]
    assert body["count"] == 1


def test_download_info_zip_archive(client, tmp_path):
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("ncc.html", "<html></html>")
        z.writestr("audio/01.mp3", b"audio1")
    _drop_into_cache(tmp_path, 11, 200, "book.zip", buf.getvalue())
    r = client.get("/protocols/hummingbird/v1/download/11/200/_info")
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "archive"
    assert "ncc.html" in body["files"]
    assert "audio/01.mp3" in body["files"]


# ---------------------------------------------------------------------------
# /resources -- DODP-shaped per-content resource list (the canonical
# manifest BookPlayer and other clients use to drive multi-file downloads)
# ---------------------------------------------------------------------------


def test_resources_single_file(client, tmp_path):
    _drop_into_cache(tmp_path, 4, 100, "song.mp3", b"AUDIO")
    r = client.get("/protocols/hummingbird/v1/resources/4/100")
    assert r.status_code == 200
    body = r.json()
    assert body["contentId"] == "100"
    assert body["format"] == 4
    assert len(body["resources"]) == 1
    res = body["resources"][0]
    assert res["mimeType"] == "audio/mpeg"
    assert res["localURI"] == "song.mp3"
    assert res["size"] == 5
    assert res["uri"].endswith("/protocols/hummingbird/v1/download/4/100/song.mp3")


def test_resources_zip_archive_lists_each_entry(client, tmp_path):
    """A DAISY 2.02 zip becomes one resource per file in the archive --
    audio, smil, ncc all enumerated separately so DODP-aware clients
    can see the structure. (Audio-only filtering is the client's
    job; the server emits everything.)"""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("ncc.html", "<html></html>")
        z.writestr("book.smil", "<smil/>")
        z.writestr("audio/01.mp3", b"audio1")
        z.writestr("audio/02.mp3", b"audio2")
    _drop_into_cache(tmp_path, 11, 200, "book.zip", buf.getvalue())
    r = client.get("/protocols/hummingbird/v1/resources/11/200")
    assert r.status_code == 200
    body = r.json()
    resources = body["resources"]
    paths = {res["localURI"] for res in resources}
    assert paths == {"ncc.html", "book.smil", "audio/01.mp3", "audio/02.mp3"}
    # Verify mime types of the audio entries:
    audio = [r for r in resources if r["mimeType"] == "audio/mpeg"]
    assert len(audio) == 2
    # And the URIs are absolute and properly built:
    for res in audio:
        assert res["uri"].endswith(f"/protocols/hummingbird/v1/download/11/200/{res['localURI']}")


def test_resources_404_when_no_cache(client):
    r = client.get("/protocols/hummingbird/v1/resources/4/999")
    assert r.status_code == 404


def test_resources_requires_auth(client):
    fresh = TestClient(client.app)  # no default headers
    r = fresh.get("/protocols/hummingbird/v1/resources/4/100")
    assert r.status_code == 401


def test_download_fetch_single_file(client, tmp_path):
    _drop_into_cache(tmp_path, 4, 100, "song.mp3", b"AUDIO-DATA")
    r = client.get("/protocols/hummingbird/v1/download/4/100/song.mp3")
    assert r.status_code == 200
    assert r.content == b"AUDIO-DATA"


def test_download_fetch_404_when_path_doesnt_match_single_file(client, tmp_path):
    _drop_into_cache(tmp_path, 4, 100, "song.mp3", b"AUDIO")
    r = client.get("/protocols/hummingbird/v1/download/4/100/wrong.mp3")
    assert r.status_code == 404


def test_download_fetch_zip_member(client, tmp_path):
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("ncc.html", "<html>BODY</html>")
    _drop_into_cache(tmp_path, 11, 200, "book.zip", buf.getvalue())
    r = client.get("/protocols/hummingbird/v1/download/11/200/ncc.html")
    assert r.status_code == 200
    assert b"BODY" in r.content


def test_download_fetch_zip_member_404_when_missing(client, tmp_path):
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("ncc.html", "<html></html>")
    _drop_into_cache(tmp_path, 11, 200, "book.zip", buf.getvalue())
    r = client.get("/protocols/hummingbird/v1/download/11/200/missing.html")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# /formats and / (top-level)
# ---------------------------------------------------------------------------


def test_formats_endpoint_returns_id_to_label_map(client):
    r = client.get("/formats")
    assert r.status_code == 200
    body = r.json()
    # JSON keys are strings; integer 4 -> "MP3".
    assert body["4"] == "MP3"


# ---------------------------------------------------------------------------
# Session-token auth on /resources and /download (DAISY-Online clients)
# ---------------------------------------------------------------------------


def _cache_dir(tmp_path, fmt, node_id):
    """Locate the same cache directory the route layer will look at
    (HUMMINGBIRD_CACHE_DIR is set to ``tmp_path / "cache"`` by the
    client fixture). Pre-populating a file there is enough to drive
    the cache-HIT path on /download and /resources."""
    p = tmp_path / "cache" / str(fmt) / str(node_id)
    p.mkdir(parents=True, exist_ok=True)
    return p


def test_download_accepts_basic_auth(client, tmp_path):
    """Existing Basic-auth callers (BookPlayer, curl with -u) keep
    working. Regression guard for the dependency switch."""
    (_cache_dir(tmp_path, 4, 555) / "song.mp3").write_bytes(b"AUDIO")
    r = client.get("/protocols/hummingbird/v1/download/4/555/")
    assert r.status_code == 200


def test_download_accepts_kados_session_token(client, tmp_path):
    """KADOS contentResources hands back /download URIs that the DAISY-
    Online client (EasyReader, etc.) then fetches. Those clients
    authenticate with `Authorization: Session <token>`, NOT Basic.
    Without this support every resource fetch would 401 and the
    end-to-end download flow would never complete."""
    r = client.post(
        "/protocols/kados/v1/methods/authenticate/",
        json={"method": "authenticate", "data": {"username": "alice", "password": "secret"}},
    )
    assert r.status_code == 200
    token = r.json()["data"]["sessionToken"]

    (_cache_dir(tmp_path, 4, 666) / "song.mp3").write_bytes(b"AUDIO-BYTES")

    r = client.get(
        "/protocols/hummingbird/v1/download/4/666/",
        headers={"Authorization": f"Session {token}"},
    )
    assert r.status_code == 200
    assert r.content == b"AUDIO-BYTES"


def test_download_rejects_invalid_session_token(client, tmp_path):
    r = client.get(
        "/protocols/hummingbird/v1/download/4/777/",
        headers={"Authorization": "Session not-a-real-token"},
    )
    assert r.status_code == 401
    # No Basic challenge -- caller explicitly chose Session.
    assert r.headers.get("WWW-Authenticate") != "Basic"


def test_resources_accepts_kados_session_token(client, tmp_path):
    r = client.post(
        "/protocols/kados/v1/methods/authenticate/",
        json={"method": "authenticate", "data": {"username": "alice", "password": "secret"}},
    )
    token = r.json()["data"]["sessionToken"]

    (_cache_dir(tmp_path, 4, 888) / "track.mp3").write_bytes(b"AUDIO")

    r = client.get(
        "/protocols/hummingbird/v1/resources/4/888",
        headers={"Authorization": f"Session {token}"},
    )
    assert r.status_code == 200
