from __future__ import annotations

from pathlib import Path

from tailward.paths import (
    atomic_write_text,
    canonicalize_project_path,
    ensure_layout,
    home_dir,
    project_hash,
    projects_dir,
)


def test_ensure_layout_creates_dirs(_isolated_home: Path) -> None:
    ensure_layout()
    assert home_dir().exists()
    assert projects_dir().exists()


def test_project_hash_is_stable(_isolated_home: Path) -> None:
    h1 = project_hash("/Users/example/code/repo")
    h2 = project_hash("/Users/example/code/repo")
    assert h1 == h2
    assert len(h1) == 12


def test_project_hash_distinguishes_paths(_isolated_home: Path) -> None:
    assert project_hash("/a/b") != project_hash("/a/c")


def test_project_hash_empty_box_unchanged(_isolated_home: Path) -> None:
    """box="" (the default) must produce the byte-identical hash to the
    no-box call, so existing local projects keep their identity and need
    no data migration."""
    assert project_hash("/opt/camcontrol") == project_hash("/opt/camcontrol", box="")


def test_project_hash_box_disambiguates_same_path(_isolated_home: Path) -> None:
    """The collision fix: the same absolute path on two different boxes
    (and on the local machine) yields three distinct identities."""
    local = project_hash("/opt/camcontrol")
    box_a = project_hash("/opt/camcontrol", box="ubuclau1")
    box_b = project_hash("/opt/camcontrol", box="lab-gpu-2")
    assert len({local, box_a, box_b}) == 3


def test_canonicalize_lowercases_drive_on_windows(_isolated_home: Path, monkeypatch) -> None:
    import sys

    if sys.platform != "win32":
        # Only meaningful on Windows; just check it doesn't throw elsewhere.
        canonicalize_project_path("/tmp")
        return
    out = canonicalize_project_path("C:\\Users\\Example")
    assert out.startswith("c:")
    assert "\\" not in out


def test_atomic_write_round_trip(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "file.txt"
    atomic_write_text(target, "hello\nworld\n")
    assert target.read_text(encoding="utf-8") == "hello\nworld\n"
