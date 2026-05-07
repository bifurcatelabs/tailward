from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from tailward.daemon.state import StateStore
from tailward.daemon.watcher import TranscriptWatcher
from tailward.paths import project_hash
from tailward.storage.ledger import Ledger


async def _seed_cwd(ledger: Ledger, cwd: str) -> None:
    """Mark a project (identified by its cwd) as seeded so the
    watcher's prime pass parses its existing JSONL content. The
    daemon's prime-pass behavior is opt-in per `seeded_projects`
    table; tests that drop fixture content before `watcher.start()`
    must seed first or prime will skip the content."""
    await ledger.mark_project_seeded(project_hash(cwd))


@pytest.mark.asyncio
async def test_watcher_picks_up_existing_jsonl(tmp_path: Path) -> None:
    import os

    claude_root = Path(os.environ["CLAUDE_PROJECTS_ROOT"])
    project_dir = claude_root / "tmp-example"
    project_dir.mkdir(parents=True)
    session_file = project_dir / "demo-001.jsonl"

    fixture = (
        Path(__file__).parent / "fixtures" / "transcripts" / "sample_build.jsonl"
    ).read_text(encoding="utf-8")
    session_file.write_text(fixture, encoding="utf-8")

    ledger = Ledger()
    await ledger.connect()
    # Sample fixture has cwd="/tmp/example".
    await _seed_cwd(ledger, "/tmp/example")
    try:
        state = StateStore()
        events: list = []

        async def capture(ev, fs, is_backlog=False):
            events.append(ev)

        watcher = TranscriptWatcher(state, ledger, on_event=capture, root=claude_root)
        await watcher.start()
        # Give the prime pass a moment.
        await asyncio.sleep(0.5)
        await watcher.stop()

        assert events, "watcher should have emitted events"
        kinds = {e.kind for e in events}
        assert "user_message" in kinds
        assert "assistant_message" in kinds
        assert state.all(), "watcher should have recorded a session"
        offset = await ledger.get_offset("demo-001")
        assert offset > 0
    finally:
        await ledger.close()


def _write_minimal_jsonl(path: Path) -> None:
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(
        {
            "type": "user",
            "sessionId": path.stem,
            "cwd": "/p",
            "message": {"role": "user", "content": "hi"},
        }
    )
    path.write_text(line + "\n", encoding="utf-8")


@pytest.mark.asyncio
async def test_exclude_paths_skips_matching_project(tmp_path: Path) -> None:
    """A project listed in exclude_paths must not be primed or watched."""
    claude_root = tmp_path / "claude"
    _write_minimal_jsonl(claude_root / "C--keep" / "keep-1.jsonl")
    _write_minimal_jsonl(claude_root / "C--skip" / "skip-1.jsonl")

    ledger = Ledger()
    await ledger.connect()
    # _write_minimal_jsonl uses cwd="/p"; seed so prime parses it.
    await _seed_cwd(ledger, "/p")
    try:
        state = StateStore()
        seen: list[str] = []

        async def capture(ev, fs, is_backlog=False):
            if fs.session_id:
                seen.append(fs.session_id)

        watcher = TranscriptWatcher(
            state,
            ledger,
            on_event=capture,
            root=claude_root,
            exclude_paths=["C--skip"],
        )
        await watcher.start()
        await asyncio.sleep(0.5)
        await watcher.stop()

        assert "keep-1" in seen
        assert "skip-1" not in seen
    finally:
        await ledger.close()


