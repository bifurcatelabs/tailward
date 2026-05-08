from __future__ import annotations

import pytest

from tailward.storage.ledger import Ledger


@pytest.mark.asyncio
async def test_offset_round_trip() -> None:
    ledger = Ledger()
    await ledger.connect()
    try:
        assert await ledger.get_offset("s1") == 0
        await ledger.set_offset("s1", "/tmp/s1.jsonl", 42)
        assert await ledger.get_offset("s1") == 42
    finally:
        await ledger.close()


@pytest.mark.asyncio
async def test_upsert_session_is_backlog_false_wins() -> None:
    """A session row's ``is_backlog`` flag flips False-wins on conflict.

    Backlog upsert flags a fresh row True; a later realtime upsert
    on the same session_id de-flags to False (live observation
    overrides historical seed). The reverse — backlog upsert on a
    live-observed row — must NOT re-flag to True, since the session
    is genuinely active and the detector should fire LLM
    consolidation when it eventually closes.
    """
    ledger = Ledger()
    await ledger.connect()
    try:
        # Pure-backlog session: stays True.
        await ledger.upsert_session("s-bk", "ph", "/p", is_backlog=True)
        row = await ledger.get_session("s-bk")
        assert row is not None
        assert row["is_backlog"] == 1

        # Backlog → realtime: de-flags to False.
        await ledger.upsert_session("s-bk", "ph", "/p", is_backlog=False)
        row = await ledger.get_session("s-bk")
        assert row["is_backlog"] == 0

        # Realtime → backlog: stays False (live observation wins).
        await ledger.upsert_session("s-bk", "ph", "/p", is_backlog=True)
        row = await ledger.get_session("s-bk")
        assert row["is_backlog"] == 0

        # Default (no kwarg) is realtime.
        await ledger.upsert_session("s-rt", "ph", "/p")
        row = await ledger.get_session("s-rt")
        assert row["is_backlog"] == 0
    finally:
        await ledger.close()


@pytest.mark.asyncio
async def test_record_claim_and_drift() -> None:
    ledger = Ledger()
    await ledger.connect()
    try:
        await ledger.record_claim("s1", "ph", "claim", "contradicted", "ev")
        rows = await ledger.recent_claims("ph")
        assert len(rows) == 1
        assert rows[0]["status"] == "contradicted"

        await ledger.record_drift("s1", "ph", "drift", "med", "queued_correction", "low overlap")
        rows = await ledger.recent_drift("ph")
        assert len(rows) == 1
        assert rows[0]["severity"] == "med"
    finally:
        await ledger.close()


@pytest.mark.asyncio
async def test_violation_status_counts_groups_by_status() -> None:
    """Reflection's "approval cadence" panel reads these counts."""
    ledger = Ledger()
    await ledger.connect()
    try:
        kw = dict(tool_call_id=None, rule_id="no_rm_rf", rule_text="no rm -rf",
                  evidence="rm -rf /", severity="high")
        await ledger.record_constraint_violation("s1", "ph", **kw)
        await ledger.record_constraint_violation("s1", "ph", **{**kw, "evidence": "rm -rf /tmp", "severity": "med"})
        v_id3 = await ledger.record_constraint_violation(
            "s1", "ph", **{**kw, "evidence": "rm -rf .git"}
        )
        # Transition one to acknowledged.
        await ledger.set_violation_status(v_id3, "acknowledged")

        counts = await ledger.violation_status_counts("ph")
        assert counts["new"] == 2
        assert counts["acknowledged"] == 1
        assert counts["dismissed"] == 0
    finally:
        await ledger.close()


@pytest.mark.asyncio
async def test_claim_status_counts_groups_by_status() -> None:
    """Reflection's "verification verdicts" panel reads these counts."""
    ledger = Ledger()
    await ledger.connect()
    try:
        await ledger.record_claim("s1", "ph", "all tests pass", "verified", "ev")
        await ledger.record_claim("s1", "ph", "fixed the bug", "contradicted", "ev")
        await ledger.record_claim("s1", "ph", "added X", "verified", "ev")
        await ledger.record_claim("s1", "ph", "deployed", "unverifiable", None)

        counts = await ledger.claim_status_counts("ph")
        assert counts["verified"] == 2
        assert counts["contradicted"] == 1
        assert counts["unverifiable"] == 1
    finally:
        await ledger.close()


@pytest.mark.asyncio
async def test_user_rubric_summary_filters_by_subject() -> None:
    """The user-rubric panel must not see assistant-side rows.

    The schema column ``subject`` defaults to 'assistant' for legacy
    rows; user rows must be tagged 'user' explicitly. Verify the
    aggregate query filters correctly.
    """
    ledger = Ledger()
    await ledger.connect()
    try:
        # Two user-side rows.
        await ledger.record_rubric_score(
            "s1", "ph", turn_idx=1, dim_name="intent_clarity",
            score=4.0, evidence=None, suggestion=None,
            model_used=None, trigger=None, session_mode="build",
            subject="user",
        )
        await ledger.record_rubric_score(
            "s1", "ph", turn_idx=2, dim_name="intent_clarity",
            score=2.0, evidence=None, suggestion=None,
            model_used=None, trigger=None, session_mode="build",
            subject="user",
        )
        # One assistant-side row that must NOT pollute the user avg.
        await ledger.record_rubric_score(
            "s1", "ph", turn_idx=1, dim_name="intent_clarity",
            score=5.0, evidence=None, suggestion=None,
            model_used=None, trigger=None, session_mode="build",
            # subject defaults to 'assistant'
        )

        summary = await ledger.user_rubric_summary("ph")
        by_dim = {r["dim"]: r for r in summary["by_dim"]}
        assert "intent_clarity" in by_dim
        assert by_dim["intent_clarity"]["n"] == 2
        assert by_dim["intent_clarity"]["avg_score"] == 3.0  # avg of 4 and 2
    finally:
        await ledger.close()


