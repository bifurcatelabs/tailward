"""FastAPI application: hook IPC, health, and (from M9) the web UI."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from ..paths import (
    daemon_log_path,
    ensure_layout,
    intent_path,
    projects_dir,
)
from ..paths import (
    project_hash as hash_path,
)
from ..schema.intent import Intent, load_intent, save_intent
from ..storage.ledger import Ledger
from .state import StateStore
from .watcher import TranscriptWatcher

log = logging.getLogger("modmcp.daemon")


def _setup_logging() -> None:
    ensure_layout()
    handler = logging.FileHandler(daemon_log_path(), encoding="utf-8")
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    if not any(isinstance(h, logging.FileHandler) for h in root.handlers):
        root.addHandler(handler)
    root.setLevel(logging.INFO)


class Daemon:
    def __init__(self) -> None:
        self.state = StateStore()
        self.ledger = Ledger()
        self.watcher: TranscriptWatcher | None = None
        # drift/audit/surface workers are wired in later milestones.
        self.drift = None
        self.audit = None
        self.surface = None
        self.qwen = None


def create_app() -> FastAPI:
    _setup_logging()
    daemon = Daemon()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await daemon.ledger.connect()

        async def on_event(ev, fs):
            # Per-turn hooks for drift/audit are enqueued here starting at M6/M7.
            if daemon.drift is not None and ev.kind == "assistant_message" and fs.session_id:
                await daemon.drift.enqueue(ev, fs)
            if daemon.audit is not None and ev.kind == "assistant_message" and fs.session_id:
                await daemon.audit.enqueue(ev, fs)

        daemon.watcher = TranscriptWatcher(daemon.state, daemon.ledger, on_event=on_event)
        await daemon.watcher.start()

        # Lazy init of M5+ workers if their deps are importable.
        try:
            from .qwen import QwenClient
            daemon.qwen = QwenClient()
        except Exception as e:
            log.warning("qwen client unavailable: %s", e)

        try:
            from .drift import DriftWorker
            daemon.drift = DriftWorker(daemon)
            await daemon.drift.start()
        except Exception as e:
            log.warning("drift worker unavailable: %s", e)

        try:
            from .audit import AuditWorker
            daemon.audit = AuditWorker(daemon)
            await daemon.audit.start()
        except Exception as e:
            log.warning("audit worker unavailable: %s", e)

        try:
            from .surface import Surfacer
            daemon.surface = Surfacer(daemon)
        except Exception as e:
            log.warning("surfacer unavailable: %s", e)

        log.info("modmcp daemon started")
        try:
            yield
        finally:
            if daemon.watcher:
                await daemon.watcher.stop()
            if daemon.drift:
                await daemon.drift.stop()
            if daemon.audit:
                await daemon.audit.stop()
            await daemon.ledger.close()
            log.info("modmcp daemon stopped")

    app = FastAPI(title="modmcp", lifespan=lifespan)
    app.state.daemon = daemon

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "ok": True,
            "sessions": len(daemon.state.all()),
            "ts": datetime.now(UTC).isoformat(),
        }

    @app.post("/hook/userpromptsubmit")
    async def hook_userpromptsubmit(req: Request) -> JSONResponse:
        payload = await req.json()
        cwd = payload.get("cwd") or payload.get("project_root") or ""
        session_id = payload.get("session_id") or payload.get("sessionId")
        raw_prompt = payload.get("prompt") or ""
        if not cwd:
            return JSONResponse({})
        try:
            ip = intent_path(cwd)
            if not ip.exists():
                return JSONResponse({})
            intent: Intent = load_intent(ip)
        except Exception as e:
            log.warning("hook intent load failed: %s", e)
            return JSONResponse({})

        ph = hash_path(cwd)
        session = None
        if session_id:
            session = daemon.state.get(session_id)

        pieces: list[str] = []

        deliver_preamble = False
        if session is None or not session.preamble_delivered:
            deliver_preamble = True

        if deliver_preamble:
            preamble = _build_preamble(intent)
            if preamble:
                pieces.append(preamble)
            if session is not None:
                session.preamble_delivered = True

        if session_id and intent.front.phase2_turns_remaining > 0:
            corrections = await daemon.ledger.drain_corrections(session_id)
            if corrections:
                pieces.append("## Warden corrections (previous turn)")
                pieces.extend(f"- {c}" for c in corrections)

        if session_id and session is not None:
            intent.front.phase2_turns_remaining = max(
                0, intent.front.phase2_turns_remaining - 1
            )
            save_intent(intent, ip)

        if not pieces:
            return JSONResponse({})

        additional_context = "\n\n".join(pieces).strip()
        return JSONResponse(
            {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": additional_context,
                },
                # Also return at top level for hook implementations that prefer it.
                "additionalContext": additional_context,
                "prompt": raw_prompt,
                "ph": ph,
            }
        )

    @app.get("/api/projects")
    async def list_projects() -> list[dict]:
        out: list[dict] = []
        pd = projects_dir()
        if not pd.exists():
            return out
        for sub in sorted(pd.iterdir()):
            if not sub.is_dir():
                continue
            intent_file = sub / "intent.md"
            if not intent_file.exists():
                continue
            try:
                intent = load_intent(intent_file)
                out.append(
                    {
                        "hash": sub.name,
                        "name": intent.front.project_name,
                        "path": intent.front.project_path,
                        "updated": intent.front.updated.isoformat(),
                        "session_mode": intent.front.session_mode,
                        "phase2_turns_remaining": intent.front.phase2_turns_remaining,
                    }
                )
            except Exception as e:
                log.warning("failed to load intent for %s: %s", sub, e)
        return out

    @app.get("/api/projects/{ph}/intent")
    async def api_get_intent(ph: str) -> dict:
        path = _resolve_intent_path(ph)
        intent = load_intent(path)
        return {
            "front": intent.front.model_dump(mode="json"),
            "sections": intent.sections,
        }

    @app.put("/api/projects/{ph}/intent")
    async def api_put_intent(ph: str, body: dict) -> dict:
        path = _resolve_intent_path(ph)
        current = load_intent(path)
        sections = body.get("sections") or {}
        for k, v in sections.items():
            if k in current.sections:
                current.sections[k] = (v or "").rstrip() + "\n"
        # Snapshot before overwrite
        _snapshot(path)
        current.front.updated = datetime.now(UTC)
        save_intent(current, path)
        return {"ok": True}

    @app.get("/api/projects/{ph}/ledger")
    async def api_ledger(ph: str) -> list[dict]:
        return await daemon.ledger.recent_claims(ph)

    @app.get("/api/projects/{ph}/drift")
    async def api_drift(ph: str) -> list[dict]:
        return await daemon.ledger.recent_drift(ph)

    # Mount web UI (M9). Import lazily so M0 boots even if templates missing.
    try:
        from ..web.routes import mount_web

        mount_web(app)
    except Exception as e:
        log.warning("web UI not mounted: %s", e)

    return app


def _resolve_intent_path(ph: str) -> Path:
    target = projects_dir() / ph / "intent.md"
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"no intent for project {ph}")
    return target


def _build_preamble(intent: Intent) -> str:
    parts = ["## Warden preamble (captured intent)"]
    for section in (
        "Receiving Posture",
        "Active Goal",
        "Open Threads",
        "Active Rules",
        "Known Agent Drift Patterns",
        "Known User Drift Patterns",
        "Commitments (pending)",
    ):
        body = (intent.sections.get(section) or "").strip()
        if body:
            parts.append(f"### {section}\n{body}")
    return "\n\n".join(parts)


def _snapshot(intent_file: Path) -> None:
    if not intent_file.exists():
        return
    archive = intent_file.parent / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    target = archive / f"intent-{ts}.md"
    target.write_bytes(intent_file.read_bytes())
