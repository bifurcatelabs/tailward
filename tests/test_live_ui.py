"""Live session view: routes, replay, SSE subscription, ack/dismiss."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from tailward.daemon.app import create_app
from tailward.paths import intent_path, project_hash
from tailward.schema.intent import empty_intent, save_intent


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


def test_project_page_serves_spa_shell(tmp_path: Path) -> None:
    """The ``/p/<ph>`` route now serves the SPA shell (rules viewer is
    rendered client-side from /v2/projects/<ph>). The legacy Jinja
    intent editor was retired in v2.1 — the v1 ``pill-passive`` chrome
    no longer ships."""
    proj = tmp_path / "projview"
    proj.mkdir()
    ph = _seed(proj)
    with TestClient(create_app()) as client:
        r = client.get(f"/p/{ph}")
        assert r.status_code == 200
        # SPA shell mounts ``<div id="app">`` and serves the bundle
        # script (or build-hint fallback).
        assert 'id="app"' in r.text
        assert (
            "/static/dist/" in r.text
            or "npm install &amp;&amp; npm run build" in r.text
        )


def test_landing_page_serves_spa_shell(tmp_path: Path) -> None:
    """The ``/`` landing route serves the same SPA shell that all
    other top-level pages use; main.js inspects the URL to render
    the LandingView client-side."""
    with TestClient(create_app()) as client:
        r = client.get("/")
        assert r.status_code == 200
        assert 'id="app"' in r.text


def test_v2_projects_endpoint_returns_list(tmp_path: Path) -> None:
    """LandingView reads from /v2/projects."""
    proj = tmp_path / "projlist"
    proj.mkdir()
    ph = _seed(proj)
    with TestClient(create_app()) as client:
        daemon = client.app.state.daemon

        async def _seed_session():
            await daemon.ledger.upsert_session("s-list", ph, str(proj))

        _run(_seed_session())

        r = client.get("/v2/projects")
        assert r.status_code == 200
        body = r.json()
        assert "projects" in body
        hashes = [p["project_hash"] for p in body["projects"]]
        assert ph in hashes


def test_v2_projects_includes_filesystem_discovered(tmp_path: Path) -> None:
    """Projects with JSONL transcripts under ``claude_projects_root()``
    appear in /v2/projects even when the watcher hasn't observed them
    live and ``session_state`` has no row. Closes the fresh-launch hole
    where non-seeded projects were invisible to the Seed UI.
    """
    import json
    import os

    fake_cwd = tmp_path / "fs-only-proj"
    fake_cwd.mkdir()
    expected_hash = project_hash(str(fake_cwd))

    claude_root = Path(os.environ["CLAUDE_PROJECTS_ROOT"])
    proj_dir = claude_root / "fs-only-proj-encoded"
    proj_dir.mkdir(parents=True, exist_ok=True)
    jsonl = proj_dir / "session-x.jsonl"
    # Real Claude Code transcripts prefix with metadata events
    # (queue-operation, permission-mode) that lack ``cwd``; the field
    # appears once the first user/assistant turn fires. Discovery has
    # to scan past the prefix.
    jsonl.write_text(
        json.dumps({"type": "queue-operation", "operation": "enqueue"}) + "\n"
        + json.dumps({"type": "permission-mode", "permissionMode": "acceptEdits"}) + "\n"
        + json.dumps({"type": "user", "cwd": str(fake_cwd), "uuid": "u1"}) + "\n",
        encoding="utf-8",
    )

    with TestClient(create_app()) as client:
        r = client.get("/v2/projects")
        assert r.status_code == 200
        body = r.json()
        rows = {p["project_hash"]: p for p in body["projects"]}
        assert expected_hash in rows
        row = rows[expected_hash]
        assert row["project_path"] == str(fake_cwd)
        assert row["session_count"] == 0
        assert row["last_active_at"] is None
        assert row["latest_session_id"] is None
        assert row["seeded"] is False


def test_v2_projects_dedupes_alt_cwd_encodings(tmp_path: Path) -> None:
    """A Claude Code directory can hold sessions written with different
    ``cwd`` encodings (Claude Code's encoding has shifted across
    versions, and ``C:/foo/bar`` + ``C:\\foo-bar`` encode to the same
    directory name). When the ledger already tracks the project under
    one encoding, the filesystem leg must not surface a ghost row for
    the alternate encoding from the same directory.
    """
    import json
    import os

    from tailward.paths import claude_dir_name

    # Ledger row uses one cwd encoding; the JSONL on disk uses
    # another — both encode to the same Claude Code directory name.
    ledger_cwd = str(tmp_path / "ghost-target")
    alt_cwd = str(tmp_path / "ghost-target-alt")
    assert claude_dir_name(ledger_cwd) != claude_dir_name(alt_cwd)
    # Force the dir name to match the ledger row's encoding so we're
    # exercising the dedup, not the no-collision happy path.
    dir_name = claude_dir_name(ledger_cwd)
    ph = project_hash(ledger_cwd)
    (tmp_path / "ghost-target").mkdir()
    _seed(tmp_path / "ghost-target")  # creates intent.md for ledger_cwd

    claude_root = Path(os.environ["CLAUDE_PROJECTS_ROOT"])
    proj_dir = claude_root / dir_name
    proj_dir.mkdir(parents=True, exist_ok=True)
    (proj_dir / "session-a.jsonl").write_text(
        json.dumps({"type": "user", "cwd": alt_cwd, "uuid": "u1"}) + "\n",
        encoding="utf-8",
    )

    with TestClient(create_app()) as client:
        daemon = client.app.state.daemon

        async def _seed_session():
            await daemon.ledger.upsert_session("s-dedup", ph, ledger_cwd)

        _run(_seed_session())

        r = client.get("/v2/projects")
        body = r.json()
        hashes = [p["project_hash"] for p in body["projects"]]
        assert ph in hashes
        # The alternate-encoding hash must not appear — the ledger row
        # already represents this directory under a different cwd.
        assert project_hash(alt_cwd) not in hashes


def test_post_local_llm_config_writes_and_reloads(tmp_path: Path) -> None:
    """Settings UI POST: writes config.toml + rebuilds the in-memory
    LLM client so endpoint / model changes take effect on the next
    call without a daemon restart. The endpoint summary reflects the
    new values immediately."""
    with TestClient(create_app()) as client:
        # Pre-update sanity: the endpoint defaults are loopback / empty.
        before = client.get("/llm-profiles").json()["endpoint"]
        assert before["endpoint"] == "http://127.0.0.1:8080/v1"

        r = client.post("/v2/config/local-llm", json={
            "local_llm_endpoint": "http://10.0.0.5:9000/v1",
            "local_llm_model": "test-model-7B",
            "local_llm_temperature": 0.4,
            "local_llm_max_tokens": 12000,
        })
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert "local_llm_endpoint" in body["applied"]

        # Endpoint summary now reflects the persisted values.
        after = client.get("/llm-profiles").json()["endpoint"]
        assert after["endpoint"] == "http://10.0.0.5:9000/v1"
        assert after["default_model"] == "test-model-7B"
        assert after["temperature"] == 0.4
        assert after["max_tokens"] == 12000


def test_post_local_llm_config_rejects_bad_endpoint(tmp_path: Path) -> None:
    """Endpoint URL has to start with http:// or https:// — anything
    else is a config-shape error the user should see immediately,
    not a runtime failure on the next LLM call."""
    with TestClient(create_app()) as client:
        r = client.post("/v2/config/local-llm", json={
            "local_llm_endpoint": "ftp://nope/v1",
        })
        assert r.status_code == 400
        assert "http" in r.text.lower()


def test_post_local_llm_config_rejects_unknown_field(tmp_path: Path) -> None:
    """Power-user fields stay TOML-only. Posting them returns 400
    (no editable fields in body) so the surface area of the POST
    endpoint stays explicitly minimal."""
    with TestClient(create_app()) as client:
        r = client.post("/v2/config/local-llm", json={
            "rubric_turn_interval": 10,
        })
        assert r.status_code == 400


def test_v2_project_detail_returns_rules(tmp_path: Path) -> None:
    """ProjectView reads from /v2/projects/<ph>; the response includes
    the parsed rules + active session_mode so the page can render
    the policy read-only."""
    proj = tmp_path / "projdetail"
    proj.mkdir()
    ph = _seed(proj)
    with TestClient(create_app()) as client:
        daemon = client.app.state.daemon

        async def _seed_session():
            await daemon.ledger.upsert_session("s-detail", ph, str(proj))

        _run(_seed_session())

        r = client.get(f"/v2/projects/{ph}")
        assert r.status_code == 200
        body = r.json()
        assert body["project_hash"] == ph
        assert "rules" in body
        assert "path" in body["rules"]
        assert "immutable" in body["rules"]
        assert "bash" in body["rules"]
        # Default-policy baseline ships immutable + bash patterns.
        assert isinstance(body["rules"]["bash"], list)
        assert len(body["rules"]["bash"]) > 0


def test_live_state_json_returns_session(tmp_path: Path) -> None:
    """The live ``state`` JSON endpoint feeds the v0.2 Svelte chassis on
    initial paint; the Jinja-rendered ``/p/<ph>/live/<sid>`` page that
    used to ride alongside it was retired in v2.0.0.
    """
    proj = tmp_path / "livesess"
    proj.mkdir()
    ph = _seed(proj)
    with TestClient(create_app()) as client:
        daemon = client.app.state.daemon

        async def _seed_session():
            await daemon.ledger.upsert_session("s-live", ph, str(proj))

        _run(_seed_session())

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


def test_live_replay_before_paginates_backwards(tmp_path: Path) -> None:
    """``?before=N`` returns the batch of events with id < N, in
    chronological order. Lets a client pass the lowest id it currently
    has rendered and receive the next-older window — the wire shape
    the load-older affordance binds to."""
    proj = tmp_path / "before-page"
    proj.mkdir()
    ph = _seed(proj)
    with TestClient(create_app()) as client:
        daemon = client.app.state.daemon

        async def _drive():
            await daemon.ledger.upsert_session("s-bp", ph, str(proj))
            for i in range(150):
                await daemon.live.publish(
                    "s-bp", ph, "turn",
                    {"turn_idx": i, "chars": i, "marker": i},
                )

        _run(_drive())

        # First fetch: page-load tail. Should be the latest 100 markers.
        r = client.get(f"/p/{ph}/live/s-bp/replay")
        assert r.status_code == 200
        first = r.json()["events"]
        assert len(first) == 100
        first_markers = [e["payload"]["marker"] for e in first]
        assert max(first_markers) == 149
        oldest_id_seen = first[0]["id"]

        # Second fetch: load older. before=oldest_id_seen returns the
        # batch immediately before that — markers 0..49 in this case.
        r2 = client.get(
            f"/p/{ph}/live/s-bp/replay?before={oldest_id_seen}"
        )
        assert r2.status_code == 200
        older = r2.json()["events"]
        # 150 events written, 100 already seen → 50 older remain.
        assert len(older) == 50
        older_markers = [e["payload"]["marker"] for e in older]
        assert older_markers == sorted(older_markers), (
            "load-older batch must be chronological"
        )
        assert max(older_markers) < min(first_markers), (
            "load-older batch must precede the initial tail"
        )

        # Third fetch: nothing older than the absolute first id.
        r3 = client.get(
            f"/p/{ph}/live/s-bp/replay?before={older[0]['id']}"
        )
        assert r3.status_code == 200
        assert r3.json()["events"] == []


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


def test_live_page_serves_svelte_chassis(tmp_path: Path) -> None:
    """The canonical ``/p/<ph>/live/<sid>`` URL serves the Svelte SPA
    bundle (formerly the ``/v2`` suffix). The route must render the
    mount node and either a bundle script tag (if ``dist/`` is built)
    or the build-hint fallback — never 500 if the bundle is missing.
    """
    proj = tmp_path / "live-page"
    proj.mkdir()
    ph = _seed(proj)
    with TestClient(create_app()) as client:
        daemon = client.app.state.daemon

        async def _seed_session():
            await daemon.ledger.upsert_session("s-live", ph, str(proj))

        _run(_seed_session())

        r = client.get(f"/p/{ph}/live/s-live")
        assert r.status_code == 200
        assert f'data-ph="{ph}"' in r.text
        assert 'data-session-id="s-live"' in r.text
        # Either a bundle script (dist built) or the build-hint message
        # — both are acceptable; a route that 500s when the bundle is
        # missing would be the regression we care about.
        assert (
            "/static/dist/" in r.text
            or "npm install &amp;&amp; npm run build" in r.text
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
