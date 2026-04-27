"""Web UI: project list + intent editor + live session view (v0.2 Svelte) +
the JSON endpoints the live + Reflection + Platform views read.

FastAPI routes mounted onto the main daemon app. The live audit surface
is the v0.2 Svelte chassis served at ``/p/<ph>/live/<sid>/v2``. The
legacy v1.1 Jinja audit pages (``live.html``, ``violations.html``,
``trends.html``, ``session_report.html``, ``ledger.html``,
``drift.html``, ``no_session.html``) and their routes were retired in
v2.0.0; cross-session aggregation lives in the Reflection view now.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..config import get_config
from ..paths import projects_dir
from ..schema.intent import SECTIONS, load_intent, save_intent
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

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        projects = []
        pd = projects_dir()
        if pd.exists():
            for sub in sorted(pd.iterdir()):
                intent_file = sub / "intent.md"
                if not intent_file.exists():
                    continue
                try:
                    intent = load_intent(intent_file)
                    projects.append(
                        {
                            "hash": sub.name,
                            "name": intent.front.project_name,
                            "path": intent.front.project_path,
                            "updated": intent.front.updated.isoformat(),
                            "mode": intent.front.session_mode,
                            "phase2": intent.front.phase2_turns_remaining,
                        }
                    )
                except Exception:
                    continue
        return templates.TemplateResponse(
            request, "index.html", {**_base_ctx(), "projects": projects}
        )

    @app.get("/p/{ph}", response_class=HTMLResponse)
    async def project_view(request: Request, ph: str) -> HTMLResponse:
        intent_file = projects_dir() / ph / "intent.md"
        if not intent_file.exists():
            raise HTTPException(404)
        intent = load_intent(intent_file)
        return templates.TemplateResponse(
            request,
            "intent.html",
            {**_base_ctx(), "ph": ph, "intent": intent, "sections": SECTIONS},
        )

    @app.post("/p/{ph}/save", response_class=HTMLResponse)
    async def project_save(request: Request, ph: str) -> HTMLResponse:
        intent_file = projects_dir() / ph / "intent.md"
        if not intent_file.exists():
            raise HTTPException(404)
        intent = load_intent(intent_file)
        form = await request.form()
        for section in SECTIONS:
            val = form.get(f"section[{section}]")
            if val is not None:
                intent.sections[section] = (val or "").rstrip() + "\n"
        mode = form.get("session_mode")
        if mode in ("build", "meta", "exploration"):
            intent.front.session_mode = mode  # type: ignore[assignment]
        raw_turns = form.get("phase2_turns_remaining")
        if raw_turns is not None and raw_turns != "":
            try:
                intent.front.phase2_turns_remaining = max(0, int(raw_turns))
            except ValueError:
                pass
        intent.front.updated = datetime.now(UTC)
        save_intent(intent, intent_file)
        return HTMLResponse(
            "<span style='color:green'>saved</span>", status_code=200
        )

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

    @app.get("/p/{ph}/live/{session_id}/v2")
    async def live_session_v2_legacy(ph: str, session_id: str) -> RedirectResponse:
        """Backward-compat redirect: the Svelte chassis used to live at
        ``/v2``; in v2.0.0 it became the default. 308 keeps any bookmarks
        working without a content-type ambiguity."""
        return RedirectResponse(url=f"/p/{ph}/live/{session_id}", status_code=308)

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
        """Derived signals for the Reflection view — no LLM calls.

        Four panels' worth of data, computed from existing live_events,
        constraint_violations, and verification_ledger rows:

        * ``intervals`` — seconds between consecutive *typed* user turns.
          Synthesized turns (Claude Code /compact) are excluded so the
          distribution reflects actual user pacing.
        * ``prompt_lengths`` — char counts of typed user prompts.
        * ``approvals`` — counts of constraint_violations by status
          (new / acknowledged / dismissed). Speaks to the user's
          response cadence on Warden's destructive-action surfacings.
        * ``verification`` — counts of verification_ledger rows by
          status (verified / contradicted / unverifiable). Speaks to
          how often the assistant's first-person completion claims
          held up under grep-based verification.
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
        verification = await daemon.ledger.claim_status_counts(ph)

        return JSONResponse({
            "intervals_seconds": intervals,
            "prompt_lengths_chars": prompt_lengths,
            "synthesized_user_turns": synthesized_count,
            "approvals": approvals,
            "verification": verification,
            "sample_size": len(rows),
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
        close_status = await daemon.ledger.session_close_status(session_id)

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
