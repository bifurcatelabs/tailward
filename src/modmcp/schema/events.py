"""Claude Code transcript event models.

Claude Code writes JSONL files at ``~/.claude/projects/<sanitized>/<session>.jsonl``.
Each line is a JSON object describing a conversation event. The exact field set has
drifted across versions; we parse defensively into a permissive model and expose
the canonical view the rest of the codebase needs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

EventType = Literal[
    "user_message", "assistant_message", "tool_use", "tool_result", "system", "unknown"
]


@dataclass
class TranscriptEvent:
    raw: dict[str, Any]
    kind: EventType
    session_id: str | None
    timestamp: datetime | None
    text: str
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None
    tool_output: str | None = None
    cwd: str | None = None


def _parse_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value))
        except (OSError, ValueError):
            return None
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _extract_text(msg: Any) -> str:
    """Claude Code messages may have string content or a list of content blocks."""
    if msg is None:
        return ""
    if isinstance(msg, str):
        return msg
    if isinstance(msg, dict):
        content = msg.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    parts.append(block.get("text", ""))
                elif btype == "tool_use":
                    name = block.get("name", "?")
                    parts.append(f"[tool_use:{name}]")
                elif btype == "tool_result":
                    parts.append(str(block.get("content", "")))
            return "\n".join(p for p in parts if p)
    return ""


def _classify(obj: dict[str, Any]) -> EventType:
    t = obj.get("type")
    if t in {"user", "human"}:
        return "user_message"
    if t in {"assistant", "ai"}:
        return "assistant_message"
    if t in {"tool_use"}:
        return "tool_use"
    if t in {"tool_result"}:
        return "tool_result"
    if t in {"system"}:
        return "system"

    # Messages are sometimes wrapped: {"type": "assistant", "message": {...}}
    msg = obj.get("message")
    if isinstance(msg, dict):
        role = msg.get("role")
        if role == "user":
            return "user_message"
        if role == "assistant":
            return "assistant_message"
    return "unknown"


def parse_line(line: str) -> TranscriptEvent | None:
    """Parse a single JSONL line into a canonical event, or ``None`` if empty."""
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None

    kind = _classify(obj)
    session_id = obj.get("sessionId") or obj.get("session_id") or obj.get("session")
    ts = _parse_timestamp(obj.get("timestamp") or obj.get("ts"))
    cwd = obj.get("cwd") or obj.get("projectPath") or obj.get("project_path")

    message = obj.get("message", obj)
    text = _extract_text(message)

    tool_name = None
    tool_input = None
    tool_output = None
    if kind == "tool_use":
        tool_name = obj.get("name") or (message or {}).get("name")
        tool_input = obj.get("input") or (message or {}).get("input")
    elif kind == "tool_result":
        tool_output = text or str(obj.get("content", ""))
    elif kind in ("user_message", "assistant_message") and isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tool_name = block.get("name")
                    tool_input = block.get("input")
                    break

    return TranscriptEvent(
        raw=obj,
        kind=kind,
        session_id=str(session_id) if session_id else None,
        timestamp=ts,
        text=text,
        tool_name=tool_name,
        tool_input=tool_input,
        tool_output=tool_output,
        cwd=str(cwd) if cwd else None,
    )


_EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "Update", "NotebookEdit", "Create"}
_PATH_KEYS = ("file_path", "path", "notebook_path", "target_file", "filePath")


def target_paths(ev: TranscriptEvent) -> list[str]:
    """Extract file paths targeted by an Edit/Write/MultiEdit-style tool call.

    Returns an empty list for reads, searches, bash, or any tool we don't
    classify as a filesystem mutation. Callers should still check whether a
    Bash command happens to be mutating (see :func:`bash_command`).
    """
    if ev.tool_input is None:
        return []

    paths: list[str] = []

    if ev.tool_name in _EDIT_TOOLS:
        for key in _PATH_KEYS:
            val = ev.tool_input.get(key)
            if isinstance(val, str) and val:
                paths.append(val)
                break
        edits = ev.tool_input.get("edits")
        if isinstance(edits, list):
            for edit in edits:
                if isinstance(edit, dict):
                    for key in _PATH_KEYS:
                        v = edit.get(key)
                        if isinstance(v, str) and v and v not in paths:
                            paths.append(v)

    if ev.tool_name == "Write" or ev.tool_name == "Create":
        pass

    return paths


def bash_command(ev: TranscriptEvent) -> str | None:
    """Return the ``command`` string of a Bash tool call, if applicable."""
    if ev.tool_input is None:
        return None
    if ev.tool_name not in {"Bash", "Shell", "run_shell"}:
        return None
    cmd = ev.tool_input.get("command") or ev.tool_input.get("cmd")
    if isinstance(cmd, str) and cmd.strip():
        return cmd
    return None
