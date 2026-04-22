"""Web UI: project list + intent editor + ledger/drift views.

FastAPI routes mounted onto the main daemon app. Pure server-rendered HTMX;
no build step.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..paths import projects_dir
from ..schema.intent import SECTIONS, load_intent, save_intent

_TEMPLATES_DIR = Path(__file__).parent / "templates"


def mount_web(app: FastAPI) -> None:
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

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
            request, "index.html", {"projects": projects}
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
            {"ph": ph, "intent": intent, "sections": SECTIONS},
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
        try:
            turns = int(form.get("phase2_turns_remaining") or 0)
            intent.front.phase2_turns_remaining = max(0, turns)
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
            request, "ledger.html", {"ph": ph, "rows": rows}
        )

    @app.get("/p/{ph}/drift", response_class=HTMLResponse)
    async def project_drift(request: Request, ph: str) -> HTMLResponse:
        daemon = request.app.state.daemon
        rows = await daemon.ledger.recent_drift(ph)
        return templates.TemplateResponse(
            request, "drift.html", {"ph": ph, "rows": rows}
        )
