"""Phase-0 remote aggregation: mirror-layout + rsync-transport helpers.

Watcher multi-root behaviour lives in test_watcher.py alongside the
watcher refactor it exercises.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from tailward.config import Config
from tailward.remote import (
    box_projects_dir,
    box_projects_roots,
    build_rsync_cmd,
    remote_mirror_root,
)


# ---------------- mirror layout helpers ----------------


def test_remote_mirror_root_unset_returns_none() -> None:
    assert remote_mirror_root(Config.default()) is None


def test_box_projects_roots_enumerates_pulled_boxes(tmp_path: Path) -> None:
    mirror = tmp_path / "mirror"
    (mirror / "boxA" / "projects" / "C--x").mkdir(parents=True)
    (mirror / "boxB" / "projects" / "C--y").mkdir(parents=True)
    # A box dir without a projects/ subdir is ignored; a stray file too.
    (mirror / "halfpulled").mkdir(parents=True)
    (mirror / "stray.txt").write_text("x", encoding="utf-8")

    cfg = replace(Config.default(), remote_mirror_root=str(mirror))
    roots = box_projects_roots(cfg)

    assert roots == [
        mirror / "boxA" / "projects",
        mirror / "boxB" / "projects",
    ]
    assert [p.parent.name for p in roots] == ["boxA", "boxB"]


def test_box_projects_dir_requires_mirror_root() -> None:
    with pytest.raises(ValueError):
        box_projects_dir(Config.default(), "ubuclau1")


def test_box_projects_dir_path_shape(tmp_path: Path) -> None:
    cfg = replace(Config.default(), remote_mirror_root=str(tmp_path / "m"))
    assert box_projects_dir(cfg, "ubuclau1") == tmp_path / "m" / "ubuclau1" / "projects"


# ---------------- rsync command builder ----------------


def test_build_rsync_cmd_basic_shape(tmp_path: Path) -> None:
    dest = tmp_path / "m" / "box" / "projects"
    cmd = build_rsync_cmd(host="h.example", user="gw", dest=dest)

    assert cmd[0] == "rsync"
    assert "--append-verify" in cmd
    assert "gw@h.example:.claude/projects/" in cmd
    # dest is passed with a trailing slash so rsync writes into it.
    assert cmd[-1] == str(dest) + "/"
    # SSH transport with first-connect host-key pinning.
    e_idx = cmd.index("-e")
    ssh = cmd[e_idx + 1]
    assert ssh.startswith("ssh")
    assert "StrictHostKeyChecking=accept-new" in ssh


def test_build_rsync_cmd_with_key_and_port(tmp_path: Path) -> None:
    dest = tmp_path / "projects"
    key = tmp_path / "tailward-pull"
    cmd = build_rsync_cmd(host="h", user="u", dest=dest, key=key, port=2222)
    ssh = cmd[cmd.index("-e") + 1]
    assert f"-i {key}" in ssh
    assert "IdentitiesOnly=yes" in ssh
    assert "-p 2222" in ssh


def test_build_rsync_cmd_custom_remote_path(tmp_path: Path) -> None:
    cmd = build_rsync_cmd(
        host="h", user="u", dest=tmp_path, remote_path="/abs/claude/projects/"
    )
    assert "u@h:/abs/claude/projects/" in cmd
