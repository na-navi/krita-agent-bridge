"""Safe file operations shared by the Krita shim.

The functions in this module avoid permanent deletion and silent overwrites.
They return structured results so HTTP endpoints can surface actionable errors.
"""

from __future__ import annotations

import ctypes
import os
import platform
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class FileResult:
    """Structured result for shim file operations."""

    ok: bool
    message: str
    path: str = ""
    data: Any = None


_MAX_BASENAME_LENGTH = 120
_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def _result(ok: bool, message: str, path: Path | str = "", data: Any = None) -> FileResult:
    return FileResult(ok=ok, message=message, path=str(path), data=data)


def sanitize_filename(name: str, max_length: int = _MAX_BASENAME_LENGTH) -> str:
    """Return a filesystem-safe filename component."""
    normalized = unicodedata.normalize("NFKC", name).strip()
    cleaned = _UNSAFE_CHARS.sub("_", normalized)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    if not cleaned:
        cleaned = "untitled"
    if cleaned.upper() in _WINDOWS_RESERVED:
        cleaned = f"{cleaned}_file"
    return cleaned[:max_length].rstrip(" .") or "untitled"


def _normalize_extension(extension: str) -> str:
    extension = extension.strip()
    if not extension:
        return ""
    if not extension.startswith("."):
        extension = f".{extension}"
    return _UNSAFE_CHARS.sub("_", extension)


def resolve_unique_path(directory: str | os.PathLike[str], basename: str, extension: str) -> Path:
    """Return the first deterministic non-colliding path in a directory."""
    parent = Path(directory)
    stem = sanitize_filename(basename)
    suffix = _normalize_extension(extension)
    candidate = parent / f"{stem}{suffix}"
    if not candidate.exists():
        return candidate

    counter = 1
    while True:
        candidate = parent / f"{stem}_{counter:03d}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def check_collision(path: str | os.PathLike[str]) -> FileResult:
    """Report whether a path already exists."""
    target = Path(path)
    exists = target.exists()
    return _result(
        ok=not exists,
        message="Path is available" if not exists else "Path already exists",
        path=target,
        data={"exists": exists, "is_file": target.is_file(), "is_dir": target.is_dir()},
    )


def _move_to_recycle_bin(path: Path) -> FileResult:
    if platform.system() != "Windows":
        return _result(False, "System trash is only implemented on Windows in this shim", path)

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", ctypes.c_void_p),
            ("wFunc", ctypes.c_uint),
            ("pFrom", ctypes.c_wchar_p),
            ("pTo", ctypes.c_wchar_p),
            ("fFlags", ctypes.c_ushort),
            ("fAnyOperationsAborted", ctypes.c_bool),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", ctypes.c_wchar_p),
        ]

    operation = SHFILEOPSTRUCTW()
    operation.hwnd = None
    operation.wFunc = 3  # FO_DELETE
    operation.pFrom = f"{path.resolve()}\0\0"
    operation.pTo = None
    operation.fFlags = 0x0040 | 0x0010 | 0x0004 | 0x0400  # allow undo, quiet, no UI
    operation.fAnyOperationsAborted = False
    operation.hNameMappings = None
    operation.lpszProgressTitle = None

    rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation))
    if rc != 0 or operation.fAnyOperationsAborted:
        return _result(False, f"Failed to move path to recycle bin (code {rc})", path)
    return _result(True, "Moved path to recycle bin", path)


def trash_file(path: str | os.PathLike[str]) -> FileResult:
    """Move a file to the system trash without permanently deleting it."""
    target = Path(path)
    if not target.exists():
        return _result(False, "File does not exist", target)
    if not target.is_file():
        return _result(False, "Path is not a file", target)
    return _move_to_recycle_bin(target)


def trash_directory(path: str | os.PathLike[str]) -> FileResult:
    """Move a directory to the system trash without permanently deleting it."""
    target = Path(path)
    if not target.exists():
        return _result(False, "Directory does not exist", target)
    if not target.is_dir():
        return _result(False, "Path is not a directory", target)
    return _move_to_recycle_bin(target)


def safe_rename(source: str | os.PathLike[str], destination: str | os.PathLike[str]) -> FileResult:
    """Rename a path only when the destination does not already exist."""
    src = Path(source)
    dest = Path(destination)
    if not src.exists():
        return _result(False, "Source does not exist", src)
    if dest.exists():
        return _result(False, "Destination already exists", dest)
    if not dest.parent.exists():
        return _result(False, "Destination directory does not exist", dest.parent)
    try:
        src.rename(dest)
    except OSError as exc:
        return _result(False, f"Rename failed: {exc}", dest)
    return _result(True, "Renamed path", dest, data={"source": str(src)})


def _backup_path_for(target: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return resolve_unique_path(target.parent, f"{target.stem}.{timestamp}", f"{target.suffix}.bak")


def safe_save(
    data: bytes | str,
    path: str | os.PathLike[str],
    overwrite: bool = False,
) -> FileResult:
    """Write data via a same-directory temp file, refusing silent overwrites."""
    target = Path(path)
    if not target.parent.exists():
        return _result(False, "Target directory does not exist", target.parent)
    if target.exists() and not overwrite:
        return _result(False, "Target already exists", target)

    temp_path = resolve_unique_path(target.parent, f".{target.name}", ".tmp")
    try:
        if isinstance(data, bytes):
            with temp_path.open("xb") as handle:
                handle.write(data)
        else:
            with temp_path.open("x", encoding="utf-8") as handle:
                handle.write(data)

        backup_path: Path | None = None
        if target.exists():
            backup_path = _backup_path_for(target)
            backup_result = safe_rename(target, backup_path)
            if not backup_result.ok:
                trash_file(temp_path)
                return backup_result

        temp_path.replace(target)
        payload = {"backup_path": str(backup_path) if backup_path else None}
        return _result(True, "Saved file", target, data=payload)
    except OSError as exc:
        if temp_path.exists():
            trash_file(temp_path)
        return _result(False, f"Save failed: {exc}", target)


def file_info(path: str | os.PathLike[str]) -> FileResult:
    """Return basic metadata for a file or directory."""
    target = Path(path)
    exists = target.exists()
    stat = target.stat() if exists else None
    data = {
        "name": target.name,
        "exists": exists,
        "size": stat.st_size if stat else 0,
        "modified": stat.st_mtime if stat else None,
        "is_file": target.is_file() if exists else False,
        "is_dir": target.is_dir() if exists else False,
    }
    return _result(True, "File info collected", target, data=data)


def list_files(directory: str | os.PathLike[str], pattern: str | None = None) -> FileResult:
    """List files in a directory, optionally filtered by a glob pattern."""
    parent = Path(directory)
    if not parent.exists():
        return _result(False, "Directory does not exist", parent)
    if not parent.is_dir():
        return _result(False, "Path is not a directory", parent)

    entries = sorted(parent.glob(pattern or "*"), key=lambda p: p.name.lower())
    files = [
        {
            "name": entry.name,
            "path": str(entry),
            "size": entry.stat().st_size if entry.is_file() else 0,
            "is_file": entry.is_file(),
            "is_dir": entry.is_dir(),
        }
        for entry in entries
    ]
    return _result(True, "Files listed", parent, data={"files": files})
