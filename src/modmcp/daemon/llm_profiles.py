"""Aggregated per-call-kind LLM configuration + prompt templates.

Surfaced via ``GET /llm-profiles`` so the Platform view can show the
user, verbatim, what Warden is asking the local LLM for each call
kind (synth / drift / query / rubric / consolidator). Transparency
about Warden's own prompts is on-mission for the trust-layer pitch:
the user shouldn't have to read source to know what the daemon is
sending on their behalf.

The prompt strings imported here are the **same constants the workers
use at runtime**, not duplicated text. Drift between this surface and
what's actually sent is therefore impossible — if the worker's call
shape changes, this module's output changes with it.
"""

from __future__ import annotations

from typing import Any

from ..config import get_config
from . import drift as drift_mod
from . import rubric_worker, session_close
from .qwen import CallKind

# Synth lives in modmcp.phase1, imported lazily so this module can be
# imported even before phase1's transcript-denoiser deps are loaded.
# The MCP server module + its ``query`` prompt was retired in v2.0.0;
# the ``query`` CallKind remains as the generic fallback in
# qwen.QwenClient.complete() but has no surfaced prompt template.


def _synth_user_template() -> str:
    # phase1 passes the denoised transcript as the user prompt verbatim.
    return "<denoised transcript text>"


def _kind_profile(kind: CallKind, system: str, user_template: str) -> dict[str, Any]:
    cfg = get_config()
    # Per-kind sampler resolution mirrors qwen.QwenClient — keeping the
    # logic in lockstep so the displayed values are what'll actually
    # be sent on the next call.
    temperature = {
        "synth": cfg.qwen_temperature_synth,
        "drift": cfg.qwen_temperature_drift,
        "query": cfg.qwen_temperature_query,
        "rubric": cfg.qwen_temperature_rubric,
        "consolidator": cfg.qwen_temperature_consolidator,
    }[kind]
    if temperature is None:
        temperature = cfg.qwen_temperature
    presence = {
        "synth": cfg.qwen_presence_penalty_synth,
        "drift": cfg.qwen_presence_penalty_drift,
        "query": cfg.qwen_presence_penalty_query,
        "rubric": cfg.qwen_presence_penalty_rubric,
        "consolidator": cfg.qwen_presence_penalty_consolidator,
    }[kind]
    if presence is None:
        presence = cfg.qwen_presence_penalty
    max_tokens = {
        "synth": cfg.qwen_max_tokens_synth,
        "drift": cfg.qwen_max_tokens_drift,
        "query": cfg.qwen_max_tokens_query,
        "rubric": cfg.qwen_max_tokens_rubric,
        "consolidator": cfg.qwen_max_tokens_consolidator,
    }[kind]
    enable_thinking = {
        "synth": cfg.qwen_enable_thinking_synth,
        "drift": cfg.qwen_enable_thinking_drift,
        "query": cfg.qwen_enable_thinking_query,
        "rubric": cfg.qwen_enable_thinking_rubric,
        "consolidator": cfg.qwen_enable_thinking_consolidator,
    }[kind]
    model_override = {
        "synth": cfg.qwen_model_synth,
        "drift": cfg.qwen_model_drift,
        "query": cfg.qwen_model_query,
        "rubric": cfg.qwen_model_rubric,
        "consolidator": cfg.qwen_model_consolidator,
    }[kind]
    return {
        "kind": kind,
        "model": model_override or cfg.qwen_model,
        "model_overridden": bool(model_override),
        "max_tokens": max_tokens,
        "temperature": temperature,
        "presence_penalty": presence,
        "top_p": cfg.qwen_top_p,
        "top_k": cfg.qwen_top_k,
        "min_p": cfg.qwen_min_p,
        "repetition_penalty": cfg.qwen_repetition_penalty,
        "enable_thinking": enable_thinking,
        "system_prompt": system,
        "user_prompt_template": user_template,
    }


def all_profiles() -> list[dict[str, Any]]:
    """Return one entry per call kind that has a surfaced prompt, in
    stable display order. The ``query`` kind is omitted: its prompt
    used to live in ``mcp_server.py`` which was retired in v2.0.0,
    and no other code path uses it."""
    # Lazy import so this module can be imported even if phase1's
    # transcript-denoiser deps aren't loaded yet.
    from .. import phase1

    return [
        _kind_profile("synth", phase1.SYSTEM, _synth_user_template()),
        _kind_profile("drift", drift_mod.PROMPT_SYSTEM, drift_mod.PROMPT_USER_TEMPLATE),
        _kind_profile("rubric", rubric_worker.PROMPT_SYSTEM, rubric_worker.PROMPT_USER_TEMPLATE),
        _kind_profile(
            "consolidator", session_close.PROMPT_SYSTEM, session_close.PROMPT_USER_TEMPLATE
        ),
    ]


def endpoint_summary() -> dict[str, Any]:
    """Endpoint-level config for the panel header (URL, context window,
    api-key-presence). The literal API key value is never returned."""
    cfg = get_config()
    return {
        "endpoint": cfg.qwen_endpoint,
        "default_model": cfg.qwen_model,
        "context_tokens": cfg.qwen_context_tokens,
        "api_key_set": bool(cfg.qwen_api_key) and cfg.qwen_api_key != "not-needed",
    }
