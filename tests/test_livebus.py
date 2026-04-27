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


def test_svelte_feed_renders_every_event_type() -> None:
    """Regression: every published event type must have a chip + body
    branch in the v0.2 Svelte feed.

    If a worker starts publishing a new event type without wiring the
    Svelte renderer, the visibility-parity guarantee breaks. The legacy
    ``live.js`` renderer was retired in v2.0.0; this test now pins the
    Svelte ``FeedItem.svelte`` instead.

    Two checks per event type:
      * a ``chipLabel`` entry maps the type to a friendly label
      * the LiveStore's ``KNOWN_EVENT_TYPES`` set includes it (so the
        SSE listener subscribes)
    """
    feed_item = Path(
        "frontend/src/lib/FeedItem.svelte"
    ).read_text(encoding="utf-8")
    live_store = Path(
        "frontend/src/lib/live.svelte.js"
    ).read_text(encoding="utf-8")
    for event_type in EVENT_TYPES:
        assert f"{event_type}:" in feed_item or f"'{event_type}'" in feed_item, (
            f"FeedItem.svelte missing chipLabel/branch for event type "
            f"{event_type!r}"
        )
        assert f"'{event_type}'" in live_store, (
            f"live.svelte.js KNOWN_EVENT_TYPES missing {event_type!r}"
        )


def test_live_event_to_json_is_parseable() -> None:
    from modmcp.daemon.livebus import LiveEvent
    ev = LiveEvent(session_id="s", project_hash="ph", type="turn",
                   payload={"turn_idx": 1, "text_preview": "hi"})
    data = json.loads(ev.to_json())
    assert data["type"] == "turn"
    assert data["payload"]["turn_idx"] == 1
