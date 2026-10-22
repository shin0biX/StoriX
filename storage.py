"""Safe filename and on-disk path handling.

User input never becomes a filesystem path. Display names are sanitized for
the database/UI; disk files are UUID-named inside STORAGE_ROOT/users/<id>/.
"""
from __future__ import annotations

import logging
import os
import re
import unicodedata
import uuid
from pathlib import Path

from config import MAX_FILENAME_LENGTH, STORAGE_ROOT

logger = logging.getLogger("storix.storage")

_DISALLOWED = re.compile(r"[^\w .()\[\]-]", re.UNICODE)
_SPACES = re.compile(r"\s+")


def sanitize_filename(original: str | None) -> str:
    """Return a display-safe filename without path components.

    Preserves Unicode letters and the original extension, removes control
    characters and separators, collapses whitespace, and caps the length.
    """
    name = unicodedata.normalize("NFKC", str(original or ""))
    # Treat both POSIX and Windows separators as path components.
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if ch >= " " and ch != "\x7f")
    name = _DISALLOWED.sub("_", name)
    name = _SPACES.sub(" ", name).strip()
    name = name.rstrip(" .")
    if not name or name in {".", ".."}:
        name = "file"

    stem, ext = os.path.splitext(name)
    if len(ext) > 16:
        ext = ext[:16]
    max_stem = max(1, MAX_FILENAME_LENGTH - len(ext))
    if len(stem) > max_stem:
        stem = stem[:max_stem]
    return f"{stem}{ext}"


def user_dir(user_id: int) -> Path:
    directory = STORAGE_ROOT / "users" / str(user_id)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def new_disk_path(user_id: int, filename: str) -> tuple[Path, str]:
    """Return (absolute path, path relative to STORAGE_ROOT)."""
    extension = Path(filename).suffix
    if len(extension) > 16:
        extension = extension[:16]
    path = user_dir(user_id) / f"{uuid.uuid4().hex}{extension}"
    return path, path.relative_to(STORAGE_ROOT).as_posix()


def resolve_stored_path(stored: str) -> Path:
    """Resolve a DB path defensively and ensure it stays inside STORAGE_ROOT."""
    raw = Path(stored)
    candidates = [raw] if raw.is_absolute() else [STORAGE_ROOT / raw, Path.cwd() / raw]
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if _is_inside(resolved, STORAGE_ROOT) and resolved.is_file():
            return resolved
    # Return the intended location so callers can report a clean 404.
    intended = (STORAGE_ROOT / raw).resolve()
    if not _is_inside(intended, STORAGE_ROOT):
        raise ValueError("stored path escapes the storage root")
    return intended


def delete_stored_path(stored: str) -> None:
    try:
        path = resolve_stored_path(stored)
    except ValueError:
        logger.error("refusing to delete path outside storage: %s", stored)
        return
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:  # pragma: no cover - filesystem dependent
        logger.error("could not delete %s: %s", path, exc)


def safe_unlink(path: str | Path) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except OSError as exc:  # pragma: no cover
        logger.error("could not remove temp file %s: %s", path, exc)


def deletion_paths_for_user_dir(user_id: int) -> Path:
    return STORAGE_ROOT / "users" / str(user_id)


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
