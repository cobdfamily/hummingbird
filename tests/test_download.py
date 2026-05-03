"""Tests for the /download helper layer (cache lookup + public-source proxy)."""

from __future__ import annotations

import importlib

import httpx
import pytest


@pytest.fixture
def download(tmp_path, monkeypatch):
    """Reload config + download with cache_dir/data_dir pointed at tmp_path."""
    monkeypatch.setenv("HUMMINGBIRD_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("HUMMINGBIRD_CACHE_DIR", str(tmp_path / "cache"))
    # Don't set PUBLIC_CONTENT_URL yet — individual tests opt in.
    monkeypatch.delenv("HUMMINGBIRD_PUBLIC_CONTENT_URL", raising=False)
    import hummingbird.config as config
    import hummingbird.download as download
    importlib.reload(config)
    importlib.reload(download)
    return download


def _reload_with_public_url(url: str):
    """After setting HUMMINGBIRD_PUBLIC_CONTENT_URL via monkeypatch,
    reload config + download so settings.public_content_url updates."""
    import hummingbird.config as config
    import hummingbird.download as download
    importlib.reload(config)
    importlib.reload(download)
    return download


# ---------------------------------------------------------------------------
# find_cached_file
# ---------------------------------------------------------------------------


def test_find_cached_file_returns_none_when_no_dir(download):
    assert download.find_cached_file(4, 999) is None


def test_find_cached_file_returns_none_when_empty_dir(download):
    cache_dir = download.cache_dir_for(4, 100)
    cache_dir.mkdir(parents=True)
    assert download.find_cached_file(4, 100) is None


def test_find_cached_file_skips_tmp_and_extensionless(download):
    cache_dir = download.cache_dir_for(4, 100)
    cache_dir.mkdir(parents=True)
    (cache_dir / "abc.tmp").write_bytes(b"in progress")
    (cache_dir / "noext").write_bytes(b"weird")
    real = cache_dir / "book.mp3"
    real.write_bytes(b"audio")
    found = download.find_cached_file(4, 100)
    assert found == real


# ---------------------------------------------------------------------------
# fetch_from_public_source
# ---------------------------------------------------------------------------


def _patch_async_client(monkeypatch, download_mod, transport: httpx.MockTransport):
    """Replace ``httpx.AsyncClient`` so the production code uses our
    MockTransport instead of doing real network I/O."""
    real_cls = httpx.AsyncClient

    def _factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_cls(*args, **kwargs)

    monkeypatch.setattr(download_mod.httpx, "AsyncClient", _factory)


@pytest.mark.asyncio
async def test_fetch_returns_none_when_no_public_url(download):
    """Empty PUBLIC_CONTENT_URL -> bail immediately, no network."""
    assert await download.fetch_from_public_source(4, 100) is None


@pytest.mark.asyncio
async def test_fetch_returns_none_on_index_failure(download, monkeypatch):
    monkeypatch.setenv("HUMMINGBIRD_PUBLIC_CONTENT_URL", "https://content.example/")
    download = _reload_with_public_url("https://content.example/")

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    transport = httpx.MockTransport(_handler)
    _patch_async_client(monkeypatch, download, transport)

    assert await download.fetch_from_public_source(4, 100) is None


@pytest.mark.asyncio
async def test_fetch_parses_json_files_dict(download, monkeypatch, tmp_path):
    """Public source returns ``{"files": ["thing.mp3"]}`` -> stream
    that filename and write it into the cache."""
    monkeypatch.setenv("HUMMINGBIRD_PUBLIC_CONTENT_URL", "https://content.example/")
    download = _reload_with_public_url("https://content.example/")

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/4/100/"):
            return httpx.Response(
                200,
                json={"files": ["song.mp3"]},
                headers={"content-type": "application/json"},
            )
        if request.url.path.endswith("/4/100/song.mp3"):
            return httpx.Response(200, content=b"AUDIO-BYTES")
        return httpx.Response(404)

    transport = httpx.MockTransport(_handler)
    _patch_async_client(monkeypatch, download, transport)

    dest = await download.fetch_from_public_source(4, 100)
    assert dest is not None
    assert dest.name == "song.mp3"
    assert dest.read_bytes() == b"AUDIO-BYTES"


@pytest.mark.asyncio
async def test_fetch_parses_json_list(download, monkeypatch):
    monkeypatch.setenv("HUMMINGBIRD_PUBLIC_CONTENT_URL", "https://content.example/")
    download = _reload_with_public_url("https://content.example/")

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/4/200/"):
            return httpx.Response(
                200,
                json=["track.mp3"],
                headers={"content-type": "application/json"},
            )
        if request.url.path.endswith("/4/200/track.mp3"):
            return httpx.Response(200, content=b"DATA")
        return httpx.Response(404)

    transport = httpx.MockTransport(_handler)
    _patch_async_client(monkeypatch, download, transport)

    dest = await download.fetch_from_public_source(4, 200)
    assert dest is not None
    assert dest.name == "track.mp3"


@pytest.mark.asyncio
async def test_fetch_parses_html_href(download, monkeypatch):
    monkeypatch.setenv("HUMMINGBIRD_PUBLIC_CONTENT_URL", "https://content.example/")
    download = _reload_with_public_url("https://content.example/")

    html_index = """
    <html><body>
    <a href="../">..</a>
    <a href="story.mp3">story.mp3</a>
    </body></html>
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/4/300/"):
            return httpx.Response(
                200, text=html_index, headers={"content-type": "text/html"}
            )
        if request.url.path.endswith("/4/300/story.mp3"):
            return httpx.Response(200, content=b"STORY")
        return httpx.Response(404)

    transport = httpx.MockTransport(_handler)
    _patch_async_client(monkeypatch, download, transport)

    dest = await download.fetch_from_public_source(4, 300)
    assert dest is not None
    assert dest.name == "story.mp3"


@pytest.mark.asyncio
async def test_fetch_returns_none_when_no_filename_found(download, monkeypatch):
    """Index reachable but neither JSON nor HTML produced a usable
    filename -> log + None."""
    monkeypatch.setenv("HUMMINGBIRD_PUBLIC_CONTENT_URL", "https://content.example/")
    download = _reload_with_public_url("https://content.example/")

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, text="<html>nothing here</html>",
            headers={"content-type": "text/html"},
        )

    transport = httpx.MockTransport(_handler)
    _patch_async_client(monkeypatch, download, transport)

    assert await download.fetch_from_public_source(4, 400) is None


@pytest.mark.asyncio
async def test_fetch_handles_stream_failure(download, monkeypatch):
    """Index OK and filename parsed, but the stream GET fails -> tmp
    cleaned up, return None."""
    monkeypatch.setenv("HUMMINGBIRD_PUBLIC_CONTENT_URL", "https://content.example/")
    download = _reload_with_public_url("https://content.example/")

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/4/500/"):
            return httpx.Response(
                200,
                json={"files": ["x.mp3"]},
                headers={"content-type": "application/json"},
            )
        return httpx.Response(500)

    transport = httpx.MockTransport(_handler)
    _patch_async_client(monkeypatch, download, transport)

    assert await download.fetch_from_public_source(4, 500) is None
    cache_dir = download.cache_dir_for(4, 500)
    assert not (cache_dir / "x.mp3").exists()
    assert not (cache_dir / "x.mp3.tmp").exists()


# ---------------------------------------------------------------------------
# ensure_cached
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_cached_returns_existing(download):
    cache_dir = download.cache_dir_for(4, 600)
    cache_dir.mkdir(parents=True)
    f = cache_dir / "cached.mp3"
    f.write_bytes(b"already here")
    assert await download.ensure_cached(4, 600) == f


@pytest.mark.asyncio
async def test_ensure_cached_returns_none_when_no_public_source(download):
    """Cache miss + no PUBLIC_CONTENT_URL -> None (no network)."""
    assert await download.ensure_cached(4, 700) is None
