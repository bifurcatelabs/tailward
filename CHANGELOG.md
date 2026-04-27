# Changelog

All notable changes to warden are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] — v2.1.0

### Added

- Svelte landing page at `/`. Lists projects warden has seen, with
  session count, last-active timestamp, active session_mode, and
  click-through to each project's most-recent session. Replaces the
  legacy Jinja project-index page; the SPA now owns every visible
  surface.
- Svelte project rules viewer at `/p/<ph>`. Read-only render of the
  parsed `CompiledPolicy` (path allow/deny globs, immutable files,
  forbidden bash patterns) + active `session_mode` for the project,
  with a pointer at `intent.md` for editing.
- New JSON endpoints `GET /v2/projects` and `GET /v2/projects/<ph>`
  that the new Svelte views read.
- `TopStrip` component on non-session pages (the rich `HeaderBar`
  continues to wrap the live audit surface).

### Changed

- `main.js` parses `window.location.pathname` to pick which top-level
  Svelte view to render (landing / project / session). Same bundle
  serves all three pages.

### Removed

- Legacy Jinja intent editor (`/p/<ph>` GET + `/p/<ph>/save` POST)
  and the `intent.html` / `index.html` / `base.html` templates.
  `intent.md` remains the source of truth — users hand-edit it; the
  daemon re-reads on each turn so changes land without restart.
  Decided per `memory/project_v3_handoff_deprecation.md` and the
  `memory/project_no_injection_position.md` framing.

## [v2.0.0] — trust layer cutover

The v2.0.0 release reframes the project as a **trust layer for AI coding
workflows** and retires the legacy v1.1 surfaces in favor of a single
multi-view Svelte SPA. See `memory/project_v2_cutover_plan.md` for the
full cutover sequence.

### Added (on `v0.2-trust-layer` branch since v1.1.0)

- Multi-view v0.2 Svelte chassis (`/v2`) with hash routing across three
  views: Session, Reflection, Platform.
- **Session view:** live event feed (Server-Sent Events + polling
  fallback), session timeline strip, hero metrics, copper visual identity.
- **Reflection view:** four panels of derived audit signal (idle gaps,
  prompt-length distribution, destructive-action approval cadence, claim
  verification verdicts), an LLM-scored self-rubric across four user-side
  dimensions (intent clarity, context coverage, verification engagement,
  mode coherence), and a cross-project past-sessions table with a
  per-session deep view (8-mode score card + per-dimension rubric
  trajectory).
- **Platform view:** per-turn metrics (TTFT, TPS, cache hit ratio) charted
  with vendored uPlot, local LLM endpoint probe worker, LLM call budget
  panel, and a transparency panel exposing per-call-kind sampler params
  + verbatim prompts (`/llm-profiles`).
- Cross-project session picker in the HeaderBar; `watch_paths` /
  `exclude_paths` config for opt-in scope.
- `ModeProfile` registry keyed off free-form `session_mode` labels;
  permissive default for unknown labels (yolo, etc.) so non-build sessions
  aren't punished by build-mode rubric framing.
- Logical-turn coalescing keyed on `message_id`; per-turn `turn_metrics`
  table with token usage and stop reasons.
- LLM-call instrumentation (`llm_call_metrics` table) recording every
  Qwen call's kind, max_tokens, finish_reason, duration, and usage
  breakdown. Enabled the data-backed rubric token-budget calibration.
- Detection + flagging of Claude Code's `/compact` synthesized turns
  (`isCompactSummary: true`) as a distinct `compact_summary` event type
  with explicit chip + copy in the live feed.
- Session-state persistence: `started_at`, cumulative token totals,
  `last_message_id`, `last_model` survive daemon restarts.
- Session-mode column on `rubric_scores`, `scope_snapshots`, and
  `turn_metrics` for honest cross-session aggregation.

### Changed

- **Live URL collapsed** `/p/<ph>/live/<sid>/v2` → `/p/<ph>/live/<sid>`.
  The Svelte chassis is now the canonical live audit surface; the
  `/v2` URL stays as a 308 redirect for bookmarks. Template renamed
  `live_v2.html` → `live.html`.
