"""Aggregated per-call-kind LLM configuration + prompt templates.

Surfaced via ``GET /llm-profiles`` so the Platform view can show the
user, verbatim, what tailward is asking the local LLM for each call
kind (synth / drift / query / rubric / consolidator). Transparency
about tailward's own prompts is on-mission for the trust-layer pitch:
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
from .local_llm import CallKind

# Synth lives in tailward.phase1, imported lazily so this module can be
# imported even before phase1's transcript-denoiser deps are loaded.
# The MCP server module + its ``query`` prompt was retired in v2.0.0;
# the ``query`` CallKind remains as the generic fallback in
# LocalLLMClient.complete() but has no surfaced prompt template.


def _synth_user_template() -> str:
    # phase1 passes the denoised transcript as the user prompt verbatim.
    return "<denoised transcript text>"


def _kind_profile(
    kind: CallKind,
    system: str,
    user_template: str,
    *,
    display_kind: str | None = None,
) -> dict[str, Any]:
    cfg = get_config()
    # Sampler + identity follow the routed profile for this kind.
    # Surfaced verbatim so the Platform/Settings transparency panel
    # shows what the worker will actually send, including which
    # profile (1 or 2) handles it.
    route_idx = cfg.route_for(kind)
    profile = cfg.profile(route_idx)
    return {
        "kind": display_kind or kind,
        "profile": route_idx,
        "model": profile.model,
        "max_tokens": profile.max_tokens,
        "temperature": profile.temperature,
        "top_p": profile.top_p,
        "top_k": profile.top_k,
        "min_p": profile.min_p,
        "presence_penalty": profile.presence_penalty,
        "repetition_penalty": profile.repetition_penalty,
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
    from . import synthesis_worker

    return [
        # Two synth flavors share the same ``synth`` CallKind (same
        # sampler params) but use different system prompts. The
        # comprehensive form is end-of-session-shaped (Intent JSON);
        # the incremental form is mid-session-shaped (markdown blob,
        # designed to chain into a comprehensive merge).
        _kind_profile(
            "synth",
            phase1.SYSTEM,
            _synth_user_template(),
            display_kind="synth (comprehensive)",
        ),
        _kind_profile(
            "synth",
            synthesis_worker.SYSTEM_INCREMENTAL,
            (
                "<denoised events from the live session — USER:/ASSISTANT:/"
                "[tool_use:NAME]/[tool_result] lines joined by `---`, "
                "capped at synthesis_max_input_tokens (default 24000) input "
                "tokens; tool inputs/outputs truncated to 500 chars each>"
            ),
            display_kind="synth (incremental)",
        ),
        _kind_profile("drift", drift_mod.PROMPT_SYSTEM, drift_mod.PROMPT_USER_TEMPLATE),
        _kind_profile("rubric", rubric_worker.PROMPT_SYSTEM, rubric_worker.PROMPT_USER_TEMPLATE),
        _kind_profile(
            "consolidator", session_close.PROMPT_SYSTEM, session_close.PROMPT_USER_TEMPLATE
        ),
    ]


def _profile_dict(cfg, idx: int) -> dict[str, Any]:
    """Serialize a profile's editable surface for the Settings UI. The
    raw api_key value is never returned (boolean ``api_key_set`` only);
    writes go through ``POST /v2/config/local-llm``."""
    p = cfg.profile(idx)
    return {
        "idx": idx,
        "enabled": True if idx == 1 else cfg.local_llm_2_enabled,
        "endpoint": p.endpoint,
        "model": p.model,
        "context_tokens": p.context_tokens,
        "temperature": p.temperature,
        "top_p": p.top_p,
        "top_k": p.top_k,
        "min_p": p.min_p,
        "presence_penalty": p.presence_penalty,
        "repetition_penalty": p.repetition_penalty,
        "max_tokens": p.max_tokens,
        "api_key_set": bool(p.api_key) and p.api_key != "not-needed",
    }


def endpoint_summary() -> dict[str, Any]:
    """Editable surface for both profiles + the routing matrix. UI
    consumes this to render the two-profile Settings card."""
    cfg = get_config()
    return {
        "profiles": [_profile_dict(cfg, 1), _profile_dict(cfg, 2)],
        "routing": {
            "drift": cfg.local_llm_route_drift,
            "audit": cfg.local_llm_route_audit,
            "rubric": cfg.local_llm_route_rubric,
            "user_rubric": cfg.local_llm_route_user_rubric,
            "synth": cfg.local_llm_route_synth,
            "consolidator": cfg.local_llm_route_consolidator,
            "query": cfg.local_llm_route_query,
        },
    }
