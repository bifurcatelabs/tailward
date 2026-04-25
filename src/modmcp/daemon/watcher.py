"""Transcript watcher.

Tails ``~/.claude/projects/<sanitized>/*.jsonl`` and emits parsed events into
the daemon's in-memory state and downstream queues. Robust to log rotation,
partial writes, and daemon restarts (offsets persist in SQLite).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from watchfiles import Change, awatch

from ..paths import claude_projects_root, project_hash
from ..schema.events import TranscriptEvent, parse_line
from ..storage.ledger import Ledger
from .state import StateStore

log = logging.getLogger(__name__)

EventHandler = Callable[[TranscriptEvent, "FileState"], Awaitable[None]]


class FileState:
    __slots__ = ("path", "offset", "session_id", "project_path", "project_hash")

    def __init__(self, path: Path) -> None:
        self.path = path
        self.offset = 0
        self.session_id: str | None = None
        self.project_path: str | None = None
        self.project_hash: str | None = None


def _sanitize_to_path(sanitized: str) -> str:
    """Claude Code encodes project paths by replacing path separators with '-'.

    We can't always reverse this unambiguously, but we can take a best-effort
    stab. The authoritative project_path is extracted from the transcript
    events themselves (``cwd`` field) as soon as we see one; this is only a
    fallback.
    """
    # Windows: "C--Users-glenn-code-example" -> "C:/Users/glenn/code/example"
    # POSIX:   "-Users-glenn-code-example"   -> "/Users/glenn/code/example"
    s = sanitized
    if len(s) >= 2 and s[1] == "-" and s[0].isalpha():
        s = s[0] + ":" + s[2:]
    return s.replace("-", "/")


class TranscriptWatcher:
    def __init__(
        self,
        state: StateStore,
        ledger: Ledger,
        on_event: EventHandler | None = None,
        root: Path | None = None,
    ) -> None:
        self._state = state
        self._ledger = ledger
        self._on_event = on_event
        self._root = root or claude_projects_root()
        self._files: dict[Path, FileState] = {}
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="transcript-watcher")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _run(self) -> None:
        if not self._root.exists():
            log.warning("Claude projects root %s does not exist yet; waiting", self._root)
            while not self._root.exists() and not self._stop.is_set():
                await asyncio.sleep(2.0)
            if self._stop.is_set():
                return

        await self._prime_existing()

        try:
            async for changes in awatch(
                self._root,
                recursive=True,
                stop_event=self._stop,
                watch_filter=_jsonl_filter,
            ):
                for _change, path_str in changes:
                    p = Path(path_str)
                    if p.suffix != ".jsonl":
                        continue
                    await self._process_file(p)
        except RuntimeError as e:
            # watchfiles raises if the watched root disappears mid-run; treat
            # that as shutdown. Anything else is a real bug — log it so it
            # doesn't vanish silently.
            if self._stop.is_set() or not self._root.exists():
                return
            log.exception("transcript watcher exited on unexpected RuntimeError: %s", e)

    async def _prime_existing(self) -> None:
        for jsonl in self._root.rglob("*.jsonl"):
            await self._process_file(jsonl)

    async def _process_file(self, path: Path) -> None:
        fs = self._files.get(path)
        if fs is None:
            fs = FileState(path)
            # Seed offset from ledger if we know this session.
            session_id = path.stem
            stored = await self._ledger.get_offset(session_id)
            fs.offset = stored
            fs.session_id = session_id
            self._files[path] = fs

        try:
            size = path.stat().st_size
        except FileNotFoundError:
            self._files.pop(path, None)
            return

        if size < fs.offset:
            # File was truncated / rotated. Start over.
            fs.offset = 0

        if size == fs.offset:
            return

        try:
            with open(path, "rb") as f:
                f.seek(fs.offset)
                chunk = f.read()
        except OSError as e:
            log.warning("failed to read %s: %s", path, e)
            return

        # Only process complete lines; leave trailing partial for next tick.
        last_nl = chunk.rfind(b"\n")
        if last_nl < 0:
            return
        consumable = chunk[: last_nl + 1]
        new_offset = fs.offset + len(consumable)

        text = consumable.decode("utf-8", errors="replace")
        for line in text.splitlines():
            ev = parse_line(line)
            if ev is None:
                continue
            await self._ingest(ev, fs)

        fs.offset = new_offset
        if fs.session_id:
            await self._ledger.set_offset(fs.session_id, str(path), fs.offset)

    async def _ingest(self, ev: TranscriptEvent, fs: FileState) -> None:
        session_id = ev.session_id or fs.session_id
        if session_id and fs.session_id is None:
            fs.session_id = session_id

        # Resolve project path lazily; prefer the cwd embedded in events.
        if ev.cwd and not fs.project_path:
            fs.project_path = ev.cwd
            fs.project_hash = project_hash(ev.cwd)
        if not fs.project_path:
            # Best-effort from the sanitized folder name.
            sanitized = fs.path.parent.name
            fs.project_path = _sanitize_to_path(sanitized)
            fs.project_hash = project_hash(fs.project_path)

        if session_id and fs.project_hash and fs.project_path:
            await self._ledger.upsert_session(session_id, fs.project_hash, fs.project_path)
            st = self._state.get_or_create(
                session_id, fs.project_path, fs.project_hash, str(fs.path)
            )
            if ev.kind == "assistant_message":
                st.turns_seen += 1
                st.last_assistant_text = ev.text
                st.last_assistant_at = ev.timestamp
            elif ev.kind == "tool_use" and ev.tool_name:
                st.record_tool_call(ev.tool_name, ev.tool_input)

        log.debug(
            "event session=%s kind=%s tool=%s chars=%d",
            session_id,
            ev.kind,
            ev.tool_name,
            len(ev.text or ""),
        )

        if self._on_event is not None:
            try:
                await self._on_event(ev, fs)
            except Exception:
                log.exception("on_event handler raised")


def _jsonl_filter(change: Change, path: str) -> bool:
    return path.endswith(".jsonl")
