"""Configuration loading from ``~/.modmcp/config.toml`` with sensible defaults."""

from __future__ import annotations

import tomllib
from dataclasses import asdict, dataclass
from typing import Any, Literal

from .paths import atomic_write_text, config_path, ensure_layout

WardenMode = Literal["passive", "active"]


@dataclass
class Config:
    # Warden active-participation mode. ``passive`` is the default in v1.1:
    # no preamble injection and no MCP tool usage influences context, the
    # daemon only observes and audits. Flip to ``active`` to re-enable the
    # UserPromptSubmit preamble + drift-corrective queue.
    warden_mode: WardenMode = "passive"

    # Qwen / llama.cpp OpenAI-compatible endpoint.
    qwen_endpoint: str = "http://127.0.0.1:8080/v1"
    qwen_model: str = "qwen2.5-8b-instruct"
    qwen_api_key: str = "not-needed"

    # Per-call-kind model overrides (empty = fall back to qwen_model). Useful
    # when the same endpoint serves multiple quants or sizes: e.g. route
    # drift at a higher-quality quant than synth if rubric classification
    # degrades under heavy quantization.
    qwen_model_synth: str = ""
    qwen_model_drift: str = ""
    qwen_model_query: str = ""
    qwen_model_rubric: str = ""
    qwen_model_consolidator: str = ""

    # Sampling (Qwen3 thinking-mode defaults).
    qwen_temperature: float = 0.6
    qwen_top_p: float = 0.95
    qwen_top_k: int = 20
    qwen_min_p: float = 0.0
    qwen_presence_penalty: float = 0.0
    qwen_repetition_penalty: float = 1.0

    # Context window of the served model (used to size transcript slices).
    qwen_context_tokens: int = 32768

    # Per-call-type output budgets. Thinking models need generous headroom.
    qwen_max_tokens_synth: int = 6000         # Phase 1 synthesis
    qwen_max_tokens_drift: int = 1500         # per-turn drift verdict
    qwen_max_tokens_query: int = 1500         # query_intent answer
    qwen_max_tokens_rubric: int = 2500        # per-sample 4-dimension rubric
    qwen_max_tokens_consolidator: int = 8000  # end-of-session 8-mode report card

    # Qwen3 thinking mode, per call-type. Synth benefits from deep reasoning;
    # drift/query are fast-path structured tasks where thinking just burns
    # tokens. Routed via ``extra_body.chat_template_kwargs.enable_thinking``.
    qwen_enable_thinking_synth: bool = True
    qwen_enable_thinking_drift: bool = False
    qwen_enable_thinking_query: bool = False
    qwen_enable_thinking_rubric: bool = True
    qwen_enable_thinking_consolidator: bool = True

    # Daemon HTTP (hook IPC + web UI) on localhost.
    http_host: str = "127.0.0.1"
    http_port: int = 7878

    # Transcript watcher: how many projects we will watch simultaneously.
    max_watch_projects: int = 32

    # Phase 2 defaults.
    phase2_turns_default: int = 8
    drift_threshold: float = 0.35  # pattern-score above which LLM check runs
    per_turn_budget_seconds: float = 3.0
    per_turn_hard_cap_seconds: float = 30.0  # thinking-mode Qwen calls can run 3-10s

    # Claim verification.
    claim_grep_budget: int = 200  # max files scanned per claim

    # Hook behavior.
    hook_timeout_ms: int = 400

    # Failure-mode audit layer (v1.1).
    # Rubric sampling: Qwen-judged rubric every N assistant turns, plus
    # triggered runs on scope creep and first-person completion claims.
    rubric_turn_interval: int = 5
    rubric_min_text_chars: int = 80     # skip trivially short turns

    # Scope tracking: files-touched baseline comes from the rolling median
    # of the previous N completed sessions for the same project. Creep
    # fires when files_touched > baseline * creep_factor OR absolute floor.
    scope_baseline_window: int = 5
    scope_creep_factor: float = 2.0
    scope_creep_floor: int = 12         # files-touched below this never fires

    # Session close (Phase 4): idle threshold after which the consolidator
    # runs; detector polls at the specified cadence.
    session_idle_seconds: float = 600.0
    session_close_poll_seconds: float = 60.0

    # Live UI transport: SSE endpoint caps + replay window. The replay
    # window applies to the page-load tail and the SSE backfill on
    # reconnect; live.js caps the rendered DOM at 200 nodes regardless,
    # so a smaller window here just trims initial paint cost.
    live_sse_max_subscribers_per_session: int = 4
    live_sse_replay_events: int = 100
    live_sse_keepalive_seconds: float = 20.0

    # v0.2 Platform probe worker. Probes the *local* LLM endpoint only
    # — synthetic probes against ``api.anthropic.com`` would mostly
    # measure ISP / CDN edge variance, not service health, so we
    # deliberately don't ping it. The local endpoint is what we
    # control and what rubric quality silently depends on.
    probe_interval_seconds: float = 30.0
    probe_timeout_seconds: float = 5.0
    probe_enabled: bool = True

    @classmethod
    def default(cls) -> Config:
        return cls()

    def to_toml(self) -> str:
        lines = ["# modmcp configuration. Restart daemon after editing.", ""]
        for key, value in asdict(self).items():
            if isinstance(value, str):
                escaped = value.replace("\\", "\\\\").replace('"', '\\"')
                lines.append(f'{key} = "{escaped}"')
            elif isinstance(value, bool):
                lines.append(f"{key} = {'true' if value else 'false'}")
            else:
                lines.append(f"{key} = {value}")
        return "\n".join(lines) + "\n"


def _coerce(raw: dict[str, Any]) -> Config:
    defaults = asdict(Config.default())
    merged: dict[str, Any] = {}
    for k, default_value in defaults.items():
        if k in raw:
            merged[k] = raw[k]
        else:
            merged[k] = default_value
    return Config(**merged)


def load_config() -> Config:
    """Load config from disk, creating the default file on first access."""
    ensure_layout()
    path = config_path()
    if not path.exists():
        cfg = Config.default()
        atomic_write_text(path, cfg.to_toml())
        return cfg
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    return _coerce(raw)


_cached: Config | None = None


def get_config(*, reload: bool = False) -> Config:
    global _cached
    if _cached is None or reload:
        _cached = load_config()
    return _cached
