"""Web UI: project list + intent editor + live session view + failure-mode trends.

FastAPI routes mounted onto the main daemon app. Pure server-rendered HTMX
with a single SSE client for the live page. No build step.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
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

    @app.get("/p/{ph}/ledger", response_class=HTMLResponse)
    async def project_ledger(request: Request, ph: str) -> HTMLResponse:
        daemon = request.app.state.daemon
        rows = await daemon.ledger.recent_claims(ph)
        return templates.TemplateResponse(
            request, "ledger.html", {**_base_ctx(), "ph": ph, "rows": rows}
        )

    @app.get("/p/{ph}/drift", response_class=HTMLResponse)
    async def project_drift(request: Request, ph: str) -> HTMLResponse:
        daemon = request.app.state.daemon
        rows = await daemon.ledger.recent_drift(ph)
        return templates.TemplateResponse(
            request, "drift.html", {**_base_ctx(), "ph": ph, "rows": rows}
        )

    # ------------------------------------------------------------------
    # Live session view (v1.1 failure-mode audit UI anchor)
    # ------------------------------------------------------------------

    @app.get("/p/{ph}/live", response_class=HTMLResponse)
    async def live_redirect(request: Request, ph: str) -> HTMLResponse:
        daemon = request.app.state.daemon
        sid = await daemon.ledger.latest_session_for_project(ph)
        if not sid:
            return templates.TemplateResponse(
                request, "no_session.html", {**_base_ctx(), "ph": ph}
            )
        return await live_session(request, ph, sid)

    @app.get("/p/{ph}/live/{session_id}", response_class=HTMLResponse)
    async def live_session(request: Request, ph: str, session_id: str) -> HTMLResponse:
        daemon = request.app.state.daemon
        session = await daemon.ledger.get_session(session_id)
        if session is None or session["project_hash"] != ph:
            raise HTTPException(404)
        close_status = await daemon.ledger.session_close_status(session_id)
        # The session_close row records when the consolidator ran, but the
        # session may resume writing afterwards (long-idle then back).
        # Treat the close status as stale if last_seen_at is newer than
        # the consolidation timestamp; show "active" in that case so the
        # badge reflects current liveness, not yesterday's report state.
        if close_status == "done":
            close_row = await daemon.ledger.session_close_row(session_id)
            last_seen = session.get("last_seen_at") if session else None
            closed_at = close_row.get("closed_at") if close_row else None
            if last_seen and closed_at and str(last_seen) > str(closed_at):
                close_status = "active"
        report_rows = await daemon.ledger.session_report(session_id)
        return templates.TemplateResponse(
            request,
            "live.html",
            {
                **_base_ctx(),
                "ph": ph,
                "session": session,
                "session_id": session_id,
                "close_status": close_status or ("idle" if session else None),
                "report_rows": report_rows,
            },
        )

    @app.get("/p/{ph}/live/{session_id}/v2", response_class=HTMLResponse)
    async def live_session_v2(request: Request, ph: str, session_id: str) -> HTMLResponse:
        """v0.2 chassis surface — Svelte bundle takes over from ``#app``.

        Lives alongside ``/live/{session_id}`` (the production v1.1
        surface) so v0.2 work doesn't disrupt active dogfooding. When
        the v2 surface fully covers v1.1's features the legacy template
        and ``live.js`` get retired in one pass.
        """
        daemon = request.app.state.daemon
        session = await daemon.ledger.get_session(session_id)
        if session is None or session["project_hash"] != ph:
            raise HTTPException(404)
        bundle_js, bundle_css = _resolve_v2_bundle()
        return templates.TemplateResponse(
            request,
            "live_v2.html",
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

    @app.get("/p/{ph}/violations", response_class=HTMLResponse)
    async def violations_page(request: Request, ph: str) -> HTMLResponse:
        daemon = request.app.state.daemon
        rows = await daemon.ledger.recent_violations(ph, limit=200)
        return templates.TemplateResponse(
            request, "violations.html", {**_base_ctx(), "ph": ph, "rows": rows}
        )

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
    # Session permalink + trends
    # ------------------------------------------------------------------

    @app.get("/p/{ph}/sessions/{session_id}", response_class=HTMLResponse)
    async def session_report_page(
        request: Request, ph: str, session_id: str
    ) -> HTMLResponse:
        daemon = request.app.state.daemon
        session = await daemon.ledger.get_session(session_id)
        if session is None or session["project_hash"] != ph:
            raise HTTPException(404)
        report_rows = await daemon.ledger.session_report(session_id)
        violations = await daemon.ledger.violations_for_session(session_id)
        rubric_rows = await daemon.ledger.rubric_scores_for_session(session_id)
        return templates.TemplateResponse(
            request,
            "session_report.html",
            {
                **_base_ctx(),
                "ph": ph,
                "session": session,
                "session_id": session_id,
                "report_rows": report_rows,
                "violations": violations,
                "rubric_rows": rubric_rows,
            },
        )

    @app.get("/p/{ph}/trends", response_class=HTMLResponse)
    async def trends_page(request: Request, ph: str) -> HTMLResponse:
        daemon = request.app.state.daemon
        rows = await daemon.ledger.recent_session_reports(ph, limit=20)
        by_mode: dict[int, list[dict]] = defaultdict(list)
        by_session: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            by_mode[int(r["mode_id"])].append(r)
            by_session[r["session_id"]].append(r)

        violations = await daemon.ledger.recent_violations(ph, limit=200)
        vcount: dict[str, int] = defaultdict(int)
        for v in violations:
            vcount[v["rule_id"]] += 1
        top_rules = sorted(vcount.items(), key=lambda kv: -kv[1])[:10]

        return templates.TemplateResponse(
            request,
            "trends.html",
            {
                **_base_ctx(),
                "ph": ph,
                "by_mode": by_mode,
                "by_session": by_session,
                "top_rules": top_rules,
                "rule_texts": {v["rule_id"]: v["rule_text"] for v in violations},
            },
        )

    # ------------------------------------------------------------------
    # JSON helpers for live view initial paint
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # v0.2 Platform endpoints — global telemetry not scoped to a session
    # ------------------------------------------------------------------

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

        return JSONResponse({
            "session": session,
            "last_snapshot": last_snap,
            "violations_count": len(violations),
            "rubric_dim_avg": {k: sum(v) / len(v) for k, v in dim_avgs.items()},
            "rubric_samples": len(rubric_rows),
            "report_rows": report_rows,
            "close_status": close_status,
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
