from __future__ import annotations

import pytest

from modmcp.storage.ledger import Ledger


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
async def test_correction_queue_drain_once() -> None:
    ledger = Ledger()
    await ledger.connect()
    try:
        await ledger.enqueue_correction("s1", "ph", "stay on goal")
        await ledger.enqueue_correction("s1", "ph", "minimal change")
        drained = await ledger.drain_corrections("s1")
        assert drained == ["stay on goal", "minimal change"]
        drained2 = await ledger.drain_corrections("s1")
        assert drained2 == []
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
