"""End-of-session consolidator: report card produced across 8 modes."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from tailward.daemon.app import create_app
from tailward.daemon.session_close import FAILURE_MODES, SessionCloseDetector
from tailward.paths import project_hash


class _FakeLocalLLM:
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
        # Build a canned LLM response covering all 8 mode ids.
        modes = {
            str(mid): {
                "score": 4.0,
                "evidence": f"good across mode {mid}",
                "suggestion": "keep doing what you're doing",
            }
            for mid, _, _ in FAILURE_MODES
        }
        daemon.local_llm = _FakeLocalLLM({"modes": modes})

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
            session_id=session_id, project_hash=ph, project_path=str(proj),
            is_backlog=False, last_seen_at=None,
        )
        await detector._consolidate(cs)  # noqa: SLF001

        rows = await daemon.ledger.session_report(session_id)
        assert len(rows) == len(FAILURE_MODES)
        assert {r["mode_id"] for r in rows} == {mid for mid, _, _ in FAILURE_MODES}
        for r in rows:
            assert 0.0 <= float(r["score"]) <= 5.0
            assert r["model_used"] == "test-consolidator"


@pytest.mark.asyncio
async def test_consolidator_skips_llm_on_backlog(tmp_path: Path) -> None:
    """Backlog sessions (seeded historical, never observed live) honor
    the no-LLM-on-backlog rule. Even with the LLM client wired up,
    the consolidator skips the LLM call and lands a rule-based-only
    session report. Live publishes carry the session's actual close
    time as event_ts and skip broadcast so past-session views see
    the report at the right point in time without flooding the live
    feed.
    """
    proj = tmp_path / "close_backlog"
    proj.mkdir()

    with TestClient(create_app()) as client:
        daemon = client.app.state.daemon

        # LLM client is wired up — proves the gate is what's skipping
        # the LLM call, not the absent-client branch.
        modes = {
            str(mid): {"score": 4.0, "evidence": "ev", "suggestion": "sug"}
            for mid, _, _ in FAILURE_MODES
        }
        called = []

        class _FakeLocalLLMTracking:
            payload = {"modes": modes}

            async def complete_json(self, system: str, user: str, **kw) -> dict:
                called.append(kw)
                return self.payload

            def resolve_model(self, kind: str) -> str:
                return "test-consolidator"

        daemon.local_llm = _FakeLocalLLMTracking()

        session_id = "s-backlog"
        ph = project_hash(str(proj))
        await daemon.ledger.upsert_session(session_id, ph, str(proj), is_backlog=True)
        # Seed a violation so the rule-based aggregator has input.
        await daemon.ledger.record_constraint_violation(
            session_id, ph,
            tool_call_id="t1", rule_id="forbidden-bash:rmrf", rule_text="no rm -rf",
            evidence="rm -rf /", severity="high",
        )

        sess_row = await daemon.ledger.get_session(session_id)
        assert sess_row["is_backlog"] == 1

        detector = SessionCloseDetector(daemon)
        cs = SimpleNamespace(
            session_id=session_id,
            project_hash=ph,
            project_path=str(proj),
            is_backlog=True,
            last_seen_at=sess_row["last_seen_at"],
        )
        await detector._consolidate(cs)  # noqa: SLF001

        # LLM was not called.
        assert called == []

        # Report rows landed (rule-based fallback path).
        rows = await daemon.ledger.session_report(session_id)
        assert len(rows) == len(FAILURE_MODES)
        # No LLM run, so model_used is None on every mode.
        assert all(r["model_used"] is None for r in rows)


@pytest.mark.asyncio
async def test_progress_aggregation_penalises_violations(tmp_path: Path) -> None:
    """Rule-based fallback path: no LLM; ensure progress scores still land."""
    proj = tmp_path / "close_no_llm"
    proj.mkdir()

    with TestClient(create_app()) as client:
        daemon = client.app.state.daemon
        daemon.local_llm = None  # Force pure-progress path.

        session_id = "s-no-llm"
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
            session_id=session_id, project_hash=ph, project_path=str(proj),
            is_backlog=False, last_seen_at=None,
        )
        await detector._consolidate(cs)  # noqa: SLF001

        rows = await daemon.ledger.session_report(session_id)
        by_mode = {r["mode_id"]: r for r in rows}
        # Two high-severity violations -> constraint-respecting modes penalised.
        assert by_mode[1]["score"] < 5.0
        assert by_mode[10]["score"] < 5.0
