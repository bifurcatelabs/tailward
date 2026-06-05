"""Configuration loading from ``~/.tailward/config.toml`` with sensible defaults."""

from __future__ import annotations

import tomllib
from dataclasses import asdict, dataclass, field
from ipaddress import ip_address
from typing import Any

from .paths import atomic_write_text, config_path, ensure_layout


def is_loopback_bind(host: str) -> bool:
    """True if ``host`` keeps the daemon invisible beyond the local machine.

    Loopback bindings (``127.0.0.1``, ``localhost``, ``::1``, anything in
    ``127.0.0.0/8``) keep the audit surface unreachable from other devices.
    Non-loopback bindings (``0.0.0.0``, an explicit network IP) expose the
    unauthenticated audit data to anything on the wire — see
    ``project_localhost_binding_security.md`` in memory for the foot-gun
    discussion.
    """
    if not host:
        return False
    h = host.strip().lower()
    if h in ("localhost",):
        return True
    try:
        return ip_address(h).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class LocalLLMProfile:
    """Read-only snapshot of a single profile's identity + sampler config.

    Returned by ``Config.profile()`` so workers can ask for "the profile
    that handles synth calls" without binding to specific field names —
    insulates callers from the flat per-profile schema in ``Config``.
    """
    enabled: bool
    endpoint: str
    api_key: str
    model: str
    context_tokens: int
    temperature: float
    top_p: float
    top_k: int
    min_p: float
    presence_penalty: float
    repetition_penalty: float
    max_tokens: int


# Call-kind → routing-field name. Used by ``Config.route_for`` so
# unknown kinds map cleanly to profile 1 instead of raising.
_ROUTE_FIELDS = {
    "drift": "local_llm_route_drift",
    "audit": "local_llm_route_audit",
    "rubric": "local_llm_route_rubric",
    "user_rubric": "local_llm_route_user_rubric",
    "synth": "local_llm_route_synth",
    "consolidator": "local_llm_route_consolidator",
    "query": "local_llm_route_query",
}


