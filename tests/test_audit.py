from __future__ import annotations

from pathlib import Path

from tailward.daemon.audit import (
    _is_test_file,
    _match_in_string_literal,
    _verify_claim,
    extract_claims,
)


def test_extract_claims_finds_strong_claims() -> None:
    text = (
        "I refactored the Parser class and removed all FooBar references. "
        "Everything is fully tested."
    )
    claims = extract_claims(text)
    assert claims, "should detect at least one claim"
    blobs = [c.text.lower() for c in claims]
    assert any("refactored" in b for b in blobs)
    assert any("removed" in b for b in blobs)


def test_extract_claims_accepts_adverbs() -> None:
    """First-person patterns should match through adverbs like ``just``/``already``."""
    claims = extract_claims("I just deleted every test file under tests/.")
    assert claims
    assert any("deleted" in c.text.lower() for c in claims)


def test_extract_claims_ignores_bare_quantifiers() -> None:
    """``all three features`` / ``all over the codebase`` / ``all expected sections``
    are not completion claims and must NOT fire audit — that was the main
    ledger-noise source before tightening."""
    noisy = (
        "All three features work. It's all over the codebase. "
        "The response has all expected sections. All good."
    )
    assert extract_claims(noisy) == []


def test_extract_claims_catches_existence_negation() -> None:
    claims = extract_claims("The QwenClient class is gone from the codebase.")
    assert claims
    assert any("gone" in c.text.lower() for c in claims)


def test_verify_removal_claim_contradicted_when_symbol_still_present(tmp_path: Path) -> None:
    (tmp_path / "src.py").write_text("class FooBar:\n    pass\n", encoding="utf-8")
    from tailward.daemon.audit import Claim

    claim = Claim(text="I removed all FooBar references", candidates=["FooBar"])
    status, evidence = _verify_claim(claim, tmp_path, budget=100)
    assert status == "contradicted"
    assert "FooBar" in (evidence or "")


def test_verify_addition_claim_contradicted_when_missing(tmp_path: Path) -> None:
    (tmp_path / "src.py").write_text("print('hi')\n", encoding="utf-8")
    from tailward.daemon.audit import Claim

    claim = Claim(text="I added the NewThing helper", candidates=["NewThing"])
    status, _ = _verify_claim(claim, tmp_path, budget=100)
    assert status == "contradicted"


def test_verify_unverifiable_without_candidates(tmp_path: Path) -> None:
    from tailward.daemon.audit import Claim

    claim = Claim(text="I removed all of it", candidates=[])
    status, _ = _verify_claim(claim, tmp_path, budget=100)
    assert status == "unverifiable"


def test_verify_removal_verified_when_absent(tmp_path: Path) -> None:
    """Genuine removal claim: identifier not present in repo -> verified."""
    (tmp_path / "src.py").write_text("print('nothing to see')\n", encoding="utf-8")
    from tailward.daemon.audit import Claim

    claim = Claim(text="I removed the OldThing helper", candidates=["OldThing"])
    status, evidence = _verify_claim(claim, tmp_path, budget=100)
    assert status == "verified"
    assert "absence confirmed" in (evidence or "").lower()


def test_verify_skips_change_verbs_without_direction(tmp_path: Path) -> None:
    """Renames/refactors with neither removal nor addition keywords should be
    skipped entirely — we don't have directional semantics to check them,
    so recording them as verified was just ledger pollution."""
    (tmp_path / "src.py").write_text("class Thing: pass\n", encoding="utf-8")
    from tailward.daemon.audit import Claim

    claim = Claim(text="I refactored Thing for clarity", candidates=["Thing"])
    status, _ = _verify_claim(claim, tmp_path, budget=100)
    assert status == "skip"


def test_extract_skips_quoted_meta_text() -> None:
    """Backticks, fenced code blocks, and blockquotes are quotation
    contexts — what's inside them is not a first-person completion
    claim by the model. Without this filter, a turn that *discusses*
    the audit ("the audit flagged my 'I removed FooBar'") would itself
    trip the audit and contradict against the test file FooBar lives
    in. Real false-positive seen during dogfooding."""
    inline_quoted = 'The audit flagged my `"I removed FooBar"` from earlier.'
    assert extract_claims(inline_quoted) == []

    fenced = (
        "Here's what the assistant said:\n"
        "```\n"
        "I removed TranscriptEvent from the codebase entirely.\n"
        "```\n"
        "But that wasn't really a claim, just an example."
    )
    assert extract_claims(fenced) == []

    blockquote = (
        "Earlier I noted:\n"
        "> I deleted the QwenClient class\n"
        "Worth revisiting later."
    )
    assert extract_claims(blockquote) == []

    # Sanity: an *unquoted* claim in the same paragraph still fires.
    mixed = (
        "Earlier I said `I removed FooBar` (just an example). "
        "But really, I removed the OldHandler class today."
    )
    claims = extract_claims(mixed)
    assert any("OldHandler" in c.text for c in claims), claims
    # The quoted FooBar must NOT have produced a claim.
    assert not any("FooBar" in c.text for c in claims), claims


