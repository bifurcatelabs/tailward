"""Scope worker per-session counters + creep detection."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from modmcp.daemon.app import create_app
from modmcp.paths import project_hash
from modmcp.schema.events import TranscriptEvent


def _edit(idx: int, path: str) -> TranscriptEvent:
    return TranscriptEvent(
        raw={"id": f"t{idx}"}, kind="tool_use", session_id="s-scope",
        timestamp=None, text="",
        tool_name="Edit",
        tool_input={"file_path": path, "old_string": "a", "new_string": "b" * 100},
    )


def _assistant(text: str = "turn done") -> TranscriptEvent:
    return TranscriptEvent(
        raw={}, kind="assistant_message", session_id="s-scope",
        timestamp=None, text=text,
    )


@pytest.mark.asyncio
async def test_scope_worker_records_snapshots(tmp_path: Path) -> None:
    proj = tmp_path / "scope_proj"
    proj.mkdir()

    with TestClient(create_app()) as client:
        daemon = client.app.state.daemon

        class _FS:
            session_id = "s-scope"
            project_path = str(proj)
            project_hash = project_hash(str(proj))

        fs = _FS()
        await daemon.ledger.upsert_session(fs.session_id, fs.project_hash, fs.project_path)
        state = daemon.state.get_or_create(
            fs.session_id, fs.project_path, fs.project_hash, "dummy"
        )
        state.turns_seen = 1

        for i, p in enumerate(["a.py", "b.py", "c.py"]):
            await daemon.scope.enqueue(_edit(i, p), fs)
        await daemon.scope.enqueue(_assistant(), fs)

        # Wait for the snapshot that reflects all three edits — earlier
        # snapshots fire per tool_use so they show counts 1, 2, 3 in
        # sequence. Without this, the poll grabs the first one.
        for _ in range(40):
            rows = await daemon.ledger.scope_snapshots_for_session(fs.session_id)
            if any(r["files_touched_count"] >= 3 for r in rows):
                break
            await asyncio.sleep(0.1)
        assert rows, "no scope snapshot recorded"
        last = rows[-1]
        assert last["files_touched_count"] == 3
        assert last["diff_bytes"] > 0


@pytest.mark.asyncio
async def test_scope_worker_fires_creep(tmp_path: Path, monkeypatch) -> None:
    proj = tmp_path / "scope_creep"
    proj.mkdir()

    # Seed an intent labeled "build" so the build mode profile applies.
    # Default / unlabeled sessions resolve to the permissive profile,
    # which has scope_creep_floor=None — creep never fires there by
    # design.
    from modmcp.paths import intent_path
    from modmcp.schema.intent import empty_intent, save_intent
    intent = empty_intent(str(proj), proj.name)
    intent.front.session_mode = "build"
    save_intent(intent, intent_path(str(proj)))

    # Tighten the build profile's creep floor for this test only;
    # a real session would touch many more files before tripping it.
    from modmcp.daemon import mode_profile as mp
    tight_build = mp.ModeProfile(
        name="build",
        description="test override",
        scope_creep_floor=3,
        scope_creep_factor=1.0,
        rubric_dimensions=mp.ALL_RUBRIC_DIMENSIONS,
        scope_event_label="creep",
    )
    monkeypatch.setitem(mp._REGISTRY, "build", tight_build)

    with TestClient(create_app()) as client:
        daemon = client.app.state.daemon

        ph = project_hash(str(proj))
        # Seed a baseline row pretending a prior session peaked at 2 files.
        await daemon.ledger.record_scope_snapshot(
            "prior-session", ph,
            turn_idx=1, files_touched_count=2, diff_bytes=0,
            tool_kinds_json="{}", is_creep=False, baseline=0,
        )

        class _FS:
            session_id = "s-creep"
            project_path = str(proj)
            project_hash = ph

        fs = _FS()
        await daemon.ledger.upsert_session(fs.session_id, fs.project_hash, fs.project_path)
        state = daemon.state.get_or_create(
            fs.session_id, fs.project_path, fs.project_hash, "dummy"
        )
        state.turns_seen = 1

        for i, p in enumerate(["a.py", "b.py", "c.py", "d.py", "e.py"]):
            await daemon.scope.enqueue(_edit(i, p), fs)
        await daemon.scope.enqueue(_assistant(), fs)

        for _ in range(40):
            rows = await daemon.ledger.scope_snapshots_for_session(fs.session_id)
            creep = [r for r in rows if r.get("is_creep")]
            if creep:
                break
            await asyncio.sleep(0.1)
        assert creep, "scope creep was not flagged"
