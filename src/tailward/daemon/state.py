"""In-memory per-session state tracked by the daemon."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class SessionState:
    session_id: str
    project_path: str
    project_hash: str
    jsonl_path: str
    # Remote-provenance label: "" for local sessions, else the followed
    # box name. Carried so workers that derive on-disk project state
    # (synthesis snapshots, intent.md) locate it under the same
    # box-aware project_hash the session is keyed by.
    box: str = ""
    turns_seen: int = 0
    last_assistant_text: str = ""
    last_assistant_at: datetime | None = None
    recent_tool_calls: list[dict[str, Any]] = field(default_factory=list)
    # Last seen ``message.id`` from an assistant event. Claude Code splits
    # a logical turn across multiple JSONL events (one per content block);
    # all blocks of one turn share this id, so the watcher can recover
    # the human-perceived turn count by comparing against this value.
    last_message_id: str | None = None
    # Cumulative token usage across the whole session. Sums apply once
    # per logical turn (when message_id changes), not per content block,
    # because Claude Code duplicates the usage block across all events
    # of a turn.
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_read_tokens: int = 0
    total_cache_creation_tokens: int = 0
    last_model: str | None = None
    # Tracks which logical turns we've already emitted a ``turn``
    # LiveBus event for. We defer the emit past leading ``thinking``
    # blocks so the feed entry's preview shows real prose / tool action,
    # not an empty header. Each message_id gets at most one turn entry
    # in the feed.
    last_turn_emit_msg_id: str | None = None

    # In-flight assistant turn used to derive per-turn metrics
    # (prompt_to_response_ms, response_duration_ms, output_tps,
    # cache_hit_ratio). Closed and persisted to the turn_metrics table
    # when the next logical turn starts or a user_message arrives.
    in_flight_turn: dict[str, Any] | None = None
    # Wall-clock of the most recent user_message (datetime). Pairs with
    # the next assistant turn's first block to compute prompt latency.
    last_user_msg_at: datetime | None = None

    def record_tool_call(self, name: str, tool_input: dict[str, Any] | None) -> None:
        self.recent_tool_calls.append(
            {"name": name, "input": tool_input or {}, "at": datetime.utcnow().isoformat()}
        )
        if len(self.recent_tool_calls) > 200:
            self.recent_tool_calls = self.recent_tool_calls[-200:]


class StateStore:
    """Registry of live sessions."""

    def __init__(self) -> None:
        self._by_session: dict[str, SessionState] = {}

    def get_or_create(
        self,
        session_id: str,
        project_path: str,
        project_hash: str,
        jsonl_path: str,
        box: str = "",
    ) -> SessionState:
        st = self._by_session.get(session_id)
        if st is None:
            st = SessionState(
                session_id=session_id,
                project_path=project_path,
                project_hash=project_hash,
                jsonl_path=jsonl_path,
                box=box,
            )
            self._by_session[session_id] = st
        return st

    def hydrate(self, row: dict) -> SessionState | None:
        """Restore a SessionState from a persisted ``session_state`` row.

        Called by the watcher on daemon start so cumulative counters
        (turns_seen, total_*_tokens, last_message_id, last_model)
        survive a restart instead of resetting to zero. If the session
        is already in memory (e.g. another caller raced ahead) we leave
        the live copy untouched.
        """
        sid = row.get("session_id")
        if not sid or sid in self._by_session:
            return self._by_session.get(sid) if sid else None
        st = SessionState(
            session_id=sid,
            project_path=row.get("project_path") or "",
            project_hash=row.get("project_hash") or "",
            jsonl_path="",  # filled by the watcher when the next event arrives
            box=row.get("box") or "",
            turns_seen=int(row.get("turns_seen") or 0),
            last_message_id=row.get("last_message_id"),
            total_input_tokens=int(row.get("total_input_tokens") or 0),
            total_output_tokens=int(row.get("total_output_tokens") or 0),
            total_cache_read_tokens=int(row.get("total_cache_read_tokens") or 0),
            total_cache_creation_tokens=int(row.get("total_cache_creation_tokens") or 0),
            last_model=row.get("last_model"),
        )
        self._by_session[sid] = st
        return st

    def get(self, session_id: str) -> SessionState | None:
        return self._by_session.get(session_id)

    def all(self) -> list[SessionState]:
        return list(self._by_session.values())

    def drop(self, session_id: str) -> None:
        self._by_session.pop(session_id, None)
