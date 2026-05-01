"""Exfiltration detection — scan text for known secret patterns and
redact matches before they land in live_events.

The dispatcher calls :func:`scan` on tool_call input previews before
publishing. Any match triggers two outputs:

1. The original event's input_preview is sanitized via :func:`redact`
   so the secret never reaches ``live_events.payload``. The audit log
   stores only the redacted form.
2. A separate ``exfiltration_alert`` LiveBus event surfaces the match
   to the live feed (pattern name + redacted preview + source tool
   kind) so the user sees the leak in real time.

Pattern library is conservative by design — high-signal patterns
(canonical prefixes + length anchors) only. False positives on the
audit feed have a real cost: noise, ack-fatigue, eventual ignoring of
real alerts. Generic patterns (e.g., 40-char base64 strings, generic
bearer tokens) are intentionally excluded; they belong in a
high-recall mode that's separate from the always-on detector.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class Pattern:
    """A single secret-shape pattern.

    ``name`` is the stable token used in alert payloads + tests.
    ``regex`` is the compiled detector. ``description`` is for the UI
    tooltip (and future config docs).
    """

    name: str
    description: str
    regex: re.Pattern[str]


@dataclass
class PatternMatch:
    """A single match found during a scan.

    ``matched_text`` carries the actual secret for redaction purposes
    only — callers must not persist it in any database or log.
    ``redacted_preview`` is the safe-to-store form (used in the
    alert event payload and to replace the secret in the source
    event's input_preview).
    """

    pattern_name: str
    matched_text: str
    redacted_preview: str


# Conservative library: prefix + length-anchored patterns for known
# credential shapes. Order doesn't matter — :func:`scan` reports every
# match independently. To extend, add patterns here; tests in
# ``tests/test_exfiltration.py`` should pin each new pattern's
# detection + non-detection contract.
PATTERNS: Final[list[Pattern]] = [
    Pattern(
        name="openai_api_key",
        description="OpenAI API key (sk- prefix, ≥20 alphanum after)",
        regex=re.compile(r"\bsk-[a-zA-Z0-9]{20,}\b"),
    ),
    Pattern(
        name="anthropic_api_key",
        description="Anthropic API key (sk-ant- prefix)",
        regex=re.compile(r"\bsk-ant-[a-zA-Z0-9_-]{20,}\b"),
    ),
    Pattern(
        name="github_pat_classic",
        description="GitHub classic personal access token (ghp_ prefix, 36 alphanum)",
        regex=re.compile(r"\bghp_[a-zA-Z0-9]{36}\b"),
    ),
    Pattern(
        name="github_pat_fine_grained",
        description="GitHub fine-grained personal access token (github_pat_ prefix, 82 alphanum)",
        regex=re.compile(r"\bgithub_pat_[a-zA-Z0-9_]{82}\b"),
    ),
    Pattern(
        name="aws_access_key",
        description="AWS access key ID (AKIA prefix, 16 uppercase alphanum)",
        regex=re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    ),
    Pattern(
        name="stripe_live_key",
        description="Stripe live secret key (sk_live_ prefix)",
        regex=re.compile(r"\bsk_live_[a-zA-Z0-9]{24,}\b"),
    ),
    Pattern(
        name="stripe_test_key",
        description="Stripe test secret key (sk_test_ prefix)",
        regex=re.compile(r"\bsk_test_[a-zA-Z0-9]{24,}\b"),
    ),
    Pattern(
        name="slack_token",
        description="Slack token (xoxb / xoxp / xoxs / xoxa)",
        regex=re.compile(r"\bxox[bpsa]-[0-9]+-[0-9]+(?:-[a-zA-Z0-9]+)+\b"),
    ),
    Pattern(
        name="private_key_block",
        description="PEM-encoded private key block",
        regex=re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----"),
    ),
]


def _redact(match_text: str) -> str:
    """Reduce a matched secret to a safe-to-store preview.

    Keeps the first 4 and last 4 chars when the match is long enough
    to make a partial preview useful for human recognition without
    revealing exploitable material. Short matches collapse to a fixed
    sentinel so even partial leakage isn't possible.
    """
    if len(match_text) <= 12:
        return "[REDACTED]"
    return f"{match_text[:4]}...[REDACTED]...{match_text[-4:]}"


def scan(text: str | None) -> list[PatternMatch]:
    """Scan text for known secret patterns.

    Returns a list of :class:`PatternMatch` — one entry per distinct
    occurrence (same pattern matching twice produces two entries).
    Empty list when no matches found or when text is None / empty.
    """
    if not text:
        return []
    out: list[PatternMatch] = []
    for pattern in PATTERNS:
        for m in pattern.regex.finditer(text):
            matched = m.group()
            out.append(
                PatternMatch(
                    pattern_name=pattern.name,
                    matched_text=matched,
                    redacted_preview=_redact(matched),
                )
            )
    return out


def redact(text: str | None, matches: list[PatternMatch]) -> str | None:
    """Replace each matched secret in text with its redacted preview.

    Idempotent for already-redacted text (the redacted form contains
    no pattern-matching content). Returns ``None`` when text is None.
    """
    if text is None:
        return None
    if not matches:
        return text
    out = text
    for m in matches:
        out = out.replace(m.matched_text, m.redacted_preview)
    return out
