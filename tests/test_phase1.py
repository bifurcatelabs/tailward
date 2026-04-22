from __future__ import annotations

from pathlib import Path

from modmcp.phase1 import _denoise, _payload_to_intent, _validate, synthesize
from modmcp.schema.intent import empty_intent, load_intent, save_intent


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


class _FakeQwen:
    """Drop-in stand-in for QwenClient with a canned JSON response."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    async def complete_json(self, system, user, **kw):
        return self._payload


def test_synthesize_writes_valid_intent(tmp_path: Path) -> None:
    transcript = (
        Path(__file__).parent / "fixtures" / "transcripts" / "sample_build.jsonl"
    )
    intent = empty_intent("/tmp/fake", "fake")
    fake = _FakeQwen(
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