@pytest.mark.asyncio
async def test_watch_paths_whitelists_only_listed(tmp_path: Path) -> None:
    """When watch_paths is non-empty, projects not on the list are skipped."""
    claude_root = tmp_path / "claude"
    _write_minimal_jsonl(claude_root / "C--in" / "in-1.jsonl")
    _write_minimal_jsonl(claude_root / "C--out" / "out-1.jsonl")

    ledger = Ledger()
    await ledger.connect()
    # _write_minimal_jsonl uses cwd="/p"; seed so prime parses it.
    await _seed_cwd(ledger, "/p")
    try:
        state = StateStore()
        seen: list[str] = []

        async def capture(ev, fs, is_backlog=False):
            if fs.session_id:
                seen.append(fs.session_id)

        watcher = TranscriptWatcher(
            state,
            ledger,
            on_event=capture,
            root=claude_root,
            watch_paths=["C--in"],
        )
        await watcher.start()
        await asyncio.sleep(0.5)
        await watcher.stop()

        assert "in-1" in seen
        assert "out-1" not in seen
    finally:
        await ledger.close()


@pytest.mark.asyncio
async def test_filestate_rehydrates_project_from_session_state(
    tmp_path: Path,
) -> None:
    """A daemon restart while the user's cwd is in a subdir must NOT
    relocate the session under a phantom project_hash.

    Reproduces the bug where ``FileState`` was seeded only with the
    offset on init; the next post-offset event's ``cwd`` then set
    project_path to whatever happened to be live (e.g. a
    ``C:/warden/frontend`` working directory during an npm run).
    Workers downstream (rubric, scope, etc.) then persisted under
    the wrong hash, invisible to the canonical project's UI.
    """
    import json

    from tailward.paths import project_hash

    claude_root = tmp_path / "claude"
    proj_dir = claude_root / "C--warden"
    proj_dir.mkdir(parents=True)
    sid = "demo-session"
    jsonl = proj_dir / f"{sid}.jsonl"

    # First event's cwd is a subdir — would hash to a different value
    # than the canonical project root.
    line = json.dumps(
        {
            "type": "user",
            "sessionId": sid,
            "cwd": "C:/warden/frontend",
            "message": {"role": "user", "content": "hello from subdir"},
        }
    )
    jsonl.write_text(line + "\n", encoding="utf-8")

    canonical_path = "C:/warden"
    canonical_hash = project_hash(canonical_path)
    subdir_hash = project_hash("C:/warden/frontend")
    assert canonical_hash != subdir_hash  # sanity

    ledger = Ledger()
    await ledger.connect()
    # The watcher's prime _is_seeded check reads the first event's cwd
    # ("C:/warden/frontend") to compute the project_hash. Seed that
    # hash so prime parses; the test then asserts FileState rehydrates
    # the canonical_hash from session_state, not the subdir hash.
    await _seed_cwd(ledger, "C:/warden/frontend")
    try:
        # Pre-seed session_state as it would be after a prior daemon run.
        await ledger.upsert_session(sid, canonical_hash, canonical_path)

        state = StateStore()
        watcher = TranscriptWatcher(state, ledger, root=claude_root)
        await watcher.start()
        await asyncio.sleep(0.5)
        await watcher.stop()

        fs = watcher._files[jsonl]
        assert fs.project_hash == canonical_hash, (
            f"FileState should rehydrate from session_state "
            f"({canonical_hash}), not derive from event cwd "
            f"({subdir_hash})"
        )
        assert fs.project_path == canonical_path
    finally:
        await ledger.close()


@pytest.mark.asyncio
async def test_watch_paths_accepts_desanitized_form(tmp_path: Path) -> None:
    """``C:/warden`` and ``C--warden`` should both work as filter entries.

    Users will type whichever form is natural in their config; the
    matcher checks both the sanitized folder name and its de-sanitized
    path.
    """
    claude_root = tmp_path / "claude"
    _write_minimal_jsonl(claude_root / "C--warden" / "w-1.jsonl")
    _write_minimal_jsonl(claude_root / "C--other" / "o-1.jsonl")

    ledger = Ledger()
    await ledger.connect()
    # _write_minimal_jsonl uses cwd="/p"; seed so prime parses it.
    await _seed_cwd(ledger, "/p")
    try:
        state = StateStore()
        seen: list[str] = []

        async def capture(ev, fs, is_backlog=False):
            if fs.session_id:
                seen.append(fs.session_id)

        watcher = TranscriptWatcher(
            state,
            ledger,
            on_event=capture,
            root=claude_root,
            watch_paths=["C:/warden"],
        )
        await watcher.start()
        await asyncio.sleep(0.5)
        await watcher.stop()

        assert "w-1" in seen
        assert "o-1" not in seen
    finally:
        await ledger.close()


