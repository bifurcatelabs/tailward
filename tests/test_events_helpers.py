"""target_paths / bash_command helpers in schema/events.py."""

from __future__ import annotations

from modmcp.schema.events import TranscriptEvent, bash_command, target_paths


def _ev(tool_name: str | None, tool_input: dict | None) -> TranscriptEvent:
    return TranscriptEvent(
        raw={}, kind="tool_use", session_id=None, timestamp=None,
        text="", tool_name=tool_name, tool_input=tool_input,
    )


def test_target_paths_edit_tool() -> None:
    ev = _ev("Edit", {"file_path": "src/foo.py", "old_string": "x", "new_string": "y"})
    assert target_paths(ev) == ["src/foo.py"]


def test_target_paths_write_tool() -> None:
    ev = _ev("Write", {"file_path": "README.md", "content": "hi"})
    assert target_paths(ev) == ["README.md"]


def test_target_paths_multi_edit_aggregates_edits() -> None:
    ev = _ev(
        "MultiEdit",
        {
            "file_path": "src/a.py",
            "edits": [
                {"file_path": "src/b.py", "old_string": "x", "new_string": "y"},
                {"file_path": "src/b.py"},
                {"file_path": "src/c.py"},
            ],
        },
    )
    paths = target_paths(ev)
    assert "src/a.py" in paths
    assert "src/b.py" in paths
    assert "src/c.py" in paths


def test_target_paths_read_tool_returns_empty() -> None:
    ev = _ev("Read", {"file_path": "src/foo.py"})
    assert target_paths(ev) == []


def test_target_paths_none_for_empty_input() -> None:
    assert target_paths(_ev("Edit", None)) == []


def test_bash_command_bash_tool() -> None:
    ev = _ev("Bash", {"command": "pytest -q"})
    assert bash_command(ev) == "pytest -q"


def test_bash_command_non_bash_returns_none() -> None:
    ev = _ev("Edit", {"file_path": "x.py"})
    assert bash_command(ev) is None


def test_bash_command_empty_string() -> None:
    ev = _ev("Bash", {"command": ""})
    assert bash_command(ev) is None
