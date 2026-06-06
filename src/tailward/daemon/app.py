"""FastAPI application: hook IPC, health, and (from M9) the web UI."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

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
        self.local_llm = None
        # v1.1 failure-mode audit layer workers.
        self.constraints = None
        self.scope = None
        self.rubric = None
        self.user_rubric = None
        self.session_close = None
        # v0.2 platform probe worker.
        self.probe = None
        # Remote auto-pull worker (keeps followed boxes' mirrors fresh).
        self.remote_pull = None
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
            # project_hash, type, created_at, event_ts, id) is
            # reconstructed from the row's own columns when serving
            # replays. ``ev.to_json()`` bakes in ``ev.id=0`` here
            # because the row id isn't known until after this insert,
            # which broke SSE-replay dedup and the ack/dismiss
            # buttons on the live page.
            #
            # ``ev.event_ts`` (Unix epoch float) becomes the persisted
            # ``event_ts`` column for source-event-time reconstruction
            # in past-session views; ``created_at`` is set by the
            # ledger as insert-time so the live feed remains
            # monotonic by fire-time.
            import json as _json

            event_ts_iso = (
                datetime.fromtimestamp(ev.event_ts, UTC).isoformat()
                if ev.event_ts
                else None
            )
            return await daemon.ledger.record_live_event(
                ev.session_id,
                ev.project_hash,
                ev.type,
                _json.dumps(ev.payload, ensure_ascii=False, default=str),
                event_ts=event_ts_iso,
            )

        daemon.live.set_persister(_persist_live)

        async def on_event(ev, fs, is_backlog: bool = False):
            # ``is_backlog=True`` for events parsed from existing JSONL
            # content during the watcher's prime pass at startup or
            # when a project is opted-in via the Seed UI. ``False`` for
            # real-time events arriving via filesystem change
            # notifications. LLM-call workers (drift, audit, rubric,
            # user_rubric, synthesis) skip backlog events to prevent
            # every daemon startup from firing tens of LLM calls
            # catching up on history. Rule-based workers (constraints,
            # scope) and structural-data writes (ledger, live-bus
            # rows) still fire — those are effectively free and
            # produce useful audit signal even on backlog content.
            #
            # Live broadcasts are gated on backlog: persisted to
            # ``live_events`` (so the past-session view of a seeded
            # project shows real turns) but not fanned out to SSE
            # subscribers (so historical replay doesn't flood the
            # live feed and lock the browser main thread).
            # ``event_ts`` carries the JSONL event's original
            # timestamp into the dedicated source-event column,
            # leaving ``created_at`` as insert-time so the live
            # feed sorts monotonically by fire-time without
            # reversal.
            _event_ts_epoch: float | None = (
                ev.timestamp.timestamp() if ev.timestamp else None
            )
            _pub_kwargs: dict[str, Any] = {
                "broadcast": not is_backlog,
                "event_ts": _event_ts_epoch,
            }
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
                        **_pub_kwargs,
                    )
                # Passive OS toast for the user who's stepped away. Debounced
                # per (session, kind=exfiltration) so a burst of matches in
                # one payload doesn't spam. The detail of which patterns
                # matched is in the live feed; the toast just says "look".
                if not is_backlog:
                    pattern_summary = ", ".join(
                        sorted({m.pattern_name for m in matches})
                    )
                    daemon.surface.os_notify(
                        fs.session_id,
                        "exfiltration",
                        "high",
                        f"Secret pattern detected in {source_event_type}: "
                        f"{pattern_summary}. Rotate if real.",
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
            # Both are LLM-cost; skipped on backlog.
            if not is_backlog:
                if daemon.drift is not None and ev.kind == "assistant_message" and fs.session_id:
                    await daemon.drift.enqueue(ev, fs)
                if (
                    daemon.audit is not None
                    and ev.kind in ("assistant_message", "user_message")
                    and fs.session_id
                ):
                    await daemon.audit.enqueue(ev, fs)

            # Rule-based and LLM-based workers under one project gate.
            # Rule-based (constraints, scope) ALWAYS fire — they're pure
            # regex/arithmetic, effectively free, and produce useful
            # audit signal on backlog content. LLM-based (rubric,
            # user_rubric, synthesis) skip backlog to prevent the
            # rapid-fire LLM activity at daemon startup catching up on
            # history.
            if fs.session_id and fs.project_hash:
                if daemon.constraints is not None and ev.kind in (
                    "tool_use",
                    "assistant_message",
                ):
                    await daemon.constraints.enqueue(ev, fs, is_backlog=is_backlog)
                if daemon.scope is not None and ev.kind in (
                    "tool_use",
                    "assistant_message",
                ):
                    await daemon.scope.enqueue(ev, fs, is_backlog=is_backlog)
                if not is_backlog:
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
                    **_pub_kwargs,
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
                            **_pub_kwargs,
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
                            **_pub_kwargs,
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
                # Redact each string field of tool_input independently so
                # the full version (used by the UI's expand-on-click view)
                # carries the same redaction the preview gets, and so
                # secrets in fields the preview's cherry-pick doesn't
                # surface (e.g. the ``new_string`` arg of an Edit call)
                # are scanned too. Single-pass: each field gets exactly
                # one _check_leaks call, no double-publish on alerts.
                redacted_input: dict[str, Any] = {}
                leaks: list[str] = []
                for k, v in (ev.tool_input or {}).items():
                    if isinstance(v, str) and v:
                        r, ls = await _check_leaks(
                            v, "tool_call", tool=ev.tool_name, field=k
                        )
                        redacted_input[k] = r
                        leaks.extend(ls)
                    else:
                        redacted_input[k] = v
                input_preview = _shorten_tool_input(redacted_input)
                input_full = _full_tool_input(redacted_input)
                tc_payload: dict[str, Any] = {
                    "tool": ev.tool_name,
                    "input_preview": input_preview,
                    "permission_mode": mode_at_call,
                }
                # Only attach the full form when it carries information
                # the preview doesn't (multiple args, or any single arg
                # longer than the preview cap). Otherwise the expand
                # affordance would just re-show the same one line.
                if input_full and input_full != input_preview:
                    tc_payload["input_full"] = input_full
                if leaks:
                    tc_payload["secrets_redacted"] = leaks
                await daemon.live.publish(
                    fs.session_id,
                    fs.project_hash,
                    "tool_call",
                    tc_payload,
                    **_pub_kwargs,
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
                    **_pub_kwargs,
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
                    **_pub_kwargs,
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
                        **_pub_kwargs,
                    )

        cfg = get_config()
        # Local projects root plus any followed remote boxes' mirror roots
        # (Phase 0 remote aggregation). Box roots are enumerated once at
        # startup; a box pulled while the daemon runs is picked up on the
        # next restart.
        from ..paths import claude_projects_root
        from ..remote import box_projects_roots

        mirror_roots = box_projects_roots(cfg)
        roots = [claude_projects_root(), *mirror_roots]
        # Map each mirror root to its box label (<mirror>/<box>/projects),
        # so the watcher folds box provenance into those projects' hashes.
        root_boxes = {pr: pr.parent.name for pr in mirror_roots}
        if mirror_roots:
            log.info(
                "watching %d remote-mirror root(s) alongside the local root",
                len(mirror_roots),
            )
        daemon.watcher = TranscriptWatcher(
            daemon.state,
            daemon.ledger,
            on_event=on_event,
            roots=roots,
            root_boxes=root_boxes,
            watch_paths=cfg.watch_paths,
            exclude_paths=cfg.exclude_paths,
        )
        await daemon.watcher.start()

        # Lazy init of M5+ workers if their deps are importable.
        try:
            from .local_llm import LocalLLMClient
            daemon.local_llm = LocalLLMClient()
            # Wire the metric recorder. The client awaits the HTTP
            # call directly on the event loop now (no thread bridge),
            # so the recorder doesn't need a loop handle anymore.
            daemon.local_llm.attach_recorder(daemon.ledger)
        except Exception as e:
            log.warning("local LLM client unavailable: %s", e)

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
            from .remote_pull_worker import RemotePullWorker
            daemon.remote_pull = RemotePullWorker(daemon)
            await daemon.remote_pull.start()
        except Exception as e:
            log.warning("remote-pull worker unavailable: %s", e)

        try:
            from .synthesis_worker import SynthesisWorker
            daemon.synthesis = SynthesisWorker(daemon)
            await daemon.synthesis.start()
        except Exception as e:
            log.warning("synthesis worker unavailable: %s", e)

        # Test-only hooks for tests/test_daemon_shutdown.py. Production
        # code path: env vars are unset and these are no-ops.
        #
        # TAILWARD_TEST_SLOW_WORKER_SECS — stop() awaits a cancellable
        # asyncio.sleep. Verifies wait_for-based cancellation works.
        # TAILWARD_TEST_HUNG_WORKER_SECS — stop() awaits asyncio.to_thread
        # wrapping time.sleep, which is uncancellable. Reproduces the
        # actual production failure: cancelling the coroutine doesn't
        # cancel the underlying thread, and asyncio's loop teardown
        # blocks on executor.shutdown waiting for the thread.
        test_slow_secs = os.environ.get("TAILWARD_TEST_SLOW_WORKER_SECS")
        if test_slow_secs:
            secs = float(test_slow_secs)
            class _TestSlowWorker:
                async def stop(self) -> None:
                    await asyncio.sleep(secs)
            daemon._test_slow_worker = _TestSlowWorker()  # type: ignore[attr-defined]

        test_hung_secs = os.environ.get("TAILWARD_TEST_HUNG_WORKER_SECS")
        if test_hung_secs:
            import time as _time
            secs = float(test_hung_secs)
            class _TestHungWorker:
                async def stop(self) -> None:
                    # to_thread(time.sleep) — cancelling the coroutine
                    # does NOT interrupt time.sleep in the executor
                    # thread. Same failure mode as a blocking HTTP
                    # call to a slow LLM endpoint.
                    await asyncio.to_thread(_time.sleep, secs)
            daemon._test_hung_worker = _TestHungWorker()  # type: ignore[attr-defined]

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
            # Bounded parallel worker shutdown.
            #
            # Each worker.stop() can block waiting on its in-flight
            # work — most importantly, asyncio.to_thread calls into
            # the LLM HTTP client, which aren't natively cancellable.
            # Without bounding, a slow inference (10-60s) holds the
            # entire daemon shutdown hostage. We give each worker a
            # 5s budget; if it exceeds, we abandon it and the
            # underlying thread continues until its blocking call
            # returns (the post-server.run() grace loop in
            # __main__.py force-exits the process if threads remain).
            #
            # Parallel via gather so worst case is ~5s total, not
            # 5s × N workers. Workers are independent — they consume
            # from the watcher's event stream but don't depend on
            # each other for shutdown. Ledger close is unbounded
            # (pending writes matter for data integrity).
            #
            # Proper fix tracked: replace to_thread+sync OpenAI client
            # with native async client (httpx) so LLM calls cancel at
            # the socket level when worker.stop() propagates cancel.
            workers = [
                ("watcher", daemon.watcher),
                ("drift", daemon.drift),
                ("audit", daemon.audit),
                ("constraints", daemon.constraints),
                ("scope", daemon.scope),
                ("rubric", daemon.rubric),
                ("user_rubric", getattr(daemon, "user_rubric", None)),
                ("session_close", daemon.session_close),
                ("probe", daemon.probe),
                ("remote_pull", getattr(daemon, "remote_pull", None)),
                ("synthesis", getattr(daemon, "synthesis", None)),
                ("test_slow", getattr(daemon, "_test_slow_worker", None)),
                ("test_hung", getattr(daemon, "_test_hung_worker", None)),
            ]

            async def _stop_with_budget(name: str, worker: Any) -> None:
                if worker is None:
                    return
                try:
                    await asyncio.wait_for(worker.stop(), timeout=5.0)
                except TimeoutError:
                    log.warning(
                        "shutdown: %s.stop() exceeded 5s budget; "
                        "abandoning. Cooperative cancel reaches the "
                        "LLM client's HTTP call directly now, so this "
                        "branch should be reserved for workers blocked "
                        "on something other than the LLM.",
                        name,
                    )
                except Exception as e:
                    log.warning(
                        "shutdown: %s.stop() raised %s: %s",
                        name, type(e).__name__, e,
                    )

            await asyncio.gather(
                *[_stop_with_budget(n, w) for n, w in workers],
                return_exceptions=True,
            )
            await daemon.ledger.close()
            log.info("tailward daemon stopped")

            # Safety net for executor threads we don't own.
            #
            # Gated so it only fires in the actual daemon process —
            # in-process FastAPI usage (TestClient, in-tree ASGI
            # hosting, future programmatic embedding) must NOT kill
            # the host. ``__main__.py`` sets this flag explicitly;
            # everything else leaves it unset.
            #
            # ``asyncio.run()`` (which uvicorn's ``Server.run()`` uses)
            # calls ``loop.shutdown_default_executor()`` in its finally
            # block, which blocks until every ThreadPoolExecutor worker
            # thread finishes. With AsyncOpenAI driving the LLM client,
            # there's no longer a ``to_thread``-wrapped sync HTTP call
            # holding a thread open — cooperative cancel reaches the
            # socket directly via httpx. So in normal operation this
            # exit is a no-op past the data-integrity work above
            # (worker stops + ledger close).
            #
            # We keep it as a safety net for anything else that might
            # have parked work on the default executor — third-party
            # libraries calling ``run_in_executor``, future workers
            # not yet refactored, or some hung native-code call. The
            # process should not linger tens of seconds after a clean
            # graceful teardown for any of those reasons either.
            if getattr(app.state, "exit_on_lifespan_close", False):
                os._exit(0)

    app = FastAPI(title="tailward", lifespan=lifespan)
    app.state.daemon = daemon

    # Allow the packaged Tauri webview (served from the bundled
    # ``tauri.localhost`` origin, not the daemon) to call the daemon's
    # API + SSE cross-origin. The desktop app keeps Tauri IPC strictly
    # local; the daemon only ever serves DATA to this origin, never
    # control — so a remote daemon reached over an SSH tunnel can't drive
    # local Tauri commands. Same-origin browser/dev use ignores these
    # headers entirely.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://tauri.localhost",
            "https://tauri.localhost",
            "tauri://localhost",
        ],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Content-Security-Policy + standard security headers.
    #
    # Tauri-side CSP is explicitly disabled (``"csp": null`` in
    # tauri.conf.json), so daemon-served headers are the only line of
    # defense against XSS-via-injected-content escalating through the
    # webview's JS-to-Rust bridge. v2 (pipx) users get the same
    # protection in any browser pointed at the daemon.
    #
    # Policy rationale:
    # - ``default-src 'self'``: only same-origin (the daemon) by default.
    # - ``script-src 'self'``: strict — no inline scripts, no eval. The
    #   Svelte SPA bundle is served from /static/dist; nothing else
    #   should execute JS.
    # - ``style-src 'self' 'unsafe-inline'``: Svelte component styles
    #   and the live.html inline fallback need inline. CSS injection
    #   on a loopback service is low-risk vs. the cost of nonces.
    # - ``connect-src 'self'``: XHR/fetch/EventSource only to the
    #   daemon. Blocks data exfil to external endpoints if a script
    #   does get injected.
    # - ``img-src 'self' data:``: inline favicons, icons, generated
    #   images.
    # - ``frame-ancestors 'none'``: no embedding in iframes (clickjack).
    # - ``object-src 'none'``: no Flash/applets.
    # - ``base-uri 'self'``: prevent <base> hijacking of relative URLs.
    # - ``form-action 'self'``: forms only submit to the daemon.
    CSP_POLICY = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "connect-src 'self'; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'none'"
    )

    @app.middleware("http")
    async def _security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        # X-Content-Type-Options applies to all responses (cheap, no
        # downside; blocks MIME sniffing on JSON/SSE/HTML alike).
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        # CSP + frame-options are HTML-specific (don't pollute JSON
        # responses with browser-rendering directives).
        ctype = response.headers.get("content-type", "")
        if ctype.startswith("text/html"):
            response.headers.setdefault("Content-Security-Policy", CSP_POLICY)
            response.headers.setdefault("X-Frame-Options", "DENY")
        return response

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "ok": True,
            "sessions": len(daemon.state.all()),
            "ts": datetime.now(UTC).isoformat(),
        }

    @app.post("/shutdown")
    async def shutdown(request: Request) -> dict[str, Any]:
        """Cooperative shutdown signal for the v3 Tauri shell.

        Flips uvicorn's ``should_exit`` flag; the server completes the
        current response, runs the lifespan ``finally`` block (workers
        stop, ledger closes), and exits cleanly. The PyInstaller
        bootloader follows because its child Python interpreter dies.

        No auth — daemon binds to loopback by default. Anyone with
        localhost access can already terminate the process at the OS
        level; this just exposes a graceful path.
        """
        server = getattr(request.app.state, "uvicorn_server", None)
        if server is None:
            raise HTTPException(
                status_code=503,
                detail="shutdown unavailable: server reference not set",
            )
        server.should_exit = True
        return {"ok": True}

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

    @app.get("/api/projects/{ph}/seeded")
    async def api_is_project_seeded(ph: str) -> dict[str, Any]:
        """Whether the project is opted into deep-parse on startup.

        UI uses this to render the per-project "seeded" indicator on
        the project list and to decide whether to show the Seed
        button in default vs already-seeded state."""
        return {"project_hash": ph, "seeded": await daemon.ledger.is_project_seeded(ph)}

    @app.post("/api/projects/{ph}/seed")
    async def api_seed_project(ph: str) -> dict[str, Any]:
        """Opt the project into deep-parse on startup. Triggers an
        immediate parse pass over its existing JSONL content. Workers
        fire per the backlog/realtime distinction (rule-based yes,
        LLM-cost no — see ``project_llm_inference_load_principles.md``).

        Idempotent — re-seeding a seeded project re-parses without
        changing the ``seeded_at`` timestamp."""
        if not daemon.watcher:
            raise HTTPException(
                status_code=503,
                detail="watcher not initialized; daemon may still be starting",
            )
        files_seeded = await daemon.watcher.seed_project(ph)
        return {
            "ok": True,
            "project_hash": ph,
            "files_seeded": files_seeded,
        }

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
        mode_label = session_mode_for_project(
            fs.project_path, box=getattr(fs, "box", "")
        )
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
    """Preview of tool args for the live feed scrolling view.

    Caps the chosen field at 360 chars — long enough that Bash
    commands and Grep patterns typically convey intent at a glance,
    short enough that the row stays scannable. The expand-on-click
    full view is for when the user needs the rest.
    """
    if not tool_input:
        return ""
    pieces: list[str] = []
    for key in ("file_path", "path", "command", "pattern", "query", "url"):
        val = tool_input.get(key)
        if isinstance(val, str) and val:
            pieces.append(f"{key}={val[:360]}")
            break
    if not pieces:
        keys = ", ".join(sorted(tool_input.keys())[:4])
        pieces.append(f"keys=[{keys}]")
    return " ".join(pieces)


def _full_tool_input(tool_input: dict[str, Any] | None, max_bytes: int = 4096) -> str:
    """JSON-serialized tool args for the expand-on-click view.

    Stored as JSON because that's what the per-row copy button hands
    off to the clipboard — the user gets a programmatically parseable
    form on paste. The frontend post-processes this for display
    (unescaping embedded newlines so multi-line bash heredocs render
    legibly) while keeping the raw JSON for copy.

    Capped at ``max_bytes`` so a Write call with a megabyte of file
    content can't bloat ``live_events.payload``. Truncation is marked
    explicitly so the UI can render a "[truncated]" affordance rather
    than implying the full input ended where the cap fell.
    """
    if not tool_input:
        return ""
    text = json.dumps(tool_input, ensure_ascii=False, indent=2, default=str)
    if len(text) <= max_bytes:
        return text
    return text[:max_bytes] + "\n…[truncated]"
