"""Auth-dependency coverage: parses Basic header, validates against the
active plugin (or env credentials in standalone mode), caches positive
results, and rejects missing / wrong / malformed creds with 401."""

from __future__ import annotations

import base64
import importlib

import pytest
from fastapi.testclient import TestClient


def _basic(user: str, pw: str) -> dict[str, str]:
    token = base64.b64encode(f"{user}:{pw}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.fixture
def standalone_client(tmp_path, monkeypatch):
    """Standalone mode: no plugin, env credentials are alice/secret."""
    monkeypatch.setenv("HUMMINGBIRD_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("HUMMINGBIRD_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("HUMMINGBIRD_USERNAME", "alice")
    monkeypatch.setenv("HUMMINGBIRD_PASSWORD", "secret")
    monkeypatch.setenv("HUMMINGBIRD_PLUGIN", "")
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
    return TestClient(main.app)


# ---------------------------------------------------------------------------
# Missing / malformed Authorization header -> 401
# ---------------------------------------------------------------------------


def test_missing_header_returns_401(standalone_client):
    r = standalone_client.get("/protocols/hummingbird/v1/bookshelf/list")
    assert r.status_code == 401
    assert r.headers.get("WWW-Authenticate") == "Basic"


def test_non_basic_scheme_returns_401(standalone_client):
    """Bearer / Session / etc. aren't recognised by this dependency."""
    r = standalone_client.get(
        "/protocols/hummingbird/v1/bookshelf/list",
        headers={"Authorization": "Bearer abc"},
    )
    assert r.status_code == 401


def test_malformed_base64_returns_401(standalone_client):
    r = standalone_client.get(
        "/protocols/hummingbird/v1/bookshelf/list",
        headers={"Authorization": "Basic !!!not-base64!!!"},
    )
    assert r.status_code == 401


def test_basic_without_colon_returns_401(standalone_client):
    encoded = base64.b64encode(b"alicesecret").decode()
    r = standalone_client.get(
        "/protocols/hummingbird/v1/bookshelf/list",
        headers={"Authorization": f"Basic {encoded}"},
    )
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Standalone-mode validation (env creds)
# ---------------------------------------------------------------------------


def test_valid_env_credentials_pass(standalone_client):
    r = standalone_client.get(
        "/protocols/hummingbird/v1/bookshelf/list",
        headers=_basic("alice", "secret"),
    )
    assert r.status_code == 200
    assert r.json()["username"] == "alice"


def test_wrong_password_returns_401(standalone_client):
    r = standalone_client.get(
        "/protocols/hummingbird/v1/bookshelf/list",
        headers=_basic("alice", "wrong"),
    )
    assert r.status_code == 401


def test_wrong_username_returns_401(standalone_client):
    r = standalone_client.get(
        "/protocols/hummingbird/v1/bookshelf/list",
        headers=_basic("eve", "secret"),
    )
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Plugin-mode validation: caching avoids spamming the plugin
# ---------------------------------------------------------------------------


class _CountingPlugin:
    """Counts authenticate() invocations so we can verify the cache."""

    name = "counting"

    def __init__(self):
        self.auth_calls = 0
        self.authenticate_returns = True

    async def authenticate(self, username, password):
        self.auth_calls += 1
        return self.authenticate_returns

    async def list_bookshelf(self, username):
        return []

    async def add_to_bookshelf(self, username, node_id):
        return True

    async def remove_from_bookshelf(self, username, node_id):
        return True

    async def search(self, username, query, formats, page):
        from hummingbird.models import SearchResult
        return SearchResult(query=query, page=page, books=[])

    async def set_bookmark(self, username, content_id, bookmark):
        return True

    async def get_bookmark(self, username, content_id):
        return {}

    async def download(self, username, fmt, node_id, cache_dir):
        raise NotImplementedError


@pytest.fixture
def plugin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("HUMMINGBIRD_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("HUMMINGBIRD_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("HUMMINGBIRD_USERNAME", "")
    monkeypatch.setenv("HUMMINGBIRD_PASSWORD", "")
    monkeypatch.setenv("HUMMINGBIRD_PLUGIN", "")
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

    counting = _CountingPlugin()
    plugins._active = counting
    plugins._loaded = True
    return TestClient(main.app), counting, auth


def test_plugin_validates_and_caches(plugin_client):
    """First REST hit triggers plugin.authenticate; subsequent hits within
    TTL_SECONDS reuse the cache and don't re-invoke the plugin."""
    client, plugin, _ = plugin_client
    headers = _basic("bob", "pw")
    r1 = client.get("/protocols/hummingbird/v1/bookshelf/list", headers=headers)
    assert r1.status_code == 200
    assert plugin.auth_calls == 1
    r2 = client.get("/protocols/hummingbird/v1/bookshelf/list", headers=headers)
    assert r2.status_code == 200
    # Cache hit -- plugin.authenticate not re-invoked.
    assert plugin.auth_calls == 1


def test_cache_expiry_re_invokes_plugin(plugin_client, monkeypatch):
    """Once the TTL elapses, the next request re-validates with the plugin."""
    client, plugin, auth = plugin_client
    headers = _basic("bob", "pw")
    client.get("/protocols/hummingbird/v1/bookshelf/list", headers=headers)
    assert plugin.auth_calls == 1
    # Fast-forward past the TTL.
    import time
    real_monotonic = time.monotonic
    monkeypatch.setattr(time, "monotonic", lambda: real_monotonic() + auth.TTL_SECONDS + 1)
    client.get("/protocols/hummingbird/v1/bookshelf/list", headers=headers)
    assert plugin.auth_calls == 2


def test_plugin_rejection_returns_401(plugin_client):
    client, plugin, _ = plugin_client
    plugin.authenticate_returns = False
    r = client.get(
        "/protocols/hummingbird/v1/bookshelf/list",
        headers=_basic("bob", "wrong"),
    )
    assert r.status_code == 401
    assert plugin.auth_calls == 1


def test_login_populates_cache(plugin_client):
    """/login -> success -> auth cache is pre-populated so the next REST
    hit doesn't re-trigger plugin.authenticate."""
    client, plugin, _ = plugin_client
    r = client.post(
        "/protocols/hummingbird/v1/login",
        json={"username": "bob", "password": "pw"},
    )
    assert r.status_code == 200
    assert plugin.auth_calls == 1
    # Subsequent REST request with the same creds is served from cache.
    r = client.get(
        "/protocols/hummingbird/v1/bookshelf/list",
        headers=_basic("bob", "pw"),
    )
    assert r.status_code == 200
    assert plugin.auth_calls == 1
