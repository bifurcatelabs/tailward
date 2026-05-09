"""Rubric worker: cadence + trigger + LLM JSON parsing."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from tailward.daemon.app import create_app
from tailward.daemon.rubric_worker import DIMENSIONS
from tailward.paths import project_hash
from tailward.schema.events import TranscriptEvent


class _FakeLocalLLM:
    model = "test-model"

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[tuple[str, str]] = []

    async def complete_json(self, system: str, user: str, **kw) -> dict:
        self.calls.append((system, user))
        return self.payload

    def resolve_model(self, kind: str) -> str:
        return self.model


def _assistant(text: str) -> TranscriptEvent:
    return TranscriptEvent(
        raw={}, kind="assistant_message", session_id="s-rubric",
        timestamp=None, text=text,
    )


@pytest.mark.asyncio
async def test_rubric_fires_on_cadence_and_records_all_dimensions(tmp_path: Path) -> None:
    proj = tmp_path / "rub"
    proj.mkdir()

    # Seed an intent with session_mode="build" so the rubric runs all
    # four dimensions. Unlabeled / yolo sessions fall through to the
    # permissive default profile which only scores uncertainty_honesty.
    from tailward.paths import intent_path
    from tailward.schema.intent import empty_intent, save_intent
    intent = empty_intent(str(proj), proj.name)
    intent.front.session_mode = "build"
    save_intent(intent, intent_path(str(proj)))

    with TestClient(create_app()) as client:
        daemon = client.app.state.daemon
        payload = {
            name: {"score": 4, "evidence": f"ev-{name}", "suggestion": "keep going"}
            for name, _ in DIMENSIONS
        }
        daemon.local_llm = _FakeLocalLLM(payload)
        daemon.rubric._daemon.local_llm = daemon.local_llm  # noqa: SLF001

        fs = SimpleNamespace(
            session_id="s-rubric",
            project_path=str(proj),
            project_hash=project_hash(str(proj)),
        )
        await daemon.ledger.upsert_session(
            fs.session_id, fs.project_hash, fs.project_path
        )
        state = daemon.state.get_or_create(
            fs.session_id, fs.project_path, fs.project_hash, "dummy"
        )

        long_text = (
            "This is a deliberate turn that explains the invariants we preserve, "
            "states assumptions openly, and walks through the maintainability cost "
            "of each touched file along with its provenance reason."
        )
        # Push five turns; default interval is 5, so turn #5 triggers.
        for i in range(5):
            state.turns_seen = i + 1
            await daemon.rubric.enqueue(_assistant(long_text), fs)

        async def _wait() -> list[dict]:
            for _ in range(60):
                rows = await daemon.ledger.rubric_scores_for_session(fs.session_id)
                if rows:
                    return rows
                await asyncio.sleep(0.1)
            return []

        rows = await _wait()
        assert rows, "rubric worker did not emit any rows"
        dims = {r["dim_name"] for r in rows}
        assert dims == {name for name, _ in DIMENSIONS}
        for r in rows:
            assert 0.0 <= float(r["score"]) <= 5.0


@pytest.mark.asyncio
async def test_rubric_triggers_on_completion_claim(tmp_path: Path) -> None:
    proj = tmp_path / "rub_claim"
    proj.mkdir()

    with TestClient(create_app()) as client:
        daemon = client.app.state.daemon
        payload = {
            name: {"score": 2, "evidence": "brief", "suggestion": ""}
            for name, _ in DIMENSIONS
        }
        daemon.local_llm = _FakeLocalLLM(payload)
        daemon.rubric._daemon.local_llm = daemon.local_llm  # noqa: SLF001

        fs = SimpleNamespace(
            session_id="s-claim",
            project_path=str(proj),
            project_hash=project_hash(str(proj)),
        )
        await daemon.ledger.upsert_session(
            fs.session_id, fs.project_hash, fs.project_path
        )
        state = daemon.state.get_or_create(
            fs.session_id, fs.project_path, fs.project_hash, "dummy"
        )
        state.turns_seen = 1

        # Single turn with a first-person completion claim (not on cadence).
        await daemon.rubric.enqueue(
            _assistant(
                "I just implemented the new parser module and it handles every "
                "edge case we discussed; shipping the change now."
            ),
            fs,
        )

        for _ in range(60):
            rows = await daemon.ledger.rubric_scores_for_session(fs.session_id)
            if rows:
                break
            await asyncio.sleep(0.1)
        assert rows, "completion claim did not trigger the rubric"
