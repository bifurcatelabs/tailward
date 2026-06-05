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


def _path_matches_filter(jsonl_path: Path, root: Path, entries: list[str]) -> bool:
    """True when ``jsonl_path`` matches any entry in ``entries``.

    Each entry is matched against either the sanitized folder name
    (Claude Code's per-project directory name, e.g. ``C--warden``) or
    the de-sanitized form of that name. Both forms are accepted so users
    can write ``C:/warden`` or ``C--warden`` — whichever they prefer —
    in their config and have it work.
    """
    try:
        rel = jsonl_path.relative_to(root)
    except ValueError:
        return False
    if not rel.parts:
        return False
    sanitized = rel.parts[0]
    candidates = {sanitized, _sanitize_to_path(sanitized)}
    return any(entry in candidates for entry in entries)

log = logging.getLogger(__name__)

EventHandler = Callable[[TranscriptEvent, "FileState", bool], Awaitable[None]]
"""Callback signature: ``(event, file_state, is_backlog) -> Awaitable[None]``.

``is_backlog=True`` for events parsed from existing file content during
``_prime_existing`` (the initial scan at watcher start). ``False`` for
events that arrive via filesystem-change notifications after start.
Handlers use this to skip costly LLM-call workers on backlog while
still recording structural data (turns, tool calls) to the ledger
and live-bus for display."""


class FileState:
    __slots__ = (
        "path",
        "offset",
        "session_id",
        "project_path",
        "project_hash",
        "last_permission_mode",
        "tool_use_names",
    )

    def __init__(self, path: Path) -> None:
        self.path = path
        self.offset = 0
        self.session_id: str | None = None
        self.project_path: str | None = None
        self.project_hash: str | None = None
        # Carry-forward state for detecting permission-mode transitions.
        # ``None`` until the first event carrying ``permissionMode``; on
        # subsequent events that carry a different value, the dispatcher
        # emits a ``permission_mode_change`` LiveBus event. Not persisted
        # to session_state — daemon restart loses one transition at most,
        # which is acceptable.
        self.last_permission_mode: str | None = None
        # In-memory map of tool_use_id -> tool_name for resolving the
        # tool name on a later tool_result (specifically for interrupted
        # results — the result's content block has only the tool_use_id,
        # not the name). Bounded to ~200 entries so a long session
        # doesn't grow unbounded; FIFO eviction is fine because tool
        # results almost always arrive within a few events of the emit.
        self.tool_use_names: dict[str, str] = {}


