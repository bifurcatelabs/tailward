"""FastAPI application: hook IPC, health, and (from M9) the web UI."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException

from ..config import get_config, is_loopback_bind
from ..paths import (
    daemon_log_path,
    ensure_layout,
    projects_dir,
)
from ..schema import exfiltration
from ..schema.intent import load_intent, save_intent
from ..storage.ledger import Ledger
from .livebus import LiveBus
from .state import StateStore
from .watcher import TranscriptWatcher

log = logging.getLogger("tailward.daemon")


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
        self.user_rubric = None
        self.session_close = None
        # v0.2 platform probe worker.
        self.probe = None
        # v2.6 session-synthesis stream worker.
        self.synthesis = None
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
            # Exfiltration helper: scans any text that's about to land
            # in live_events.payload, emits an exfiltration_alert per
            # match, and returns the redacted form. Wrapping every
            # publish site means secrets in *any* observed channel
            # (typed prompts, assistant responses, tool inputs,
            # away-summary recaps, /compact synthesis) get caught and
            # sanitized before storage. ``extra`` flows into the alert
            # payload (e.g., the source tool name on tool_call leaks).
            async def _check_leaks(
                text, source_event_type, **extra
            ) -> tuple[str, list[str]]:
                """Scan ``text`` for known secret patterns.

                Returns ``(redacted_text, leaks)`` where ``leaks`` is the
                list of matched pattern names (empty if none). Callers
                attach the list to the source event's payload as
                ``secrets_redacted`` so the FeedItem can render a
                sub-badge marking *which* turn the secret originated in,
                not just the standalone alert event the dashboard also
                receives.
                """
                if not text or not fs.session_id or not fs.project_hash:
                    return text, []
                matches = exfiltration.scan(text)
                if not matches:
                    return text, []
                for m in matches:
                    await daemon.live.publish(
                        fs.session_id,
                        fs.project_hash,
                        "exfiltration_alert",
                        {
                            "pattern": m.pattern_name,
                            "redacted": m.redacted_preview,
                            "source_event_type": source_event_type,
                            **extra,
                        },
                    )
                return (
                    exfiltration.redact(text, matches),
                    [m.pattern_name for m in matches],
                )

            # In-flight turn tracking for per-turn inference-path metrics.
            # We close the in-flight turn at two transition points: (a)
            # a new logical assistant turn starts (different message_id),
            # and (b) a user_message arrives (the human responded, so
            # any pending assistant turn is done). Mid-turn assistant
            # blocks just extend the last_block_at timestamp.
            st = daemon.state.get(fs.session_id) if fs.session_id else None
            if st is not None:
                if ev.kind == "assistant_message":
                    if ev.new_turn:
                        await _close_turn_metric(daemon, st, fs)
                        if (
                            ev.message_id
                            and ev.model
                            and ev.model != "<synthetic>"
                        ):
                            st.in_flight_turn = {
                                "message_id": ev.message_id,
                                "first_block_at": ev.timestamp,
                                "last_block_at": ev.timestamp,
                                "model": ev.model,
                                "stop_reason": ev.stop_reason,
                                "usage": dict(ev.usage) if ev.usage else None,
                                "turn_idx": st.turns_seen,
                                "prompt_to_response_ms": _ms_between(
                                    st.last_user_msg_at, ev.timestamp
                                ),
                            }
                    elif st.in_flight_turn and ev.message_id == st.in_flight_turn.get(
                        "message_id"
                    ):
                        if ev.timestamp:
                            st.in_flight_turn["last_block_at"] = ev.timestamp
                        # Usage / stop_reason are duplicated across blocks
                        # of one logical turn; the most recent values win.
                        if ev.usage:
                            st.in_flight_turn["usage"] = dict(ev.usage)
                        if ev.stop_reason:
                            st.in_flight_turn["stop_reason"] = ev.stop_reason
                elif ev.kind == "user_message":
                    await _close_turn_metric(daemon, st, fs)
                    if ev.timestamp:
                        st.last_user_msg_at = ev.timestamp

            # Drift only fires on assistant turns (we're judging the assistant's
            # trajectory). Audit fires on BOTH user and assistant turns — user
            # turns often carry strong first-person claims about external
            # state ("I just deleted X") that tailward should check against the
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
                if (
                    getattr(daemon, "user_rubric", None) is not None
                    and ev.kind == "user_message"
                    and not ev.synthesized
                    and _looks_like_human_prompt(ev)
                ):
                    await daemon.user_rubric.enqueue(ev, fs)
                if (
                    getattr(daemon, "synthesis", None) is not None
                    and ev.kind in (
                        "assistant_message",
                        "user_message",
                        "tool_use",
                        "tool_result",
                    )
                ):
                    await daemon.synthesis.enqueue(ev, fs)

            # Publish turn-level markers to the live bus so the web feed
            # sees activity even without worker findings. Fires at most
            # once per logical turn (per message_id), but deferred past
            # any leading ``thinking`` blocks so the feed entry's
            # preview reflects real prose or a tool action \u2014 not an
            # empty header. Tool-only turns still fire (with empty
            # preview) so the right-rail token totals advance.
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
                # Send full prose; the feed UI is the audit surface and
                # truncating here defeats the point. CSS in live.html
                # caps the rendered height with overflow:auto so a
                # multi-screen response doesn't blow out the feed
                # column \u2014 the data is still all there.
                preview = visible.replace("\r", "").rstrip()
                preview, leaks = await _check_leaks(preview, "turn")
                payload: dict[str, Any] = {
                    "turn_idx": st.turns_seen if st else 0,
                    "text_preview": preview,
                    "chars": len(visible),
                    "message_id": ev.message_id,
                }
                if leaks:
                    payload["secrets_redacted"] = leaks
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
            #
            # Tool-emitted synthesized turns (Claude Code's /compact
            # persists its summary as ``type: "user"`` with
            # ``isCompactSummary: true``) are surfaced as a distinct
            # event type so the feed/timeline can render them as
            # synthesized rather than treating them as the user typing.
            if (
                fs.session_id
                and fs.project_hash
                and ev.kind == "user_message"
                and _looks_like_human_prompt(ev)
            ):
                preview = (ev.text or "").replace("\r", "").rstrip()
                if preview.strip():
                    if ev.synthesized and ev.synthesis_kind == "compact_summary":
                        preview, leaks = await _check_leaks(preview, "compact_summary")
                        cs_payload: dict[str, Any] = {
                            "text_preview": preview,
                            "chars": len(ev.text or ""),
                            "source": "claude_code_compact",
                        }
                        if leaks:
                            cs_payload["secrets_redacted"] = leaks
                        await daemon.live.publish(
                            fs.session_id,
                            fs.project_hash,
                            "compact_summary",
                            cs_payload,
                        )
                    else:
                        preview, leaks = await _check_leaks(preview, "user_turn")
                        ut_payload: dict[str, Any] = {
                            "text_preview": preview,
                            "chars": len(ev.text or ""),
                        }
                        if leaks:
                            ut_payload["secrets_redacted"] = leaks
                        await daemon.live.publish(
                            fs.session_id,
                            fs.project_hash,
                            "user_turn",
                            ut_payload,
                        )

            # Tool-call markers fire whenever a tool_use is present, whether
            # the event is a bare ``tool_use`` or an assistant message that
            # wraps the block in its content list. Real Claude Code only
            # emits the latter. ``permission_mode`` falls back to the
            # carry-forward FileState value because assistant events
            # (where tool_use blocks live) do NOT carry the
            # ``permissionMode`` field directly — only dedicated
            # ``permission-mode`` events and a fraction of user events
            # do. Without the fallback, every tool_call would tag as
            # null and the Reflection-view matrix would show every
            # call under the "untagged" column. The tool_use_id → name
            # mapping is cached so a later interrupted tool_result can
            # resolve the original tool name.
            #
            # Exfiltration scan: before publishing, scan the input
            # preview for known secret patterns. Any match redacts the
            # secret in the published payload (so live_events.payload
            # never stores the cleartext) and emits a separate
            # exfiltration_alert event surfacing the leak in the live
            # feed. See ``tailward.schema.exfiltration``.
            if fs.session_id and fs.project_hash and ev.tool_name:
                mode_at_call = ev.permission_mode or fs.last_permission_mode
                input_preview, leaks = await _check_leaks(
                    _shorten_tool_input(ev.tool_input),
                    "tool_call",
                    tool=ev.tool_name,
                )
                tc_payload: dict[str, Any] = {
                    "tool": ev.tool_name,
                    "input_preview": input_preview,
                    "permission_mode": mode_at_call,
                }
                if leaks:
                    tc_payload["secrets_redacted"] = leaks
                await daemon.live.publish(
                    fs.session_id,
                    fs.project_hash,
                    "tool_call",
                    tc_payload,
                )
                if ev.tool_use_id:
                    fs.tool_use_names[ev.tool_use_id] = ev.tool_name
                    # Bound the cache so a long session doesn't grow
                    # unbounded. Tool results almost always arrive
                    # within a few events of the emit, so 256 entries
                    # is comfortably more than needed.
                    if len(fs.tool_use_names) > 256:
                        # FIFO eviction: drop the oldest 32 entries
                        # in one pass to amortize the cost.
                        oldest = list(fs.tool_use_names.keys())[:32]
                        for k in oldest:
                            fs.tool_use_names.pop(k, None)

            # Tool interrupted: the user declined or interrupted a tool
            # call. Claude Code flags this on the tool_result event via
            # ``toolUseResult.interrupted: true`` at the JSONL top level.
            # The closest signal Claude Code exposes to an explicit
            # "user denied" decision; rare in practice but a real audit
            # signal when it happens. Tool name resolved via the cached
            # map populated on the original emit.
            if (
                fs.session_id
                and fs.project_hash
                and ev.interrupted
                and ev.tool_use_id
            ):
                tool_name = fs.tool_use_names.pop(ev.tool_use_id, None)
                # Same fallback as the tool_call branch above — the
                # tool_result event is wrapped as a user message and
                # often does not carry permissionMode directly.
                mode_at_interrupt = ev.permission_mode or fs.last_permission_mode
                await daemon.live.publish(
                    fs.session_id,
                    fs.project_hash,
                    "tool_interrupted",
                    {
                        "tool": tool_name,
                        "tool_use_id": ev.tool_use_id,
                        "permission_mode": mode_at_interrupt,
                    },
                )

            # Permission-mode transitions. Claude Code carries
            # ``permissionMode`` on most events and emits dedicated
            # ``type: "permission-mode"`` events when the user changes
            # the mode (Shift+Tab cycles through default / acceptEdits /
            # bypassPermissions / plan). We surface a feed event only on
            # actual transitions — first-time-set after watcher init or
            # daemon restart is treated as initialization, not a change.
            if (
                fs.session_id
                and fs.project_hash
                and ev.permission_mode is not None
                and fs.last_permission_mode is not None
                and ev.permission_mode != fs.last_permission_mode
            ):
                await daemon.live.publish(
                    fs.session_id,
                    fs.project_hash,
                    "permission_mode_change",
                    {
                        "mode": ev.permission_mode,
                        "previous_mode": fs.last_permission_mode,
                    },
                )
            if ev.permission_mode is not None:
                fs.last_permission_mode = ev.permission_mode

            # Away-summary captures. Claude Code emits these as
            # ``type: "system"`` with ``subtype: "away_summary"`` when
            # it observes the user has stepped away — the ``content``
            # carries a structured recap (goal / current task / next
            # action) for when the user returns. We surface them in
            # the live feed so the session timeline reflects when the
            # user was actively driving vs idle. No carry-forward
            # state — every away_summary is independent.
            if (
                fs.session_id
                and fs.project_hash
                and ev.kind == "system"
                and ev.raw.get("subtype") == "away_summary"
            ):
                content = (ev.text or str(ev.raw.get("content", ""))).strip()
                if content:
                    content, leaks = await _check_leaks(content, "away_summary")
                    as_payload: dict[str, Any] = {"content": content}
                    if leaks:
                        as_payload["secrets_redacted"] = leaks
                    await daemon.live.publish(
                        fs.session_id,
                        fs.project_hash,
                        "away_summary",
                        as_payload,
                    )

        cfg = get_config()
        daemon.watcher = TranscriptWatcher(
            daemon.state,
            daemon.ledger,
            on_event=on_event,
            watch_paths=cfg.watch_paths,
            exclude_paths=cfg.exclude_paths,
        )
        await daemon.watcher.start()

        # Lazy init of M5+ workers if their deps are importable.
        try:
            from .qwen import QwenClient
            daemon.qwen = QwenClient()
            # Bridge in the metrics recorder. The QwenClient runs LLM
            # calls from a worker thread (via ``asyncio.to_thread``);
            # the recorder needs a reference to this event loop to
            # post the aiosqlite write back from that thread.
            daemon.qwen.attach_recorder(
                daemon.ledger, asyncio.get_running_loop()
            )
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
            from .user_rubric_worker import UserRubricWorker
            daemon.user_rubric = UserRubricWorker(daemon)
            await daemon.user_rubric.start()
        except Exception as e:
            log.warning("user rubric worker unavailable: %s", e)

        try:
            from .session_close import SessionCloseDetector
            daemon.session_close = SessionCloseDetector(daemon)
            await daemon.session_close.start()
        except Exception as e:
            log.warning("session-close detector unavailable: %s", e)

        try:
            from .probe_worker import ProbeWorker
            daemon.probe = ProbeWorker(daemon)
            await daemon.probe.start()
        except Exception as e:
            log.warning("probe worker unavailable: %s", e)

        try:
            from .synthesis_worker import SynthesisWorker
            daemon.synthesis = SynthesisWorker(daemon)
            await daemon.synthesis.start()
        except Exception as e:
            log.warning("synthesis worker unavailable: %s", e)

        log.info("tailward daemon started")
        cfg = get_config()
        if not is_loopback_bind(cfg.http_host):
            log.warning(
                "SECURITY: tailward is bound to %s, not loopback. "
                "Every device that can reach this port can read all "
                "sessions, intent files, drift verdicts, and captured "
                "exfiltration alerts. tailward has no authentication. "
                "Recommended: bind to 127.0.0.1 and use SSH or "
                "WireGuard tunnel forwarding for cross-device access. "
                "See README 'Network exposure' section.",
                cfg.http_host,
            )
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
            if getattr(daemon, "user_rubric", None):
                await daemon.user_rubric.stop()
            if daemon.session_close:
                await daemon.session_close.stop()
            if daemon.probe:
                await daemon.probe.stop()
            if getattr(daemon, "synthesis", None):
                await daemon.synthesis.stop()
            await daemon.ledger.close()
            log.info("tailward daemon stopped")

    app = FastAPI(title="tailward", lifespan=lifespan)
    app.state.daemon = daemon

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "ok": True,
            "sessions": len(daemon.state.all()),
            "ts": datetime.now(UTC).isoformat(),
        }

    @app.get("/api/bind-info")
    async def bind_info() -> dict[str, Any]:
        """Surface the daemon's HTTP binding for the Settings view.

        Returns a non-null ``warning`` when the bind exposes the
        unauthenticated audit surface beyond loopback. UI renders this
        as a banner; a daemon-side log warning fires at startup with
        the same message in less compressed form.
        """
        cfg = get_config()
        is_loopback = is_loopback_bind(cfg.http_host)
        warning = None
        if not is_loopback:
            warning = (
                f"tailward is bound to {cfg.http_host} — every device that "
                "can reach this port can read all sessions, intent files, "
                "drift verdicts, and captured events. tailward has no "
                "authentication. Recommended: bind to 127.0.0.1 and use "
                "SSH or WireGuard tunnel forwarding for cross-device access."
            )
        return {
            "http_host": cfg.http_host,
            "http_port": cfg.http_port,
            "is_loopback": is_loopback,
            "warning": warning,
        }

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


def _snapshot(intent_file: Path) -> None:
    if not intent_file.exists():
        return
    archive = intent_file.parent / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    target = archive / f"intent-{ts}.md"
    target.write_bytes(intent_file.read_bytes())


def _ms_between(start, end) -> int | None:
    """Whole milliseconds from ``start`` to ``end`` (datetimes), or
    ``None`` if either is missing. Negative deltas are clamped to None
    so clock skew can't poison metrics."""
    if start is None or end is None:
        return None
    try:
        delta = (end - start).total_seconds() * 1000.0
    except Exception:
        return None
    if delta < 0:
        return None
    return int(delta)


