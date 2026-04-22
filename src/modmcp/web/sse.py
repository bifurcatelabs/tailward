"""Server-Sent Events endpoint for the live session view.

Reads replay from the ledger, subscribes to :class:`~modmcp.daemon.livebus.LiveBus`,
and yields ``data:`` frames in SSE wire format. Keepalive comments stop
long-polling proxies from cutting the connection. Subscriber caps are
enforced at the bus level; we gracefully 503 if a session is saturated so
the browser backs off rather than thrashing.
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import Request
from fastapi.responses import PlainTextResponse, StreamingResponse

from ..config import get_config

log = logging.getLogger(__name__)


def stream_for_session(request: Request, ph: str, session_id: str):
    daemon = request.app.state.daemon
    ledger = daemon.ledger
    bus = daemon.live
    cfg = get_config()

    async def event_gen():
        try:
            since_id = int(request.query_params.get("since", "0") or 0)
        except ValueError:
            since_id = 0

        replay = await ledger.live_events_for_session(
            session_id, since_id=since_id, limit=cfg.live_sse_replay_events
        )
        for row in replay:
            yield _frame_from_row(row)

        queue = await bus.subscribe(session_id)
        if queue is None:
            yield "event: saturated\ndata: {\"reason\":\"too many subscribers\"}\n\n"
            return

        try:
            yield f"event: hello\ndata: {json.dumps({'session': session_id})}\n\n"
            while True:
                if await request.is_disconnected():
                    return
                try:
                    ev = await asyncio.wait_for(
                        queue.get(), timeout=cfg.live_sse_keepalive_seconds
                    )
                    yield _frame_from_event(ev)
                except TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            await bus.unsubscribe(session_id, queue)

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(
        event_gen(), media_type="text/event-stream", headers=headers
    )


def _frame_from_event(ev) -> str:
    return f"id: {ev.id}\nevent: {ev.type}\ndata: {ev.to_json()}\n\n"


def _frame_from_row(row: dict) -> str:
    return f"id: {row['id']}\nevent: {row['event_type']}\ndata: {row['payload']}\n\n"


async def poll_events(request: Request, ph: str, session_id: str) -> PlainTextResponse:
    """Fallback polling endpoint when EventSource isn't available."""
    daemon = request.app.state.daemon
    try:
        since_id = int(request.query_params.get("since", "0") or 0)
    except ValueError:
        since_id = 0
    rows = await daemon.ledger.live_events_for_session(
        session_id, since_id=since_id, limit=500
    )
    body = {
        "events": [
            {
                "id": r["id"],
                "event_type": r["event_type"],
                "payload": json.loads(r["payload"]) if r["payload"] else {},
                "created_at": r["created_at"],
            }
            for r in rows
        ],
        "next_since": rows[-1]["id"] if rows else since_id,
    }
    return PlainTextResponse(
        json.dumps(body, default=str), media_type="application/json"
    )
