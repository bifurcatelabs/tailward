"""Phase 1 auto-synthesis: transcript -> captured-intent via Qwen."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from .schema.events import parse_line
from .schema.intent import SECTIONS, Intent

log = logging.getLogger(__name__)

SYSTEM = """You synthesize a structured handoff document from a Claude Code session transcript.

Rules:
- Be specific, not generic. Capture rules and commitments VERBATIM where the user or assistant stated them.
- Keep each list item under 25 words. Keep the whole document under 800 tokens.
- Prefer fewer high-signal items over many vague ones. Empty arrays are fine.
- Output strict JSON with exactly these keys — no prose, no markdown fences:

{
  "receiving_posture": "1-3 sentences on how the next agent should receive the user",
  "active_goal": "concrete, bounded description of what this session is trying to accomplish",
  "open_threads": ["thread [priority:high|med|low] [status]", ...],
  "active_rules": ["rule verbatim", ...],
  "known_user_drift_patterns": ["pattern -> redirect template", ...],
  "known_agent_drift_patterns": ["pattern -> corrective", ...],
  "commitments_pending": ["commitment verbatim", ...],
  "recent_claims": ["claim verbatim", ...],
  "notes": "brief free-form; may be empty string",
  "session_mode": "build|meta|exploration"
}
"""


def _max_chars() -> int:
    """Size the transcript slice to fit the model's context window.

    Leaves room for system prompt + synthesis output budget.
    """
    from tailward.config import get_config

    cfg = get_config()
    reserved = cfg.qwen_max_tokens_synth + 1500  # output + system prompt
    usable_tokens = max(2048, cfg.qwen_context_tokens - reserved)
    # ~3.2 chars/token is a conservative English estimate.
    return int(usable_tokens * 3.2)


def _denoise(transcript_text: str) -> str:
    """Drop tool-result noise and collapse runs of whitespace."""
    kept: list[str] = []
    for line in transcript_text.splitlines():
        ev = parse_line(line)
        if ev is None:
            continue
        if ev.kind == "tool_result":
            snippet = (ev.tool_output or "")[:500]
            kept.append(f"[tool_result] {snippet}")
        elif ev.kind == "tool_use":
            kept.append(f"[tool_use:{ev.tool_name}] {json.dumps(ev.tool_input or {})[:500]}")
        elif ev.kind in ("user_message", "assistant_message"):
            role = "USER" if ev.kind == "user_message" else "ASSISTANT"
            kept.append(f"{role}: {ev.text}")
    joined = "\n".join(kept)
    cap = _max_chars()
    if len(joined) > cap:
        joined = joined[-cap:]
    return joined


_SECTION_MAP = {
    "receiving_posture": "Receiving Posture",
    "active_goal": "Active Goal",
    "open_threads": "Open Threads",
    "active_rules": "Active Rules",
    "known_user_drift_patterns": "Known User Drift Patterns",
    "known_agent_drift_patterns": "Known Agent Drift Patterns",
    "commitments_pending": "Commitments (pending)",
    "recent_claims": "Recent Claims",
    "notes": "Notes",
}


def _validate(payload: dict) -> list[str]:
    errors: list[str] = []
    for key in _SECTION_MAP:
        if key not in payload:
            errors.append(f"missing key: {key}")
    list_keys = {
        "open_threads",
        "active_rules",
        "known_user_drift_patterns",
        "known_agent_drift_patterns",
        "commitments_pending",
        "recent_claims",
    }
    for k in list_keys:
        if k in payload and not isinstance(payload[k], list):
            errors.append(f"{k} must be a list")
    mode = payload.get("session_mode")
    if mode not in (None, "build", "meta", "exploration"):
        errors.append("session_mode must be build|meta|exploration")
    return errors


def _payload_to_intent(payload: dict, intent: Intent) -> Intent:
    touched = False
    for key, section in _SECTION_MAP.items():
        val = payload.get(key)
        if val is None:
            continue
        if isinstance(val, list):
            if not val:
                intent.sections[section] = "\n"
                continue
            body = "\n".join(f"- {item}" for item in val)
            intent.sections[section] = body + "\n"
        else:
            intent.sections[section] = (str(val).rstrip()) + "\n"
        touched = True
    # Ensure every canonical section exists.
    for s in SECTIONS:
        intent.sections.setdefault(s, "\n")
    if payload.get("session_mode") in ("build", "meta", "exploration"):
        intent.front.session_mode = payload["session_mode"]
        touched = True
    # Bump the frontmatter ``updated`` timestamp when the synth payload
    # actually changed something. Prior versions left this stale, so
    # comprehensive synth would write fresh sections but the metadata
    # still pointed at whenever the intent.md was first created.
    if touched:
        intent.front.updated = datetime.now(UTC)
    return intent


async def _run_synth(qwen, denoised: str, intent: Intent) -> dict:
    """Inner async core: call Qwen, validate, retry once if needed.
    Mutates ``intent.front.incomplete`` on persistent validation failure
    so save points carry the warning. Returns the best payload it got.
    """
    try:
        payload = await qwen.complete_json(SYSTEM, denoised, kind="synth")
    except Exception as e:
        log.warning("phase 1 first-pass failed: %s", e)
        payload = {}
    errors = _validate(payload)
    if not errors:
        return payload

    retry_prompt = (
        denoised
        + "\n\n---\nYour previous output failed validation with errors: "
        + "; ".join(errors)
        + ". Return corrected JSON only, strictly matching the schema. Keep values concise."
    )
    try:
        payload2 = await qwen.complete_json(SYSTEM, retry_prompt, kind="synth")
    except Exception as e:
        log.warning("phase 1 retry failed: %s", e)
        intent.front.incomplete = True
        return payload  # use whatever partial we have
    errors2 = _validate(payload2)
    if errors2:
        log.warning("phase 1 retry still failing: %s", errors2)
        intent.front.incomplete = True
        # Keep whichever payload has more filled fields.
        if len(payload2) > len(payload):
            return payload2
        return payload
    return payload2


async def synthesize_async(qwen, transcript: Path, intent: Intent) -> Intent:
    """Async-native variant of :func:`synthesize`. Use from inside a
    running event loop (e.g. the synthesis worker's comprehensive
    trigger) where ``asyncio.run`` would error."""
    text = Path(transcript).read_text(encoding="utf-8", errors="replace")
    denoised = _denoise(text)
    payload = await _run_synth(qwen, denoised, intent)
    return _payload_to_intent(payload, intent)


def synthesize(qwen, transcript: Path, intent: Intent) -> Intent:
    """Sync wrapper: load transcript, call Qwen, validate + retry once, fill intent.

    Used by the CLI ``warden handoff`` path. Internal callers from
    inside an event loop (the ``synthesis_worker`` comprehensive
    trigger) should use :func:`synthesize_async` instead.
    """
    text = Path(transcript).read_text(encoding="utf-8", errors="replace")
    denoised = _denoise(text)

    try:
        payload = asyncio.run(_run_synth(qwen, denoised, intent))
    except RuntimeError:
        # If there's an existing event loop (unlikely from CLI), fall back.
        loop = asyncio.new_event_loop()
        try:
            payload = loop.run_until_complete(_run_synth(qwen, denoised, intent))
        finally:
            loop.close()

    return _payload_to_intent(payload, intent)