async def _close_turn_metric(daemon, st, fs) -> None:
    """If a turn is in-flight, persist its derived metrics to the
    ledger and publish a ``turn_metric`` LiveBus event. Idempotent —
    safe to call when no turn is in-flight."""
    turn = st.in_flight_turn
    if not turn:
        return
    st.in_flight_turn = None

    first = turn.get("first_block_at")
    last = turn.get("last_block_at")
    response_duration_ms = _ms_between(first, last)

    usage = turn.get("usage") or {}
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    cache_read = int(usage.get("cache_read_input_tokens") or 0)
    cache_create = int(usage.get("cache_creation_input_tokens") or 0)

    # Output tokens-per-second: noisy at small durations, but the
    # trend is what matters. Skip when we don't have a meaningful
    # window — sub-100ms intervals are usually a single-block turn
    # where Claude Code grouped emit events tightly.
    output_tps = None
    if response_duration_ms and response_duration_ms >= 100 and output_tokens > 0:
        output_tps = round(output_tokens / (response_duration_ms / 1000.0), 2)

    cache_hit_ratio = None
    cache_total = cache_read + input_tokens + cache_create
    if cache_total > 0:
        cache_hit_ratio = round(cache_read / cache_total, 4)

    try:
        from .mode_profile import session_mode_for_project
        mode_label = session_mode_for_project(fs.project_path)
    except Exception:
        mode_label = None

    try:
        await daemon.ledger.record_turn_metric(
            fs.session_id,
            fs.project_hash,
            message_id=turn.get("message_id"),
            turn_idx=turn.get("turn_idx"),
            model=turn.get("model"),
            stop_reason=turn.get("stop_reason"),
            prompt_to_response_ms=turn.get("prompt_to_response_ms"),
            response_duration_ms=response_duration_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read,
            cache_creation_tokens=cache_create,
            output_tps=output_tps,
            cache_hit_ratio=cache_hit_ratio,
            first_block_at=first.isoformat() if first else None,
            last_block_at=last.isoformat() if last else None,
            session_mode=mode_label,
        )
    except Exception:
        log.exception("turn-metric persist failed for %s", fs.session_id)
        return

    try:
        await daemon.live.publish(
            fs.session_id,
            fs.project_hash,
            "turn_metric",
            {
                "message_id": turn.get("message_id"),
                "turn_idx": turn.get("turn_idx"),
                "model": turn.get("model"),
                "stop_reason": turn.get("stop_reason"),
                "prompt_to_response_ms": turn.get("prompt_to_response_ms"),
                "response_duration_ms": response_duration_ms,
                "output_tokens": output_tokens,
                "cache_read_tokens": cache_read,
                "output_tps": output_tps,
                "cache_hit_ratio": cache_hit_ratio,
            },
        )
    except Exception:
        log.exception("turn-metric publish failed for %s", fs.session_id)


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
