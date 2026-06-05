"""Phase-0 remote aggregation: mirror-layout + rsync-transport helpers,
plus the box-provenance collision behaviour through the watcher → ledger.

Generic watcher multi-root behaviour lives in test_watcher.py.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest

from tailward.config import Config
from tailward.daemon.state import StateStore
from tailward.daemon.watcher import TranscriptWatcher
from tailward.paths import project_hash
from tailward.remote import (
    box_projects_dir,
    box_projects_roots,
    build_rsync_cmd,
    remote_mirror_root,
)
from tailward.storage.ledger import Ledger


def _write_jsonl(path: Path, cwd: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(
        {
            "type": "user",
            "sessionId": path.stem,
            "cwd": cwd,
            "message": {"role": "user", "content": "hi"},
        }
    )
    path.write_text(line + "\n", encoding="utf-8")


# ---------------- box provenance / collision ----------------


@pytest.mark.asyncio
async def test_same_path_on_two_sources_stays_distinct(tmp_path: Path) -> None:
    """The headline collision case: a local project and a remote box both
    working in ``/opt/camcontrol`` must NOT merge — they get distinct
    project hashes and surface as two rows, the remote one labelled."""
    local_root = tmp_path / "claude" / "projects"
    box_root = tmp_path / "mirror" / "ubuclau1" / "projects"
    # Same cwd on both sources — would collide without box provenance.
    _write_jsonl(local_root / "-opt-camcontrol" / "local.jsonl", "/opt/camcontrol")
    _write_jsonl(box_root / "-opt-camcontrol" / "remote.jsonl", "/opt/camcontrol")

    local_hash = project_hash("/opt/camcontrol")
    box_hash = project_hash("/opt/camcontrol", box="ubuclau1")
    assert local_hash != box_hash  # sanity

    ledger = Ledger()
    await ledger.connect()
    await ledger.mark_project_seeded(local_hash)
    await ledger.mark_project_seeded(box_hash)
    try:
        watcher = TranscriptWatcher(
            StateStore(),
            ledger,
            roots=[local_root, box_root],
            root_boxes={box_root: "ubuclau1"},
        )
        await watcher.start()
        await asyncio.sleep(0.5)
        await watcher.stop()

        summary = {r["project_hash"]: r for r in await ledger.projects_summary()}
        assert local_hash in summary and box_hash in summary, (
            "local and remote same-path projects should be two distinct rows"
        )
        # Both report the same path; only the remote row carries the box.
        assert summary[local_hash]["project_path"] == "/opt/camcontrol"
        assert summary[box_hash]["project_path"] == "/opt/camcontrol"
        assert (summary[local_hash]["box"] or "") == ""
        assert summary[box_hash]["box"] == "ubuclau1"
    finally:
        await ledger.close()


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