- **CLI binary renamed** `modmcp` → `warden`. `modmcp` kept as a
  backward-compat alias so wired-up Claude Code hook configs keep
  working; both binaries invoke the same entry point. Slated for
  removal in v3.0.0.
- Rubric calibration: `qwen_max_tokens_rubric` raised 2500 → 6000 based
  on observed truncation rates. Per-call-kind sampler params introduced
  matching Qwen3's published profiles (rubric on precise-coding profile,
  others on general).
- Default mode flips from implicit-build to explicit-permissive: yolo /
  unknown labels no longer trigger build-mode rubric framing.
- Internal Python package retains the name `modmcp` (`src/modmcp/`,
  `~/.modmcp/` state dir, `MODMCP_HOME` env var) — deliberate light-path
  rebrand, see `memory/project_v2_cutover_plan.md`.

### Fixed

- `FileState` rehydrates `project_path` and `project_hash` from
  `session_state` on init. Prior to this fix, daemon restart while the
  user's cwd was in a subdirectory could seed FileState with the
  subdirectory's hash, persisting downstream worker output (rubric,
  scope, turn_metrics, live_events) under a phantom project that was
  invisible to the canonical project's UI.
- Audit-rail dispatch correctly handles tool_use blocks embedded in
  assistant messages (the actual Claude Code transcript shape) rather
  than only bare `kind="tool_use"` events.
- LiveBus payload envelope no longer bakes `id=0` into stored rows; the
  envelope is reconstructed from row columns at fetch.

### Removed

- v1.1 Jinja-served audit surfaces and routes: `live.html`,
  `violations.html`, `trends.html`, `session_report.html`,
  `no_session.html`, `drift.html`, `ledger.html`, plus the legacy
  `live.js` partial renderer.
- The non-`/v2` route handlers: `/p/<ph>/live`, `/p/<ph>/live/<sid>`,
  `/p/<ph>/violations` (Jinja list page; ack/dismiss POST endpoints
  retained), `/p/<ph>/trends`, `/p/<ph>/sessions/<sid>`,
  `/p/<ph>/drift`, `/p/<ph>/ledger`. Reflection-view panels cover the
  cross-session aggregation use case.
- `src/modmcp/mcp_server.py` + `warden mcp` CLI subcommand +
  `mcp>=1.0` dependency. The trust-layer surface is effective without
  MCP integration; MCP was an active-mode affordance and the project
  pivoted away from active mode.
- `tests/test_mcp_server.py`, `tests/test_e2e_smoke.py` (the active-
  mode smoke; `test_e2e_passive_smoke.py` is the canonical regression).
- The `EVENT_TYPES`-vs-`live.js` regression test in `test_livebus.py`
  is replaced with one pinning the Svelte `FeedItem.svelte` chip
  branches and `live.svelte.js` `KNOWN_EVENT_TYPES` set.

## [v1.1.0] — passive-first audit pivot

The v1 active mode (preamble injection, drift-corrective queue) was
demoted to opt-in and unmaintained. Default flipped to `passive`: the
daemon observes and audits without injecting into the prompt stream.

### Added

- Failure-mode audit layer scoring 8 of 10 trust-breaking dimensions
  from `failure modes.md`.
- Constraints worker (rule-based), scope worker (files-touched
  baseline + creep detection), rubric worker (Qwen-judged 4-dimension
  per-turn rubric), audit claim verifier, drift detector, session-close
  consolidator producing 8-mode session report cards.
- Live UI at `/p/<hash>/live/<session_id>` with SSE-driven feed.

### Changed

- Default `warden_mode` flipped to `passive`. The `active` mode lives
  on as opt-in scaffolding for users who want preamble + correction
  injection.

## [v0.1.0] — initial release

Session-handoff prototype: phase-1 transcript synthesis into
`intent.md`, MCP server exposing handoff tools, hook bridges into
Claude Code's pre-prompt and pre-tool-use lifecycle.