def _sanitize_to_path(sanitized: str) -> str:
    """Claude Code encodes project paths by replacing path separators with '-'.

    We can't always reverse this unambiguously, but we can take a best-effort
    stab. The authoritative project_path is extracted from the transcript
    events themselves (``cwd`` field) as soon as we see one; this is only a
    fallback.
    """
    # Windows: "C--Users-example-code-repo" -> "C:/Users/example/code/repo"
    # POSIX:   "-Users-example-code-repo"   -> "/Users/example/code/repo"
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
        roots: list[Path] | None = None,
        watch_paths: list[str] | None = None,
        exclude_paths: list[str] | None = None,
    ) -> None:
        self._state = state
        self._ledger = ledger
        self._on_event = on_event
        # Multiple "projects roots" are watched identically: the local
        # ``~/.claude/projects/`` plus, optionally, one ``<box>/projects``
        # directory per followed remote box (transcript mirrors). Each
        # root behaves the same — the sanitized per-project folder is its
        # immediate child, so all path logic (``relative_to``, the
        # sanitized-name fallback, the watch/exclude filters) works
        # unchanged once it's keyed to the *owning* root rather than a
        # single global one. ``roots`` takes precedence; ``root`` is the
        # back-compat single-root form; default is the local root only.
        if roots is not None:
            self._roots = [Path(r) for r in roots]
        elif root is not None:
            self._roots = [Path(root)]
        else:
            self._roots = [claude_projects_root()]
        # Primary root — first in the list. Retained for callers/tests
        # that reference a single root and for log messages.
        self._root = self._roots[0]
        self._files: dict[Path, FileState] = {}
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        # Empty list = no constraint (watch everything). See
        # Config.watch_paths / Config.exclude_paths for semantics.
        self._watch_paths = list(watch_paths or ())
        self._exclude_paths = list(exclude_paths or ())
        # Project hashes the user has opted into deep-parse via the
        # Seed mechanism. Loaded from the ledger on ``_run`` start;
        # mutated in-memory by ``seed_project``. Non-seeded projects
        # have their JSONLs offset-marked at EOF on prime so
        # pre-existing content stays invisible until seeded, while
        # real-time appends (via the awatch loop) still process.
        self._seeded_hashes: set[str] = set()

    def _root_for(self, path: Path) -> Path | None:
        """Return the watched root that contains ``path``, or ``None``.

        Project-path logic (the sanitized-folder name, the watch/exclude
        filters) is all relative to *which* root a JSONL lives under, so
        every per-path operation resolves its owning root first."""
        for r in self._roots:
            try:
                path.relative_to(r)
                return r
            except ValueError:
                continue
        return None

    def _iter_jsonl(self):
        """Yield every ``*.jsonl`` under all watched roots that exist."""
        for r in self._roots:
            if not r.exists():
                continue
            yield from r.rglob("*.jsonl")

    def _path_allowed(self, jsonl_path: Path) -> bool:
        root = self._root_for(jsonl_path)
        if root is None:
            return False
        if self._exclude_paths and _path_matches_filter(
            jsonl_path, root, self._exclude_paths
        ):
            return False
        if self._watch_paths and not _path_matches_filter(
            jsonl_path, root, self._watch_paths
        ):
            return False
        return True

    async def start(self) -> None:
        self._stop.clear()
        # ``_prime_done`` lets ``start()`` block until the prime pass
        # has finished. Without this, the watcher task is scheduled
        # but may not begin executing before downstream code (tests
        # writing fixture events, the daemon serving requests) starts
        # mutating the watched filesystem. Prime would then see the
        # mutations as "pre-existing content" and mark-eof the offset,
        # silently dropping events. Lifespan startup awaits start(),
        # so blocking here ensures the daemon is fully primed before
        # serving anything.
        self._prime_done = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name="transcript-watcher")
        await self._prime_done.wait()

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
        if not any(r.exists() for r in self._roots):
            log.warning(
                "no watched projects root exists yet (%s); waiting",
                ", ".join(str(r) for r in self._roots),
            )
            while not any(r.exists() for r in self._roots) and not self._stop.is_set():
                await asyncio.sleep(2.0)
            if self._stop.is_set():
                # Unblock start() before bailing.
                self._prime_done.set()
                return

        # Hydrate in-memory SessionState from the persisted session_state
        # rows so cumulative counters (turns_seen, totals, last_model,
        # last_message_id) survive a daemon restart.
        try:
            for row in await self._ledger.all_session_state():
                self._state.hydrate(row)
        except Exception:
            log.exception("session-state hydrate failed; continuing fresh")

        # Load the seeded-project set. Non-seeded projects have their
        # JSONLs skipped on prime; only their post-startup appends
        # process via the awatch loop.
        try:
            self._seeded_hashes = await self._ledger.seeded_project_hashes()
        except Exception:
            log.exception(
                "seeded_project_hashes load failed; treating no projects "
                "as seeded (prime pass will skip all historical content)"
            )
            self._seeded_hashes = set()

        try:
            await self._prime_existing()
        finally:
            # Signal start() that prime is done (or has failed) so
            # the lifespan caller unblocks. Done in finally so an
            # exception during prime doesn't leave start() hung.
            self._prime_done.set()

        # ``awatch`` raises if handed a non-existent path, so only watch
        # roots that exist right now. A remote-mirror root created after
        # daemon start (first pull of a new box) is picked up on the next
        # restart — interval auto-discovery is a later phase.
        watch_roots = [r for r in self._roots if r.exists()]
        try:
            async for changes in awatch(
                *watch_roots,
                recursive=True,
                stop_event=self._stop,
                watch_filter=_jsonl_filter,
            ):
                for _change, path_str in changes:
                    p = Path(path_str)
                    if p.suffix != ".jsonl":
                        continue
                    if not self._path_allowed(p):
                        continue
                    # Real-time path: filesystem change after watcher
                    # start. Events get full worker enqueue.
                    await self._process_file(p, is_backlog=False)
        except RuntimeError as e:
            # watchfiles raises if a watched root disappears mid-run; treat
            # that as shutdown. Anything else is a real bug — log it so it
            # doesn't vanish silently.
            if self._stop.is_set() or not any(r.exists() for r in self._roots):
                return
            log.exception("transcript watcher exited on unexpected RuntimeError: %s", e)

    async def _prime_existing(self) -> None:
        # Initial scan. Events parsed here predate watcher start, so
        # they're "backlog" (handlers skip LLM-call workers on these
        # while still recording structural data for UI rendering).
        #
        # Per-project gating: only seeded projects get their historical
        # content parsed. Non-seeded non-empty files have their JSONL
        # offsets advanced to EOF — that prevents the first real-time
        # append from being read against a stale offset and firing a
        # burst of "new" events on pre-existing content. Empty files
        # fall through to a normal ``_process_file`` call that will
        # early-return on size==offset==0; this preserves the
        # ``self._files`` registration that downstream awatch processing
        # expects on first encounter of a freshly-created file.
        for jsonl in self._iter_jsonl():
            if not self._path_allowed(jsonl):
                continue
            if self._is_seeded(jsonl):
                await self._process_file(jsonl, is_backlog=True)
            else:
                try:
                    size = jsonl.stat().st_size
                except FileNotFoundError:
                    continue
                if size == 0:
                    # Empty file: register FileState via _process_file
                    # (which will early-return), no offset write needed.
                    await self._process_file(jsonl, is_backlog=True)
                else:
                    await self._mark_offset_at_eof(jsonl)

    def _is_seeded(self, jsonl_path: Path) -> bool:
        """Check whether the project containing this JSONL has been
        opted into deep-parse via the Seed mechanism.

        Reads the first event line from the JSONL to extract its cwd
        and compute the canonical project_hash. Falls back to the
        sanitized-folder-name derivation when the file is empty or
        the first line lacks a cwd. Reading first-line is necessary
        because Claude Code's sanitized folder names don't always
        round-trip cleanly to the original path — sessions in the
        same project can share a cwd that doesn't match what the
        sanitization heuristic produces. The cwd-derived hash is
        what the ``/seed`` API and the UI use, so checking against
        that ensures the seeded set actually matches user intent."""
        try:
            with open(jsonl_path, "rb") as f:
                first = f.readline()
        except OSError:
            return False
        if first:
            ev = parse_line(first.decode("utf-8", errors="replace"))
            if ev is not None and ev.cwd:
                return project_hash(ev.cwd) in self._seeded_hashes
        # Empty file or first line lacks cwd — best-effort fallback.
        root = self._root_for(jsonl_path)
        if root is None:
            return False
        try:
            rel = jsonl_path.relative_to(root)
        except ValueError:
            return False
        if not rel.parts:
            return False
        sanitized = rel.parts[0]
        candidate_hash = project_hash(_sanitize_to_path(sanitized))
        return candidate_hash in self._seeded_hashes

    async def _mark_offset_at_eof(self, path: Path) -> None:
        """For non-seeded JSONLs at prime time: persist an offset equal
        to the current file size so the awatch loop's first event
        reads only post-startup appends — not pre-existing content
        that would otherwise be processed as 'new' real-time events
        and fire LLM-call workers on backlog material.

        Empty files are skipped (offset 0 == file size 0 is a no-op
        anyway, and writing it can interact awkwardly with watchfiles
        timing on freshly-touched fixture files)."""
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            return
        if size == 0:
            return
        session_id = path.stem
        try:
            await self._ledger.set_offset(session_id, str(path), size)
        except Exception:
            log.exception(
                "set_offset(EOF) failed for %s; non-seeded project may "
                "burst-fire workers on first real-time append",
                session_id,
            )

    async def seed_project(self, project_hash_value: str) -> int:
        """Mark a project as seeded and parse all its existing JSONL
        content. Returns the number of files processed.

        Workers fire per the backlog/realtime distinction — rule-based
        (constraints, scope) on every event, LLM-cost (drift, audit,
        rubric, user_rubric, synthesis) skip backlog. So seeding a
        project is cheap on the user's local LLM even for projects
        with long history.

        Idempotent: re-seeding an already-seeded project re-parses
        from scratch (offset reset to 0). Useful if the user wants to
        rebuild the ledger view of a project's history.

        Wrapped in ``ledger.batch_commits()`` so the thousands of
        per-event ledger writes commit once on exit instead of once
        per call. Empirical: turns a 25-file / 34k-event re-seed
        from ~4m20s of fsync-dominated wall-clock into seconds, with
        much larger headroom on slower storage subsystems where the
        per-fsync latency dominates.
        """
        async with self._ledger.batch_commits():
            await self._ledger.mark_project_seeded(project_hash_value)
            self._seeded_hashes.add(project_hash_value)

            count = 0
            for jsonl in self._iter_jsonl():
                if not self._path_allowed(jsonl):
                    continue
                # Resolve the JSONL's project via cwd-from-first-line
                # (preferred, matches what /seed received from the UI)
                # with sanitized-folder-name as fallback.
                candidate_hash: str | None = None
                try:
                    with open(jsonl, "rb") as f:
                        first = f.readline()
                except OSError:
                    first = b""
                if first:
                    ev = parse_line(first.decode("utf-8", errors="replace"))
                    if ev is not None and ev.cwd:
                        candidate_hash = project_hash(ev.cwd)
                if candidate_hash is None:
                    root = self._root_for(jsonl)
                    if root is None:
                        continue
                    try:
                        rel = jsonl.relative_to(root)
                    except ValueError:
                        continue
                    if not rel.parts:
                        continue
                    candidate_hash = project_hash(_sanitize_to_path(rel.parts[0]))
                if candidate_hash != project_hash_value:
                    continue
                session_id = jsonl.stem
                # Reset offset and forget any cached FileState so
                # ``_process_file`` reads from byte 0 and re-parses.
                await self._ledger.set_offset(session_id, str(jsonl), 0)
                self._files.pop(jsonl, None)
                await self._process_file(jsonl, is_backlog=True)
                count += 1
        return count

    async def _process_file(self, path: Path, *, is_backlog: bool = False) -> None:
        fs = self._files.get(path)
        if fs is None:
            fs = FileState(path)
            # Seed offset from ledger if we know this session.
            session_id = path.stem
            stored = await self._ledger.get_offset(session_id)
            fs.offset = stored
            fs.session_id = session_id
            # Rehydrate the project from session_state if this session
            # is already known. Without this, a daemon restart while
            # the user's cwd is in a subdir (e.g. ``C:\warden\frontend``
            # for an ``npm run build``) would seed FileState from the
            # next post-offset event's cwd — which is the subdir, not
            # the project root — and every downstream worker would
            # persist under a phantom project_hash. The session_state
            # row carries the canonical pair; trust it.
            try:
                sess = await self._ledger.get_session(session_id)
            except Exception:
                sess = None
            if sess:
                fs.project_path = sess["project_path"]
                fs.project_hash = sess["project_hash"]
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
            await self._ingest(ev, fs, is_backlog=is_backlog)

        fs.offset = new_offset
        if fs.session_id:
            await self._ledger.set_offset(fs.session_id, str(path), fs.offset)

    async def _ingest(
        self, ev: TranscriptEvent, fs: FileState, *, is_backlog: bool = False
    ) -> None:
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
            await self._ledger.upsert_session(
                session_id, fs.project_hash, fs.project_path, is_backlog=is_backlog
            )
            st = self._state.get_or_create(
                session_id, fs.project_path, fs.project_hash, str(fs.path)
            )
            if ev.kind == "assistant_message":
                # Coalesce on message_id: every content block of one
                # logical turn shares the same id. Synthetic events
                # without an id (tests, future formats) fall back to
                # one-turn-per-event behavior.
                ev.new_turn = (
                    ev.message_id is None
                    or ev.message_id != st.last_message_id
                )
                if ev.new_turn:
                    st.turns_seen += 1
                    st.last_message_id = ev.message_id
                    if ev.usage:
                        st.total_input_tokens += int(
                            ev.usage.get("input_tokens") or 0
                        )
                        st.total_output_tokens += int(
                            ev.usage.get("output_tokens") or 0
                        )
                        st.total_cache_read_tokens += int(
                            ev.usage.get("cache_read_input_tokens") or 0
                        )
                        st.total_cache_creation_tokens += int(
                            ev.usage.get("cache_creation_input_tokens") or 0
                        )
                    # Claude Code uses ``<synthetic>`` for compaction /
                    # system summarization passes; those aren't real model
                    # invocations and should not overwrite the displayed
                    # model identity.
                    if ev.model and ev.model != "<synthetic>":
                        st.last_model = ev.model
                    # Persist the cumulative-progress columns so a
                    # daemon restart hydrates back to the same values
                    # instead of resetting to zero.
                    try:
                        await self._ledger.update_session_progress(
                            session_id,
                            turns_seen=st.turns_seen,
                            last_message_id=st.last_message_id,
                            total_input_tokens=st.total_input_tokens,
                            total_output_tokens=st.total_output_tokens,
                            total_cache_read_tokens=st.total_cache_read_tokens,
                            total_cache_creation_tokens=st.total_cache_creation_tokens,
                            last_model=st.last_model,
                        )
                    except Exception:
                        log.exception(
                            "session-progress persist failed for %s",
                            session_id,
                        )
                if ev.text:
                    st.last_assistant_text = ev.text
                if ev.timestamp:
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
                await self._on_event(ev, fs, is_backlog)
            except Exception:
                log.exception("on_event handler raised")


def _jsonl_filter(change: Change, path: str) -> bool:
    return path.endswith(".jsonl")
