"""Web UI: project list + intent editor + live session view + failure-mode trends.

FastAPI routes mounted onto the main daemon app. Pure server-rendered HTMX
with a single SSE client for the live page. No build step.
"""

from __future__ import annotations

import json
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
from .sse import poll_events, stream_for_session

_WEB_DIR = Path(__file__).parent
_TEMPLATES_DIR = _WEB_DIR / "templates"
_STATIC_DIR = _WEB_DIR / "static"


def mount_web(app: FastAPI) -> None:
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

    def _base_ctx() -> dict:
        return {"warden_mode": get_config().warden_mode}

    if _STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

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
        try:
            since_id = int(request.query_params.get("since", "0") or 0)
        except ValueError:
            since_id = 0
        rows = await daemon.ledger.live_events_for_session(
            session_id, since_id=since_id, limit=500
        )
        return JSONResponse({
            "events": [
                {
                    "id": r["id"],
                    "event_type": r["event_type"],
                    "payload": json.loads(r["payload"]) if r["payload"] else {},
                    "created_at": r["created_at"],
                }
                for r in rows
            ],
            "next_since": rows[-1]["id"] if rows else since_id,
        })
