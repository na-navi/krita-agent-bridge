"""Tests for shim safe file operations (Issue #30)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shim import safe_files
from shim.safe_files import (
    FileResult,
    check_collision,
    file_info,
    list_files,
    resolve_unique_path,
    safe_rename,
    safe_save,
    sanitize_filename,
    trash_directory,
    trash_file,
)


def test_sanitize_filename_strips_unsafe_characters() -> None:
    assert sanitize_filename('bad<>:"/\\|?*\x00 name.png') == "bad_ name.png"
    assert sanitize_filename("CON") == "CON_file"
    assert sanitize_filename("   ") == "untitled"


def test_resolve_unique_path_uses_deterministic_counter(tmp_path: Path) -> None:
    (tmp_path / "art.png").write_text("a", encoding="utf-8")
    (tmp_path / "art_001.png").write_text("b", encoding="utf-8")
    assert resolve_unique_path(tmp_path, "art", "png") == tmp_path / "art_002.png"


def test_check_collision_reports_existing_path(tmp_path: Path) -> None:
    target = tmp_path / "exists.txt"
    target.write_text("x", encoding="utf-8")
    result = check_collision(target)
    assert not result.ok
    assert result.data["exists"] is True


def test_safe_rename_refuses_destination_collision(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    destination = tmp_path / "destination.txt"
    source.write_text("source", encoding="utf-8")
    destination.write_text("destination", encoding="utf-8")
    result = safe_rename(source, destination)
    assert not result.ok
    assert source.exists()
    assert destination.read_text(encoding="utf-8") == "destination"


def test_safe_rename_success(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    destination = tmp_path / "destination.txt"
    source.write_text("source", encoding="utf-8")
    result = safe_rename(source, destination)
    assert result.ok
    assert not source.exists()
    assert destination.read_text(encoding="utf-8") == "source"


def test_safe_save_refuses_existing_target(tmp_path: Path) -> None:
    target = tmp_path / "out.txt"
    target.write_text("old", encoding="utf-8")
    result = safe_save("new", target)
    assert not result.ok
    assert target.read_text(encoding="utf-8") == "old"


def test_safe_save_writes_new_file(tmp_path: Path) -> None:
    target = tmp_path / "out.txt"
    result = safe_save("content", target)
    assert result.ok
    assert target.read_text(encoding="utf-8") == "content"
    assert not list(tmp_path.glob("*.tmp"))


def test_safe_save_overwrite_creates_backup(tmp_path: Path) -> None:
    target = tmp_path / "out.txt"
    target.write_text("old", encoding="utf-8")
    result = safe_save("new", target, overwrite=True)
    assert result.ok
    assert target.read_text(encoding="utf-8") == "new"
    backups = list(tmp_path.glob("*.bak"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "old"


def test_trash_file_uses_trash_boundary(monkeypatch, tmp_path: Path) -> None:
    fake_trash = tmp_path / "trash"
    fake_trash.mkdir()
    target = tmp_path / "doomed.txt"
    target.write_text("x", encoding="utf-8")

    def fake_move(path: Path) -> FileResult:
        path.rename(fake_trash / path.name)
        return FileResult(True, "moved", str(path))

    monkeypatch.setattr(safe_files, "_move_to_recycle_bin", fake_move)
    result = trash_file(target)
    assert result.ok
    assert not target.exists()
    assert (fake_trash / "doomed.txt").exists()


def test_trash_directory_uses_trash_boundary(monkeypatch, tmp_path: Path) -> None:
    fake_trash = tmp_path / "trash"
    fake_trash.mkdir()
    target = tmp_path / "folder"
    target.mkdir()

    def fake_move(path: Path) -> FileResult:
        path.rename(fake_trash / path.name)
        return FileResult(True, "moved", str(path))

    monkeypatch.setattr(safe_files, "_move_to_recycle_bin", fake_move)
    result = trash_directory(target)
    assert result.ok
    assert not target.exists()
    assert (fake_trash / "folder").is_dir()


def test_file_info_and_list_files(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("abc", encoding="utf-8")
    info = file_info(target)
    assert info.ok
    assert info.data["name"] == "a.txt"
    assert info.data["size"] == 3

    listing = list_files(tmp_path, "*.txt")
    assert listing.ok
    assert [entry["name"] for entry in listing.data["files"]] == ["a.txt"]