@dataclass
class Config:
    # Local LLM profiles — two slots. Each profile is fully self-
    # contained: endpoint, auth, model identity, sampler params.
    # Different profiles can hit different inference servers (useful
    # for direct-to-GPU-node routing without a model router) or
    # different model identities served by the same backend.
    #
    # Profile 1 is the default; every worker kind routes here unless
    # ``local_llm_route_<kind>`` is set to 2. Profile 2 is opt-in
    # (``local_llm_2_enabled``); when disabled, routes pointing at it
    # silently fall back to profile 1.
    #
    # Sampler param set follows what recent frontier models publish in
    # release notes: temperature, top_p, top_k, min_p,
    # presence_penalty, repetition_penalty, max_tokens. ``top_k`` /
    # ``min_p`` / ``repetition_penalty`` aren't standard OpenAI API
    # params; they're sent via ``extra_body`` to OpenAI-compatible
    # servers (vLLM, llama.cpp). ``presence_penalty`` is standard.

    # --- Profile 1 (default) ---
    local_llm_1_endpoint: str = "http://127.0.0.1:8080/v1"
    local_llm_1_api_key: str = "not-needed"
    # Model name your serving stack expects. Empty = pick a model.
    # Recent dense models that work well on consumer-class hardware:
    # Qwen3.6-27B (Q6_K for accuracy, Q4 for speed), Gemma-4-31B-Instruct.
    local_llm_1_model: str = ""
    # Context window of the served model (used to size transcript slices).
    local_llm_1_context_tokens: int = 32768
    local_llm_1_temperature: float = 0.6
    local_llm_1_top_p: float = 0.95
    local_llm_1_top_k: int = 20
    local_llm_1_min_p: float = 0.0
    local_llm_1_presence_penalty: float = 0.0
    local_llm_1_repetition_penalty: float = 1.0
    # Output budget. Thinking models need generous headroom — the budget
    # covers any reasoning preamble plus the visible output.
    local_llm_1_max_tokens: int = 8000

    # --- Profile 2 (optional alternate) ---
    local_llm_2_enabled: bool = False
    local_llm_2_endpoint: str = "http://127.0.0.1:8080/v1"
    local_llm_2_api_key: str = "not-needed"
    local_llm_2_model: str = ""
    local_llm_2_context_tokens: int = 32768
    local_llm_2_temperature: float = 0.6
    local_llm_2_top_p: float = 0.95
    local_llm_2_top_k: int = 20
    local_llm_2_min_p: float = 0.0
    local_llm_2_presence_penalty: float = 0.0
    local_llm_2_repetition_penalty: float = 1.0
    local_llm_2_max_tokens: int = 8000

    # --- Per-call routing (1 = profile_1, 2 = profile_2) ---
    local_llm_route_drift: int = 1
    local_llm_route_audit: int = 1
    local_llm_route_rubric: int = 1
    local_llm_route_user_rubric: int = 1
    local_llm_route_synth: int = 1
    local_llm_route_consolidator: int = 1
    local_llm_route_query: int = 1

    # Daemon HTTP (hook IPC + web UI) on localhost.
    http_host: str = "127.0.0.1"
    http_port: int = 7878

    # Transcript watcher: how many projects we will watch simultaneously.
    max_watch_projects: int = 32

    # Remote transcript aggregation (Phase 0). When set to a directory,
    # the watcher consumes it as ADDITIONAL projects roots alongside the
    # local ``claude_projects_root()``. Transcripts pulled from remote
    # boxes via ``tailward remote pull`` land as
    # ``<remote_mirror_root>/<box>/projects/<sanitized>/*.jsonl``; each
    # ``<box>/projects`` directory is watched exactly like the local
    # projects root, so remote sessions ingest through the same pipeline.
    # Empty = local-only (default). MUST NOT point inside
    # ``~/.claude/projects/`` nor equal a watched local root — that would
    # double-ingest and collide project hashes. Box roots are enumerated
    # at daemon start; a box pulled while the daemon is running is picked
    # up on the next restart (interval auto-discovery is a later phase).
    remote_mirror_root: str = ""

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
    per_turn_hard_cap_seconds: float = 30.0  # thinking-mode LLM calls can run 3-10s

    # Claim verification.
    claim_grep_budget: int = 200  # max files scanned per claim

    # Hook behavior.
    hook_timeout_ms: int = 400

    # Failure-mode audit layer (v1.1).
    # Rubric sampling: LLM-judged rubric every N assistant turns, plus
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

    def profile(self, idx: int) -> LocalLLMProfile:
        """Return profile 1 or 2's identity + sampler snapshot. Unknown
        indices fall back to profile 1."""
        if idx == 2:
            return LocalLLMProfile(
                enabled=self.local_llm_2_enabled,
                endpoint=self.local_llm_2_endpoint,
                api_key=self.local_llm_2_api_key,
                model=self.local_llm_2_model,
                context_tokens=self.local_llm_2_context_tokens,
                temperature=self.local_llm_2_temperature,
                top_p=self.local_llm_2_top_p,
                top_k=self.local_llm_2_top_k,
                min_p=self.local_llm_2_min_p,
                presence_penalty=self.local_llm_2_presence_penalty,
                repetition_penalty=self.local_llm_2_repetition_penalty,
                max_tokens=self.local_llm_2_max_tokens,
            )
        return LocalLLMProfile(
            enabled=True,
            endpoint=self.local_llm_1_endpoint,
            api_key=self.local_llm_1_api_key,
            model=self.local_llm_1_model,
            context_tokens=self.local_llm_1_context_tokens,
            temperature=self.local_llm_1_temperature,
            top_p=self.local_llm_1_top_p,
            top_k=self.local_llm_1_top_k,
            min_p=self.local_llm_1_min_p,
            presence_penalty=self.local_llm_1_presence_penalty,
            repetition_penalty=self.local_llm_1_repetition_penalty,
            max_tokens=self.local_llm_1_max_tokens,
        )

    def route_for(self, kind: str) -> int:
        """Map a worker call kind to its routed profile index (1 or 2).
        Silent fallback to 1 when the route points at a disabled
        profile 2 — disabling profile 2 in Settings shouldn't break any
        worker that was routed there."""
        field_name = _ROUTE_FIELDS.get(kind)
        if field_name is None:
            return 1
        idx = getattr(self, field_name, 1)
        if idx == 2 and not self.local_llm_2_enabled:
            return 1
        return 2 if idx == 2 else 1

    def profile_for(self, kind: str) -> LocalLLMProfile:
        """Resolve a call kind directly to the profile that handles it."""
        return self.profile(self.route_for(kind))

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

    # Migration: pre-profile schema used unprefixed local_llm_* keys
    # for what is now profile 1. Map old → new so existing configs
    # keep their values without manual editing. The next ``to_toml``
    # write replaces the legacy keys with the new prefixed shape.
    _LEGACY_PROFILE_1 = {
        "local_llm_endpoint": "local_llm_1_endpoint",
        "local_llm_api_key": "local_llm_1_api_key",
        "local_llm_model": "local_llm_1_model",
        "local_llm_context_tokens": "local_llm_1_context_tokens",
        "local_llm_temperature": "local_llm_1_temperature",
        "local_llm_max_tokens": "local_llm_1_max_tokens",
    }
    for legacy, new_key in _LEGACY_PROFILE_1.items():
        if legacy in raw and new_key not in raw:
            raw[new_key] = raw[legacy]

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
