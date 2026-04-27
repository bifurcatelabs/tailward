from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from modmcp.daemon.state import StateStore
from modmcp.daemon.watcher import TranscriptWatcher
from modmcp.storage.ledger import Ledger


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
    try:
        state = StateStore()
        events: list = []

        async def capture(ev, fs):
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
    try:
        state = StateStore()
        seen: list[str] = []

        async def capture(ev, fs):
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
    try:
        state = StateStore()
        seen: list[str] = []

        async def capture(ev, fs):
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
    try:
        state = StateStore()
        seen: list[str] = []

        async def capture(ev, fs):
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
