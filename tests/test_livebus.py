"""LiveBus pub/sub + event type contract."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from tailward.daemon.livebus import EVENT_TYPES, LiveBus


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


def test_publish_call_sites_use_registered_event_types() -> None:
    """Inverse of ``test_svelte_feed_renders_every_event_type``.

    That test guards FeedItem/live.svelte.js sync to ``EVENT_TYPES``.
    This test catches the *other* direction: a worker that publishes
    a type missing from ``EVENT_TYPES``. ``permission_mode_change``,
    ``away_summary``, and ``tool_interrupted`` shipped that way —
    ``publish()`` raised ``ValueError``, the call sites' ``try / except``
    swallowed it, and no chip ever rendered. The earlier test was happy
    because the unregistered types weren't in the iteration set.

    Approach: AST-walk every ``src/tailward/daemon/**/*.py`` file, find
    Call nodes whose attribute is ``publish`` and whose third positional
    arg (or ``event_type=`` kwarg) is a string literal. Assert each is
    in ``EVENT_TYPES``. Variable-driven publish calls are out of scope
    — workers in this codebase always pass literals.
    """
    import ast

    daemon_dir = Path("src/tailward/daemon")
    assert daemon_dir.is_dir(), "daemon source dir not found from test cwd"

    found_types: set[str] = set()
    for py_file in daemon_dir.rglob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr == "publish"):
                continue
            # publish(session_id, project_hash, event_type, payload)
            if (
                len(node.args) >= 3
                and isinstance(node.args[2], ast.Constant)
                and isinstance(node.args[2].value, str)
            ):
                found_types.add(node.args[2].value)
            for kw in node.keywords:
                if (
                    kw.arg == "event_type"
                    and isinstance(kw.value, ast.Constant)
                    and isinstance(kw.value.value, str)
                ):
                    found_types.add(kw.value.value)

    unregistered = found_types - EVENT_TYPES
    assert not unregistered, (
        f"daemon code publishes event type(s) missing from "
        f"livebus.EVENT_TYPES: {sorted(unregistered)}. Register them + "
        f"wire chip/body in FeedItem.svelte + add to KNOWN_EVENT_TYPES "
        f"in live.svelte.js. See livebus.py docstring for the contract."
    )

    # Sanity: a non-trivial number of literal types should always be
    # found. If this floor trips, the AST scan likely broke (e.g.,
    # someone restructured publish call sites to use a variable for
    # event_type, defeating the static check). Investigate the scan
    # logic; don't just lower the threshold to make this pass.
    assert len(found_types) >= 5, (
        f"only found {len(found_types)} literal event_types across "
        f"daemon publish() call sites — the AST walk may have stopped "
        f"working. Found: {sorted(found_types)}"
    )


def test_live_event_to_json_is_parseable() -> None:
    from tailward.daemon.livebus import LiveEvent
    ev = LiveEvent(session_id="s", project_hash="ph", type="turn",
                   payload={"turn_idx": 1, "text_preview": "hi"})
    data = json.loads(ev.to_json())
    assert data["type"] == "turn"
    assert data["payload"]["turn_idx"] == 1
