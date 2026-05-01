from __future__ import annotations

import json

from tailward.schema.events import parse_line


def test_parse_user_message_with_string_content() -> None:
    line = json.dumps(
        {
            "type": "user",
            "sessionId": "s1",
            "cwd": "/p",
            "message": {"role": "user", "content": "hello"},
        }
    )
    ev = parse_line(line)
    assert ev is not None
    assert ev.kind == "user_message"
    assert ev.text == "hello"
    assert ev.session_id == "s1"
    assert ev.cwd == "/p"


def test_parse_assistant_with_content_blocks() -> None:
    line = json.dumps(
        {
            "type": "assistant",
            "sessionId": "s2",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Sure."},
                    {"type": "tool_use", "name": "Read", "input": {"path": "a"}},
                ],
            },
        }
    )
    ev = parse_line(line)
    assert ev is not None
    assert ev.kind == "assistant_message"
    assert "Sure." in ev.text
    assert ev.tool_name == "Read"
    assert ev.tool_input == {"path": "a"}


def test_parse_empty_and_garbage() -> None:
    assert parse_line("") is None
    assert parse_line("not json") is None
    ev = parse_line('{"type": "weirdo"}')
    assert ev is not None
    assert ev.kind == "unknown"


def test_parse_compact_summary_flag_detected() -> None:
    """Claude Code's /compact persists its synthesized summary as
    type=user with isCompactSummary=true. The parser must surface that
    flag so downstream code can render it as synthesized rather than
    treating it as a typed user turn."""
    line = json.dumps(
        {
            "type": "user",
            "sessionId": "s3",
            "cwd": "/p",
            "isCompactSummary": True,
            "isVisibleInTranscriptOnly": True,
            "message": {
                "role": "user",
                "content": "This session is being continued from a previous conversation...",
            },
        }
    )
    ev = parse_line(line)
    assert ev is not None
    assert ev.kind == "user_message"
    assert ev.synthesized is True
    assert ev.synthesis_kind == "compact_summary"


def test_parse_regular_user_turn_not_synthesized() -> None:
    line = json.dumps(
        {
            "type": "user",
            "sessionId": "s4",
            "cwd": "/p",
            "message": {"role": "user", "content": "actual prompt"},
        }
    )
    ev = parse_line(line)
    assert ev is not None
    assert ev.synthesized is False
    assert ev.synthesis_kind is None
