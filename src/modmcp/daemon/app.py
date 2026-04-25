"""FastAPI application: hook IPC, health, and (from M9) the web UI."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from ..config import get_config
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
from .livebus import LiveBus
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
        # v1.1 failure-mode audit layer workers.
        self.constraints = None
        self.scope = None
        self.rubric = None
        self.session_close = None
        # Live event bus; persister is attached after ledger connects.
        cfg = get_config()
        self.live = LiveBus(
            max_subscribers_per_session=cfg.live_sse_max_subscribers_per_session,
        )


def create_app() -> FastAPI:
    _setup_logging()
    daemon = Daemon()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await daemon.ledger.connect()

        async def _persist_live(ev) -> int:
            # Store only the inner payload; the envelope (session_id,
            # project_hash, type, created_at, id) is reconstructed from the
            # row's own columns when serving replays. ``ev.to_json()``
            # bakes in ``ev.id=0`` here because the row id isn't known
            # until after this insert, which broke SSE-replay dedup and
            # the ack/dismiss buttons on the live page.
            import json as _json

            return await daemon.ledger.record_live_event(
                ev.session_id,
                ev.project_hash,
                ev.type,
                _json.dumps(ev.payload, ensure_ascii=False, default=str),
            )

        daemon.live.set_persister(_persist_live)

        async def on_event(ev, fs):
            # Drift only fires on assistant turns (we're judging the assistant's
            # trajectory). Audit fires on BOTH user and assistant turns — user
            # turns often carry strong first-person claims about external
            # state ("I just deleted X") that Warden should check against the
            # actual repo before that context shapes the next assistant turn.
            if daemon.drift is not None and ev.kind == "assistant_message" and fs.session_id:
                await daemon.drift.enqueue(ev, fs)
            if (
                daemon.audit is not None
                and ev.kind in ("assistant_message", "user_message")
                and fs.session_id
            ):
                await daemon.audit.enqueue(ev, fs)

            # v1.1 failure-mode workers
            if fs.session_id and fs.project_hash:
                if daemon.constraints is not None and ev.kind in (
                    "tool_use",
                    "assistant_message",
                ):
                    await daemon.constraints.enqueue(ev, fs)
                if daemon.scope is not None and ev.kind in (
                    "tool_use",
                    "assistant_message",
                ):
                    await daemon.scope.enqueue(ev, fs)
                if daemon.rubric is not None and ev.kind == "assistant_message":
                    await daemon.rubric.enqueue(ev, fs)

            # Publish turn-level markers to the live bus so the web feed
            # sees activity even without worker findings. Fires at most
            # once per logical turn (per message_id), but deferred past
            # any leading ``thinking`` blocks so the feed entry's
            # preview reflects real prose or a tool action \u2014 not an
            # empty header. Tool-only turns still fire (with empty
            # preview) so the right-rail token totals advance.
            st = daemon.state.get(fs.session_id) if fs.session_id else None
            already_emitted = (
                st.last_turn_emit_msg_id if st else None
            )
            should_publish_turn = (
                fs.session_id
                and fs.project_hash
                and ev.kind == "assistant_message"
                and ev.message_id
                and ev.message_id != already_emitted
                and (_has_visible_text(ev) or ev.tool_name is not None)
            )
            if should_publish_turn:
                # Pull text from ``text`` content blocks specifically so
                # the preview never shows the synthetic
                # "[tool_use:Edit]" marker that ``_extract_text`` emits
                # for tool_use blocks.
                visible = _visible_text(ev)
                preview = visible.strip().replace("\r", "")
                if len(preview) > 280:
                    preview = preview[:280] + "\u2026"
                payload: dict[str, Any] = {
                    "turn_idx": st.turns_seen if st else 0,
                    "text_preview": preview,
                    "chars": len(visible),
                    "message_id": ev.message_id,
                }
                # Skip Claude Code's synthetic-model marker (compaction,
                # system summarization) so the model badge tracks real
                # assistant turns only.
                if ev.model and ev.model != "<synthetic>":
                    payload["model"] = ev.model
                if ev.stop_reason:
                    payload["stop_reason"] = ev.stop_reason
                if ev.usage:
                    payload["usage"] = {
                        "input_tokens": int(ev.usage.get("input_tokens") or 0),
                        "output_tokens": int(ev.usage.get("output_tokens") or 0),
                        "cache_read_input_tokens": int(
                            ev.usage.get("cache_read_input_tokens") or 0
                        ),
                        "cache_creation_input_tokens": int(
                            ev.usage.get("cache_creation_input_tokens") or 0
                        ),
                    }
                if st is not None:
                    payload["totals"] = {
                        "input_tokens": st.total_input_tokens,
                        "output_tokens": st.total_output_tokens,
                        "cache_read_input_tokens": st.total_cache_read_tokens,
                        "cache_creation_input_tokens": st.total_cache_creation_tokens,
                    }
                await daemon.live.publish(
                    fs.session_id,
                    fs.project_hash,
                    "turn",
                    payload,
                )
                if st is not None:
                    st.last_turn_emit_msg_id = ev.message_id
            # User-prompt markers. Claude Code wraps tool *results* as
            # user_message events too (content blocks of type
            # ``tool_result``); those are tool output, not human prompts,
            # so filter them out before publishing.
            if (
                fs.session_id
                and fs.project_hash
                and ev.kind == "user_message"
                and _looks_like_human_prompt(ev)
            ):
                preview = (ev.text or "").strip().replace("\r", "")
                if len(preview) > 280:
                    preview = preview[:280] + "…"
                if preview:
                    await daemon.live.publish(
                        fs.session_id,
                        fs.project_hash,
                        "user_turn",
                        {
                            "text_preview": preview,
                            "chars": len(ev.text or ""),
                        },
                    )

            # Tool-call markers fire whenever a tool_use is present, whether
            # the event is a bare ``tool_use`` or an assistant message that
            # wraps the block in its content list. Real Claude Code only
            # emits the latter.
            if fs.session_id and fs.project_hash and ev.tool_name:
                await daemon.live.publish(
                    fs.session_id,
                    fs.project_hash,
                    "tool_call",
                    {
                        "tool": ev.tool_name,
                        "input_preview": _shorten_tool_input(ev.tool_input),
                    },
                )

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

        try:
            from .constraints_worker import ConstraintsWorker
            daemon.constraints = ConstraintsWorker(daemon)
            await daemon.constraints.start()
        except Exception as e:
            log.warning("constraints worker unavailable: %s", e)

        try:
            from .scope_worker import ScopeWorker
            daemon.scope = ScopeWorker(daemon)
            await daemon.scope.start()
        except Exception as e:
            log.warning("scope worker unavailable: %s", e)

        try:
            from .rubric_worker import RubricWorker
            daemon.rubric = RubricWorker(daemon)
            await daemon.rubric.start()
        except Exception as e:
            log.warning("rubric worker unavailable: %s", e)

        try:
            from .session_close import SessionCloseDetector
            daemon.session_close = SessionCloseDetector(daemon)
            await daemon.session_close.start()
        except Exception as e:
            log.warning("session-close detector unavailable: %s", e)

        log.info("modmcp daemon started (mode=%s)", get_config().warden_mode)
        try:
            yield
        finally:
            if daemon.watcher:
                await daemon.watcher.stop()
            if daemon.drift:
                await daemon.drift.stop()
            if daemon.audit:
                await daemon.audit.stop()
            if daemon.constraints:
                await daemon.constraints.stop()
            if daemon.scope:
                await daemon.scope.stop()
            if daemon.rubric:
                await daemon.rubric.stop()
            if daemon.session_close:
                await daemon.session_close.stop()
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

        # Passive mode: daemon still receives the POST (so Claude Code
        # doesn't error on hook configuration) but injects nothing. All
        # observability goes through the web UI instead.
        if get_config().warden_mode == "passive":
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


def _visible_text(ev) -> str:
    """Concatenate ``text`` content blocks for an assistant/user event.

    ``ev.text`` from :func:`parse_line` includes synthetic markers like
    ``[tool_use:Edit]`` and the bodies of ``tool_result`` blocks, which
    we do not want as user-facing previews. This helper pulls only the
    real ``text`` blocks so the live feed shows actual prose.
    """
    raw_msg = ev.raw.get("message") if isinstance(ev.raw, dict) else None
    if not isinstance(raw_msg, dict):
        return ev.text or ""
    content = raw_msg.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            t = block.get("text") or ""
            if t:
                parts.append(t)
    return "\n".join(parts)


def _has_visible_text(ev) -> bool:
    return bool(_visible_text(ev).strip())


def _looks_like_human_prompt(ev) -> bool:
    """True when an event's user_message wraps actual prompt text.

    Claude Code reuses ``type=user`` for tool results — the content list
    carries ``tool_result`` blocks rather than plain text. Those should
    not appear in the live feed as if the human typed them. We treat an
    event as a real prompt when its message.content has at least one
    text block (or is a bare string), and isn't dominated by tool
    results.
    """
    raw_msg = ev.raw.get("message") if isinstance(ev.raw, dict) else None
    if not isinstance(raw_msg, dict):
        return bool((ev.text or "").strip())
    content = raw_msg.get("content")
    if isinstance(content, str):
        return bool(content.strip())
    if not isinstance(content, list):
        return False
    has_text = False
    has_tool_result = False
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text" and (block.get("text") or "").strip():
            has_text = True
        elif btype == "tool_result":
            has_tool_result = True
    if has_text:
        return True
    return not has_tool_result and bool((ev.text or "").strip())


def _shorten_tool_input(tool_input: dict[str, Any] | None) -> str:
    """Compact preview of tool args for the live feed (no full file bodies)."""
    if not tool_input:
        return ""
    pieces: list[str] = []
    for key in ("file_path", "path", "command", "pattern", "query", "url"):
        val = tool_input.get(key)
        if isinstance(val, str) and val:
            pieces.append(f"{key}={val[:120]}")
            break
    if not pieces:
        keys = ", ".join(sorted(tool_input.keys())[:4])
        pieces.append(f"keys=[{keys}]")
    return " ".join(pieces)
