"""Session synthesis stream — continuous capture, multiple consumers.

A single worker writes compaction-resistant markdown snapshots to
``~/.modmcp/projects/<hash>/snapshots/`` as a session runs. Multiple
triggers feed the same writer:

* **Periodic (token-based).** Every time the assistant turn's reported
  ``input_tokens`` has grown by ``synthesis_periodic_tokens`` since the
  last snapshot, fire an incremental synthesis covering the recent
  assistant-text window.
* **On-demand.** A "synthesize now" endpoint enqueues the same writer
  with the current session state. (Wired in a follow-up commit.)
* **Threshold (comprehensive).** When estimated context fullness
  crosses a higher bar, fire a comprehensive synth that produces a
  fresh ``intent.md``. (Wired in a follow-up commit.)

This module ships the **periodic** path as the foundation slice. The
on-demand and comprehensive paths reuse this worker's writer
(``_persist``) without changing the trigger plumbing.

The synthesizer is the local LLM ("qwen") — same engine ``phase1.py``
already uses for end-of-session synthesis. Each snapshot writes a
sidecar metadata file so A/B testing across models / prompts / sampler
params is first-class: regenerate any snapshot with different params,
inspect the sidecar to tell runs apart.

Passive-first: the snapshot is an artifact, not an intervention. The
user (or a fresh session via opt-in) decides what to do with it. No
prompt injection; no interruption of the active session.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..config import get_config
from ..paths import atomic_write_text, project_dir
from ..schema.events import TranscriptEvent

if TYPE_CHECKING:
    from .app import Daemon

log = logging.getLogger(__name__)


# Incremental snapshot prompt. Comprehensive synth keeps using
# ``phase1.SYSTEM`` (Intent-shaped JSON output). This one targets a
# short prose blob suitable for chaining into a comprehensive merge.
SYSTEM_INCREMENTAL = """You produce a concise prose snapshot of the most recent activity in a coding session.

