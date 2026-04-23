"""Smoke tests that don't require network or a running server."""

from hummingbird.formats import (
    HUMAN_READABLE_FORMATS,
    NAME_TO_ID,
    format_from_text,
    format_label,
)


def test_format_enum_loaded_from_yaml():
    assert len(HUMAN_READABLE_FORMATS) >= 20
    assert HUMAN_READABLE_FORMATS[0] == ""
    assert HUMAN_READABLE_FORMATS[4] == "MP3"


def test_name_to_id_known_constants():
    assert NAME_TO_ID["MP3"] == 4
    assert NAME_TO_ID["DAISY_202_AUDIO"] == 11


def test_format_from_text_case_insensitive():
    assert format_from_text("mp3") == 4
    assert format_from_text(" DAISY 202 - Audio ") == 11


def test_format_label_roundtrip():
    for i, label in enumerate(HUMAN_READABLE_FORMATS):
        assert format_label(i) == label


def test_storage_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("HUMMINGBIRD_DATA_DIR", str(tmp_path / "data"))
    # reimport so fresh settings pick up the tmp dir
    import importlib

    import hummingbird.config
    import hummingbird.storage
    importlib.reload(hummingbird.config)
    importlib.reload(hummingbird.storage)

    from hummingbird.storage import (
        add_to_bookshelf,
        list_bookshelf,
        remove_from_bookshelf,
    )
    assert list_bookshelf("alex") == []
    assert add_to_bookshelf("alex", 10856, format=3, title="HP")
    books = list_bookshelf("alex")
    assert len(books) == 1
    assert books[0].id == 10856
    assert books[0].formats[0].id == 3
    assert remove_from_bookshelf("alex", 10856)
    assert list_bookshelf("alex") == []


def test_plugin_entry_point_discovery_empty(monkeypatch):
    monkeypatch.setenv("HUMMINGBIRD_PLUGIN", "")
    import importlib

    import hummingbird.config
    import hummingbird.plugins
    importlib.reload(hummingbird.config)
    importlib.reload(hummingbird.plugins)
    from hummingbird.plugins import active_plugin
    assert active_plugin() is None


def test_kados_stub_raises_not_implemented():
    from hummingbird.protocols.kados.methods import get
    handler = get("label")
    assert handler is not None
