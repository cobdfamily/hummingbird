"""Storage-layer unit tests — bookshelf + session JSON I/O."""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def storage(tmp_path, monkeypatch):
    """Re-import hummingbird.storage with the data dir pointed at
    a per-test ``tmp_path``. Avoids cross-test pollution since
    storage writes JSON to disk."""
    monkeypatch.setenv("HUMMINGBIRD_DATA_DIR", str(tmp_path))
    import hummingbird.config as config
    import hummingbird.storage as storage
    importlib.reload(config)
    importlib.reload(storage)
    return storage


def test_list_bookshelf_empty_when_no_file(storage):
    assert storage.list_bookshelf("alice") == []


def test_add_to_bookshelf_writes_entry(storage):
    ok = storage.add_to_bookshelf("alice", 42, format=1, title="Moby Dick")
    assert ok is True
    shelf = storage.list_bookshelf("alice")
    assert len(shelf) == 1
    assert shelf[0].id == 42
    assert shelf[0].title == "Moby Dick"
    assert shelf[0].formats[0].id == 1


def test_add_to_bookshelf_idempotent_on_duplicate(storage):
    """Same (book, format) added twice -> still one entry, ok=True."""
    storage.add_to_bookshelf("alice", 42, format=1, title="X")
    storage.add_to_bookshelf("alice", 42, format=1, title="X")
    assert len(storage.list_bookshelf("alice")) == 1


def test_add_same_book_different_format_keeps_both(storage):
    storage.add_to_bookshelf("alice", 42, format=1, title="X")
    storage.add_to_bookshelf("alice", 42, format=2, title="X")
    assert len(storage.list_bookshelf("alice")) == 2


def test_remove_from_bookshelf_drops_one_format(storage):
    storage.add_to_bookshelf("alice", 42, format=1, title="X")
    storage.add_to_bookshelf("alice", 42, format=2, title="X")
    ok = storage.remove_from_bookshelf("alice", 42, format=1)
    assert ok is True
    remaining = storage.list_bookshelf("alice")
    assert len(remaining) == 1
    assert remaining[0].formats[0].id == 2


def test_remove_from_bookshelf_drops_all_formats_when_format_none(storage):
    """``format=None`` drops every format of the book — useful
    for "remove from shelf" without caring which copy."""
    storage.add_to_bookshelf("alice", 42, format=1, title="X")
    storage.add_to_bookshelf("alice", 42, format=2, title="X")
    ok = storage.remove_from_bookshelf("alice", 42, format=None)
    assert ok is True
    assert storage.list_bookshelf("alice") == []


def test_remove_from_bookshelf_returns_false_when_no_match(storage):
    """Nothing matched -> ok=False so the route can 404 cleanly."""
    storage.add_to_bookshelf("alice", 42, format=1, title="X")
    ok = storage.remove_from_bookshelf("alice", 999, format=1)
    assert ok is False
    # And the shelf wasn't rewritten.
    assert len(storage.list_bookshelf("alice")) == 1


# ---------------------------------------------------------------------------
# sessions
# ---------------------------------------------------------------------------


def test_read_session_returns_none_when_no_file(storage):
    assert storage.read_session("alice") is None


def test_write_then_read_session_roundtrips(storage):
    storage.write_session("alice", access_token="abc", scope="library")
    record = storage.read_session("alice")
    assert record is not None
    assert record["username"] == "alice"
    assert record["access_token"] == "abc"
    assert record["scope"] == "library"
    assert "created_at" in record


def test_clear_session_removes_file(storage):
    storage.write_session("alice", access_token="abc")
    storage.clear_session("alice")
    assert storage.read_session("alice") is None


def test_clear_session_idempotent_when_no_file(storage):
    """clear_session on a never-logged-in user is a silent no-op
    (not an error). Important so logout calls always succeed
    even if the session was already evicted."""
    # Should not raise.
    storage.clear_session("never-logged-in")