Rules:
- 5-10 short bullets covering: what was done, what's open, what was decided.
- Be specific. Include file paths, function names, and decisions verbatim where possible.
- Output plain markdown (no code fences, no JSON wrapper).
- Aim for 200-400 words. The snapshot will be combined with siblings later.
- If the window is empty or pure tool-call noise, output a single line: (no notable activity)
"""


@dataclass
class _SessionState:
    session_id: str
    project_hash: str
    project_path: str | None
    # Latest ``input_tokens`` value observed on an assistant turn. Used
    # as the running fullness indicator + the baseline for the next
    # snapshot's threshold check.
    last_input_tokens: int = 0
    # ``input_tokens`` value at the time the most recent snapshot was
    # captured. ``last_input_tokens - last_snapshot_input_tokens`` gives
    # the "tokens added since last snapshot" delta the trigger watches.
    last_snapshot_input_tokens: int = 0
    in_flight: bool = False
    text_window: list[str] = field(default_factory=list)
    last_snapshot_at: float | None = None


class SynthesisWorker:
    """Token-threshold-driven snapshotter.

    Mirrors the rubric/scope worker pattern: an enqueue-driven async
    queue, processed serially in ``_run``. Sequential processing keeps
    the local LLM from getting hammered by simultaneous synth calls
    across overlapping turns.
    """

    # Roll the assistant-text window at this length so incremental
    # synth prompts stay bounded even on long sessions.
    _WINDOW_CAP: int = 32

    def __init__(self, daemon: Daemon) -> None:
        self._daemon = daemon
        self._q: asyncio.Queue[tuple[TranscriptEvent, object]] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._by_session: dict[str, _SessionState] = {}
        self._lock = asyncio.Lock()

    async def enqueue(self, ev: TranscriptEvent, fs) -> None:
        await self._q.put((ev, fs))

    async def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="synthesis-worker")

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
                log.exception("synthesis processing failed")

    async def _process(self, ev: TranscriptEvent, fs) -> None:
        if ev.kind != "assistant_message":
            return
        if not fs.session_id or not fs.project_hash:
            return
        # Only assistant turns carry usage we can lean on.
        usage = ev.usage or {}
        in_tokens_raw = usage.get("input_tokens")
        if in_tokens_raw is None:
            return
        try:
            in_tokens = int(in_tokens_raw)
        except (TypeError, ValueError):
            return
        # Cache reads occupy the model's context window even though
        # they're cheaper to bill — count them toward fullness.
        try:
            cache_read = int(usage.get("cache_read_input_tokens") or 0)
        except (TypeError, ValueError):
            cache_read = 0
        fullness = in_tokens + cache_read

        state = self._by_session.setdefault(
            fs.session_id,
            _SessionState(
                session_id=fs.session_id,
                project_hash=fs.project_hash,
                project_path=getattr(fs, "project_path", None),
            ),
        )

        text = (ev.text or "").strip()
        if text:
            state.text_window.append(f"ASSISTANT: {text}")
            if len(state.text_window) > self._WINDOW_CAP:
                state.text_window = state.text_window[-self._WINDOW_CAP:]

        # Compaction reset: if input_tokens dropped substantially,
        # the agent's own context was compacted. Re-baseline so we
        # don't fire a stale snapshot covering pre-compaction ground.
        if fullness < state.last_input_tokens // 2 and state.last_input_tokens > 0:
            state.last_snapshot_input_tokens = fullness
        state.last_input_tokens = fullness

        threshold = int(getattr(get_config(), "synthesis_periodic_tokens", 10000))
        delta = fullness - state.last_snapshot_input_tokens
        if delta >= threshold and not state.in_flight:
            await self._fire_incremental(state)

    async def _fire_incremental(self, state: _SessionState) -> None:
        if self._daemon.qwen is None:
            log.debug("synthesis skipped: no local LLM configured")
            return
        async with self._lock:
            if state.in_flight:
                return
            state.in_flight = True

        try:
            window = "\n\n---\n\n".join(state.text_window[-16:])
            user = f"Recent activity (most recent at bottom):\n\n{window[:8000]}"
            try:
                body = await self._daemon.qwen.complete(
                    SYSTEM_INCREMENTAL, user, kind="synth"
                )
            except Exception as e:
                log.warning("synthesis call failed: %s", e)
                return
            body = (body or "").strip()
            if not body:
                body = "(no notable activity)"
            await self._persist(state, body=body, trigger="periodic")
            state.last_snapshot_at = time.time()
            state.last_snapshot_input_tokens = state.last_input_tokens
        finally:
            async with self._lock:
                state.in_flight = False

    async def _persist(
        self, state: _SessionState, *, body: str, trigger: str
    ) -> None:
        if not state.project_path:
            log.debug("synthesis persist skipped: no project_path on session state")
            return
        ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        snap_dir = project_dir(state.project_path) / "snapshots"
        snap_path = snap_dir / f"{ts}.md"
        meta_path = snap_dir / f"{ts}.meta.json"

        front = (
            "---\n"
            f"trigger: {trigger}\n"
            f"session_id: {state.session_id}\n"
            f"project_hash: {state.project_hash}\n"
            f"created_at: {ts}\n"
            "---\n\n"
        )
        atomic_write_text(snap_path, front + body + "\n")

        model = (
            self._daemon.qwen.resolve_model("synth") if self._daemon.qwen else None
        )
        meta = {
            "trigger": trigger,
            "session_id": state.session_id,
            "project_hash": state.project_hash,
            "created_at": ts,
            "model": model,
            "fullness_input_tokens": state.last_input_tokens,
            "delta_since_last_snapshot": (
                state.last_input_tokens - state.last_snapshot_input_tokens
            ),
            "window_size": len(state.text_window),
        }
        atomic_write_text(meta_path, json.dumps(meta, indent=2) + "\n")

        live = getattr(self._daemon, "live", None)
        if live is not None:
            try:
                await live.publish(
                    state.session_id,
                    state.project_hash,
                    "synthesis_captured",
                    {
                        "trigger": trigger,
                        "path": str(snap_path),
                        "created_at": ts,
                        "model": model,
                        "fullness_input_tokens": state.last_input_tokens,
                    },
                )
            except Exception:
                log.exception("synthesis_captured publish failed")
