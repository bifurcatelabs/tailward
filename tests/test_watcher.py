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
