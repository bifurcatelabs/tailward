"""Configuration loading from ``~/.tailward/config.toml`` with sensible defaults."""

from __future__ import annotations

import tomllib
from dataclasses import asdict, dataclass, field
from typing import Any

from .paths import atomic_write_text, config_path, ensure_layout


@dataclass
class Config:
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

    # Per-call-kind sampler overrides. Qwen3 publishes distinct profiles
    # per task shape (per the model card):
    #   * thinking + general:        temp=1.0, presence_penalty=1.5
    #   * thinking + precise coding: temp=0.6, presence_penalty=0.0
    #   * non-thinking:              temp=1.0, presence_penalty=1.5
    # These per-kind defaults match those profiles. ``None`` (or unset in
    # config.toml) falls back to the global ``qwen_temperature`` /
    # ``qwen_presence_penalty`` above — backward compatible for users
    # who set globals before this differentiation existed.
    qwen_temperature_synth: float | None = 1.0          # generative + thinking
    qwen_temperature_drift: float | None = 1.0          # classification
    qwen_temperature_query: float | None = 1.0          # classification
    qwen_temperature_rubric: float | None = 0.6         # judging — stability over diversity
    qwen_temperature_consolidator: float | None = 1.0   # generative + thinking
    qwen_presence_penalty_synth: float | None = 1.5
    qwen_presence_penalty_drift: float | None = 1.5
    qwen_presence_penalty_query: float | None = 1.5
    qwen_presence_penalty_rubric: float | None = 0.0
    qwen_presence_penalty_consolidator: float | None = 1.5

    # Context window of the served model (used to size transcript slices).
    qwen_context_tokens: int = 32768

    # Per-call-type output budgets. Thinking models need generous headroom
    # — the budget covers the entire ``<think>`` preamble *plus* the visible
    # output, and Qwen3-class models routinely burn 1500-3000 tokens inside
    # thinking before producing the first output token.
    #
    # ``rubric`` was 2500 until v0.2 instrumentation (commit 9e40c5a) showed
    # 33% of rubric calls hitting ``finish_reason='length'`` with the model
    # truncating mid-think and returning empty content. Bumped to 6000 to
    # match the drift/query budgets users typically configure.
    qwen_max_tokens_synth: int = 6000         # Phase 1 synthesis
    qwen_max_tokens_drift: int = 1500         # per-turn drift verdict (no thinking by default)
    qwen_max_tokens_query: int = 1500         # query_intent answer (no thinking by default)
    qwen_max_tokens_rubric: int = 6000        # per-sample 4-dimension rubric, thinking on
    qwen_max_tokens_consolidator: int = 8000  # end-of-session 8-mode report card, thinking on

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

    # Project scope filters. Default empty lists = watch every project
    # under ``~/.claude/projects/``. Both lists accept entries in either
    # form: the sanitized folder name as Claude Code stores it
    # (e.g. ``C--myproject``) or the absolute project path
    # (e.g. ``C:/code/myproject``). Match is exact and case-sensitive.
    #
    # Semantics: ``watch_paths`` whitelists (when non-empty, only listed
    # projects are watched). ``exclude_paths`` blacklists (always applies).
    # An entry in both is excluded.
    #
    # Use cases:
    #   * a personal box that also has work projects you don't want
    #     audited locally → put work paths in ``exclude_paths``.
    #   * a focused dogfooding setup → list only the project(s) you're
    #     actively reviewing in ``watch_paths``.
    watch_paths: list[str] = field(default_factory=list)
    exclude_paths: list[str] = field(default_factory=list)

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

    # User-side self-rubric (v0.2). Mirrors the assistant rubric across
    # 4 user-side dimensions (intent_clarity, context_coverage,
    # verification_engagement, mode_coherence). Runs less often than
    # the assistant rubric — user-side patterns emerge over multi-turn
    # windows so rapid sampling adds noise without signal. Synthesized
    # /compact turns are excluded.
    user_rubric_turn_interval: int = 6
    user_rubric_min_text_chars: int = 80

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

    # Synthesis stream (v2.6): periodic incremental snapshots fire when
    # the assistant turn's reported input_tokens grows by this much
    # since the last snapshot. Token-based (not turn-based) so dense
    # file-reading turns don't go uncaptured. Default sized so a typical
    # local 32k-context model gets ~3 snapshots before saturation;
    # users with bigger context windows can raise it.
    synthesis_periodic_tokens: int = 10000

    # Input budget for incremental synthesis prompts. Conservative
    # default so a 32k-context local model can synthesize comfortably;
    # users with bigger models (64k, 128k, 256k context) raise this to
    # let the prompt see more of the recent transcript. Char cap is
    # derived as ~3.2 chars/token (conservative English estimate).
    synthesis_max_input_tokens: int = 24000

    # Backoff after repeated synthesis failures. When the local LLM is
    # unreachable, the worker would otherwise keep firing on every
    # threshold crossing — generating noise in logs, retrying needlessly,
    # and hammering an upstream that's already known broken. After
    # ``synthesis_failure_threshold`` consecutive failures, suppress
    # further attempts for ``synthesis_backoff_seconds``. Any successful
    # call (periodic or on-demand) resets the counter.
    synthesis_failure_threshold: int = 3
    synthesis_backoff_seconds: float = 300.0

    # Comprehensive synth threshold (Trigger 3). When the agent's
    # observed input_tokens fullness crosses this fraction of Claude's
    # context window, fire a comprehensive synth that produces a fresh
    # ``intent.md`` for the project. The original v1 compaction-handoff
    # vision: passive artifact at the right moment, user in the middle,
    # no injection. Default 75% gives a margin before Claude Code's own
    # auto-compact heuristic typically fires (~85-90%).
    synthesis_comprehensive_fullness_pct: float = 0.75
    # Estimated upper bound of Claude's context window for the
    # comprehensive-synth trigger. Default 200k matches the standard
    # Claude variant; users on the ``[1m]`` model IDs should raise to
    # 1_000_000 so the 75% trigger doesn't fire on every active session
    # well before any real compaction risk. Used to compute the
    # absolute token threshold from the percentage above.
    synthesis_claude_context_tokens: int = 200_000

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

    # OS-level toast notifications. Off by default: the live web UI is
    # the primary surface, and an interactive coding session at the
    # same machine doesn't need OS interrupts about events the user is
    # already watching. Ledger rows and LiveBus events still fire — only
    # the OS toast is suppressed. Flip to true if you want to walk away
    # from the page and still get pinged on high-severity surfacings.
    os_notifications_enabled: bool = False

    @classmethod
    def default(cls) -> Config:
        return cls()

    def to_toml(self) -> str:
        lines = ["# tailward configuration. Restart daemon after editing.", ""]
        for key, value in asdict(self).items():
            if isinstance(value, str):
                escaped = value.replace("\\", "\\\\").replace('"', '\\"')
                lines.append(f'{key} = "{escaped}"')
            elif isinstance(value, bool):
                lines.append(f"{key} = {'true' if value else 'false'}")
            elif isinstance(value, list):
                items = ", ".join(
                    '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'
                    for v in value
                )
                lines.append(f"{key} = [{items}]")
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
