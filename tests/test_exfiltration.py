"""Pin the exfiltration-detection pattern library + redaction
contract.

These tests document which secret shapes are caught and what the
sanitized output looks like. Adding a new pattern to
``modmcp.schema.exfiltration`` should land alongside a test here
covering both detection (positive case) and a non-detection case
(text that resembles the pattern but doesn't actually match) so
false-positive risk is visible at review time.
"""

from __future__ import annotations

from modmcp.schema.exfiltration import PATTERNS, redact, scan

# Fixtures with real-format prefixes (sk_live_, xoxb-) are assembled via
# string concatenation so GitHub's secret-scanning push protection does
# not flag the source file. The runtime value still matches the regex;
# only the literal in source is obscured.


def test_no_match_returns_empty() -> None:
    assert scan("hello world, no secrets here") == []


def test_empty_or_none_returns_empty() -> None:
    assert scan("") == []
    assert scan(None) == []


# ---------- pattern detection ----------


def test_openai_api_key_detected() -> None:
    text = "running with key sk-abcdef1234567890abcdef1234567890 enabled"
    matches = scan(text)
    assert len(matches) == 1
    assert matches[0].pattern_name == "openai_api_key"
    assert matches[0].matched_text.startswith("sk-")


def test_anthropic_api_key_detected() -> None:
    text = "use sk-ant-api03-abcdefghijklmnopqrstuv to authenticate"
    matches = scan(text)
    assert len(matches) == 1
    assert matches[0].pattern_name == "anthropic_api_key"


def test_github_pat_classic_detected() -> None:
    # ghp_ + exactly 36 alphanum chars
    body = "abcdef1234567890ABCDEF1234567890abcd"  # 36 chars
    assert len(body) == 36
    text = f"token ghp_{body} set"
    matches = scan(text)
    assert len(matches) == 1
    assert matches[0].pattern_name == "github_pat_classic"


def test_github_pat_fine_grained_detected() -> None:
    # github_pat_ + 82 chars from [a-zA-Z0-9_]
    body = "A" * 82
    text = f"token github_pat_{body} set"
    matches = scan(text)
    assert len(matches) == 1
    assert matches[0].pattern_name == "github_pat_fine_grained"


def test_aws_access_key_detected() -> None:
    text = "export AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE"
    matches = scan(text)
    assert len(matches) == 1
    assert matches[0].pattern_name == "aws_access_key"


def test_stripe_live_key_detected() -> None:
    text = "STRIPE_KEY=" + "sk_" + "live_abcdef1234567890abcdefgh"
    matches = scan(text)
    assert len(matches) == 1
    assert matches[0].pattern_name == "stripe_live_key"


def test_private_key_block_detected() -> None:
    text = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAA...\n-----END RSA PRIVATE KEY-----"
    matches = scan(text)
    assert len(matches) >= 1
    assert any(m.pattern_name == "private_key_block" for m in matches)


def test_slack_token_detected() -> None:
    text = "slack=" + "xox" + "b-1234567890-9876543210-abcdefABCDEF1234567890"
    matches = scan(text)
    assert len(matches) == 1
    assert matches[0].pattern_name == "slack_token"


def test_multiple_distinct_matches() -> None:
    text = (
        "openai=sk-abcdef1234567890abcdef1234567890 "
        "aws=AKIAIOSFODNN7EXAMPLE "
        "stripe=" + "sk_" + "live_abcdef1234567890abcdefgh"
    )
    matches = scan(text)
    names = {m.pattern_name for m in matches}
    assert names == {"openai_api_key", "aws_access_key", "stripe_live_key"}


# ---------- non-detection (false-positive guards) ----------


def test_short_sk_prefix_not_matched() -> None:
    # sk- alone with too few chars after shouldn't fire
    assert scan("sk-tooshort") == []


def test_aws_lookalike_not_matched() -> None:
    # AKIA + lowercase shouldn't match the uppercase-only pattern
    assert scan("AKIAabcd1234567890") == []


def test_unrelated_string_with_sk_prefix() -> None:
    # "skating" shouldn't match openai_api_key — \b boundary + length
    assert scan("skating on thin ice") == []


# ---------- redaction ----------


def test_redaction_preserves_recognizable_endpoints() -> None:
    matches = scan("sk-abcdef1234567890abcdef1234567890")
    redacted = matches[0].redacted_preview
    # Long enough to keep first/last 4 chars
    assert redacted.startswith("sk-a")
    assert "REDACTED" in redacted


def test_redaction_never_leaks_full_match() -> None:
    """Across the pattern library, no redacted preview should contain
    the full matched secret. This catches regressions where a future
    pattern's match is short enough that the redaction passes the
    secret through as-is."""
    samples = [
        "sk-abcdef1234567890abcdef1234567890",
        "sk-ant-api03-abcdefghijklmnopqrstuv",
        "AKIAIOSFODNN7EXAMPLE",
        "sk_" + "live_abcdef1234567890abcdefgh",
        "xox" + "b-1234567890-9876543210-abcdefABCDEF1234567890",
    ]
    for s in samples:
        matches = scan(s)
        assert matches, f"sample {s!r} did not match any pattern"
        for m in matches:
            assert m.matched_text not in m.redacted_preview, (
                f"redaction leaked full match for {m.pattern_name}"
            )


def test_redact_replaces_secret_in_text() -> None:
    text = "before sk-abcdef1234567890abcdef1234567890 after"
    matches = scan(text)
    out = redact(text, matches)
    assert "sk-abcdef1234567890" not in out
    assert "REDACTED" in out
    assert "before" in out and "after" in out


def test_redact_idempotent_on_already_clean_text() -> None:
    assert redact("plain text", []) == "plain text"


def test_redact_passes_through_none() -> None:
    assert redact(None, []) is None


# ---------- pattern-library shape ----------


def test_pattern_names_unique() -> None:
    """Pattern names are used as stable tokens in alert payloads.
    Duplicates would silently break downstream consumers."""
    names = [p.name for p in PATTERNS]
    assert len(names) == len(set(names)), "duplicate pattern names"


def test_every_pattern_has_description() -> None:
    """Descriptions feed UI tooltips and future config docs."""
    for p in PATTERNS:
        assert p.description, f"pattern {p.name!r} missing description"
