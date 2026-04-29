"""Schema-pinning regression test for the Claude Code JSONL parser.

The parser in :mod:`modmcp.schema.events` makes structural assumptions
about Claude Code's transcript format. Those assumptions are warden's
primary external failure surface (see
``memory/project_upstream_fragility.md``): a major schema change at
the upstream end can silently misclassify every event without raising
a single error, because the parser is deliberately permissive.

This test pins the field shapes we depend on. If Claude Code releases
a major schema change — fields renamed, types reshaped, the
``isCompactSummary`` flag spelled differently — these assertions fire
hard rather than letting the audit silently corrupt.

**Pinned against:** the set of Claude Code versions in
``modmcp.schema.audit.VALIDATED_VERSIONS`` (currently 2.1.117,
2.1.119, 2.1.121). 2.1.119 was the running session at the original
audit on 2026-04-28; 2.1.121 was verified compatible after a fresh
session post-restart. The audit script (``audit_jsonl``) is the
authoritative re-audit tool — see the upstream-fragility memory
for the version-drift-during-restart finding.

Fixtures are synthetic and shape-faithful: they reproduce the
structural skeleton observed in real transcripts without including
verbatim user-prompt content. When validating against a new Claude
Code version: re-run ``audit_jsonl`` against a representative recent
transcript, compare against the field inventory documented in the
upstream-fragility memory, and add the new version to
``VALIDATED_VERSIONS`` when the diff is empty.
"""

from __future__ import annotations

import json

from modmcp.schema.audit import VALIDATED_VERSIONS
from modmcp.schema.events import parse_line

# Most-recent version we've audited against. Used as the value for
# the synthetic ``version`` field in fixtures; the structural
# assertions are version-agnostic but representative fixtures should
# reflect what's actually in the wild.
CLAUDE_CODE_VERSION_TESTED = "2.1.121"
assert CLAUDE_CODE_VERSION_TESTED in VALIDATED_VERSIONS

# Fields the parser hard-depends on at the top level of every event.
EXPECTED_TOP_LEVEL_FIELDS = {
    "type",        # event-type discriminator (_classify)
    "sessionId",   # session_id population
    "cwd",         # project_path resolution (with FileState rehydrate as backstop)
    "message",     # wraps the conversational payload
    "timestamp",   # event ordering + idle gaps
}

# Fields the parser reads off the wrapped message dict.
EXPECTED_MESSAGE_FIELDS = {
    "role",        # disambiguates user/assistant when ``type`` is wrapper-only
    "content",     # text + tool_use + tool_result + thinking blocks
    "id",          # message_id, used to coalesce per-block events into turns
    "model",       # model identity per turn (Platform view)
    "usage",       # token accounting (turn_metrics, llm_call_metrics)
    "stop_reason", # turn-end classification
}

# Top-level event types Claude Code emits that fall to ``kind="unknown"``
# in the parser as of 2026-04-28. They aren't bugs — the parser is
# permissive — but we pin them so a future schema change that *adds*
# them to the recognized set isn't silently merged. Each is an event
# type we've observed in the wild but don't process.
KNOWN_UNHANDLED_TYPES = (
    "permission-mode",
    "last-prompt",
    "file-history-snapshot",
    "attachment",
    "queue-operation",
)


def _line(obj: dict) -> str:
    return json.dumps(obj)


# ---------- assistant_message: plain text response ----------


def test_assistant_message_with_text_block() -> None:
    line = _line(
        {
            "type": "assistant",
            "sessionId": "sess-1",
            "cwd": "C:/warden",
            "timestamp": "2026-04-28T12:00:00.000Z",
            "uuid": "u1",
            "version": CLAUDE_CODE_VERSION_TESTED,
            "gitBranch": "main",
            "message": {
                "role": "assistant",
                "id": "msg_01abc",
                "model": "claude-opus-4-7",
                "type": "message",
                "content": [{"type": "text", "text": "Hello, world."}],
                "stop_reason": "end_turn",
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 5,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
            },
        }
    )
    ev = parse_line(line)
    assert ev is not None
    assert ev.kind == "assistant_message"
    assert ev.session_id == "sess-1"
    assert ev.cwd == "C:/warden"
    assert ev.message_id == "msg_01abc"
    assert ev.model == "claude-opus-4-7"
    assert ev.stop_reason == "end_turn"
    assert ev.text == "Hello, world."
    assert ev.usage is not None
    assert ev.usage["input_tokens"] == 100


