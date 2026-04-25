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


def _multiblock_assistant(
    cwd: str,
    blocks: list[dict],
    *,
    message_id: str,
    model: str = "claude-opus-4-7",
    stop_reason: str = "end_turn",
    usage: dict | None = None,
) -> dict:
    """Emit one JSONL line representing one block of a logical turn.

    Real Claude Code splits a single response into multiple JSONL events
    (thinking, text, tool_use, ...) — each is a separate line, but they
    all share the same ``message.id`` (and duplicate the same ``usage``
    and ``stop_reason``). The watcher coalesces on ``message_id``.
    """
    return {
        "type": "assistant",
        "sessionId": SESSION_ID,
        "cwd": cwd,
        "message": {
            "id": message_id,
            "role": "assistant",
            "model": model,
            "content": blocks,
            "stop_reason": stop_reason,
            "usage": usage or {
                "input_tokens": 6,
                "output_tokens": 143,
                "cache_read_input_tokens": 19065,
                "cache_creation_input_tokens": 10161,
            },
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


COALESCE_SESSION_ID = "session-coalesce-001"


@pytest.fixture
def coalesce_jsonl(tmp_path: Path) -> Path:
    claude_root = Path(os.environ["CLAUDE_PROJECTS_ROOT"])
    sess_dir = claude_root / "coalesce-project-sanitized"
    sess_dir.mkdir(parents=True)
    f = sess_dir / f"{COALESCE_SESSION_ID}.jsonl"
    f.touch()
    return f


@pytest.fixture
def coalesce_project(tmp_path: Path) -> Path:
    proj = tmp_path / "coalesce-project"
    proj.mkdir()
    return proj


def test_logical_turn_coalescing_and_usage_capture(
    coalesce_project: Path, coalesce_jsonl: Path
) -> None:
    """One Claude response = one logical turn, regardless of block count.

    Drives a four-event JSONL: three blocks (thinking, text, tool_use)
    sharing message.id ``msg_A``, then one block (text) under message.id
    ``msg_B``. Asserts:

    1. ``state.turns_seen`` advances to 2, not 4.
    2. Cumulative token totals reflect each msg_id once, not per-block.
    3. The LiveBus emits two ``turn`` events, not four.
    4. Each turn LiveBus payload carries ``model`` and ``usage``.
    """
    cwd = str(coalesce_project)
    # A's three blocks all share msg_A. B is a single text block.
    msg_a_usage = {
        "input_tokens": 10,
        "output_tokens": 200,
        "cache_read_input_tokens": 5000,
        "cache_creation_input_tokens": 100,
    }
    msg_b_usage = {
        "input_tokens": 5,
        "output_tokens": 50,
        "cache_read_input_tokens": 5200,
        "cache_creation_input_tokens": 0,
    }

    def _override_session_id(ev: dict) -> dict:
        ev["sessionId"] = COALESCE_SESSION_ID
        return ev

    events = [
        _override_session_id(_user_event(cwd, "go")),
        _override_session_id(
            _multiblock_assistant(
                cwd,
                [{"type": "thinking", "thinking": "let me think"}],
                message_id="msg_A",
                stop_reason="tool_use",
                usage=msg_a_usage,
            )
        ),
        _override_session_id(
            _multiblock_assistant(
                cwd,
                [{"type": "text", "text": "Working on it now."}],
                message_id="msg_A",
                stop_reason="tool_use",
                usage=msg_a_usage,
            )
        ),
        _override_session_id(
            _multiblock_assistant(
                cwd,
                [{
                    "type": "tool_use",
                    "id": "tc-A1",
                    "name": "Read",
                    "input": {"file_path": "README.md"},
                }],
                message_id="msg_A",
                stop_reason="tool_use",
                usage=msg_a_usage,
            )
        ),
        _override_session_id(
            _multiblock_assistant(
                cwd,
                [{"type": "text", "text": "Done; that file is large."}],
                message_id="msg_B",
                stop_reason="end_turn",
                usage=msg_b_usage,
            )
        ),
    ]

    with TestClient(create_app()) as client:
        daemon = client.app.state.daemon
        daemon.qwen = None
        _append_jsonl(coalesce_jsonl, events)

        # Wait for the watcher to drain the jsonl.
        assert _wait_until(
            lambda: (daemon.state.get(COALESCE_SESSION_ID) is not None)
            and daemon.state.get(COALESCE_SESSION_ID).turns_seen >= 2
        ), (
            "watcher did not advance turns_seen to 2; got: "
            f"{getattr(daemon.state.get(COALESCE_SESSION_ID), 'turns_seen', None)}"
        )

        st = daemon.state.get(COALESCE_SESSION_ID)
        assert st.turns_seen == 2, (
            f"expected exactly 2 logical turns from 4 assistant events, "
            f"got {st.turns_seen}"
        )
        # Cumulative tokens must count each message_id once, not per block.
        assert st.total_input_tokens == msg_a_usage["input_tokens"] + msg_b_usage["input_tokens"], (
            f"input tokens overcounted: {st.total_input_tokens}"
        )
        assert st.total_output_tokens == msg_a_usage["output_tokens"] + msg_b_usage["output_tokens"]
        assert st.total_cache_read_tokens == (
            msg_a_usage["cache_read_input_tokens"] + msg_b_usage["cache_read_input_tokens"]
        )
        assert st.last_model == "claude-opus-4-7"

        # LiveBus turn events: exactly two (one per logical turn), each
        # carrying model + usage.
        def _turn_events() -> list[dict]:
            rows = _run(daemon.ledger.live_events_for_session(COALESCE_SESSION_ID))
            import json as _json
            return [
                _json.loads(r["payload"]) for r in rows
                if r["event_type"] == "turn"
            ]

        assert _wait_until(lambda: len(_turn_events()) >= 2), (
            f"expected 2 turn events, got: {_turn_events()}"
        )
        turn_payloads = _turn_events()
        assert len(turn_payloads) == 2, (
            f"too many turn events (mid-turn blocks leaked); got {len(turn_payloads)}"
        )
        for p in turn_payloads:
            assert p.get("model") == "claude-opus-4-7", p
            assert "usage" in p and p["usage"]["output_tokens"] > 0, p
            assert "totals" in p, p
        # The deferred-emit path must surface the actual prose in the
        # preview — not an empty string from the leading thinking block
        # and not the synthetic ``[tool_use:...]`` marker.
        previews = [p.get("text_preview") for p in turn_payloads]
        assert "Working on it now." in previews, (
            f"text-bearing turn missing prose in preview: {previews}"
        )
        assert "Done; that file is large." in previews, (
            f"second turn missing prose in preview: {previews}"
        )


RESTART_SESSION_ID = "session-restart-001"


@pytest.fixture
def restart_jsonl(tmp_path: Path) -> Path:
    claude_root = Path(os.environ["CLAUDE_PROJECTS_ROOT"])
    sess_dir = claude_root / "restart-project-sanitized"
    sess_dir.mkdir(parents=True)
    f = sess_dir / f"{RESTART_SESSION_ID}.jsonl"
    f.touch()
    return f


@pytest.fixture
def restart_project(tmp_path: Path) -> Path:
    proj = tmp_path / "restart-project"
    proj.mkdir()
    return proj


def test_session_state_survives_daemon_restart(
    restart_project: Path, restart_jsonl: Path
) -> None:
    """Cumulative counters reload from session_state across a daemon restart.

    Drives a session through one logical turn, tears down the daemon,
    spins up a fresh daemon against the same ``MODMCP_HOME``, and asserts
    the watcher hydrates the in-memory ``SessionState`` from the
    persisted columns instead of starting from zero. Without this, every
    daemon restart visibly "resets" the live UI's turn count and token
    totals, even though the session is still ongoing.
    """
    cwd = str(restart_project)
    usage = {
        "input_tokens": 7,
        "output_tokens": 250,
        "cache_read_input_tokens": 30000,
        "cache_creation_input_tokens": 500,
    }

    def _override(ev: dict) -> dict:
        ev["sessionId"] = RESTART_SESSION_ID
        return ev

    events = [
        _override(_user_event(cwd, "go")),
        _override(
            _multiblock_assistant(
                cwd,
                [{"type": "text", "text": "Working."}],
                message_id="msg_R1",
                stop_reason="end_turn",
                usage=usage,
            )
        ),
    ]

    # First boot: drive the session, let progress persist.
    with TestClient(create_app()) as client:
        daemon = client.app.state.daemon
        daemon.qwen = None
        _append_jsonl(restart_jsonl, events)

        assert _wait_until(
            lambda: (daemon.state.get(RESTART_SESSION_ID) is not None)
            and daemon.state.get(RESTART_SESSION_ID).turns_seen == 1
        ), "first-boot watcher did not record a logical turn"

        # Confirm the persistence row landed before we tear down.
        def _persisted() -> dict | None:
            rows = _run(daemon.ledger.all_session_state())
            return next(
                (r for r in rows if r["session_id"] == RESTART_SESSION_ID),
                None,
            )

        assert _wait_until(
            lambda: (
                _persisted() is not None
                and (_persisted() or {}).get("turns_seen") == 1
            )
        ), f"session_state row not persisted: {_persisted()}"

        persisted_row = _persisted()
        assert persisted_row["last_message_id"] == "msg_R1"
        assert persisted_row["total_output_tokens"] == usage["output_tokens"]
        assert persisted_row["total_cache_read_tokens"] == usage["cache_read_input_tokens"]
        assert persisted_row["last_model"] == "claude-opus-4-7"

    # Second boot against the same MODMCP_HOME: hydration must restore.
    with TestClient(create_app()) as client2:
        daemon2 = client2.app.state.daemon

        assert _wait_until(
            lambda: daemon2.state.get(RESTART_SESSION_ID) is not None,
            timeout=5.0,
        ), "second-boot watcher never hydrated the session"

        st2 = daemon2.state.get(RESTART_SESSION_ID)
        assert st2.turns_seen == 1, f"turns_seen reset to {st2.turns_seen}"
        assert st2.last_message_id == "msg_R1"
        assert st2.total_output_tokens == usage["output_tokens"]
        assert st2.total_cache_read_tokens == usage["cache_read_input_tokens"]
        assert st2.last_model == "claude-opus-4-7"
