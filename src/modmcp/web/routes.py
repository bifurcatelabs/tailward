"""Web UI routes: SPA shell for landing / project / session pages, plus
the JSON endpoints the SPA reads.

FastAPI routes mounted onto the main daemon app. As of v2.1 the Svelte
SPA owns every visible page (landing at ``/``, project rules viewer at
``/p/<ph>``, session audit at ``/p/<ph>/live/<sid>``) — main.js
inspects the URL to pick the top-level view. The legacy v1 Jinja
intent editor was retired alongside the v1.1 audit pages.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..config import get_config
from .sse import poll_events, stream_for_session, unwrap_stored_payload

_WEB_DIR = Path(__file__).parent
_TEMPLATES_DIR = _WEB_DIR / "templates"
_STATIC_DIR = _WEB_DIR / "static"
_DIST_DIR = _STATIC_DIR / "dist"


def _resolve_v2_bundle() -> tuple[str | None, str | None]:
    """Return (js_url, css_url) for the v0.2 Svelte bundle, or (None, None).

    Vite writes ``dist/.vite/manifest.json`` (or ``dist/manifest.json``
    depending on version) mapping the entry input to its hashed output
    files. We prefer the manifest so cache-busted filenames are picked
    up; fall back to ``None`` if the bundle hasn't been built yet so
    the template can render a "run npm run build" hint instead of a
    broken script tag.
    """
    import json as _json

    manifest_candidates = [
        _DIST_DIR / ".vite" / "manifest.json",
        _DIST_DIR / "manifest.json",
    ]
    for mf in manifest_candidates:
        if not mf.exists():
            continue
        try:
            data = _json.loads(mf.read_text(encoding="utf-8"))
        except Exception:
            continue
        # Vite keys the manifest by the input filename; we use index.html.
        entry = data.get("index.html") or next(iter(data.values()), None)
        if not isinstance(entry, dict):
            continue
        js_file = entry.get("file")
        css_files = entry.get("css") or []
        js_url = f"/static/dist/{js_file}" if js_file else None
        css_url = f"/static/dist/{css_files[0]}" if css_files else None
        return js_url, css_url
    return None, None


def mount_web(app: FastAPI) -> None:
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

    def _base_ctx() -> dict:
        return {"warden_mode": get_config().warden_mode}

    if _STATIC_DIR.exists():
        # Disable caching on static assets so live.js / CSS edits land
        # without forcing a hard reload. The dev/local-first posture
        # makes this preferable to a cache-busting build step; the cost
        # is one extra GET on each navigation.
        class _NoCacheStatic(StaticFiles):
            async def get_response(self, path, scope):
                resp = await super().get_response(path, scope)
                resp.headers["Cache-Control"] = "no-store"
                return resp

        app.mount("/static", _NoCacheStatic(directory=str(_STATIC_DIR)), name="static")

    def _spa_shell(request: Request, *, title: str = "warden") -> HTMLResponse:
        """Render the SPA shell template for non-session pages.

        The same Svelte bundle serves landing (``/``), project rules
        (``/p/<ph>``), and the session audit surface
        (``/p/<ph>/live/<sid>``) — main.js inspects the URL pathname
        to pick which top-level view to render. The shell is just a
        ``<div id="app">`` mount node + the bundle script.
        """
        bundle_js, bundle_css = _resolve_v2_bundle()
        return templates.TemplateResponse(
            request,
            "live.html",
            {
                **_base_ctx(),
                "ph": "",
                "session": None,
                "session_id": "",
                "title": title,
                "v2_bundle_js": bundle_js,
                "v2_bundle_css": bundle_css,
            },
        )

    @app.get("/", response_class=HTMLResponse)
    async def landing(request: Request) -> HTMLResponse:
        """Landing page — Svelte SPA reads /v2/projects to populate."""
        return _spa_shell(request, title="warden")

    @app.get("/p/{ph}", response_class=HTMLResponse)
    async def project_view(request: Request, ph: str) -> HTMLResponse:
        """Project rules viewer — Svelte SPA reads /v2/projects/<ph>.

        Replaces the legacy Jinja intent editor in v2.1; the SPA
        renders the parsed CompiledPolicy + session_mode read-only.
        ``intent.md`` remains the source of truth and is hand-edited.
        """
        # Reject obviously-bogus hashes early so 404 routing is honest.
        if not ph or "/" in ph:
            raise HTTPException(404)
        return _spa_shell(request, title=f"warden · {ph[:8]}")

    # ------------------------------------------------------------------
    # Live session view — Svelte chassis at the canonical URL
    # ------------------------------------------------------------------

    @app.get("/p/{ph}/live/{session_id}", response_class=HTMLResponse)
    async def live_session(request: Request, ph: str, session_id: str) -> HTMLResponse:
        """Live audit surface for a session.

        Serves the Svelte SPA bundle from ``frontend/dist/``. The
        legacy v1.1 Jinja audit page that used to live at this URL was
        retired in v2.0.0; the Svelte chassis (formerly at the
        ``/v2`` suffix) is now the only live UI.
        """
        daemon = request.app.state.daemon
        session = await daemon.ledger.get_session(session_id)
        if session is None or session["project_hash"] != ph:
            raise HTTPException(404)
        bundle_js, bundle_css = _resolve_v2_bundle()
        return templates.TemplateResponse(
            request,
            "live.html",
            {
                **_base_ctx(),
                "ph": ph,
                "session": session,
                "session_id": session_id,
                "v2_bundle_js": bundle_js,
                "v2_bundle_css": bundle_css,
            },
        )

    @app.get("/p/{ph}/live/{session_id}/stream")
    async def live_stream(request: Request, ph: str, session_id: str):
        return stream_for_session(request, ph, session_id)

    @app.get("/p/{ph}/live/{session_id}/events")
    async def live_events_poll(request: Request, ph: str, session_id: str):
        return await poll_events(request, ph, session_id)

    # ------------------------------------------------------------------
    # Violations page + ack/dismiss
    # ------------------------------------------------------------------

    @app.post("/p/{ph}/violations/{vid}/ack")
    async def violation_ack(request: Request, ph: str, vid: int) -> JSONResponse:
        daemon = request.app.state.daemon
        ok = await daemon.ledger.set_violation_status(vid, "acknowledged")
        return JSONResponse({"ok": ok, "status": "acknowledged"})

    @app.post("/p/{ph}/violations/{vid}/dismiss")
    async def violation_dismiss(request: Request, ph: str, vid: int) -> JSONResponse:
        daemon = request.app.state.daemon
        ok = await daemon.ledger.set_violation_status(vid, "dismissed")
        return JSONResponse({"ok": ok, "status": "dismissed"})

    # ------------------------------------------------------------------
    # Rubric feedback
    # ------------------------------------------------------------------

    @app.post("/p/{ph}/rubric/{score_id}/feedback")
    async def rubric_feedback(request: Request, ph: str, score_id: int) -> JSONResponse:
        daemon = request.app.state.daemon
        body = await request.json()
        verdict = body.get("verdict", "disagree")
        note = body.get("note")
        await daemon.ledger.record_rubric_feedback(score_id, verdict, note)
        return JSONResponse({"ok": True})

    # ------------------------------------------------------------------
    # v0.2 Platform endpoints — global telemetry not scoped to a session
    # ------------------------------------------------------------------

    @app.get("/llm-profiles")
    async def llm_profiles(request: Request) -> JSONResponse:
        """Per-call-kind config + verbatim prompts.

        Surfaces what Warden itself is sending to the local LLM —
        model, sampler params, max_tokens, thinking on/off, plus the
        unredacted system prompt and user-prompt template for each of
        synth / drift / query / rubric / consolidator. Read by the
        Platform view's transparency panel.
        """
        from ..daemon.llm_profiles import all_profiles, endpoint_summary

        return JSONResponse({
            "endpoint": endpoint_summary(),
            "profiles": all_profiles(),
        })

    @app.get("/llm-metrics/summary")
    async def llm_metrics_summary(request: Request) -> JSONResponse:
        """Per-call-kind aggregates of LLM call instrumentation.

        Answers the open question of whether ``qwen_max_tokens_rubric``
        / ``_consolidator`` are silently truncating mid-think. Read
        the ``n_length`` count and the ratio of ``avg_completion`` to
        ``configured_max_tokens`` per kind; high ``n_length`` with
        ``avg_completion`` near ``configured_max_tokens`` is the smoking
        gun.
        """
        daemon = request.app.state.daemon
        rows = await daemon.ledger.llm_call_metrics_summary()
        return JSONResponse({"by_kind": rows})

    @app.get("/v2/reflection/{ph}/self-rubric")
    async def v2_self_rubric(request: Request, ph: str) -> JSONResponse:
        """User-side rubric summary + recent samples for a project.

        Powers the Reflection-view self-rubric panel: per-dimension
        averages alongside recent specific evidence/suggestion rows.
        """
        daemon = request.app.state.daemon
        try:
            limit = int(request.query_params.get("limit", "30") or 30)
        except ValueError:
            limit = 30
        summary = await daemon.ledger.user_rubric_summary(ph)
        recent = await daemon.ledger.user_rubric_recent(ph, limit=limit)
        return JSONResponse({
            "by_dim": summary["by_dim"],
            "recent": recent,
        })

    @app.get("/v2/reflection/sessions")
    async def v2_reflection_sessions(request: Request) -> JSONResponse:
        """Cross-project sessions list with summary stats.

        Returns one row per session: id, recency, project, turns,
        token totals, avg rubric score (across all dimensions, all
        turns), rubric sample count, and whether the session-close
        consolidator produced a 8-mode report card. Powers the
        Reflection-view past-sessions table.
        """
        daemon = request.app.state.daemon
        try:
            limit = int(request.query_params.get("limit", "50") or 50)
        except ValueError:
            limit = 50
        rows = await daemon.ledger.recent_sessions_with_summary(limit=limit)
        return JSONResponse({"sessions": rows})

    @app.get("/v2/reflection/sessions/{session_id}")
    async def v2_reflection_session_detail(
        request: Request, session_id: str
    ) -> JSONResponse:
        """Per-session deep view: 8-mode report card + rubric trajectory.

        Used when the user expands a row in the Reflection sessions
        table. Returns enough to render both the score card and a
        per-turn rubric plot without further round-trips.
        """
        daemon = request.app.state.daemon
        session = await daemon.ledger.get_session(session_id)
        if session is None:
            raise HTTPException(404)
        report = await daemon.ledger.session_report(session_id)
        trajectory = await daemon.ledger.session_rubric_trajectory(session_id)
        return JSONResponse({
            "session": session,
            "report": report,
            "trajectory": trajectory,
        })

    @app.get("/v2/reflection/{ph}")
    async def v2_reflection_signals(request: Request, ph: str) -> JSONResponse:
        """User-side derived signals for the Reflection view — no LLM calls.

        Reflection is the user-side surface ("am I behaving?"). Anything
        the third-party model emitted (``verification``, ``stop_reasons``)
        moved to ``/v2/platform/{ph}/agent-behavior`` so endpoint shape
        matches view identity.

        * ``intervals_seconds`` — seconds between consecutive *typed* user
          turns. Synthesized turns (Claude Code /compact) are excluded so
          the distribution reflects actual user pacing.
        * ``prompt_lengths_chars`` — char counts of typed user prompts.
        * ``synthesized_user_turns`` — count of /compact-style turns.
        * ``approvals`` — constraint_violations by status (user response
          cadence on destructive-action surfacings).
        * ``tool_calls_by_mode`` — tool counts grouped by user-selected
          permission mode.
        * ``memory_edits`` — count of memory-file edits made by the user.
        """
        import json as _json

        daemon = request.app.state.daemon
        try:
            limit = int(request.query_params.get("limit", "500") or 500)
        except ValueError:
            limit = 500

        rows = await daemon.ledger.user_turn_rows(ph, limit=limit)

        intervals: list[float] = []
        prompt_lengths: list[int] = []
        synthesized_count = 0
        prev_ts: float | None = None
        prev_session: str | None = None
        from datetime import datetime as _dt

        for r in rows:
            if r["event_type"] == "compact_summary":
                synthesized_count += 1
                # A compact_summary breaks the user-pacing chain — the
                # next typed turn shouldn't be treated as adjacent to
                # the previous one across a synthesis boundary.
                prev_ts = None
                continue
            try:
                ts = _dt.fromisoformat(r["created_at"]).timestamp()
            except Exception:
                ts = None
            try:
                payload = _json.loads(r["payload"]) if r.get("payload") else {}
            except Exception:
                payload = {}
            chars = payload.get("chars")
            if isinstance(chars, int) and chars > 0:
                prompt_lengths.append(chars)
            # Gap is intra-session only: switching sessions doesn't
            # describe user pacing within a coherent task.
            if ts is not None and prev_ts is not None and prev_session == r["session_id"]:
                intervals.append(round(ts - prev_ts, 3))
            prev_ts = ts
            prev_session = r["session_id"]

        approvals = await daemon.ledger.violation_status_counts(ph)
        tool_modes = await daemon.ledger.tool_calls_by_mode(ph)
        memory_edits = await daemon.ledger.memory_edit_count(ph)

        return JSONResponse({
            "intervals_seconds": intervals,
            "prompt_lengths_chars": prompt_lengths,
            "synthesized_user_turns": synthesized_count,
            "approvals": approvals,
            "tool_calls_by_mode": tool_modes,
            "memory_edits": memory_edits,
            "sample_size": len(rows),
        })

    @app.get("/v2/platform/{ph}/agent-behavior")
    async def v2_platform_agent_behavior(request: Request, ph: str) -> JSONResponse:
        """Third-party-provider-facing signals for the Platform view.

        Anthropic-emitted or Anthropic-text-derived metrics across the
        project's assistant messages — what the served model did, not
        what the user did:

        * ``stop_reasons`` — distribution of ``stop_reason`` values
          across assistant messages, deduped per ``message_id``.
        * ``verification`` — counts of ``verification_ledger`` rows by
          status (verified / contradicted / unverifiable). Hybrid
          signal — the claim text comes from the model; the grep is
          local — but the *audit question* is third-party-facing
          ("is the served model accurate?").
        """
        daemon = request.app.state.daemon
        verification = await daemon.ledger.claim_status_counts(ph)
        stop_reasons = await daemon.ledger.stop_reason_counts(ph)
        return JSONResponse({
            "verification": verification,
            "stop_reasons": stop_reasons,
        })

    @app.get("/p/{ph}/search")
    async def project_search(
        request: Request,
        ph: str,
    ) -> JSONResponse:
        """Project-scoped substring search across content-bearing
        live_events (user turns, assistant turns, tool calls,
        synthesized turns, claims, away-summary recaps). Returns a
        list of result rows for the search panel to render.

        Query parameter ``q`` is the substring to match (case-insensitive).
        ``limit`` defaults to 50; capped at 200.
        """
        daemon = request.app.state.daemon
        q = (request.query_params.get("q") or "").strip()
        try:
            limit = min(int(request.query_params.get("limit", "50") or 50), 200)
        except ValueError:
            limit = 50
        if not q:
            return JSONResponse({"query": "", "results": [], "limit": limit})
        results = await daemon.ledger.search_events(ph, q, limit)
        return JSONResponse({
            "query": q,
            "results": results,
            "limit": limit,
            "count": len(results),
        })

    @app.get("/v2/projects")
    async def v2_projects(request: Request) -> JSONResponse:
        """Cross-project landing-page data: one row per project warden
        has seen, with session count, last activity, intent.md presence,
        most-recent session id (for click-through), and the active
        session_mode label.
        """
        from ..daemon.mode_profile import session_mode_for_project
        from ..paths import intent_path, project_dir

        daemon = request.app.state.daemon
        rows = await daemon.ledger.projects_summary()
        out = []
        for r in rows:
            ph = r["project_hash"]
            project_path = r["project_path"]
            try:
                intent_exists = intent_path(project_path).exists()
            except Exception:
                intent_exists = False
            try:
                pdir = project_dir(project_path).exists()
            except Exception:
                pdir = False
            try:
                latest_sid = await daemon.ledger.latest_session_for_project(ph)
            except Exception:
                latest_sid = None
            mode = session_mode_for_project(project_path) if intent_exists else None
            out.append({
                "project_hash": ph,
                "project_path": project_path,
                "session_count": int(r["session_count"]),
                "last_active_at": r["last_active_at"],
                "intent_exists": bool(intent_exists),
                "project_dir_exists": bool(pdir),
                "latest_session_id": latest_sid,
                "session_mode": mode,
            })
        return JSONResponse({"projects": out})

    @app.get("/v2/projects/{ph}")
    async def v2_project_detail(request: Request, ph: str) -> JSONResponse:
        """Per-project rules viewer payload: parsed CompiledPolicy +
        active session_mode + intent.md path. Read-only — the file is
        edited externally.
        """
        from dataclasses import asdict

        from ..daemon.mode_profile import (
            active_profile_for_project,
            session_mode_for_project,
        )
        from ..paths import intent_path
        from ..schema.constraints import default_policy, parse_active_rules
        from ..schema.intent import load_intent

        daemon = request.app.state.daemon
        rows = await daemon.ledger.projects_summary()
        match = next((r for r in rows if r["project_hash"] == ph), None)
        if match is None:
            raise HTTPException(404)
        project_path = match["project_path"]
        intent_md = intent_path(project_path)

        # Compose policy = baseline + parsed-from-intent.md (if present).
        policy = default_policy()
        if intent_md.exists():
            try:
                intent = load_intent(intent_md)
                rules_body = intent.sections.get("Active Rules", "") or ""
                parsed = parse_active_rules(rules_body)
                # Merge parsed onto baseline. Baseline immutable + bash
                # patterns stay; allow/deny gets concatenated with
                # parsed entries; rule_texts comes only from parsed.
                policy.path.allow.extend(parsed.path.allow)
                policy.path.deny.extend(parsed.path.deny)
                policy.immutable.paths.extend(parsed.immutable.paths)
                policy.bash.patterns.extend(parsed.bash.patterns)
                policy.rule_texts.update(parsed.rule_texts)
            except Exception:
                pass

        latest_sid = await daemon.ledger.latest_session_for_project(ph)
        mode_label = session_mode_for_project(project_path)
        profile = active_profile_for_project(project_path)
        return JSONResponse({
            "project_hash": ph,
            "project_path": project_path,
            "intent_path": str(intent_md),
            "intent_exists": intent_md.exists(),
            "latest_session_id": latest_sid,
            "session_mode": mode_label,
            "mode_profile": {
                "name": profile.name,
                "description": profile.description,
                "is_default": mode_label is None or profile.name == "default",
            },
            "rules": {
                "path": asdict(policy.path),
                "immutable": list(policy.immutable.paths),
                "bash": list(policy.bash.patterns),
                "rule_texts": dict(policy.rule_texts),
            },
        })

    @app.get("/v2/sessions/recent")
    async def v2_recent_sessions(request: Request) -> JSONResponse:
        """Recent sessions across all watched projects, newest first.

        Powers the v0.2 HeaderBar session picker. Cross-project by
        design — the picker is the affordance for jumping between
        sessions when reviewing multiple projects' audit signal.
        Default limit reflects "what fits in a small panel"; raise
        via ?limit= for the Reflection-view sessions table when that
        lands.
        """
        daemon = request.app.state.daemon
        try:
            limit = int(request.query_params.get("limit", "30") or 30)
        except ValueError:
            limit = 30
        rows = await daemon.ledger.recent_sessions(limit=limit)
        return JSONResponse({"sessions": rows})

    @app.get("/probes/recent")
    async def probes_recent(request: Request) -> JSONResponse:
        """Recent probe results across all targets, oldest first.

        Used by the Platform view's polling (we don't push these onto
        the per-session LiveBus because probes are global, not
        session-scoped).
        """
        daemon = request.app.state.daemon
        try:
            limit = int(request.query_params.get("limit", "100") or 100)
        except ValueError:
            limit = 100
        target = request.query_params.get("target") or None
        rows = await daemon.ledger.recent_probe_results(target=target, limit=limit)
        return JSONResponse({"probes": rows})

    @app.get("/p/{ph}/turn-metrics")
    async def project_turn_metrics(
        request: Request, ph: str
    ) -> JSONResponse:
        """Recent per-turn metrics for a project.

        Optional ``model`` query param filters to a single model so the
        Platform view can chart "this model over time" without mixing
        models on the same series.
        """
        daemon = request.app.state.daemon
        model = request.query_params.get("model") or None
        try:
            limit = int(request.query_params.get("limit", "200") or 200)
        except ValueError:
            limit = 200
        rows = await daemon.ledger.turn_metrics_for_project(
            ph, model=model, limit=limit
        )
        return JSONResponse({"metrics": rows})

    @app.get("/p/{ph}/live/{session_id}/state")
    async def live_state(request: Request, ph: str, session_id: str) -> JSONResponse:
        daemon = request.app.state.daemon
        session = await daemon.ledger.get_session(session_id)
        if session is None or session["project_hash"] != ph:
            raise HTTPException(404)
        snapshots = await daemon.ledger.scope_snapshots_for_session(session_id)
        last_snap = snapshots[-1] if snapshots else None
        rubric_rows = await daemon.ledger.rubric_scores_for_session(session_id)
        violations = await daemon.ledger.violations_for_session(session_id)
        report_rows = await daemon.ledger.session_report(session_id)

        # Close-status with resume detection. The close worker stamps
        # session_close.consolidation_status when the session goes idle
        # past ``session_idle_seconds``; that status sticks even if the
        # user comes back and resumes the session (the worker's tick
        # skips already-closed sessions). Without resume awareness, the
        # frontend's closed-session badge would fire for every session
        # that's ever been auto-closed once. Compare last_seen_at vs.
        # closed_at — if the session has activity past the close, treat
        # it as null (live again) for UI purposes.
        close_row = await daemon.ledger.session_close_row(session_id)
        close_status = None
        if close_row:
            closed_at = close_row.get("closed_at")
            last_seen = (session or {}).get("last_seen_at") if session else None
            if closed_at and last_seen and str(last_seen) > str(closed_at):
                close_status = None  # resumed past the close
            else:
                close_status = close_row.get("consolidation_status")

        dim_avgs: dict[str, list[float]] = defaultdict(list)
        for r in rubric_rows:
            dim_avgs[r["dim_name"]].append(float(r["score"]))

        # Resolve the active mode profile for this project so the
        # client can render the mode chip and tune its surfacing
        # (e.g. show "spread" instead of "creep" for non-build modes).
        from ..daemon.mode_profile import (
            active_profile_for_project,
            session_mode_for_project,
        )
        project_path = session.get("project_path") if session else None
        session_mode_label = session_mode_for_project(project_path)
        profile = active_profile_for_project(project_path)

        return JSONResponse({
            "session": session,
            "last_snapshot": last_snap,
            "violations_count": len(violations),
            "rubric_dim_avg": {k: sum(v) / len(v) for k, v in dim_avgs.items()},
            "rubric_samples": len(rubric_rows),
            "report_rows": report_rows,
            "close_status": close_status,
            "session_mode": session_mode_label,
            "mode_profile": {
                "name": profile.name,
                "description": profile.description,
                "scope_creep_floor": profile.scope_creep_floor,
                "scope_event_label": profile.scope_event_label,
                "rubric_dimensions": list(profile.rubric_dimensions),
                "is_default": session_mode_label is None
                    or profile.name == "default",
            },
        })

    @app.get("/p/{ph}/live/{session_id}/replay")
    async def live_replay(request: Request, ph: str, session_id: str) -> JSONResponse:
        daemon = request.app.state.daemon
        cfg = get_config()
        try:
            since_id = int(request.query_params.get("since", "0") or 0)
        except ValueError:
            since_id = 0
        try:
            before_id = int(request.query_params.get("before", "0") or 0)
        except ValueError:
            before_id = 0
        # Three modes:
        #   ?before=N  → backward pagination ("load older"): the
        #                next-older batch with id < N, chronological.
        #   ?since=N (>0) → forward catch-up after a reconnect; events
        #                with id > N, chronological.
        #   else (default) → tail of the session for page-load.
        if before_id > 0:
            rows = await daemon.ledger.live_events_before(
                session_id, before_id=before_id, limit=cfg.live_sse_replay_events
            )
        elif since_id <= 0:
            rows = await daemon.ledger.live_events_recent(
                session_id, limit=cfg.live_sse_replay_events
            )
        else:
            rows = await daemon.ledger.live_events_for_session(
                session_id, since_id=since_id, limit=cfg.live_sse_replay_events
            )
        return JSONResponse({
            "events": [
                {
                    "id": r["id"],
                    "event_type": r["event_type"],
                    "payload": unwrap_stored_payload(r["payload"]),
                    "created_at": r["created_at"],
                }
                for r in rows
            ],
            "next_since": rows[-1]["id"] if rows else since_id,
        })

    @app.get("/p/{ph}/live/{session_id}/arc")
    async def live_arc(request: Request, ph: str, session_id: str) -> JSONResponse:
        """Lightweight per-event triples spanning the full session.

        SessionTimeline fetches this on mount so the arc reflects every
        event recorded for the session, not just the bootstrap window
        the feed replay returns. Payloads are intentionally omitted —
        the strip only needs ``(id, event_type, created_at)`` to render
        ticks, clusters, and idle bands.
        """
        daemon = request.app.state.daemon
        rows = await daemon.ledger.session_arc_triples(session_id)
        return JSONResponse({
            "events": [
                {
                    "id": r["id"],
                    "event_type": r["event_type"],
                    "created_at": r["created_at"],
                }
                for r in rows
            ],
        })

    async def _project_path_for_hash(daemon, ph: str) -> str | None:
        rows = await daemon.ledger.projects_summary()
        match = next((r for r in rows if r["project_hash"] == ph), None)
        return match["project_path"] if match else None

    @app.get("/p/{ph}/live/{session_id}/snapshots")
    async def live_snapshots(request: Request, ph: str, session_id: str) -> JSONResponse:
        """List session-synthesis snapshots written by the synthesis_worker.

        Reads ``~/.modmcp/projects/<ph>/snapshots/*.meta.json`` and filters
        to the requested session. The Session-view ``SnapshotsPanel``
        consumes this for backfill on mount; new captures arrive via the
        ``synthesis_captured`` LiveBus event.

        Query parameter ``limit`` caps the number of returned rows
        (newest-first; default 50, max 1000). The response always
        includes ``total`` so the panel can offer a "show all N"
        expand when the on-disk count exceeds the returned slice.
        """
        import json as _json
        from ..paths import project_dir

        try:
            limit = int(request.query_params.get("limit", "50") or 50)
        except ValueError:
            limit = 50
        limit = max(1, min(limit, 1000))

        daemon = request.app.state.daemon
        project_path = await _project_path_for_hash(daemon, ph)
        if project_path is None:
            raise HTTPException(404, detail="project not found")

        snap_dir = project_dir(project_path) / "snapshots"
        if not snap_dir.exists():
            return JSONResponse({"snapshots": [], "total": 0})

        items: list[dict] = []
        for meta_file in sorted(snap_dir.glob("*.meta.json")):
            try:
                meta = _json.loads(meta_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            if meta.get("session_id") != session_id:
                continue
            items.append({
                "created_at": meta.get("created_at"),
                "trigger": meta.get("trigger"),
                "model": meta.get("model"),
                "fullness_input_tokens": meta.get("fullness_input_tokens"),
                "delta_since_last_snapshot": meta.get("delta_since_last_snapshot"),
            })
        items.sort(key=lambda r: r.get("created_at") or "", reverse=True)
        total = len(items)
        return JSONResponse({"snapshots": items[:limit], "total": total})

    @app.get("/p/{ph}/live/{session_id}/snapshots/{ts}")
    async def live_snapshot_body(
        request: Request, ph: str, session_id: str, ts: str
    ) -> JSONResponse:
        """Return the markdown body + sidecar metadata for one snapshot."""
        import json as _json
        from ..paths import project_dir

        # Reject anything but the canonical timestamp shape so this can't
        # be coaxed into reading arbitrary files via path traversal.
        import re
        if not re.fullmatch(r"\d{8}T\d{6}Z", ts):
            raise HTTPException(400, detail="malformed snapshot timestamp")

        daemon = request.app.state.daemon
        project_path = await _project_path_for_hash(daemon, ph)
        if project_path is None:
            raise HTTPException(404, detail="project not found")

        snap_dir = project_dir(project_path) / "snapshots"
        body_path = snap_dir / f"{ts}.md"
        meta_path = snap_dir / f"{ts}.meta.json"
        if not body_path.exists() or not meta_path.exists():
            raise HTTPException(404, detail="snapshot not found")

        try:
            meta = _json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
        # Belt-and-suspenders: confirm sidecar agrees with the requested
        # session before returning content.
        if meta.get("session_id") != session_id:
            raise HTTPException(404, detail="snapshot not found")
        body = body_path.read_text(encoding="utf-8")
        return JSONResponse({"meta": meta, "body": body})

    @app.post("/p/{ph}/live/{session_id}/synthesize")
    async def live_synthesize(
        request: Request, ph: str, session_id: str
    ) -> JSONResponse:
        """Trigger an on-demand synthesis snapshot for this session.

        Status codes are distinct so the UI can give an honest reason:

        * **200** — snapshot landed; body is its metadata
        * **404** — project not found in the ledger
        * **409** — another synth in flight for this session
        * **502** — LLM call itself failed (details in the live feed
          via the ``synthesis_failed`` event)
        * **503** — synthesis worker / local LLM not configured
        """
        from ..daemon import synthesis_worker as sw

        daemon = request.app.state.daemon
        if getattr(daemon, "synthesis", None) is None:
            raise HTTPException(503, detail="synthesis worker unavailable")
        project_path = await _project_path_for_hash(daemon, ph)
        if project_path is None:
            raise HTTPException(404, detail="project not found")
        try:
            meta = await daemon.synthesis.on_demand(
                session_id=session_id,
                project_hash=ph,
                project_path=project_path,
            )
        except sw.NoLocalLLM:
            raise HTTPException(
                503,
                detail="no local LLM configured — synthesis needs a "
                "qwen-compatible endpoint; configure one in settings",
            )
        except sw.SynthesisInFlight:
            raise HTTPException(
                409,
                detail="another synthesis is already in flight for this "
                "session (likely a periodic capture). Wait for the "
                "synthesis_captured event, then retry.",
            )
        if meta is None:
            raise HTTPException(
                502,
                detail="synthesis call failed — see the live feed for "
                "the underlying error",
            )
        return JSONResponse(meta)
