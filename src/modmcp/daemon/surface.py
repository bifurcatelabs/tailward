"""Surfacing: notify the user of high-stakes drift or contradicted claims.

MCP elicitation is the preferred channel, but it requires cooperation from
the MCP session. Since the daemon doesn't own the MCP connection in v1, the
MCP server process writes surfacings to the ledger; the agent (or a separate
notification) picks them up. The OS-level fallback (plyer) guarantees the
user sees something regardless.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

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
                title=f"modmcp: {kind} ({severity})",
                message=text[:200],
                app_name="modmcp",
                timeout=10,
            )
        except Exception as e:
            log.info("notification suppressed (%s); text: %s", e, text[:200])
