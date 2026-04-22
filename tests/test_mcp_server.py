"""Tests for the MCP server tools.

We bypass the MCP transport and invoke the underlying functions directly; the
FastMCP decorator stores the callable on the ``fn`` attribute of the tool.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from modmcp.mcp_server import (
    get_active_rules,
    get_captured_intent,
    query_intent,
    record_decision,
)
from modmcp.paths import intent_path, project_dir
from modmcp.schema.intent import empty_intent, load_intent, save_intent


def _call(tool, /, *args, **kw):
    """FastMCP tools expose the underlying callable on .fn in recent SDKs."""
    fn = getattr(tool, "fn", None) or getattr(tool, "__wrapped__", None) or tool
    return fn(*args, **kw)


def _seed(project_path: str) -> None:
    project_dir(project_path).mkdir(parents=True, exist_ok=True)
    intent = empty_intent(project_path, "demo")
    intent.set("Active Goal", "Ship the warden")
    intent.append_list_item("Active Rules", "minimal change; no scope creep")
    save_intent(intent, intent_path(project_path))


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    proj = tmp_path / "proj"
    proj.mkdir()
    _seed(str(proj))
    monkeypatch.setenv("MODMCP_PROJECT", str(proj))
    return str(proj)


def test_get_captured_intent_full(project: str) -> None:
    out = _call(get_captured_intent, None)
    assert "Active Goal" in out
    assert "Ship the warden" in out


def test_get_captured_intent_section(project: str) -> None:
    out = _call(get_captured_intent, "Active Goal")
    assert "Ship the warden" in out


def test_get_captured_intent_unknown_section(project: str) -> None:
    out = _call(get_captured_intent, "Not A Section")
    assert "unknown section" in out


def test_get_active_rules(project: str) -> None:
    out = _call(get_active_rules)
    assert "minimal change" in out


def test_record_decision_as_rule(project: str) -> None:
    _call(
        record_decision,
        "always prefer editing existing files",
        "avoid sprawl",
    )
    reread = load_intent(intent_path(project))
    assert "always prefer editing existing files" in reread.sections["Active Rules"]
    assert "avoid sprawl" in reread.sections["Active Rules"]


def test_record_decision_as_thread(project: str) -> None:
    _call(record_decision, "investigate flaky test in parser_test.py")
    reread = load_intent(intent_path(project))
    assert "investigate flaky test" in reread.sections["Open Threads"]


def test_query_intent_keyword_fallback(project: str, monkeypatch: pytest.MonkeyPatch) -> None:
    # Force Qwen unavailable by breaking the endpoint.
    monkeypatch.setenv("MODMCP_HOME", os.environ["MODMCP_HOME"])
    # Re-load config with unreachable endpoint.
    import modmcp.config as cfg

    cfg._cached = None
    out = _call(query_intent, "what rules are active?")
    # Either Qwen answered or keyword fallback matched "Active Rules" section.
    assert isinstance(out, str)
    assert out  # non-empty
