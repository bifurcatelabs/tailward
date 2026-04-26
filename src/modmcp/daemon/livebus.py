"""In-process publish/subscribe for the live session view.

Every worker that detects something publishes a typed event here. Two
consumers attach:

1. The SQLite ``live_events`` table — durable replay for page reload and the
   history views.
2. The Server-Sent Events endpoint — one ``asyncio.Queue`` per HTTP
   subscriber, filtered by session id.

Event payloads are JSON-serializable dicts. The ``type`` field is a fixed
vocabulary enforced by :data:`EVENT_TYPES`. Adding a new detection without
registering its type here (and a matching partial renderer client-side) is
the failure mode the regression test in ``tests/test_livebus.py`` guards
against.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)


EVENT_TYPES: frozenset[str] = frozenset(
    {
        "turn",
        "user_turn",
        "tool_call",
        "constraint_violation",
        "scope_snapshot",
        "scope_creep",
        "rubric_in_flight",
        "rubric_sample",
        "rubric_done",
        "report_progress",
        "report_ready",
        "drift",
        "claim",
        "session_closed",
        "turn_metric",
    }
)


@dataclass
class LiveEvent:
    session_id: str
    project_hash: str
    type: str
    payload: dict[str, Any]
    id: int = 0
    created_at: float = field(default_factory=time.time)

    def to_json(self) -> str:
        return json.dumps(
            {
                "id": self.id,
                "session_id": self.session_id,
                "project_hash": self.project_hash,
                "type": self.type,
                "payload": self.payload,
                "created_at": self.created_at,
            },
            ensure_ascii=False,
            default=str,
        )


Persister = Callable[[LiveEvent], Awaitable[int]]


class LiveBus:
    """Per-session fan-out with a bounded subscriber cap.

    ``publish`` is non-blocking on slow subscribers — queues are bounded
    with ``drop-oldest`` semantics so a disconnected browser tab cannot
    back-pressure the daemon. The ledger is the source of truth; clients
    replay missed events via ``live_events_for_session(..., since_id=N)``.
    """

    def __init__(
        self,
        *,
        max_subscribers_per_session: int = 4,
        queue_size: int = 128,
        persist: Persister | None = None,
    ) -> None:
        self._subs: dict[str, list[asyncio.Queue[LiveEvent]]] = {}
        self._max_subs = max_subscribers_per_session
        self._queue_size = queue_size
        self._persist = persist
        self._recent: dict[str, deque[LiveEvent]] = {}
        self._recent_per_session = 100
        self._lock = asyncio.Lock()

    def set_persister(self, persist: Persister) -> None:
        self._persist = persist

    async def publish(
        self,
        session_id: str,
        project_hash: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> LiveEvent:
        if event_type not in EVENT_TYPES:
            raise ValueError(
                f"unknown live event type {event_type!r}; "
                f"register it in livebus.EVENT_TYPES + client partials"
            )
        ev = LiveEvent(
            session_id=session_id,
            project_hash=project_hash,
            type=event_type,
            payload=payload,
        )

        if self._persist is not None:
            try:
                ev.id = await self._persist(ev)
            except Exception:
                log.exception("livebus: persist failed for %s", event_type)

        recent = self._recent.setdefault(
            session_id, deque(maxlen=self._recent_per_session)
        )
        recent.append(ev)

        subs = list(self._subs.get(session_id, ()))
        for q in subs:
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    q.put_nowait(ev)
                except asyncio.QueueFull:
                    pass
        return ev

    async def subscribe(self, session_id: str) -> asyncio.Queue[LiveEvent] | None:
        async with self._lock:
            subs = self._subs.setdefault(session_id, [])
            if len(subs) >= self._max_subs:
                return None
            q: asyncio.Queue[LiveEvent] = asyncio.Queue(maxsize=self._queue_size)
            subs.append(q)
            return q

    async def unsubscribe(
        self, session_id: str, q: asyncio.Queue[LiveEvent]
    ) -> None:
        async with self._lock:
            subs = self._subs.get(session_id)
            if subs and q in subs:
                subs.remove(q)
                if not subs:
                    self._subs.pop(session_id, None)

    def subscriber_count(self, session_id: str) -> int:
        return len(self._subs.get(session_id, ()))
