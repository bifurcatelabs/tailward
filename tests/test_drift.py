from __future__ import annotations

from modmcp.daemon.drift import _mode_mismatch, _tokens


def test_tokens_extracts_identifier_like_words() -> None:
    toks = _tokens("Refactor the Parser module; add Pydantic.")
    assert "refactor" in toks
    assert "parser" in toks
    assert "pydantic" in toks
    assert "a" not in toks
    assert "xx" not in toks


def test_mode_mismatch_detects_meta_in_build_session() -> None:
    assert _mode_mismatch(
        "build", "Let's brainstorm whether we should even do this."
    )


def test_mode_mismatch_allows_on_mode_text() -> None:
    assert not _mode_mismatch("build", "Implementing the fix in parser.py now.")
