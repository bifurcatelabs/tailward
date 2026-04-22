"""Configuration loading from ``~/.modmcp/config.toml`` with sensible defaults."""

from __future__ import annotations

import tomllib
from dataclasses import asdict, dataclass
from typing import Any

from .paths import atomic_write_text, config_path, ensure_layout


@dataclass
class Config:
    # Qwen / llama.cpp OpenAI-compatible endpoint.
    qwen_endpoint: str = "http://127.0.0.1:8080/v1"
    qwen_model: str = "qwen2.5-8b-instruct"
    qwen_api_key: str = "not-needed"

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
    qwen_max_tokens_synth: int = 6000     # Phase 1 synthesis
    qwen_max_tokens_drift: int = 1500     # per-turn drift verdict
    qwen_max_tokens_query: int = 1500     # query_intent answer

    # Daemon HTTP (hook IPC + web UI) on localhost.
    http_host: str = "127.0.0.1"
    http_port: int = 7878

    # Transcript watcher: how many projects we will watch simultaneously.
    max_watch_projects: int = 32

    # Phase 2 defaults.
    phase2_turns_default: int = 8
    drift_threshold: float = 0.35  # pattern-score above which LLM check runs
    per_turn_budget_seconds: float = 3.0
    per_turn_hard_cap_seconds: float = 10.0

    # Claim verification.
    claim_grep_budget: int = 200  # max files scanned per claim

    # Hook behavior.
    hook_timeout_ms: int = 400

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
