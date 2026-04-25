"""End-to-end smoke test for the opt-in **active** continuity loop.

Boots the daemon via FastAPI TestClient (which runs the lifespan, so watcher
+ drift + audit workers are live), then drives it with a synthetic JSONL
session. Validates:

1. Watcher ingests events and populates session state.
2. Drift worker flags an off-goal turn and queues a corrective.
3. Audit worker classifies a contradicted claim against the fake repo.
4. UserPromptSubmit hook delivers the preamble on first call.
5. UserPromptSubmit hook delivers the queued corrective on a subsequent call.

Active mode is an opt-in, user-owned path (see the README). The canonical
regression test for the supported passive audit layer lives in
``test_e2e_passive_smoke.py``; this file stays only to guard against active
mode breaking silently.

Runs entirely offline — Qwen is nulled out so all LLM paths fall back to
heuristics.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from modmcp.daemon.app import create_app
from modmcp.paths import intent_path, project_hash
from modmcp.schema.intent import empty_intent, save_intent

SESSION_ID = "session-smoke-001"


def _append_jsonl(path: Path, events: list[dict]) -> None:
    with open(path, "a", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")
        f.flush()
        os.fsync(f.fileno())


def _wait_until(predicate, *, timeout: float = 5.0, interval: float = 0.1) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if predicate():
                return True
        except Exception:
            pass
        time.sleep(interval)
    return False


@pytest.fixture
def fake_project(tmp_path: Path) -> Path:
    """A project dir with a file that will contradict a removal claim."""
    proj = tmp_path / "fake-project"
    proj.mkdir()
    (proj / "parser.py").write_text(
        "class FooBar:\n    pass\n\n\ndef legacy_parse(s):\n    return s\n",
        encoding="utf-8",
    )
    return proj


@pytest.fixture
def seeded_intent(fake_project: Path) -> Path:
    intent = empty_intent(str(fake_project), "fake-project")
    intent.set("Active Goal", "Refactor the parser module to use pydantic models")
    intent.append_list_item("Open Threads", "wire pydantic through parser [high]")
    intent.append_list_item("Active Rules", "minimal change; no scope creep")
    target = intent_path(str(fake_project))
    save_intent(intent, target)
    return target


@pytest.fixture
def jsonl_path(tmp_path: Path) -> Path:
    """Empty transcript file under the fake Claude projects root."""
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


def test_full_pipeline_smoke(
    active_mode: Path,
    fake_project: Path,
    seeded_intent: Path,
    jsonl_path: Path,
) -> None:
    cwd = str(fake_project)
    ph = project_hash(cwd)

    with TestClient(create_app()) as client:
        daemon = client.app.state.daemon
        # Force heuristic paths so the test never talks to Qwen.
        daemon.qwen = None
        if daemon.drift is not None:
            daemon.drift._daemon.qwen = None  # noqa: SLF001
        if daemon.audit is not None:
            daemon.audit._daemon.qwen = None  # noqa: SLF001

        # ---- Turn 1: on-goal assistant turn (should NOT trigger drift).
        _append_jsonl(
            jsonl_path,
            [
                _user_event(cwd, "please refactor parser.py to use pydantic"),
                _assistant_event(
                    "I'll refactor the parser module now and add pydantic models for the parse result."
                ),
            ],
        )

        assert _wait_until(
            lambda: daemon.state.get(SESSION_ID) is not None,
            timeout=10.0,
        ), "watcher did not ingest the session"

        assert _wait_until(
            lambda: (daemon.state.get(SESSION_ID).turns_seen if daemon.state.get(SESSION_ID) else 0) >= 1,
            timeout=10.0,
        ), "watcher did not count the first assistant turn"

        # ---- First hook call: preamble delivered.
        r = client.post(
            "/hook/userpromptsubmit",
            json={"session_id": SESSION_ID, "cwd": cwd, "prompt": "ok thanks"},
        )
        assert r.status_code == 200
        body = r.json()
        assert "Warden preamble" in body["additionalContext"]
        assert "Refactor the parser module" in body["additionalContext"]

        # ---- Turn 2: off-goal assistant turn + contradicted claim.
        # Text deliberately shares zero tokens with goal/threads so the
        # heuristic drift score hits zero; avoids build-mode hint words so
        # mode-mismatch doesn't fire either. Also contains a strong-claim
        # that contradicts `parser.py` which still defines FooBar.
        _append_jsonl(
            jsonl_path,
            [
                _user_event(cwd, "sure whatever"),
                _assistant_event(
                    "Brainstorming database scaling ideas. Thinking about "
                    "sharding and caching layers. I removed FooBar entirely."
                ),
            ],
        )

        # Drift worker should record an event for this turn (heuristic should
        # flag low overlap with the "refactor parser pydantic" goal).
        def _drift_recorded() -> bool:
            import asyncio

            loop = asyncio.new_event_loop()
            try:
                rows = loop.run_until_complete(daemon.ledger.recent_drift(ph))
            finally:
                loop.close()
            return any(r["session_id"] == SESSION_ID for r in rows)

        assert _wait_until(_drift_recorded, timeout=10.0), (
            "drift worker did not record an event for the off-goal turn"
        )

        # Audit worker should classify the FooBar removal claim as contradicted.
        def _contradicted_claim() -> bool:
            import asyncio

            loop = asyncio.new_event_loop()
            try:
                rows = loop.run_until_complete(daemon.ledger.recent_claims(ph))
            finally:
                loop.close()
            return any(
                r["session_id"] == SESSION_ID and r["status"] == "contradicted"
                for r in rows
            )

        assert _wait_until(_contradicted_claim, timeout=10.0), (
            "audit worker did not flag the FooBar claim as contradicted"
        )

        # ---- Second hook call: corrective from the drift queue should land.
        r = client.post(
            "/hook/userpromptsubmit",
            json={"session_id": SESSION_ID, "cwd": cwd, "prompt": "next"},
        )
        assert r.status_code == 200
        body = r.json()
        ac = body.get("additionalContext", "")
        assert "Warden corrections" in ac, (
            f"corrective was not injected; additionalContext=\n{ac}"
        )

        # Verify the surfacing table got a row from the contradicted claim.
        import asyncio

        loop = asyncio.new_event_loop()
        try:
            surfacings = loop.run_until_complete(
                daemon.ledger.conn.execute(
                    "SELECT COUNT(*) AS n FROM surfacings WHERE session_id=?",
                    (SESSION_ID,),
                )
            )
            row = loop.run_until_complete(surfacings.fetchone())
        finally:
            loop.close()
        assert row[0] >= 1, "contradicted claim should have produced a surfacing row"
