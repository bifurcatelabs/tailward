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
    turns_seen: int = 0
    last_assistant_text: str = ""
    last_assistant_at: datetime | None = None
    recent_tool_calls: list[dict[str, Any]] = field(default_factory=list)
    preamble_delivered: bool = False

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
        self, session_id: str, project_path: str, project_hash: str, jsonl_path: str
    ) -> SessionState:
        st = self._by_session.get(session_id)
        if st is None:
            st = SessionState(
                session_id=session_id,
                project_path=project_path,
                project_hash=project_hash,
                jsonl_path=jsonl_path,
            )
            self._by_session[session_id] = st
        return st

    def get(self, session_id: str) -> SessionState | None:
        return self._by_session.get(session_id)

    def all(self) -> list[SessionState]:
        return list(self._by_session.values())

    def drop(self, session_id: str) -> None:
        self._by_session.pop(session_id, None)
