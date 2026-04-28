"""Tests for the schema-audit utility.

Covers two slices:

1. ``audit_jsonl`` walks a JSONL and produces a structured report
   matching the empirical audit script that produced the
   ``project_upstream_fragility.md`` memory.
2. ``is_validated_version`` reports honestly when an event carries a
   Claude Code version warden hasn't been audited against — the
   intended use is a future ``warden doctor`` warning.
"""

from __future__ import annotations

import json
from pathlib import Path

from modmcp.schema.audit import (
    VALIDATED_VERSIONS,
    audit_jsonl,
    claude_code_version,
    is_validated_version,
)


def _write(path: Path, lines: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(o) for o in lines) + "\n", encoding="utf-8")
    return path


def test_audit_inventories_types_keys_and_versions(tmp_path: Path) -> None:
    jsonl = _write(
        tmp_path / "session.jsonl",
        [
            {
                "type": "assistant",
                "version": "2.1.119",
                "sessionId": "s1",
                "cwd": "/p",
                "message": {
                    "role": "assistant",
                    "id": "m1",
                    "content": [
                        {"type": "text", "text": "hello"},
                        {"type": "tool_use", "name": "Read"},
                    ],
                },
            },
            {
                "type": "user",
                "version": "2.1.119",
                "sessionId": "s1",
                "cwd": "/p",
                "message": {"role": "user", "content": "go"},
            },
            {
                "type": "permission-mode",
                "version": "2.1.119",
                "sessionId": "s1",
            },
            # Older session with an earlier version.
            {
                "type": "assistant",
                "version": "2.1.117",
                "sessionId": "s0",
                "cwd": "/p",
                "message": {
                    "role": "assistant",
                    "id": "m0",
                    "content": [{"type": "thinking", "thinking": "..."}],
                },
            },
            # Garbage line; should be tolerated.
            {"type": None},
        ],
    )

    report = audit_jsonl(jsonl)

    assert report.lines_total == 5
    assert report.lines_parsed == 5
    assert report.top_types["assistant"] == 2
    assert report.top_types["user"] == 1
    assert report.top_types["permission-mode"] == 1
    # The dict line whose ``type`` is None falls under ``<missing>``.
    assert report.top_types["<missing>"] == 1

    assert report.versions["2.1.119"] == 3
    assert report.versions["2.1.117"] == 1
    assert report.latest_version == "2.1.119"

    assert report.message_keys["role"] == 3
    assert report.message_keys["id"] == 2
    assert report.message_keys["content"] == 3

    assert report.content_block_types["text"] == 1
    assert report.content_block_types["tool_use"] == 1
    assert report.content_block_types["thinking"] == 1

    # Top-level field census surfaces the fields our parser depends on.
    assert report.top_keys["type"] == 5
    assert report.top_keys["sessionId"] == 4
    assert report.top_keys["cwd"] == 3


def test_audit_handles_missing_file_gracefully(tmp_path: Path) -> None:
    """Audit of a nonexistent file returns an empty report without
    raising. Useful when the audit is bound to a UI surface that
    might be invoked before any JSONL exists for a session."""
    report = audit_jsonl(tmp_path / "does-not-exist.jsonl")
    assert report.lines_total == 0
    assert report.lines_parsed == 0
    assert report.latest_version is None


def test_audit_skips_unparseable_lines(tmp_path: Path) -> None:
    p = tmp_path / "garbage.jsonl"
    p.write_text(
        "\n".join(
            [
                '{"type": "user", "sessionId": "s1"}',
                "not json",
                "",
                '{"type": "assistant", "sessionId": "s1"}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    report = audit_jsonl(p)
    # 4 non-empty lines hit the loop; 2 parse cleanly.
    assert report.lines_total == 3
    assert report.lines_parsed == 2


def test_claude_code_version_extracts_string_field() -> None:
    assert claude_code_version({"version": "2.1.119"}) == "2.1.119"
    assert claude_code_version({"version": ""}) is None
    assert claude_code_version({"version": 119}) is None  # not a string
    assert claude_code_version({}) is None
    assert claude_code_version(None) is None  # type: ignore[arg-type]


def test_validated_versions_includes_audit_baseline() -> None:
    """Pin: the version that wrote the JSONL we audited on 2026-04-28
    must remain in VALIDATED_VERSIONS. If we drop a version from the
    list without re-auditing against it, this test fails — the kind
    of regression that's hard to catch otherwise."""
    assert "2.1.119" in VALIDATED_VERSIONS
    assert is_validated_version("2.1.119") is True
    # Versions we haven't audited against return False.
    assert is_validated_version("3.0.0") is False
    assert is_validated_version(None) is False
    assert is_validated_version("") is False
