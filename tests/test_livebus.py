"""LiveBus pub/sub + event type contract."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from modmcp.daemon.livebus import EVENT_TYPES, LiveBus


@pytest.mark.asyncio
async def test_subscribe_receives_published_events() -> None:
    bus = LiveBus()
    q = await bus.subscribe("s1")
    assert q is not None

    await bus.publish("s1", "ph", "turn", {"turn_idx": 1})
    await bus.publish("s1", "ph", "tool_call", {"tool": "Edit"})

    a = await asyncio.wait_for(q.get(), 1.0)
    b = await asyncio.wait_for(q.get(), 1.0)
    assert a.type == "turn"
    assert b.type == "tool_call"


@pytest.mark.asyncio
async def test_subscriber_cap_returns_none() -> None:
    bus = LiveBus(max_subscribers_per_session=2)
    a = await bus.subscribe("s1")
    b = await bus.subscribe("s1")
    c = await bus.subscribe("s1")
    assert a is not None and b is not None
    assert c is None


@pytest.mark.asyncio
async def test_unknown_event_type_raises() -> None:
    bus = LiveBus()
    with pytest.raises(ValueError):
        await bus.publish("s1", "ph", "definitely_not_registered", {})


@pytest.mark.asyncio
async def test_publish_only_session_isolated() -> None:
    bus = LiveBus()
    qa = await bus.subscribe("s1")
    qb = await bus.subscribe("s2")

    await bus.publish("s1", "ph", "turn", {"turn_idx": 7})
    a = await asyncio.wait_for(qa.get(), 1.0)
    assert a.type == "turn"

    assert qb.empty(), "s2 subscriber should not receive s1 events"


def test_js_client_has_renderer_for_every_event_type() -> None:
    """Regression: live.js must carry a partial renderer for every bus type.

    If a worker ever starts publishing a new event type without wiring the
    client-side renderer, the visibility-parity guarantee breaks.
    """
    js = Path("src/modmcp/web/static/live.js").read_text(encoding="utf-8")
    for event_type in EVENT_TYPES:
        assert f"{event_type}:" in js or f'"{event_type}"' in js, (
            f"live.js missing renderer for event type {event_type!r}"
        )


def test_live_event_to_json_is_parseable() -> None:
    from modmcp.daemon.livebus import LiveEvent
    ev = LiveEvent(session_id="s", project_hash="ph", type="turn",
                   payload={"turn_idx": 1, "text_preview": "hi"})
    data = json.loads(ev.to_json())
    assert data["type"] == "turn"
    assert data["payload"]["turn_idx"] == 1
