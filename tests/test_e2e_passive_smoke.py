"""End-to-end smoke for the supported **passive** audit layer.

Boots the daemon (default ``warden_mode = "passive"``), drives it with a
synthetic JSONL session, and asserts the v1.1 audit layer produces signal
from watcher ingestion all the way through to the ledger and LiveBus:

1. A forbidden-bash tool_use (``git push --force``) writes a row to
   ``constraint_violations`` with a ``forbidden-bash`` rule id.
2. An edit to a baseline-immutable path (``.github/workflows/ci.yml``)
   writes a second row with an ``immutable`` rule id.
3. Every detection mirrors to ``live_events`` via LiveBus.
4. The scope worker records a ``scope_snapshots`` row on the assistant
   turn that follows the tool calls.
5. The UserPromptSubmit hook returns ``{}`` even when an ``intent.md``
   with content exists — passive mode never injects into the prompt.

Runs fully offline; Qwen is nulled out. Complements ``test_e2e_smoke.py``,
which guards the opt-in active-mode continuity loop.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from modmcp.daemon.app import create_app
from modmcp.paths import intent_path, project_hash
from modmcp.schema.intent import empty_intent, save_intent

SESSION_ID = "session-passive-001"


def _append_jsonl(path: Path, events: list[dict]) -> None:
    with open(path, "a", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")
        f.flush()
        os.fsync(f.fileno())


def _wait_until(predicate, *, timeout: float = 10.0, interval: float = 0.1) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if predicate():
                return True
        except Exception:
            pass
        time.sleep(interval)
    return False


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def fake_project(tmp_path: Path) -> Path:
    proj = tmp_path / "fake-project"
    proj.mkdir()
    return proj


@pytest.fixture
def seeded_intent(fake_project: Path) -> Path:
    """Seed an intent so the passive hook has *something* to ignore.

    If the file is absent the hook short-circuits on path lookup rather than
    on the mode check; the assertion that passive suppresses injection is
    only meaningful when an intent would otherwise be delivered.
    """
    intent = empty_intent(str(fake_project), "fake-project")
    intent.set("Active Goal", "maintain the parser module")
    target = intent_path(str(fake_project))
    save_intent(intent, target)
    return target


@pytest.fixture
def jsonl_path() -> Path:
    claude_root = Path(os.environ["CLAUDE_PROJECTS_ROOT"])
    sess_dir = claude_root / "fake-project-sanitized"
    sess_dir.mkdir(parents=True)
    f = sess_dir / f"{SESSION_ID}.jsonl"
    f.touch()
    return f


def _user_event(cwd: str, text: str) -> dict:
    return {
        "type": "user",
        "sessionId": SESSION_ID,
        "cwd": cwd,
        "message": {"role": "user", "content": text},
    }


def _assistant_event(text: str) -> dict:
    return {
        "type": "assistant",
        "sessionId": SESSION_ID,
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
        },
    }


def _assistant_with_tool_use(
    cwd: str, name: str, tool_input: dict, tc_id: str
) -> dict:
    """Emit a tool call in Claude Code's actual transcript shape.

    Claude Code does not write bare ``{"type": "tool_use", ...}`` lines;
    every tool call lands as an ``assistant`` message whose content list
    contains a ``tool_use`` block. The watcher and workers must dispatch
    on that shape, not just on a top-level ``tool_use`` type.
    """
    return {
        "type": "assistant",
        "sessionId": SESSION_ID,
        "cwd": cwd,
        "message": {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": tc_id, "name": name, "input": tool_input},
            ],
        },
    }


def test_passive_pipeline_smoke(
    fake_project: Path, seeded_intent: Path, jsonl_path: Path
) -> None:
    cwd = str(fake_project)
    ph = project_hash(cwd)

    with TestClient(create_app()) as client:
        daemon = client.app.state.daemon
        daemon.qwen = None  # force offline / heuristic paths

        _append_jsonl(
            jsonl_path,
            [
                _user_event(cwd, "please tidy up"),
                _assistant_with_tool_use(
                    cwd,
                    "Bash",
                    {"command": "git push --force origin main"},
                    tc_id="tc-bash-1",
                ),
                _assistant_with_tool_use(
                    cwd,
                    "Edit",
                    {
                        "file_path": ".github/workflows/ci.yml",
                        "old_string": "foo",
                        "new_string": "bar",
                    },
                    tc_id="tc-edit-1",
                ),
                _assistant_event("Done; I tidied things up."),
            ],
        )

        # ---- Baseline forbidden-bash + immutable-path violations land.
        def _violations() -> list[dict]:
            return _run(daemon.ledger.recent_violations(ph))

        assert _wait_until(lambda: len(_violations()) >= 2), (
            f"expected 2 baseline violations, got: {_violations()}"
        )
        rule_ids = {r["rule_id"] for r in _violations()}
        assert any(r.startswith("forbidden-bash:") for r in rule_ids), rule_ids
        assert any(r.startswith("immutable:") for r in rule_ids), rule_ids

        # ---- Scope snapshots emit on assistant turns; at least one must
        # reflect the Edit's path landing in files_touched. Claude Code
        # splits a logical turn into multiple assistant events (one per
        # content block), so we assert the eventual state rather than
        # the count of the first snapshot.
        def _snapshots() -> list[dict]:
            return _run(daemon.ledger.scope_snapshots_for_session(SESSION_ID))

        assert _wait_until(
            lambda: any(s["files_touched_count"] >= 1 for s in _snapshots())
        ), f"no snapshot recorded files_touched >= 1; got: {_snapshots()}"

        # ---- LiveBus mirrored both detections.
        def _live_types() -> set[str]:
            rows = _run(daemon.ledger.live_events_for_session(SESSION_ID))
            return {r["event_type"] for r in rows}

        assert _wait_until(
            lambda: {"constraint_violation", "scope_snapshot"} <= _live_types()
        ), f"LiveBus missing expected types; saw: {_live_types()}"

        # ---- Hook in passive mode never injects, even with intent present.
        r = client.post(
            "/hook/userpromptsubmit",
            json={"session_id": SESSION_ID, "cwd": cwd, "prompt": "next"},
        )
        assert r.status_code == 200
        assert r.json() == {}, (
            "passive mode must not return a preamble or corrections; "
            f"got: {r.json()}"
        )