# ---------- assistant_message: tool_use embedded in content blocks ----------


def test_assistant_message_with_tool_use_block() -> None:
    """Real Claude Code embeds tool_use blocks inside an assistant
    message's content list, not as a bare ``type=tool_use`` event.
    The parser must surface the tool_name + tool_input from there."""
    line = _line(
        {
            "type": "assistant",
            "sessionId": "sess-1",
            "cwd": "C:/warden",
            "timestamp": "2026-04-28T12:00:01.000Z",
            "message": {
                "role": "assistant",
                "id": "msg_01tool",
                "model": "claude-opus-4-7",
                "type": "message",
                "content": [
                    {"type": "text", "text": "Reading the file."},
                    {
                        "type": "tool_use",
                        "id": "tu_01",
                        "name": "Read",
                        "input": {"file_path": "src/foo.py"},
                    },
                ],
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 50, "output_tokens": 20},
            },
        }
    )
    ev = parse_line(line)
    assert ev is not None
    assert ev.kind == "assistant_message"
    assert ev.tool_name == "Read"
    assert ev.tool_input == {"file_path": "src/foo.py"}
    assert ev.stop_reason == "tool_use"


# ---------- assistant_message: thinking + text blocks ----------


def test_assistant_message_with_thinking_block_drops_thinking_text() -> None:
    """Thinking blocks are intentionally not surfaced as visible text
    by ``_extract_text``. The watcher defers turn emit until visible
    text appears (see app.py). This pins that intentional behavior so
    a refactor doesn't accidentally start including thinking content
    in audit signal."""
    line = _line(
        {
            "type": "assistant",
            "sessionId": "sess-1",
            "cwd": "C:/warden",
            "message": {
                "role": "assistant",
                "id": "msg_01think",
                "model": "claude-opus-4-7",
                "type": "message",
                "content": [
                    {"type": "thinking", "thinking": "internal reasoning..."},
                    {"type": "text", "text": "Here's my answer."},
                ],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 200, "output_tokens": 10},
            },
        }
    )
    ev = parse_line(line)
    assert ev is not None
    assert "internal reasoning" not in ev.text
    assert ev.text == "Here's my answer."


# ---------- user_message: typed prompt ----------


def test_user_message_typed_prompt() -> None:
    line = _line(
        {
            "type": "user",
            "sessionId": "sess-1",
            "cwd": "C:/warden",
            "timestamp": "2026-04-28T12:00:10.000Z",
            "message": {"role": "user", "content": "go ahead"},
        }
    )
    ev = parse_line(line)
    assert ev is not None
    assert ev.kind == "user_message"
    assert ev.text == "go ahead"
    assert ev.synthesized is False
    assert ev.synthesis_kind is None


# ---------- user_message: synthesized /compact summary ----------


def test_user_message_compact_summary_is_flagged_synthesized() -> None:
    """Claude Code's ``/compact`` persists the model-generated summary
    as ``type: "user"`` with ``isCompactSummary: true``. This is the
    most consequential schema field for the trust-layer pitch — if the
    flag goes away or gets renamed, warden silently loses its
    "synthesized turn" detection."""
    line = _line(
        {
            "type": "user",
            "sessionId": "sess-1",
            "cwd": "C:/warden",
            "isCompactSummary": True,
            "isVisibleInTranscriptOnly": True,
            "timestamp": "2026-04-28T12:05:00.000Z",
            "message": {
                "role": "user",
                "content": (
                    "This session is being continued from a previous "
                    "conversation that ran out of context."
                ),
            },
        }
    )
    ev = parse_line(line)
    assert ev is not None
    assert ev.kind == "user_message"
    assert ev.synthesized is True
    assert ev.synthesis_kind == "compact_summary"


# ---------- user_message: tool_result wrapper (NOT a typed prompt) ----------