# ---------------------------------------------------------------------------
# bookmarks
# ---------------------------------------------------------------------------


def test_read_bookmark_empty_when_no_file(storage):
    assert storage.read_bookmark("alice", 42) == {}


def test_write_then_read_bookmark_roundtrips(storage):
    ok = storage.write_bookmark("alice", 42, {"currentTime": 12.5, "duration": 60.0})
    assert ok is True
    bookmark = storage.read_bookmark("alice", 42)
    assert bookmark["currentTime"] == 12.5
    assert bookmark["duration"] == 60.0
    assert "updated_at" in bookmark


def test_write_bookmark_overwrites_prior_value(storage):
    storage.write_bookmark("alice", 42, {"currentTime": 1.0})
    storage.write_bookmark("alice", 42, {"currentTime": 99.0})
    assert storage.read_bookmark("alice", 42)["currentTime"] == 99.0


def test_write_bookmark_empty_payload(storage):
    """A None / empty bookmark still creates a file with just the
    timestamp -- useful for "I started the book but no progress yet"."""
    ok = storage.write_bookmark("alice", 42, {})
    assert ok is True
    bookmark = storage.read_bookmark("alice", 42)
    assert list(bookmark.keys()) == ["updated_at"]


def test_clear_bookmark_removes_file(storage):
    storage.write_bookmark("alice", 42, {"currentTime": 1.0})
    assert storage.clear_bookmark("alice", 42) is True
    assert storage.read_bookmark("alice", 42) == {}


def test_clear_bookmark_returns_false_when_no_file(storage):
    assert storage.clear_bookmark("alice", 999) is False


def test_bookmarks_isolated_per_user_and_per_content(storage):
    storage.write_bookmark("alice", 42, {"currentTime": 1.0})
    storage.write_bookmark("alice", 43, {"currentTime": 2.0})
    storage.write_bookmark("bob", 42, {"currentTime": 3.0})
    assert storage.read_bookmark("alice", 42)["currentTime"] == 1.0
    assert storage.read_bookmark("alice", 43)["currentTime"] == 2.0
    assert storage.read_bookmark("bob", 42)["currentTime"] == 3.0


# ---------------------------------------------------------------------------
# due_date (loan-period)
# ---------------------------------------------------------------------------


def test_add_with_due_date_round_trips_through_list_bookshelf(storage):
    storage.add_to_bookshelf(
        "alice", 42, format=1, title="X", due_date="2026-06-01T00:00:00+00:00"
    )
    shelf = storage.list_bookshelf("alice")
    assert shelf[0].due_date == "2026-06-01T00:00:00+00:00"


def test_get_due_date_returns_stored_value(storage):
    storage.add_to_bookshelf(
        "alice", 42, format=1, title="X", due_date="2026-06-01T00:00:00+00:00"
    )
    assert storage.get_due_date("alice", 42) == "2026-06-01T00:00:00+00:00"


def test_get_due_date_returns_none_when_unset(storage):
    """No loan period (the NNELS case) -> due_date is None."""
    storage.add_to_bookshelf("alice", 42, format=1, title="X")
    assert storage.get_due_date("alice", 42) is None


def test_get_due_date_returns_none_when_book_not_on_shelf(storage):
    assert storage.get_due_date("alice", 999) is None


def test_old_shelf_files_without_due_date_load_cleanly(storage, tmp_path):
    """Persisted shelves written before due_date existed don't carry
    the field; the loader has to tolerate that or we lose backwards
    compatibility with existing operator deployments."""
    # Hand-craft an old-style shelf file.
    import json
    path = tmp_path / "bookshelves" / "alice.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([{
        "id": 42, "format": 1, "title": "X", "added_at": "2026-01-01T00:00:00+00:00"
    }]))
    shelf = storage.list_bookshelf("alice")
    assert len(shelf) == 1
    assert shelf[0].due_date is None