# ---------- test-file string-literal filter (verifier scope fix) ----------


def test_is_test_file_detects_tests_directory() -> None:
    assert _is_test_file("C:/warden/tests/test_audit.py")
    assert _is_test_file("/home/u/proj/tests/foo.py")


def test_is_test_file_detects_test_filename() -> None:
    assert _is_test_file("src/test_helpers.py")
    assert _is_test_file("src/helpers_test.py")
    assert _is_test_file("src/test_helpers.js")


def test_is_test_file_rejects_non_test_paths() -> None:
    assert not _is_test_file("src/tailward/daemon/audit.py")
    assert not _is_test_file("README.md")
    # ``test`` substring inside a non-test filename shouldn't count.
    assert not _is_test_file("src/contest_results.py")


def test_match_in_string_literal_double_quotes() -> None:
    assert _match_in_string_literal('text = "I removed FooBar"', "FooBar")


def test_match_in_string_literal_single_quotes() -> None:
    assert _match_in_string_literal("text = 'OldHandler is gone'", "OldHandler")


def test_match_in_string_literal_outside_quotes() -> None:
    assert not _match_in_string_literal("from mymod import OldHandler", "OldHandler")
    assert not _match_in_string_literal("class FooBar: pass", "FooBar")
    assert not _match_in_string_literal("# OldHandler removed", "OldHandler")


def test_match_in_string_literal_handles_escapes() -> None:
    # An escaped quote inside the string mustn't flip the state and let
    # a later identifier slip through as "outside the string."
    assert _match_in_string_literal(r'msg = "say \"FooBar\" out loud"', "FooBar")


def test_verify_skips_test_string_literal_match(tmp_path: Path) -> None:
    """Removal claim shouldn't be contradicted when the only repo
    occurrence is inside a string literal in a test file.

    This was the dogfooding failure mode: warden's own test_audit.py
    pins the extractor on names like FooBar / OldHandler by passing
    them as literal strings, then the verifier later greps the repo
    for those same names and counts the test fixtures as evidence
    the symbol still exists.
    """
    from tailward.daemon.audit import Claim

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_audit.py").write_text(
        'def test_extract():\n'
        '    text = "I removed all FooBar references"\n'
        '    assert extract_claims(text)\n',
        encoding="utf-8",
    )
    claim = Claim(
        text="I removed all FooBar references", candidates=["FooBar"]
    )
    status, evidence = _verify_claim(claim, tmp_path, budget=100)
    assert status == "verified", (status, evidence)
    assert "absence confirmed" in (evidence or "").lower()


def test_verify_test_file_real_import_still_contradicts(tmp_path: Path) -> None:
    """A real import / class-def in a test file (not in a string literal)
    is genuine evidence the symbol exists — the filter must not over-skip
    these or claims would silently verify against actual contradiction."""
    from tailward.daemon.audit import Claim

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_things.py").write_text(
        "from mymod import FooBar\n"
        "def test_foobar(): assert FooBar()\n",
        encoding="utf-8",
    )
    claim = Claim(
        text="I removed all FooBar references", candidates=["FooBar"]
    )
    status, evidence = _verify_claim(claim, tmp_path, budget=100)
    assert status == "contradicted", (status, evidence)


def test_extract_filters_plain_english_identifiers() -> None:
    """Lowercase dictionary words must not become grep candidates. Real
    bug: user said ``tests/ is gone`` and the audit grepped for ``gone``,
    finding the word ``gone`` inside test fixtures and claiming the
    repo contradicted itself. Only code-shaped tokens (CamelCase,
    snake_case, CONSTANT, or contains-digit) should survive."""
    claims = extract_claims(
        "I removed all QwenClient and foo_bar references; the widget is gone."
    )
    assert claims
    # Flatten all candidate lists from every claim pattern that matched.
    cands = {c.lower() for cl in claims for c in cl.candidates}
    # Code-shaped tokens kept:
    assert "qwenclient" in cands or "foo_bar" in cands
    # Plain English words dropped:
    for junk in ("gone", "widget", "references", "removed"):
        assert junk not in cands, f"{junk!r} leaked into audit candidates"