def test_user_message_tool_result_wrapper_parses() -> None:
    """Claude Code wraps tool *results* as ``type=user`` events with a
    tool_result block in content. The parser produces ``user_message``
    kind, but downstream code (``_looks_like_human_prompt``) filters
    these out before publishing as user_turn."""
    line = _line(
        {
            "type": "user",
            "sessionId": "sess-1",
            "cwd": "C:/warden",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tu_01",
                        "content": "file contents here",
                    }
                ],
            },
        }
    )
    ev = parse_line(line)
    assert ev is not None
    assert ev.kind == "user_message"
    # Synthesized flag must NOT fire on tool results (only on /compact).
    assert ev.synthesized is False


# ---------- system events ----------


def test_system_event_parses() -> None:
    line = _line(
        {
            "type": "system",
            "sessionId": "sess-1",
            "subtype": "init",
            "timestamp": "2026-04-28T12:00:00.000Z",
        }
    )
    ev = parse_line(line)
    assert ev is not None
    assert ev.kind == "system"


def test_system_away_summary_extracts_content() -> None:
    """``type: "system"``, ``subtype: "away_summary"`` events carry a
    ``content`` string at the top level (not inside a wrapped
    ``message``). The dispatcher reads it via ``ev.text`` to publish
    the away_summary LiveBus event. Pin the extraction so a future
    nesting change (e.g., wrapping into ``message.content``) doesn't
    silently drop the signal.

    The subtype is preserved on ``ev.raw`` for the dispatcher's
    branch check."""
    content = (
        "Goal is dogfooding warden. Current task is item 8. "
        "Next action: implement aggregation panel."
    )
    line = _line(
        {
            "type": "system",
            "subtype": "away_summary",
            "sessionId": "sess-1",
            "content": content,
            "timestamp": "2026-04-29T01:00:00.000Z",
        }
    )
    ev = parse_line(line)
    assert ev is not None
    assert ev.kind == "system"
    assert ev.text == content
    # The dispatcher discriminates on subtype directly on ev.raw —
    # pin the field name so a rename trips this test.
    assert ev.raw.get("subtype") == "away_summary"


# ---------- unknown event types: graceful degradation ----------


def test_known_unhandled_types_do_not_crash() -> None:
    """Pin: the event types Claude Code emits that we don't process
    must continue to fall to ``kind="unknown"`` without raising. If
    one of these starts being recognized as a known kind in a future
    Claude Code release, this test fails — surfacing that we should
    decide whether to start consuming it instead of ignoring it."""
    for type_name in KNOWN_UNHANDLED_TYPES:
        line = _line(
            {
                "type": type_name,
                "sessionId": "sess-1",
                "timestamp": "2026-04-28T12:00:00.000Z",
            }
        )
        ev = parse_line(line)
        assert ev is not None, f"parser refused to parse {type_name!r}"
        assert ev.kind == "unknown", (
            f"{type_name!r} should fall to 'unknown' until we explicitly "
            f"add a classifier branch — got {ev.kind!r}"
        )


# ---------- permissionMode field extraction ----------


def test_permission_mode_extracted_from_regular_event() -> None:
    """Claude Code carries ``permissionMode`` on most regular events
    (user / assistant / etc.). The parser extracts it onto
    ``ev.permission_mode`` so the dispatcher can detect transitions
    against carry-forward state in FileState. Pinning this so a
    field rename upstream (e.g., to ``permission_mode`` or
    ``permissions_mode``) trips the test rather than silently dropping
    the signal."""
    line = _line(
        {
            "type": "user",
            "sessionId": "sess-1",
            "cwd": "C:/warden",
            "permissionMode": "acceptEdits",
            "timestamp": "2026-04-29T01:00:00.000Z",
            "message": {"role": "user", "content": "go"},
        }
    )
    ev = parse_line(line)
    assert ev is not None
    assert ev.permission_mode == "acceptEdits"


