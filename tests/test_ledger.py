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
