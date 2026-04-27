"""Surfacing: notify the user of high-stakes drift or contradicted claims.

In the v1.1 passive-first world the **live web UI is the primary surface**:
every high-severity event is published to :class:`~modmcp.daemon.livebus.LiveBus`
and lands in the live session view + report card without touching the model's
prompt. This module handles the two auxiliary channels for situations where
the user isn't looking at the browser:

1. An OS-level toast via ``plyer`` (works in both modes).
2. MCP elicitation — only useful when ``warden_mode == "active"`` and the
   MCP session is connected. The daemon doesn't own the MCP connection, so
   the MCP server process reads surfacing rows from the ledger and relays
   them on elicitation-capable clients.

All three channels write the same surfacing row to the ledger; only the
user-visible notification is debounced.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ..config import get_config
from ..paths import project_dir

if TYPE_CHECKING:
    from .app import Daemon

log = logging.getLogger(__name__)

# Minimum seconds between OS toasts for the same (session, kind) pair. Ledger
# rows and the surfacings/ file artifacts are still written every time — we
# only debounce the user-visible notification so a noisy stretch doesn't
# spam.
_NOTIFY_DEBOUNCE_SECONDS = 90.0


class Surfacer:
    def __init__(self, daemon: Daemon) -> None:
        self._daemon = daemon
        self._last_notified: dict[tuple[str, str], float] = {}

    async def surface(
        self,
        session_id: str,
        project_hash: str,
        kind: str,
        severity: str,
        text: str,
    ) -> None:
        sid = await self._daemon.ledger.record_surfacing(
            session_id, project_hash, kind, severity, text
        )

        session = await self._daemon.ledger.get_session(session_id)
        project_path = session["project_path"] if session else None

        # File artifact per [V1 Proposal.md §8]
        if project_path:
            surf_dir = project_dir(project_path) / "surfacings"
            surf_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
            (surf_dir / f"{kind}-{severity}-{sid}-{ts}.md").write_text(
                f"# {kind} ({severity})\n\n{text}\n", encoding="utf-8"
            )

        self._notify(session_id, kind, severity, text)

    def _notify(self, session_id: str, kind: str, severity: str, text: str) -> None:
        # Off by default — the live web UI is the primary surface and
        # an OS toast on top of it is redundant for an interactive
        # session. The ledger row + LiveBus event have already fired by
        # the time we get here.
        if not get_config().os_notifications_enabled:
            return
        key = (session_id, kind)
        now = time.monotonic()
        last = self._last_notified.get(key, 0.0)
        if now - last < _NOTIFY_DEBOUNCE_SECONDS:
            log.info("notification debounced for %s/%s", session_id, kind)
            return
        self._last_notified[key] = now

        try:
            from plyer import notification  # type: ignore

            notification.notify(  # type: ignore[attr-defined]
                title=f"warden: {kind} ({severity})",
                message=text[:200],
                app_name="warden",
                timeout=10,
            )
        except Exception as e:
            log.info("notification suppressed (%s); text: %s", e, text[:200])