@pytest.mark.asyncio
async def test_recent_sessions_with_summary_includes_rubric_avg_and_report_flag() -> None:
    """Reflection's past-sessions table reads avg_score + has_report."""
    ledger = Ledger()
    await ledger.connect()
    try:
        await ledger.upsert_session("s-A", "ph", "/proj/A")
        await ledger.upsert_session("s-B", "ph", "/proj/A")

        # s-A has rubric scores; s-B doesn't.
        for turn, score in [(1, 4.0), (2, 3.0), (3, 5.0)]:
            await ledger.record_rubric_score(
                "s-A", "ph", turn_idx=turn, dim_name="provenance",
                score=score, evidence=None, suggestion=None,
                model_used=None, trigger=None, session_mode="build",
            )

        # s-A has a report card; s-B doesn't.
        await ledger.upsert_session_report(
            "s-A", "ph", mode_id=1, mode_name="provenance",
            score=4.0, evidence_json=None, suggestion="keep going",
            model_used=None,
        )

        rows = await ledger.recent_sessions_with_summary(limit=10)
        by_id = {r["session_id"]: r for r in rows}

        assert by_id["s-A"]["avg_score"] == 4.0
        assert by_id["s-A"]["sample_count"] == 3
        assert by_id["s-A"]["has_report"] == 1

        assert by_id["s-B"]["avg_score"] is None
        assert by_id["s-B"]["sample_count"] is None
        assert by_id["s-B"]["has_report"] == 0
    finally:
        await ledger.close()


@pytest.mark.asyncio
async def test_session_rubric_trajectory_returns_per_turn_per_dim() -> None:
    """Powers the deep view's per-dimension trajectory plot."""
    ledger = Ledger()
    await ledger.connect()
    try:
        await ledger.upsert_session("s1", "ph", "/p")
        for turn in (1, 2, 3):
            for dim in ("provenance", "uncertainty_honesty"):
                await ledger.record_rubric_score(
                    "s1", "ph", turn_idx=turn, dim_name=dim,
                    score=3.5, evidence=None, suggestion=None,
                    model_used=None, trigger=None, session_mode="build",
                )

        traj = await ledger.session_rubric_trajectory("s1")
        # 3 turns × 2 dims = 6 rows, ordered by (turn_idx, dim_name).
        assert len(traj) == 6
        assert traj[0]["turn_idx"] == 1
        assert traj[-1]["turn_idx"] == 3
        # Within a turn, dim_name is sorted ASC.
        assert traj[0]["dim_name"] < traj[1]["dim_name"]
    finally:
        await ledger.close()


@pytest.mark.asyncio
async def test_user_turn_rows_returns_oldest_first_with_payload() -> None:
    """Reflection derives idle-gap and prompt-length stats from this stream.
    Synthesized turns are included with their event_type so callers can
    exclude or count them as needed."""
    import asyncio as _asyncio

    ledger = Ledger()
    await ledger.connect()
    try:
        import json as _json
        for etype, payload in [
            ("user_turn", {"text_preview": "hello", "chars": 5}),
            ("compact_summary", {"text_preview": "summary", "chars": 200}),
            ("user_turn", {"text_preview": "hello again", "chars": 11}),
        ]:
            await ledger.record_live_event(
                "s1", "ph", etype, _json.dumps(payload)
            )
            await _asyncio.sleep(0.01)  # ensure ordering by id

        rows = await ledger.user_turn_rows("ph", limit=10)
        assert len(rows) == 3
        # Oldest-first per docstring contract.
        assert rows[0]["event_type"] == "user_turn"
        assert rows[1]["event_type"] == "compact_summary"
        assert rows[2]["event_type"] == "user_turn"
        # Payload should round-trip as JSON string the caller can parse.
        import json as _json
        first = _json.loads(rows[0]["payload"])
        assert first["chars"] == 5
    finally:
        await ledger.close()


@pytest.mark.asyncio
async def test_recent_sessions_spans_projects_in_recency_order() -> None:
    """recent_sessions powers the cross-project picker; rows from
    different projects must interleave by last_seen_at, newest first."""
    import asyncio as _asyncio

    ledger = Ledger()
    await ledger.connect()
    try:
        await ledger.upsert_session("s-old", "ph-A", "/proj/A")
        # Tiny delay so last_seen_at differs at second resolution.
        await _asyncio.sleep(1.05)
        await ledger.upsert_session("s-mid", "ph-B", "/proj/B")
        await _asyncio.sleep(1.05)
        await ledger.upsert_session("s-new", "ph-A", "/proj/A")

        rows = await ledger.recent_sessions(limit=10)
        ids = [r["session_id"] for r in rows]
        assert ids[0] == "s-new"
        assert ids[1] == "s-mid"
        assert ids[2] == "s-old"

        # Required columns the picker renders.
        for r in rows:
            assert "project_hash" in r
            assert "project_path" in r
            assert "last_seen_at" in r
            assert "turns_seen" in r
    finally:
        await ledger.close()
