"""Session synthesis stream — continuous capture, multiple consumers.

A single worker writes compaction-resistant markdown snapshots to
``~/.modmcp/projects/<hash>/snapshots/`` as a session runs. Multiple
triggers feed the same writer:

* **Periodic (token-based).** Every time the assistant turn's reported
  ``input_tokens`` has grown by ``synthesis_periodic_tokens`` since the
  last snapshot, fire an incremental synthesis covering the recent
  session window.
* **On-demand.** A "synthesize now" endpoint enqueues the same writer
  with the current session state.
* **Threshold (comprehensive).** When estimated context fullness
  crosses a higher bar, fire a comprehensive synth that produces a
  fresh ``intent.md``. (Wired in a follow-up commit.)

Input strategy: on every synthesis call, the worker **reads the
session JSONL from disk** and denoises it (USER:/ASSISTANT:/[tool_use:
NAME]/[tool_result] lines). That makes the JSONL the single source of
truth — bootstrap-correct across daemon restarts, no in-memory window
to keep in sync. The denoised text is truncated from the back to fit
``synthesis_max_input_tokens`` so the most recent activity always wins.

The synthesizer is the local LLM ("qwen") — same engine ``phase1.py``
already uses for end-of-session synthesis. Each snapshot writes a
sidecar metadata file (model + sampler kind + input chars + cap +
event count) so A/B testing across models / prompts / sampler params
is first-class.

Passive-first: the snapshot is an artifact, not an intervention. The
user (or a fresh session via opt-in) decides what to do with it. No
prompt injection; no interruption of the active session.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ..config import get_config
from ..paths import (
    atomic_write_text,
    canonicalize_project_path,
    claude_projects_root,
    project_dir,
)
from ..schema.events import TranscriptEvent, parse_line

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


class NoLocalLLM(Exception):
    """Raised by ``on_demand`` when the daemon has no local LLM client
    configured. The HTTP layer translates this to a 503 with a clear
    "configure one in settings" hint."""


class SynthesisInFlight(Exception):
    """Raised by ``on_demand`` when another synthesis is already running
    for the requested session. The HTTP layer translates this to a 409
    Conflict — the client should wait for the in-flight call to land
    (the ``synthesis_captured`` event will fire) and retry if needed."""


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
    last_snapshot_at: float | None = None


class SynthesisWorker:
    """Token-threshold-driven snapshotter.

    Mirrors the rubric/scope worker pattern: an enqueue-driven async
    queue, processed serially in ``_run``. Sequential processing keeps
    the local LLM from getting hammered by simultaneous synth calls
    across overlapping turns.

    The worker observes assistant turns to track input-token fullness
    for the periodic trigger, but the actual synthesis input comes
    from re-reading the session JSONL on every call — the in-memory
    state holds threshold-tracking counters only.
    """

    # Per-event char cap when formatting tool I/O — phase1's denoise
    # uses 500 here; we mirror so the same noise filtering applies
    # to incremental synth.
    _TOOL_SNIPPET_CAP: int = 500

    # Conservative chars-per-token estimate for English text. Mirrors
    # phase1._max_chars(). Used to convert synthesis_max_input_tokens
    # into a char cap on the prompt body.
    _CHARS_PER_TOKEN: float = 3.2

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

    @classmethod
    def _format_event(cls, ev: TranscriptEvent) -> str | None:
        """Render a single parsed event into a one-line label suitable
        for the incremental synth prompt. Mirrors ``phase1._denoise``:

        * ``USER: ...`` / ``ASSISTANT: ...`` for prose turns
        * ``[tool_use:<name>] <input json (capped)>`` for tool calls
        * ``[tool_result] <output (capped)>`` for tool responses

        Returns ``None`` for events that should be dropped (empty,
        non-content-bearing, or unrecognized kinds).
        """
        if ev.kind == "user_message":
            text = (ev.text or "").strip()
            return f"USER: {text}" if text else None
        if ev.kind == "assistant_message":
            text = (ev.text or "").strip()
            return f"ASSISTANT: {text}" if text else None
        if ev.kind == "tool_use":
            name = getattr(ev, "tool_name", None) or "?"
            tool_input = getattr(ev, "tool_input", None) or {}
            try:
                rendered = json.dumps(tool_input, ensure_ascii=False)
            except (TypeError, ValueError):
                rendered = str(tool_input)
            return f"[tool_use:{name}] {rendered[:cls._TOOL_SNIPPET_CAP]}"
        if ev.kind == "tool_result":
            out = getattr(ev, "tool_output", None) or ""
            snippet = out[:cls._TOOL_SNIPPET_CAP]
            return f"[tool_result] {snippet}" if snippet else None
        return None

    async def _process(self, ev: TranscriptEvent, fs) -> None:
        """Per-event handler — only updates threshold-tracking counters.

        The actual synthesis input comes from the JSONL file on disk
        when ``_capture`` runs, so this function does no event
        accumulation — it just watches assistant turns for the
        token-threshold trigger.
        """
        if not fs.session_id or not fs.project_hash:
            return

        state = self._by_session.setdefault(
            fs.session_id,
            _SessionState(
                session_id=fs.session_id,
                project_hash=fs.project_hash,
                project_path=getattr(fs, "project_path", None),
            ),
        )
        if state.project_path is None:
            state.project_path = getattr(fs, "project_path", None)

        # Threshold check piggybacks on assistant turns — those are the
        # only events that carry usage metadata.
        if ev.kind != "assistant_message":
            return
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
            await self._capture(state, trigger="periodic")
        finally:
            async with self._lock:
                state.in_flight = False

    async def on_demand(
        self,
        session_id: str,
        project_hash: str,
        project_path: str | None,
    ) -> dict | None:
        """Fire an incremental synthesis right now, regardless of token
        threshold. Reuses the same writer + livebus event as periodic.

        Raises:
            NoLocalLLM: when the daemon has no qwen client configured.
            SynthesisInFlight: when another synth is already running
                for this session (typically a periodic in flight).

        Returns the snapshot metadata dict on success, or ``None`` if
        the LLM call itself failed — in that case ``synthesis_failed``
        has already been published with the underlying error. Distinct
        return paths so the HTTP layer can give honest error messages
        rather than a generic catch-all.
        """
        state = self._by_session.setdefault(
            session_id,
            _SessionState(
                session_id=session_id,
                project_hash=project_hash,
                project_path=project_path,
            ),
        )
        if state.project_path is None:
            state.project_path = project_path

        if self._daemon.qwen is None:
            raise NoLocalLLM()
        async with self._lock:
            if state.in_flight:
                raise SynthesisInFlight()
            state.in_flight = True
        try:
            return await self._capture(state, trigger="on_demand")
        finally:
            async with self._lock:
                state.in_flight = False

    @staticmethod
    def _resolve_jsonl_fallback(
        project_path: str, session_id: str
    ) -> str | None:
        """Compute the Claude Code transcript path from
        ``(project_path, session_id)`` directly. Used when
        ``daemon.state.get(session_id)`` doesn't yet have the session —
        notably right after daemon restart, before the watcher has
        re-attached. Mirrors ``cli._resolve_transcript`` (which
        operates from the CLI side) so both surfaces resolve to the
        same file.
        """
        root = claude_projects_root()
        if not root.exists():
            return None
        canonical = canonicalize_project_path(project_path)
        candidates = [
            canonical.replace(":", "").replace("/", "-"),
            canonical.replace(":", "-").replace("/", "-"),
            canonical.replace("/", "-"),
        ]
        for c in candidates:
            d = root / c
            if not d.exists():
                continue
            f = d / f"{session_id}.jsonl"
            if f.exists():
                return str(f)
        # Last resort: scan all subdirs for a matching session_id.jsonl.
        # Cheap because Claude Code only writes one file per session.
        try:
            for d in root.iterdir():
                if not d.is_dir():
                    continue
                f = d / f"{session_id}.jsonl"
                if f.exists():
                    return str(f)
        except OSError:
            pass
        return None

    def _read_session_window(
        self, state: _SessionState, *, max_chars: int
    ) -> tuple[str, int, int | None]:
        """Read the session JSONL from disk, denoise, and truncate to
        ``max_chars`` from the back. Returns ``(body_text, event_count,
        latest_fullness)``:

        * ``body_text`` — denoised, char-capped prompt body
        * ``event_count`` — denoised lines that survived formatting
          (before char-cap truncation)
        * ``latest_fullness`` — most recent assistant turn's
          ``usage.input_tokens + cache_read_input_tokens`` value seen
          in the JSONL, or ``None`` if no assistant turn carried usage.
          Used to seed ``state.last_input_tokens`` on cold starts so
          the sidecar's ``fullness_input_tokens`` reflects reality
          even when the in-memory state hasn't streamed an assistant
          turn yet (e.g., immediately after daemon restart).

        Uses ``daemon.state.get(session_id).jsonl_path`` as the
        canonical lookup; this is the same path the watcher tails so
        we're guaranteed to read the same file Claude Code is writing.
        """
        st = self._daemon.state.get(state.session_id) if self._daemon.state else None
        jsonl_path = getattr(st, "jsonl_path", None) if st else None
        # Fallback for the bootstrap case: post-daemon-restart, the
        # watcher may not have re-attached this session yet, so
        # ``daemon.state`` doesn't know about it. Resolve the path
        # directly from the canonical project path + session id.
        if not jsonl_path and state.project_path:
            jsonl_path = self._resolve_jsonl_fallback(
                state.project_path, state.session_id
            )
        if not jsonl_path:
            return ("", 0, None)
        path = Path(jsonl_path)
        if not path.exists():
            return ("", 0, None)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            log.exception("synthesis: failed to read transcript %s", path)
            return ("", 0, None)

        kept: list[str] = []
        latest_fullness: int | None = None
        for line in text.splitlines():
            ev = parse_line(line)
            if ev is None:
                continue
            if ev.kind == "assistant_message":
                usage = ev.usage or {}
                in_tokens_raw = usage.get("input_tokens")
                if in_tokens_raw is not None:
                    try:
                        in_tokens = int(in_tokens_raw)
                        cache_read = int(usage.get("cache_read_input_tokens") or 0)
                        latest_fullness = in_tokens + cache_read
                    except (TypeError, ValueError):
                        pass
            formatted = self._format_event(ev)
            if formatted:
                kept.append(formatted)
        joined = "\n\n---\n\n".join(kept)
        if len(joined) > max_chars:
            joined = joined[-max_chars:]
        return (joined, len(kept), latest_fullness)

    async def _capture(
        self, state: _SessionState, *, trigger: str
    ) -> dict | None:
        """Shared body for both periodic and on-demand triggers.

        Caller is responsible for setting ``state.in_flight`` and
        unsetting it; this function just runs the LLM call + persist.
        Surfaces failures via the ``synthesis_failed`` LiveBus event so
        the user sees a failed call rather than a silent miss.
        """
        if self._daemon.qwen is None:
            log.debug("synthesis skipped: no local LLM configured")
            return None

        cfg = get_config()
        max_input_tokens = int(getattr(cfg, "synthesis_max_input_tokens", 24000))
        max_chars = max(2048, int(max_input_tokens * self._CHARS_PER_TOKEN))

        body_text, event_count, latest_fullness = self._read_session_window(
            state, max_chars=max_chars
        )
        # Sync in-memory state from JSONL — bootstrap-correct on cold
        # starts. Without this, an on-demand call right after daemon
        # restart records ``fullness_input_tokens: 0`` because no
        # assistant turn has streamed through the dispatcher yet.
        if latest_fullness is not None:
            state.last_input_tokens = latest_fullness
        if body_text:
            user = f"Recent activity (most recent at bottom):\n\n{body_text}"
        else:
            user = "No content captured yet in this session."
        try:
            output = await self._daemon.qwen.complete(
                SYSTEM_INCREMENTAL, user, kind="synth"
            )
        except Exception as e:
            log.warning("synthesis call failed (%s): %s", trigger, e)
            await self._publish_failure(state, trigger=trigger, error=str(e))
            return None
        output = (output or "").strip() or "(no notable activity)"
        meta = await self._persist(
            state,
            body=output,
            trigger=trigger,
            input_chars=len(body_text),
            input_chars_cap=max_chars,
            event_count=event_count,
        )
        state.last_snapshot_at = time.time()
        state.last_snapshot_input_tokens = state.last_input_tokens
        return meta

    async def _publish_failure(
        self, state: _SessionState, *, trigger: str, error: str
    ) -> None:
        live = getattr(self._daemon, "live", None)
        if live is None:
            return
        try:
            await live.publish(
                state.session_id,
                state.project_hash,
                "synthesis_failed",
                {"trigger": trigger, "error": error},
            )
        except Exception:
            log.exception("synthesis_failed publish itself failed")

    async def _persist(
        self,
        state: _SessionState,
        *,
        body: str,
        trigger: str,
        input_chars: int,
        input_chars_cap: int,
        event_count: int,
    ) -> dict | None:
        if not state.project_path:
            log.debug("synthesis persist skipped: no project_path on session state")
            return None
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
            "input_chars": input_chars,
            "input_chars_cap": input_chars_cap,
            "event_count": event_count,
            "path": str(snap_path),
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
                        "input_chars": input_chars,
                        "event_count": event_count,
                    },
                )
            except Exception:
                log.exception("synthesis_captured publish failed")
        return meta
