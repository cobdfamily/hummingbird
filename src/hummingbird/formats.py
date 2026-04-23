"""Format enum, loaded from formats.yaml (single source of truth)."""

from __future__ import annotations

from importlib import resources

import yaml

_raw = yaml.safe_load(
    resources.files(__package__).joinpath("formats.yaml").read_text()
)

_entries: list[dict] = _raw["formats"]
_max_id = max(e["id"] for e in _entries)

HUMAN_READABLE_FORMATS: list[str] = [""] * (_max_id + 1)
NAME_TO_ID: dict[str, int] = {}
_LABEL_LOWER_TO_ID: dict[str, int] = {}

for _entry in _entries:
    _fid = _entry["id"]
    _label = _entry["label"]
    _name = _entry["name"]
    if HUMAN_READABLE_FORMATS[_fid]:
        raise RuntimeError(f"duplicate format id {_fid} in formats.yaml")
    if _name in NAME_TO_ID:
        raise RuntimeError(f"duplicate format name {_name!r} in formats.yaml")
    HUMAN_READABLE_FORMATS[_fid] = _label
    NAME_TO_ID[_name] = _fid
    _LABEL_LOWER_TO_ID[_label.lower()] = _fid


def format_from_text(text: str) -> int:
    """Return the format id for a human-readable label, or 0 if unknown."""
    return _LABEL_LOWER_TO_ID.get(text.strip().lower(), 0)


def format_label(number: int) -> str:
    """Return the human-readable label for a format number, or '' if unknown."""
    if 0 <= number < len(HUMAN_READABLE_FORMATS):
        return HUMAN_READABLE_FORMATS[number]
    return ""
