"""Per-session scope tracking (failure mode 8: minimizes scope).

Maintains cumulative counters for each live session — files touched, diff
bytes estimate, and a breakdown of tool-call kinds — and emits scope-creep
events when growth outruns the rolling baseline from prior sessions for the
same project.

Counters update on every relevant event; the ledger snapshot is written on
each assistant turn so the web UI has clean "per turn" points for the
timeline. Creep events fire at most once per threshold crossing per session.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..config import get_config
from ..schema.events import TranscriptEvent, bash_command, target_paths

if TYPE_CHECKING:
    from .app import Daemon

log = logging.getLogger(__name__)


@dataclass
class _SessionScope:
    files_touched: set[str] = field(default_factory=set)
    tool_kinds: Counter = field(default_factory=Counter)
    diff_bytes: int = 0
    creep_fired: bool = False
    turn_idx: int = 0
    last_snapshot_turn: int = -1


class ScopeWorker:
    def __init__(self, daemon: Daemon) -> None:
        self._daemon = daemon
        self._q: asyncio.Queue[tuple[TranscriptEvent, object]] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._by_session: dict[str, _SessionScope] = {}

    async def enqueue(self, ev: TranscriptEvent, fs) -> None:
        await self._q.put((ev, fs))

    async def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="scope-worker")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                ev, fs = await asyncio.wait_for(self._q.get(), timeout=1.0)
            except TimeoutError:
                continue
            try:
                await self._process(ev, fs)
            except Exception:
                log.exception("scope processing failed")

    async def _process(self, ev: TranscriptEvent, fs) -> None:
        if not fs.session_id or not fs.project_hash:
            return
        scope = self._by_session.setdefault(fs.session_id, _SessionScope())

        # Tool-use signal can arrive in two shapes: a bare ``tool_use``
        # event, or an assistant message whose content list contains a
        # tool_use block. Real Claude Code transcripts only emit the
        # embedded shape; gate on tool_name presence so both work.
        if ev.tool_name:
            scope.tool_kinds[ev.tool_name] += 1
            for p in target_paths(ev):
                scope.files_touched.add(p)
                content = (ev.tool_input or {}).get("content") or (ev.tool_input or {}).get("new_string")
                if isinstance(content, str):
                    scope.diff_bytes += len(content.encode("utf-8", errors="ignore"))
            if bash_command(ev):
                scope.tool_kinds["Bash"] += 1
            # Emit snapshot post-update so the values reflect this tool
            # call. Earlier the snapshot was tied to the leading content
            # block of an assistant_message (often a ``thinking`` block
            # with no tool calls yet), so the displayed counters lagged
            # by one turn — files_touched showed 0 even when the turn
            # had clearly touched files.
            state = self._daemon.state.get(fs.session_id)
            scope.turn_idx = state.turns_seen if state else scope.turn_idx
            await self._emit_snapshot(scope, fs)
            return

        if ev.kind == "assistant_message" and ev.new_turn:
            # A turn that contained no tool calls still gets one snapshot
            # so the timeline has a marker. Skip if a tool_use earlier in
            # the same turn already emitted one (turn_idx already matches).
            state = self._daemon.state.get(fs.session_id)
            new_turn_idx = state.turns_seen if state else scope.turn_idx + 1
            if new_turn_idx == scope.last_snapshot_turn:
                return
            scope.turn_idx = new_turn_idx
            await self._emit_snapshot(scope, fs)

    async def _emit_snapshot(self, scope: _SessionScope, fs) -> None:
        from .mode_profile import (
            active_profile_for_project,
            session_mode_for_project,
        )

        cfg = get_config()
        # Mode-aware: an exploration / yolo / unknown session resolves
        # to a profile with ``scope_creep_floor=None``, in which case
        # the snapshot still records counters but no creep event ever
        # fires. Build mode keeps the SWE thresholds.
        profile = active_profile_for_project(fs.project_path)
        mode_label = session_mode_for_project(fs.project_path)
        floor = profile.scope_creep_floor
        factor = profile.scope_creep_factor

        baseline = await self._daemon.ledger.baseline_files_touched(
            fs.project_hash, cfg.scope_baseline_window
        )
        files_n = len(scope.files_touched)
        if floor is None:
            creep_threshold = None
            is_creep = False
        else:
            creep_threshold = max(floor, int(baseline * factor))
            is_creep = (
                files_n >= floor
                and baseline > 0
                and files_n > creep_threshold
            )

        tool_kinds_json = json.dumps(dict(scope.tool_kinds))
        sid = await self._daemon.ledger.record_scope_snapshot(
            fs.session_id,
            fs.project_hash,
            turn_idx=scope.turn_idx,
            files_touched_count=files_n,
            diff_bytes=scope.diff_bytes,
            tool_kinds_json=tool_kinds_json,
            is_creep=is_creep,
            baseline=baseline,
            session_mode=mode_label,
        )

        if getattr(self._daemon, "live", None) is not None:
            try:
                await self._daemon.live.publish(
                    fs.session_id,
                    fs.project_hash,
                    "scope_snapshot",
                    {
                        "id": sid,
                        "turn_idx": scope.turn_idx,
                        "files_touched": files_n,
                        "diff_bytes": scope.diff_bytes,
                        "tool_kinds": dict(scope.tool_kinds),
                        "baseline": baseline,
                        "threshold": creep_threshold,
                        # cosmetic — UI uses this to label "creep" vs
                        # "spread" depending on mode framing.
                        "label": profile.scope_event_label,
                        "session_mode": mode_label or profile.name,
                    },
                )
            except Exception:
                log.exception("live publish failed (scope_snapshot)")

        if is_creep and not scope.creep_fired:
            scope.creep_fired = True
            await self._daemon.ledger.record_drift(
                fs.session_id,
                fs.project_hash,
                "scope_creep",
                "med",
                None,
                f"files_touched={files_n} exceeded {creep_threshold} (baseline={baseline})",
            )
            if getattr(self._daemon, "live", None) is not None:
                try:
                    await self._daemon.live.publish(
                        fs.session_id,
                        fs.project_hash,
                        "scope_creep",
                        {
                            "turn_idx": scope.turn_idx,
                            "files_touched": files_n,
                            "baseline": baseline,
                            "threshold": creep_threshold,
                            "tool_kinds": dict(scope.tool_kinds),
                        },
                    )
                except Exception:
                    log.exception("live publish failed (scope_creep)")
            if self._daemon.rubric is not None:
                try:
                    await self._daemon.rubric.trigger(
                        fs.session_id, fs.project_hash, reason="scope_creep"
                    )
                except Exception:
                    log.exception("rubric trigger on creep failed")

    def scope_for(self, session_id: str) -> _SessionScope | None:
        return self._by_session.get(session_id)
