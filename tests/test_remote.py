"""Phase-0 remote aggregation: mirror-layout + rsync-transport helpers,
plus the box-provenance collision behaviour through the watcher → ledger.

Generic watcher multi-root behaviour lives in test_watcher.py.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from tailward.config import Config
from tailward.daemon.state import StateStore
from tailward.daemon.watcher import TranscriptWatcher
from tailward.paths import project_hash
from tailward.remote import (
    RemoteBox,
    box_projects_dir,
    box_projects_roots,
    build_rsync_cmd,
    discover_remote_projects,
    get_followed_box,
    load_follow_list,
    remote_mirror_root,
    remove_followed_box,
    save_follow_list,
    upsert_followed_box,
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


@pytest.mark.asyncio
async def test_shared_session_id_across_boxes_is_guarded(tmp_path: Path) -> None:
    """Two boxes carrying the same session_id must not clobber each other.
    The first box seen owns the session; the second is skipped (its offset
    write never happens), so the owning box's tracking stays intact.
    See KNOWN_LIMITATIONS.md."""
    box1 = tmp_path / "mirror" / "box1" / "projects"
    box2 = tmp_path / "mirror" / "box2" / "projects"
    # Same session_id ("dup") AND same cwd on both boxes — the collision.
    _write_jsonl(box1 / "-opt-cc" / "dup.jsonl", "/opt/cc")
    _write_jsonl(box2 / "-opt-cc" / "dup.jsonl", "/opt/cc")

    ledger = Ledger()
    await ledger.connect()
    await ledger.mark_project_seeded(project_hash("/opt/cc", box="box1"))
    await ledger.mark_project_seeded(project_hash("/opt/cc", box="box2"))
    try:
        watcher = TranscriptWatcher(
            StateStore(), ledger,
            roots=[box1, box2], root_boxes={box1: "box1", box2: "box2"},
        )
        await watcher.start()
        await asyncio.sleep(0.5)
        await watcher.stop()

        rows = [r for r in await ledger.all_session_state() if r["session_id"] == "dup"]
        assert len(rows) == 1  # PK guarantees one; assert it's box1's, not clobbered
        assert rows[0]["box"] == "box1"
        # The offset row points at box1's file — box2's skipped copy never
        # overwrote it.
        async with ledger.conn.execute(
            "SELECT jsonl_path FROM processed_offset WHERE session_id='dup'"
        ) as cur:
            offset_row = await cur.fetchone()
        assert offset_row is not None
        assert "box1" in offset_row["jsonl_path"]
        assert "box2" not in offset_row["jsonl_path"]
    finally:
        await ledger.close()


# ---------------- followed-box list (persistent) ----------------


def test_follow_list_empty_when_no_file() -> None:
    assert load_follow_list() == []


def test_follow_list_roundtrip() -> None:
    boxes = [
        RemoteBox(name="ubuclau1", host="172.16.80.207", user="twtest"),
        RemoteBox(
            name="labgpu", host="gpu.lan", user="gw", enabled=False, port=2222,
            remote_path="/srv/.claude/projects/", key="/k/pull", interval_seconds=30,
        ),
    ]
    save_follow_list(boxes)
    loaded = load_follow_list()
    assert loaded == boxes  # dataclass equality, field-for-field


def test_upsert_replaces_by_name_and_sorts() -> None:
    upsert_followed_box(RemoteBox(name="b", host="h1", user="u"))
    upsert_followed_box(RemoteBox(name="a", host="h2", user="u"))
    upsert_followed_box(RemoteBox(name="b", host="h-new", user="u"))  # replace
    boxes = load_follow_list()
    assert [b.name for b in boxes] == ["a", "b"]  # sorted, no dup
    assert get_followed_box("b").host == "h-new"


def test_remove_followed_box() -> None:
    upsert_followed_box(RemoteBox(name="x", host="h", user="u"))
    assert remove_followed_box("x") is True
    assert get_followed_box("x") is None
    assert remove_followed_box("x") is False  # already gone


# ---------------- auto-pull worker ----------------


class _FakeWatcher:
    def __init__(self) -> None:
        self.roots: set[Path] = set()

    def is_watching(self, p: Path) -> bool:
        return Path(p) in self.roots

    def add_root(self, p: Path, box: str = "") -> None:
        self.roots.add(Path(p))


class _FakeDaemon:
    def __init__(self, watcher: _FakeWatcher) -> None:
        self.watcher = watcher


@pytest.mark.asyncio
async def test_pull_worker_pulls_due_box_and_registers_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The worker pulls a due box (no real rsync) and registers its mirror
    root with the watcher so it ingests without a restart. A second tick
    inside the interval does not re-pull."""
    from tailward.daemon import remote_pull_worker as rpw

    mirror = tmp_path / "mirror"
    cfg = replace(Config.default(), remote_mirror_root=str(mirror))
    monkeypatch.setattr(rpw, "get_config", lambda: cfg)

    calls: list[str] = []

    def fake_pull(cfg_, box):
        calls.append(box.name)
        d = box_projects_dir(cfg_, box.name)  # simulate rsync creating the mirror
        d.mkdir(parents=True, exist_ok=True)
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(rpw, "pull_followed", fake_pull)

    save_follow_list([RemoteBox(name="box1", host="h", user="u", interval_seconds=600)])
    watcher = _FakeWatcher()
    worker = rpw.RemotePullWorker(_FakeDaemon(watcher))

    await worker._tick()
    assert calls == ["box1"]  # first sight = due
    assert watcher.is_watching(mirror / "box1" / "projects")  # root registered

    await worker._tick()  # within interval → no re-pull
    assert calls == ["box1"]


@pytest.mark.asyncio
async def test_pull_worker_skips_disabled_box(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tailward.daemon import remote_pull_worker as rpw

    cfg = replace(Config.default(), remote_mirror_root=str(tmp_path / "mirror"))
    monkeypatch.setattr(rpw, "get_config", lambda: cfg)
    calls: list[str] = []
    monkeypatch.setattr(rpw, "pull_followed", lambda c, b: calls.append(b.name))

    save_follow_list([RemoteBox(name="off", host="h", user="u", enabled=False)])
    worker = rpw.RemotePullWorker(_FakeDaemon(_FakeWatcher()))
    await worker._tick()
    assert calls == []  # disabled box not pulled


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


# ---------------- remote project discovery ----------------


def test_discover_remote_projects_surfaces_box_aware_entries(tmp_path: Path) -> None:
    mirror = tmp_path / "mirror"
    _write_jsonl(
        mirror / "ubuclau1" / "projects" / "-opt-camcontrol" / "s1.jsonl",
        "/opt/camcontrol",
    )
    _write_jsonl(
        mirror / "lab-gpu-2" / "projects" / "-opt-camcontrol" / "s2.jsonl",
        "/opt/camcontrol",
    )
    cfg = replace(Config.default(), remote_mirror_root=str(mirror))

    found = {(d["box"], d["project_hash"]): d for d in discover_remote_projects(cfg)}
    # Same path on two boxes → two distinct entries, each labelled + box-aware.
    assert ("ubuclau1", project_hash("/opt/camcontrol", box="ubuclau1")) in found
    assert ("lab-gpu-2", project_hash("/opt/camcontrol", box="lab-gpu-2")) in found
    assert all(d["project_path"] == "/opt/camcontrol" for d in found.values())


def test_discover_remote_projects_empty_when_unconfigured() -> None:
    assert discover_remote_projects(Config.default()) == []


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
