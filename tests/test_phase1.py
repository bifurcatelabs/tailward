from __future__ import annotations

from pathlib import Path

import pytest

from tailward.phase1 import (
    _denoise,
    _payload_to_intent,
    _render_prior_intent,
    _validate,
    synthesize,
    synthesize_async,
)
from tailward.schema.intent import empty_intent, load_intent, save_intent


def test_denoise_preserves_semantic_content() -> None:
    fixture = (
        Path(__file__).parent / "fixtures" / "transcripts" / "sample_build.jsonl"
    ).read_text(encoding="utf-8")
    out = _denoise(fixture)
    assert "USER:" in out
    assert "ASSISTANT:" in out
    assert "refactor" in out.lower()


def test_validate_catches_missing_keys() -> None:
    errors = _validate({"active_goal": "x"})
    assert any("receiving_posture" in e for e in errors)


def test_validate_catches_wrong_type() -> None:
    payload = {
        "receiving_posture": "x",
        "active_goal": "x",
        "open_threads": "not a list",
        "active_rules": [],
        "known_user_drift_patterns": [],
        "known_agent_drift_patterns": [],
        "commitments_pending": [],
        "recent_claims": [],
        "notes": "",
    }
    errors = _validate(payload)
    assert any("open_threads must be a list" in e for e in errors)


def test_payload_to_intent_fills_sections() -> None:
    intent = empty_intent("/tmp/x", "x")
    payload = {
        "receiving_posture": "be patient",
        "active_goal": "ship v1",
        "open_threads": ["wire up drift"],
        "active_rules": ["minimal change"],
        "known_user_drift_patterns": [],
        "known_agent_drift_patterns": ["over-architects"],
        "commitments_pending": [],
        "recent_claims": [],
        "notes": "",
        "session_mode": "build",
    }
    out = _payload_to_intent(payload, intent)
    assert "be patient" in out.sections["Receiving Posture"]
    assert "ship v1" in out.sections["Active Goal"]
    assert "- wire up drift" in out.sections["Open Threads"]
    assert "- minimal change" in out.sections["Active Rules"]
    assert "- over-architects" in out.sections["Known Agent Drift Patterns"]
    assert out.front.session_mode == "build"


class _FakeLocalLLM:
    """Drop-in stand-in for LocalLLMClient with a canned JSON response."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    async def complete_json(self, system, user, **kw):
        return self._payload


def test_render_prior_intent_skips_empty_sections() -> None:
    intent = empty_intent("/tmp/x", "x")
    intent.set("Active Rules", "- rule alpha\n- rule beta")
    intent.set("Active Goal", "ship the thing")
    rendered = _render_prior_intent(intent)
    assert "### Active Rules" in rendered
    assert "rule alpha" in rendered
    assert "rule beta" in rendered
    assert "### Active Goal" in rendered
    # Sections that are empty / placeholder-only should not appear
    assert "### Notes" not in rendered
    assert "### Recent Claims" not in rendered


def test_render_prior_intent_handles_empty_intent() -> None:
    intent = empty_intent("/tmp/x", "x")
    # empty_intent seeds Receiving Posture + Active Goal with placeholder
    # text. Strip those so we can exercise the truly-empty branch.
    intent.sections = {}
    rendered = _render_prior_intent(intent)
    assert "empty" in rendered.lower()


@pytest.mark.asyncio
async def test_comprehensive_synth_passes_prior_intent_to_llm(
    tmp_path: Path,
) -> None:
    """The comprehensive synth must include the prior intent's sections in
    the user prompt so the local LLM can merge new transcript activity
    with the accumulated rules/threads instead of replacing them.

    Regression test for the synthesis-clobber bug observed 2026-05-04 —
    the synth call never saw the prior intent's Active Rules, so its
    fresh-from-transcript output silently overwrote them on every
    comprehensive regeneration. Fix: pass intent.sections to the synth
    call alongside the transcript.
    """
    captured: list[tuple[str, str]] = []

    class _CapturingLocalLLM:
        async def complete_json(self, system, user, **kw):
            captured.append((system, user))
            return {
                "receiving_posture": "carry on",
                "active_goal": "ship the test",
                "open_threads": [],
                "active_rules": [
                    "distinctive prior rule alpha",
                    "distinctive prior rule beta",
                    "new rule from transcript",
                ],
                "known_user_drift_patterns": [],
                "known_agent_drift_patterns": [],
                "commitments_pending": [],
                "recent_claims": [],
                "notes": "",
                "session_mode": "build",
            }

    intent = empty_intent("/proj", "proj")
    intent.set(
        "Active Rules",
        "- distinctive prior rule alpha\n- distinctive prior rule beta",
    )
    intent.set("Active Goal", "prior goal — refactor x")

    transcript = (
        Path(__file__).parent / "fixtures" / "transcripts" / "sample_build.jsonl"
    )

    await synthesize_async(_CapturingLocalLLM(), transcript, intent)

    assert len(captured) == 1
    system, user = captured[0]
    # SYSTEM prompt instructs merge semantics
    assert "merg" in system.lower()
    assert "preserve" in system.lower() or "accumulate" in system.lower()
    # User prompt actually contains the prior rules + goal so the synth
    # can merge them into its output rather than re-deriving from scratch.
    assert "PRIOR INTENT" in user
    assert "RECENT TRANSCRIPT" in user
    assert "distinctive prior rule alpha" in user
    assert "distinctive prior rule beta" in user
    assert "prior goal — refactor x" in user


def test_synthesize_writes_valid_intent(tmp_path: Path) -> None:
    transcript = (
        Path(__file__).parent / "fixtures" / "transcripts" / "sample_build.jsonl"
    )
    intent = empty_intent("/tmp/fake", "fake")
    fake = _FakeLocalLLM(
        {
            "receiving_posture": "open, no-preamble",
            "active_goal": "refactor the transcript parser to use pydantic",
            "open_threads": ["verify API stability [high]"],
            "active_rules": ["minimal change; no unprompted tests"],
            "known_user_drift_patterns": [],
            "known_agent_drift_patterns": ["adds tests unprompted"],
            "commitments_pending": [],
            "recent_claims": ["I removed all the old dict returns"],
            "notes": "",
            "session_mode": "build",
        }
    )
    out = synthesize(fake, transcript, intent)
    target = tmp_path / "intent.md"
    save_intent(out, target)
    reread = load_intent(target)
    assert "refactor the transcript parser" in reread.sections["Active Goal"]
    assert reread.front.session_mode == "build"
