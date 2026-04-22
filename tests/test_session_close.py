"""End-of-session consolidator: report card produced across 8 modes."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from modmcp.daemon.app import create_app
from modmcp.daemon.session_close import FAILURE_MODES, SessionCloseDetector
from modmcp.paths import project_hash


class _FakeQwen:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    async def complete_json(self, system: str, user: str, **kw) -> dict:
        return self.payload

    def resolve_model(self, kind: str) -> str:
        return "test-consolidator"


@pytest.mark.asyncio
async def test_consolidator_writes_eight_mode_report(tmp_path: Path) -> None:
    proj = tmp_path / "close_proj"
    proj.mkdir()

    with TestClient(create_app()) as client:
        daemon = client.app.state.daemon
        # Build a canned Qwen response covering all 8 mode ids.
        modes = {
            str(mid): {
                "score": 4.0,
                "evidence": f"good across mode {mid}",
                "suggestion": "keep doing what you're doing",
            }
            for mid, _, _ in FAILURE_MODES
        }
        daemon.qwen = _FakeQwen({"modes": modes})

        session_id = "s-close"
        ph = project_hash(str(proj))
        await daemon.ledger.upsert_session(session_id, ph, str(proj))

        # Seed a handful of observations so the progress-aggregation branch
        # has inputs, and so the consolidator reaches the DB-backed path.
        await daemon.ledger.record_constraint_violation(
            session_id, ph,
            tool_call_id="t1", rule_id="forbidden-bash:abc", rule_text="no -f",
            evidence="git push --force ...", severity="high",
        )
        await daemon.ledger.record_scope_snapshot(
            session_id, ph,
            turn_idx=2, files_touched_count=8, diff_bytes=4096,
            tool_kinds_json="{}", is_creep=False, baseline=4,
        )
        for name, _ in (
            ("invariants_awareness", ""),
            ("uncertainty_honesty", ""),
            ("maintainability", ""),
            ("provenance", ""),
        ):
            await daemon.ledger.record_rubric_score(
                session_id, ph,
                turn_idx=3, dim_name=name, score=3.5,
                evidence="ev", suggestion="sug",
                model_used="test", trigger="cadence",
            )

        # Directly run the consolidator (bypass idle timer) via a fresh
        # detector instance bound to the same daemon.
        detector = SessionCloseDetector(daemon)
        cs = SimpleNamespace(
            session_id=session_id, project_hash=ph, project_path=str(proj)
        )
        await detector._consolidate(cs)  # noqa: SLF001

        rows = await daemon.ledger.session_report(session_id)
        assert len(rows) == len(FAILURE_MODES)
        assert {r["mode_id"] for r in rows} == {mid for mid, _, _ in FAILURE_MODES}
        for r in rows:
            assert 0.0 <= float(r["score"]) <= 5.0
            assert r["model_used"] == "test-consolidator"


@pytest.mark.asyncio
async def test_progress_aggregation_penalises_violations(tmp_path: Path) -> None:
    """Rule-based fallback path: no LLM; ensure progress scores still land."""
    proj = tmp_path / "close_no_llm"
    proj.mkdir()

    with TestClient(create_app()) as client:
        daemon = client.app.state.daemon
        daemon.qwen = None  # Force pure-progress path.

        session_id = "s-noqwen"
        ph = project_hash(str(proj))
        await daemon.ledger.upsert_session(session_id, ph, str(proj))

        for i in range(2):
            await daemon.ledger.record_constraint_violation(
                session_id, ph,
                tool_call_id=f"t{i}", rule_id=f"immutable:abc{i}",
                rule_text="no touch", evidence="edit pyproject.toml",
                severity="high",
            )

        detector = SessionCloseDetector(daemon)
        cs = SimpleNamespace(
            session_id=session_id, project_hash=ph, project_path=str(proj)
        )
        await detector._consolidate(cs)  # noqa: SLF001

        rows = await daemon.ledger.session_report(session_id)
        by_mode = {r["mode_id"]: r for r in rows}
        # Two high-severity violations -> constraint-respecting modes penalised.
        assert by_mode[1]["score"] < 5.0
        assert by_mode[10]["score"] < 5.0
