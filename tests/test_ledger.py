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
