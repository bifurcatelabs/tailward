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
    assert any("all" in b or "removed" in b for b in blobs)


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