@pytest.mark.asyncio
async def test_non_seeded_project_skipped_on_prime_seed_then_parses(
    tmp_path: Path,
) -> None:
    """End-to-end seed mechanism: a project whose JSONL content exists
    before watcher start is NOT parsed by the prime pass when the
    project is non-seeded. After ``seed_project()`` runs, that same
    content is parsed (with ``is_backlog=True`` so LLM workers skip).

    Regression guard for the v3 launch-UX fix: every daemon start
    should NOT parse arbitrary historical projects without explicit
    user opt-in. The Seed mechanism is the user opt-in surface.
    """
    claude_root = tmp_path / "claude"
    _write_minimal_jsonl(claude_root / "C--example" / "session-x.jsonl")

    ledger = Ledger()
    await ledger.connect()
    # Deliberately do NOT seed up front — exercises the skip path.
    try:
        state = StateStore()
        captured: list[tuple[bool, str | None]] = []

        async def capture(ev, fs, is_backlog=False):
            captured.append((is_backlog, ev.kind))

        watcher = TranscriptWatcher(
            state, ledger, on_event=capture, root=claude_root
        )
        await watcher.start()
        await asyncio.sleep(0.3)

        assert not captured, (
            "non-seeded project should be skipped on prime; "
            f"got {len(captured)} unexpected events"
        )

        # Seed and verify content now flows through.
        target_hash = project_hash("/p")  # _write_minimal_jsonl uses cwd="/p"
        files_seeded = await watcher.seed_project(target_hash)
        await asyncio.sleep(0.2)
        await watcher.stop()

        assert files_seeded == 1, f"expected 1 file seeded, got {files_seeded}"
        assert captured, "seed_project should have parsed the JSONL content"
        # All seeded events must carry is_backlog=True so LLM workers skip them.
        assert all(is_bl for is_bl, _ in captured), (
            "seed_project events should be tagged is_backlog=True"
        )
    finally:
        await ledger.close()


@pytest.mark.asyncio
async def test_prime_pass_tags_events_as_backlog(tmp_path: Path) -> None:
    """Events parsed from JSONL content that existed at watcher start
    must be tagged ``is_backlog=True``. Real-time events arriving after
    start are tagged ``False``. Downstream LLM-call workers use this
    distinction to skip the rapid-fire activity that otherwise fires
    on every daemon startup catching up on transcript history.

    Regression guard: if the prime path stops tagging backlog events,
    every daemon launch would fire LLM analysis on every historical
    turn — exactly the v3 launch-UX problem this distinction exists
    to prevent.
    """
    claude_root = tmp_path / "claude"
    _write_minimal_jsonl(claude_root / "C--example" / "session-a.jsonl")
    _write_minimal_jsonl(claude_root / "C--example" / "session-b.jsonl")

    ledger = Ledger()
    await ledger.connect()
    # _write_minimal_jsonl uses cwd="/p"; seed so prime parses it.
    await _seed_cwd(ledger, "/p")
    try:
        state = StateStore()
        captured: list[bool] = []

        async def capture(ev, fs, is_backlog=False):
            captured.append(is_backlog)

        watcher = TranscriptWatcher(
            state, ledger, on_event=capture, root=claude_root
        )
        await watcher.start()
        await asyncio.sleep(0.5)
        await watcher.stop()

        assert captured, "watcher should have emitted events from prime"
        # Every event from this run came from the prime pass — fixtures
        # were written before watcher start. All must be tagged backlog.
        assert all(captured), (
            f"prime pass should tag all events is_backlog=True; "
            f"got {sum(1 for x in captured if not x)} untagged"
        )
    finally:
        await ledger.close()
