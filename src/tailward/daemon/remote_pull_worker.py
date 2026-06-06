"""Remote auto-pull worker.

Periodically pulls each followed box (see ``remote.load_follow_list``) into
its local mirror on the box's own ``interval_seconds`` cadence, and registers
a newly-pulled box's mirror root with the watcher so its sessions ingest
without a daemon restart. This is the autonomy layer over the manual
``tailward remote pull`` CLI: add a box once, and the daemon keeps its mirror
fresh on its own.

The aggregator is always the local machine (the user's workstation) — pulls
land in the local mirror; nothing runs on the remote box but its existing
``sshd`` + ``rsync``.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from typing import TYPE_CHECKING

from ..config import get_config
from ..remote import (
    RsyncMissingError,
    box_projects_dir,
    load_follow_list,
    pull_followed,
    remote_mirror_root,
)

if TYPE_CHECKING:
    from .app import Daemon

log = logging.getLogger(__name__)

# How often the worker wakes to check which boxes are due. Per-box cadence
# is ``RemoteBox.interval_seconds``; this is just the polling granularity.
POLL_SECONDS = 5.0
# Floor on a box's pull cadence so a misconfigured tiny interval can't
# hammer a remote over SSH.
MIN_INTERVAL_SECONDS = 10.0


class RemotePullWorker:
    def __init__(self, daemon: Daemon) -> None:
        self._daemon = daemon
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        # box name -> monotonic time of last pull attempt.
        self._last_pull: dict[str, float] = {}

    async def start(self) -> None:
        if shutil.which("rsync") is None:
            log.info("remote-pull worker disabled: rsync not on PATH")
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="remote-pull-worker")

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
        while not self._stop.is_set():
            try:
                await self._tick()
            except Exception:
                log.exception("remote-pull tick raised")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=POLL_SECONDS)
                return  # stop event set
            except TimeoutError:
                continue

    async def _tick(self) -> None:
        cfg = get_config()
        if remote_mirror_root(cfg) is None:
            return
        now = time.monotonic()
        for box in load_follow_list():
            if not box.enabled:
                continue
            # Pick up an existing-but-unwatched mirror (covers a box pulled
            # ad-hoc via the CLI while the daemon is running, too).
            self._ensure_watched(cfg, box.name)
            last = self._last_pull.get(box.name)
            interval = max(box.interval_seconds, MIN_INTERVAL_SECONDS)
            if last is not None and (now - last) < interval:
                continue
            await self._pull(cfg, box, now)

    def _ensure_watched(self, cfg, box_name: str) -> None:
        watcher = getattr(self._daemon, "watcher", None)
        if watcher is None:
            return
        try:
            pdir = box_projects_dir(cfg, box_name)
        except ValueError:
            return
        if pdir.is_dir() and not watcher.is_watching(pdir):
            watcher.add_root(pdir, box_name)
            log.info("watching mirror root for box %s (added live)", box_name)

    async def _pull(self, cfg, box, now: float) -> None:
        # ``pull_followed`` shells out to rsync (blocking), so run it off the
        # event loop. Record the attempt time regardless of outcome so a
        # failing box backs off to its interval instead of retrying every poll.
        self._last_pull[box.name] = now
        try:
            result = await asyncio.to_thread(pull_followed, cfg, box)
        except RsyncMissingError as e:
            log.warning("remote pull skipped: %s", e)
            return
        except Exception:
            log.exception("remote pull failed for box %s", box.name)
            return
        if result.returncode != 0:
            log.warning(
                "remote pull for box %s exited %d: %s",
                box.name, result.returncode, (result.stderr or "").strip()[:300],
            )
            return
        # Success — make sure the (possibly brand-new) mirror root is watched.
        self._ensure_watched(cfg, box.name)
