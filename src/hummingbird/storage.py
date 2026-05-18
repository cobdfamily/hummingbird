"""JSON-backed default storage for bookshelves, sessions, and bookmarks.

Files:
  {data_dir}/bookshelves/{username}.json          -> list of stored-shelf entries
  {data_dir}/sessions/{username}.json             -> session record
  {data_dir}/bookmarks/{username}/{cid}.json      -> opaque bookmark JSON

Shelf entry shape:
  {"id": int, "format": int, "title": str, "added_at": ISO-8601 UTC}
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import settings
from .formats import format_label
from .models import BookRecord, FormatEntry


_FORBIDDEN_PATH_CHARS = frozenset("/\\\x00")


def _safe_component(name: str | int, *, field: str) -> str:
    """Reject filesystem-unsafe identifiers before they become part of a path.

    Usernames flow in from HTTP Basic auth and the KADOS Session-token
    resolver; KADOS contentIds flow in from arbitrary clients (KADOS
    treats them as opaque strings). Both end up as directory or file
    names below ``data_dir``. Without this guard a contentId like
    ``../sessions/admin`` would let a caller write a bookmark to any
    path the server process can reach. Each component must be non-empty,
    contain no slashes / backslashes / NUL bytes, and not be a
    ``.``/``..`` literal.
    """
    s = str(name)
    if not s:
        raise ValueError(f"{field} must not be empty")
    if len(s) > 255:
        raise ValueError(f"{field} exceeds 255 chars")
    if any(c in _FORBIDDEN_PATH_CHARS for c in s):
        raise ValueError(f"{field} contains an illegal path character")
    if s in (".", ".."):
        raise ValueError(f"{field} is a reserved path component")
    return s


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _shelf_path(username: str) -> Path:
    safe = _safe_component(username, field="username")
    return settings.data_dir / "bookshelves" / f"{safe}.json"


def _session_path(username: str) -> Path:
    safe = _safe_component(username, field="username")
    return settings.data_dir / "sessions" / f"{safe}.json"


def _bookmark_path(username: str, content_id: int | str) -> Path:
    safe_user = _safe_component(username, field="username")
    safe_cid = _safe_component(content_id, field="content_id")
    return settings.data_dir / "bookmarks" / safe_user / f"{safe_cid}.json"


# ---------- bookshelf ----------------------------------------------------


@dataclass
class ShelfEntry:
    id: int
    format: int
    title: str
    added_at: str
    due_date: str | None = None


def _read_shelf(username: str) -> list[ShelfEntry]:
    path = _shelf_path(username)
    if not path.exists():
        return []
    raw = json.loads(path.read_text())
    # ``due_date`` was added later -- tolerate older shelf files that
    # don't carry it by defaulting to None.
    return [ShelfEntry(due_date=r.get("due_date"), **{k: v for k, v in r.items() if k != "due_date"}) for r in raw]


def _write_shelf(username: str, entries: list[ShelfEntry]) -> None:
    path = _shelf_path(username)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([asdict(e) for e in entries], indent=2) + "\n")


def list_bookshelf(username: str) -> list[BookRecord]:
    """Return the on-disk bookshelf as BookRecords (one format each)."""
    out: list[BookRecord] = []
    for entry in _read_shelf(username):
        out.append(
            BookRecord(
                id=entry.id,
                title=entry.title,
                formats=[FormatEntry(id=entry.format, label=format_label(entry.format))],
                due_date=entry.due_date,
            )
        )
    return out


def add_to_bookshelf(
    username: str, node_id: int, format: int, title: str = "", due_date: str | None = None
) -> bool:
    """Append one (book, format) entry. No-op if the pair is already present."""
    entries = _read_shelf(username)
    if any(e.id == node_id and e.format == format for e in entries):
        return True
    entries.append(
        ShelfEntry(
            id=node_id, format=format, title=title,
            added_at=_utc_now(), due_date=due_date,
        )
    )
    _write_shelf(username, entries)
    return True


def get_due_date(username: str, node_id: int) -> str | None:
    """Return the due_date stored for a (user, book) pair, or None.

    Used by the KADOS ``contentReturnDate`` handler -- centralising the
    lookup in storage so the plugin-vs-storage delegation pattern can
    cleanly fall through here when the plugin doesn't override."""
    for entry in _read_shelf(username):
        if entry.id == node_id:
            return entry.due_date
    return None


def remove_from_bookshelf(username: str, node_id: int, format: int | None = None) -> bool:
    """Drop matching entries. If `format` is None, drop every format of this book."""
    entries = _read_shelf(username)
    kept = [
        e for e in entries
        if not (e.id == node_id and (format is None or e.format == format))
    ]
    if len(kept) == len(entries):
        return False
    _write_shelf(username, kept)
    return True


# ---------- sessions ------------------------------------------------------


def write_session(username: str, **fields) -> None:
    path = _session_path(username)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"username": username, "created_at": _utc_now(), **fields}
    path.write_text(json.dumps(record, indent=2) + "\n")


def read_session(username: str) -> dict | None:
    path = _session_path(username)
    if not path.exists():
        return None
    return json.loads(path.read_text())


def clear_session(username: str) -> None:
    path = _session_path(username)
    if path.exists():
        path.unlink()


# ---------- bookmarks -----------------------------------------------------


def write_bookmark(username: str, content_id: int | str, bookmark: dict) -> bool:
    """Persist an opaque bookmark dict. Overwrites any prior value."""
    path = _bookmark_path(username, content_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(bookmark or {})
    payload["updated_at"] = _utc_now()
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return True


def read_bookmark(username: str, content_id: int | str) -> dict:
    """Return the stored bookmark dict, or ``{}`` if none."""
    path = _bookmark_path(username, content_id)
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def clear_bookmark(username: str, content_id: int | str) -> bool:
    """Drop a stored bookmark. Returns False if there was nothing to drop."""
    path = _bookmark_path(username, content_id)
    if not path.exists():
        return False
    path.unlink()
    return True
