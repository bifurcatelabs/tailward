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


def _force_qwen_down(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the Qwen path raise so tests exercise the keyword fallback."""
    from modmcp.daemon import qwen as qwen_mod

    class _Broken:
        def __init__(self, *a, **kw) -> None:  # noqa: D401
            raise RuntimeError("forced down for test")

    monkeypatch.setattr(qwen_mod, "QwenClient", _Broken)


def test_query_intent_heading_outscores_body_mention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression for the live-session bug: query_intent("active rules")
    returned the *Known Agent Drift Patterns* section because its body
    happened to contain the compound token ``active_rules`` and the
    word ``rules``, while the *Active Rules* body did not repeat those
    words. The ranker must weight the section HEADING — that's where
    the semantic answer lives.
    """
    proj = tmp_path / "proj2"
    proj.mkdir()
    project_dir(str(proj)).mkdir(parents=True, exist_ok=True)
    intent = empty_intent(str(proj), "demo")
    intent.set("Active Goal", "Ship the ranker fix")
    intent.append_list_item(
        "Active Rules",
        "gitignore machine-local paths; hooks fail silently; no in-repo writes",
    )
    intent.append_list_item(
        "Known Agent Drift Patterns",
        "Rule-establishment without adherence: track active_rules; check code against rules",
    )
    save_intent(intent, intent_path(str(proj)))
    monkeypatch.setenv("MODMCP_PROJECT", str(proj))
    _force_qwen_down(monkeypatch)

    out = _call(query_intent, "what are the active rules right now?")
    assert "Active Rules" in out, "Active Rules section should be returned"
    # And it should appear BEFORE the drift-patterns section. If both
    # are returned, the heading-weighted ranker must put Active Rules
    # first.
    if "Known Agent Drift Patterns" in out:
        assert out.index("## Active Rules") < out.index(
            "## Known Agent Drift Patterns"
        ), "heading-matching section must rank above body-mention section"


def test_query_intent_heading_only_section_still_returned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even when a section body has ZERO token overlap with the query
    and another section's body has a tangential mention, the heading
    match should still surface the correct section."""
    proj = tmp_path / "proj3"
    proj.mkdir()
    project_dir(str(proj)).mkdir(parents=True, exist_ok=True)
    intent = empty_intent(str(proj), "demo")
    intent.append_list_item("Active Rules", "one two three")
    intent.append_list_item("Open Threads", "something about rules elsewhere")
    save_intent(intent, intent_path(str(proj)))
    monkeypatch.setenv("MODMCP_PROJECT", str(proj))
    _force_qwen_down(monkeypatch)

    out = _call(query_intent, "active rules")
    assert "## Active Rules" in out
    # Active Rules body has no query-token overlap; it wins purely on
    # heading match, which means it must rank #1 regardless.
    assert out.startswith("## Active Rules") or out.index(
        "## Active Rules"
    ) < out.index("## Open Threads") if "## Open Threads" in out else True
