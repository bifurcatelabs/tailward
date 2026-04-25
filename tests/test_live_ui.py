"""Live session view: routes, replay, SSE subscription, ack/dismiss."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from modmcp.daemon.app import create_app
from modmcp.paths import intent_path, project_hash
from modmcp.schema.intent import empty_intent, save_intent


def _seed(project_path: Path, goal: str = "ship") -> str:
    intent = empty_intent(str(project_path), project_path.name)
    intent.set("Active Goal", goal)
    save_intent(intent, intent_path(str(project_path)))
    return project_hash(str(project_path))


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_mode_pill_renders_in_header(tmp_path: Path) -> None:
    proj = tmp_path / "modepill"
    proj.mkdir()
    ph = _seed(proj)
    with TestClient(create_app()) as client:
        r = client.get(f"/p/{ph}")
        assert r.status_code == 200
        # conftest defaults to passive (the supported product surface).
        assert "pill-passive" in r.text
        assert ">passive<" in r.text


def test_live_index_redirects_to_latest_session(tmp_path: Path) -> None:
    proj = tmp_path / "liveidx"
    proj.mkdir()
    ph = _seed(proj)
    with TestClient(create_app()) as client:
        r = client.get(f"/p/{ph}/live")
        assert r.status_code == 200
        # No session exists; the "waiting" template should render.
        assert "Live session view" in r.text or "No active sessions" in r.text


def test_live_session_page_renders_and_state_json(tmp_path: Path) -> None:
    proj = tmp_path / "livesess"
    proj.mkdir()
    ph = _seed(proj)
    with TestClient(create_app()) as client:
        daemon = client.app.state.daemon

        async def _seed_session():
            await daemon.ledger.upsert_session("s-live", ph, str(proj))

        _run(_seed_session())

        r = client.get(f"/p/{ph}/live/s-live")
        assert r.status_code == 200
        assert "live-grid" in r.text
        assert "data-session-id=\"s-live\"" in r.text

        state = client.get(f"/p/{ph}/live/s-live/state")
        assert state.status_code == 200
        assert state.json()["session"]["session_id"] == "s-live"


def test_live_replay_returns_recent_events(tmp_path: Path) -> None:
    proj = tmp_path / "replay"
    proj.mkdir()
    ph = _seed(proj)
    with TestClient(create_app()) as client:
        daemon = client.app.state.daemon

        async def _drive():
            await daemon.ledger.upsert_session("s-replay", ph, str(proj))
            await daemon.live.publish(
                "s-replay", ph, "turn", {"turn_idx": 1, "chars": 20}
            )
            await daemon.live.publish(
                "s-replay", ph, "constraint_violation",
                {"rule_id": "x", "rule_text": "t", "evidence": "e",
                 "severity": "high", "id": 1, "tool": "Bash"}
            )

        _run(_drive())

        r = client.get(f"/p/{ph}/live/s-replay/replay")
        assert r.status_code == 200
        body = r.json()
        types = [e["event_type"] for e in body["events"]]
        assert "turn" in types
        assert "constraint_violation" in types


def test_live_replay_returns_tail_not_prefix(tmp_path: Path) -> None:
    """Bootstrap replay must return the most recent events, not the first N.

    Regression: replay was returning ``ORDER BY id LIMIT 500``, i.e. the
    *prefix* of the session. Long sessions never reached recent events
    until SSE catch-up paginated forward — which silently broke things
    like the post-fix usage/model fields not appearing on initial paint.
    """
    proj = tmp_path / "replay-tail"
    proj.mkdir()
    ph = _seed(proj)
    with TestClient(create_app()) as client:
        daemon = client.app.state.daemon

        async def _drive():
            await daemon.ledger.upsert_session("s-tail", ph, str(proj))
            # Publish one more than the default replay window to force
            # the tail/prefix distinction.
            for i in range(150):
                await daemon.live.publish(
                    "s-tail", ph, "turn",
                    {"turn_idx": i, "chars": i, "marker": i},
                )

        _run(_drive())

        r = client.get(f"/p/{ph}/live/s-tail/replay")
        assert r.status_code == 200
        body = r.json()
        markers = [e["payload"]["marker"] for e in body["events"]]
        # Latest event must be present; earliest must not.
        assert 149 in markers, "replay missing the most recent event"
        assert 0 not in markers, (
            "replay returned the session prefix, not the tail; "
            f"first marker was {markers[0]}"
        )
        # Order should be chronological so live.js renders correctly.
        assert markers == sorted(markers), (
            f"replay events not ordered chronologically: {markers[:5]}..."
        )


def test_violation_ack_and_dismiss(tmp_path: Path) -> None:
    proj = tmp_path / "violack"
    proj.mkdir()
    ph = _seed(proj)
    with TestClient(create_app()) as client:
        daemon = client.app.state.daemon

        async def _seed_violation():
            await daemon.ledger.upsert_session("s-v", ph, str(proj))
            return await daemon.ledger.record_constraint_violation(
                "s-v", ph, tool_call_id="t", rule_id="forbidden-bash:abc",
                rule_text="no force push", evidence="git push --force",
                severity="high",
            )

        vid = _run(_seed_violation())

        r = client.post(f"/p/{ph}/violations/{vid}/ack")
        assert r.status_code == 200 and r.json()["status"] == "acknowledged"

        r = client.post(f"/p/{ph}/violations/{vid}/dismiss")
        assert r.status_code == 200 and r.json()["status"] == "dismissed"


def test_polling_fallback_returns_events(tmp_path: Path) -> None:
    """Polling fallback returns the same events the SSE replay would yield.

    The SSE generator's bus-subscription path is exercised by
    ``tests/test_livebus.py``; here we verify the HTTP path clients fall back
    to when EventSource is unavailable, which also covers the replay query.
    """
    proj = tmp_path / "poll"
    proj.mkdir()
    ph = _seed(proj)
    with TestClient(create_app()) as client:
        daemon = client.app.state.daemon

        async def _seed_poll():
            await daemon.ledger.upsert_session("s-poll", ph, str(proj))
            await daemon.live.publish(
                "s-poll", ph, "turn", {"turn_idx": 1, "chars": 10}
            )

        _run(_seed_poll())

        r = client.get(f"/p/{ph}/live/s-poll/events")
        assert r.status_code == 200
        body = r.json()
        types = [e["event_type"] for e in body["events"]]
        assert "turn" in types