def test_permission_mode_extracted_from_dedicated_event() -> None:
    """Dedicated ``type: "permission-mode"`` events carry only the
    sessionId + permissionMode (no cwd, no message). Falls to
    ``kind="unknown"`` per KNOWN_UNHANDLED_TYPES, but the
    ``permissionMode`` field MUST still be extracted so the dispatcher
    sees the transition."""
    line = _line(
        {
            "type": "permission-mode",
            "permissionMode": "default",
            "sessionId": "sess-1",
        }
    )
    ev = parse_line(line)
    assert ev is not None
    assert ev.kind == "unknown"  # type itself is unhandled by classifier
    assert ev.permission_mode == "default"


def test_interrupted_extracted_from_tool_use_result() -> None:
    """``toolUseResult.interrupted: true`` is the closest signal Claude
    Code exposes to "user denied a tool call." Pinning the field name
    + nesting so a future move (e.g., flattening to top-level
    ``interrupted``) trips this test rather than silently dropping
    the audit signal."""
    line = _line(
        {
            "type": "user",
            "sessionId": "sess-1",
            "cwd": "C:/warden",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tu_42",
                        "content": "user interrupted",
                    }
                ],
            },
            "toolUseResult": {
                "interrupted": True,
                "stdout": "",
                "stderr": "",
                "isImage": False,
            },
        }
    )
    ev = parse_line(line)
    assert ev is not None
    assert ev.interrupted is True
    assert ev.tool_use_id == "tu_42"


def test_tool_use_id_extracted_from_assistant_emit() -> None:
    """The tool emit (assistant message with tool_use block) carries
    the tool_use_id as ``id`` on the block. The dispatcher caches
    this id → tool_name mapping so a later interrupted result can
    resolve which tool was declined."""
    line = _line(
        {
            "type": "assistant",
            "sessionId": "sess-1",
            "cwd": "C:/warden",
            "message": {
                "role": "assistant",
                "id": "msg_01",
                "model": "claude-opus-4-7",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tu_99",
                        "name": "Bash",
                        "input": {"command": "ls"},
                    }
                ],
            },
        }
    )
    ev = parse_line(line)
    assert ev is not None
    assert ev.tool_name == "Bash"
    assert ev.tool_use_id == "tu_99"


def test_permission_mode_absent_when_field_missing() -> None:
    """Older Claude Code versions or wrapper events may not carry
    ``permissionMode``. The field must default to None (not crash, not
    swallow as empty string)."""
    line = _line(
        {
            "type": "user",
            "sessionId": "sess-1",
            "cwd": "C:/warden",
            "message": {"role": "user", "content": "hello"},
        }
    )
    ev = parse_line(line)
    assert ev is not None
    assert ev.permission_mode is None


# ---------- top-level field inventory ----------


def test_top_level_field_inventory_pins() -> None:
    """Documents the top-level fields the parser depends on. If Claude
    Code renames ``cwd`` to ``working_directory`` (or similar), this
    test still passes — the parser would silently misclassify every
    event into the wrong project_hash. Adding a fixture line that
    *omits* ``cwd`` and asserting the parser falls back gracefully
    documents the sensitivity, but isn't a substitute for a real
    smoke test against captured-in-the-wild transcripts when a new
    Claude Code major version ships.
    """
    sample = {
        "type": "assistant",
        "sessionId": "sess-1",
        "cwd": "C:/warden",
        "timestamp": "2026-04-28T12:00:00.000Z",
        "message": {
            "role": "assistant",
            "id": "msg_01x",
            "model": "claude-opus-4-7",
            "content": [{"type": "text", "text": "ok"}],
        },
    }
    for field in EXPECTED_TOP_LEVEL_FIELDS:
        assert field in sample, (
            f"fixture missing field {field!r} the parser depends on"
        )
    for field in EXPECTED_MESSAGE_FIELDS - {"usage", "stop_reason"}:
        # usage / stop_reason are present per-turn but not on every
        # message-shape (e.g. minimal smoke fixtures), so don't pin
        # those at the inventory level.
        assert field in sample["message"], (
            f"fixture missing message field {field!r}"
        )

    # Sanity: confirm the fixture itself parses cleanly.
    ev = parse_line(_line(sample))
    assert ev is not None
    assert ev.kind == "assistant_message"
    assert ev.cwd == "C:/warden"
    assert ev.message_id == "msg_01x"
