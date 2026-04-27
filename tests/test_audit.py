from __future__ import annotations

from pathlib import Path

from modmcp.daemon.audit import _verify_claim, extract_claims


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
    from modmcp.daemon.audit import Claim

    claim = Claim(text="I removed all FooBar references", candidates=["FooBar"])
    status, evidence = _verify_claim(claim, tmp_path, budget=100)
    assert status == "contradicted"
    assert "FooBar" in (evidence or "")


def test_verify_addition_claim_contradicted_when_missing(tmp_path: Path) -> None:
    (tmp_path / "src.py").write_text("print('hi')\n", encoding="utf-8")
    from modmcp.daemon.audit import Claim

    claim = Claim(text="I added the NewThing helper", candidates=["NewThing"])
    status, _ = _verify_claim(claim, tmp_path, budget=100)
    assert status == "contradicted"


def test_verify_unverifiable_without_candidates(tmp_path: Path) -> None:
    from modmcp.daemon.audit import Claim

    claim = Claim(text="I removed all of it", candidates=[])
    status, _ = _verify_claim(claim, tmp_path, budget=100)
    assert status == "unverifiable"


def test_verify_removal_verified_when_absent(tmp_path: Path) -> None:
    """Genuine removal claim: identifier not present in repo -> verified."""
    (tmp_path / "src.py").write_text("print('nothing to see')\n", encoding="utf-8")
    from modmcp.daemon.audit import Claim

    claim = Claim(text="I removed the OldThing helper", candidates=["OldThing"])
    status, evidence = _verify_claim(claim, tmp_path, budget=100)
    assert status == "verified"
    assert "absence confirmed" in (evidence or "").lower()


def test_verify_skips_change_verbs_without_direction(tmp_path: Path) -> None:
    """Renames/refactors with neither removal nor addition keywords should be
    skipped entirely — we don't have directional semantics to check them,
    so recording them as verified was just ledger pollution."""
    (tmp_path / "src.py").write_text("class Thing: pass\n", encoding="utf-8")
    from modmcp.daemon.audit import Claim

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
