"""Tests for plugin entry-point discovery."""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest


class _DummyPlugin:
    name = "dummy"

    async def authenticate(self, username, password):
        return True

    async def list_bookshelf(self, username):
        return []

    async def add_to_bookshelf(self, username, node_id):
        return True

    async def remove_from_bookshelf(self, username, node_id):
        return True

    async def search(self, username, query, formats, page):
        return None


class _BoomOnImport:
    """Sentinel — marker only; the entry-point ``load()`` raises."""


def _reload_plugins(monkeypatch, plugin_name: str):
    """Set HUMMINGBIRD_PLUGIN and reimport config + plugins so the
    module-level ``settings.plugin`` reflects the env var."""
    monkeypatch.setenv("HUMMINGBIRD_PLUGIN", plugin_name)
    import hummingbird.config as config
    import hummingbird.plugins as plugins
    importlib.reload(config)
    importlib.reload(plugins)
    return plugins


def _fake_entry_points(eps):
    """Returns a callable shaped like importlib.metadata.entry_points."""
    def _factory(group=None):
        return list(eps)
    return _factory


def test_active_plugin_none_when_unset(monkeypatch):
    plugins = _reload_plugins(monkeypatch, "")
    assert plugins.active_plugin() is None


def test_active_plugin_warns_when_not_found(monkeypatch, caplog):
    plugins = _reload_plugins(monkeypatch, "missing-plugin")
    monkeypatch.setattr(plugins, "entry_points", _fake_entry_points([]))
    with caplog.at_level("WARNING"):
        assert plugins.active_plugin() is None
    assert any("not found" in m for m in caplog.messages)


def test_active_plugin_returns_none_when_load_raises(monkeypatch, caplog):
    plugins = _reload_plugins(monkeypatch, "boom")

    def _load():
        raise RuntimeError("import failed")

    ep = SimpleNamespace(name="boom", load=_load)
    monkeypatch.setattr(plugins, "entry_points", _fake_entry_points([ep]))

    with caplog.at_level("ERROR"):
        assert plugins.active_plugin() is None
    assert any("failed to import" in m for m in caplog.messages)


def test_active_plugin_returns_none_when_instantiation_raises(monkeypatch, caplog):
    plugins = _reload_plugins(monkeypatch, "ctor-boom")

    class _Broken:
        def __init__(self):
            raise RuntimeError("ctor blew up")

    ep = SimpleNamespace(name="ctor-boom", load=lambda: _Broken)
    monkeypatch.setattr(plugins, "entry_points", _fake_entry_points([ep]))

    with caplog.at_level("ERROR"):
        assert plugins.active_plugin() is None
    assert any("failed to instantiate" in m for m in caplog.messages)


def test_active_plugin_loads_and_caches(monkeypatch):
    plugins = _reload_plugins(monkeypatch, "dummy")

    ep = SimpleNamespace(name="dummy", load=lambda: _DummyPlugin)
    monkeypatch.setattr(plugins, "entry_points", _fake_entry_points([ep]))

    instance = plugins.active_plugin()
    assert isinstance(instance, _DummyPlugin)
    # Second call returns the same cached instance — _loaded gate.
    assert plugins.active_plugin() is instance


def test_plugin_abc_cannot_instantiate_directly():
    """Plugin is abstract — instantiating without overriding all
    hooks raises TypeError. Locks the ABC contract."""
    from hummingbird.plugins import Plugin

    with pytest.raises(TypeError):
        Plugin()  # type: ignore[abstract]
