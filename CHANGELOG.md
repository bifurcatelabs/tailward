# Changelog

All notable changes to warden are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [v2.3.0] — 2026-04-29

Pass 3 / item 8 features — permission-mode transitions, away-summary
captures, tool interruptions, and a Reflection-view tool-calls-by-mode
aggregate. Plus security follow-ups (CodeQL alerts triaged + fixed),
display-precision bug fixes, and copy/brand polish.

### Added

- **Permission-mode timeline in the live feed.** Claude Code emits
  dedicated `permission-mode` events when the user changes mode via
  Shift+Tab (default / acceptEdits / bypassPermissions / plan).
  Tailward now surfaces a `permission_mode_change` chip showing
  `previous → current` so the session timeline reflects how trust
  posture shifted across the session.
- **Away-summary chips in the live feed.** Claude Code emits
  `type: "system"`, `subtype: "away_summary"` events with structured
  recap text (goal / current task / next action) when it observes
  user idle. Surfaced as a feed chip with expandable body.
- **Tool-interrupted events.** `toolUseResult.interrupted: true` on
  a tool_result fires a `tool_interrupted` chip with the resolved
  tool name (via a cached tool_use_id → name map in FileState).
- **Reflection-view "tool calls by permission mode" panel.** Pivots
  tool_call events into a tool × mode matrix with a "declined"
  column from tool_interrupted events. New `tool_calls_by_mode(ph)`
  ledger query; extended `/v2/reflection/{ph}` response.

### Changed

- **`tool_call` LiveBus events carry `permission_mode`** in payload,
  falling back to FileState carry-forward state when the event
  itself lacks the field. Required because assistant events (where
  tool_use blocks live) do not carry `permissionMode` in Claude
  Code 2.1.x — only dedicated `permission-mode` events and a
  fraction of user events do.
- **Cache hit ratio display precision** bumped from 0 to 1 decimal
  place. Heavy prompt caching produces 0.99-0.998 ratios that
  `.toFixed(0)` was collapsing to "100%" in both the cell value
  and uplot hover. Visible variance restored.
- **README framing.** Dropped narrator-y "we" / "whole point of
  this tool" in favor of direct design statements. "Qwen"
  generalized to "local LLM" with Qwen 2.5/3 as one example
  (Gemma, Llama 3.x, Mistral, Phi all work). Config keys retain
  the `qwen_` prefix as a historical artifact (renaming would
  break existing user configs).
- **Header brand.** TopStrip + HeaderBar show `tailward 2.3.0`.
- **LLM Profiles + Platform view header copy** tightened.

### Fixed

- **Closed-session badge respects resume past close.** The close
  worker stamps `session_close.consolidation_status` when a session
  goes idle past `session_idle_seconds`; previously that status
  stuck if the user resumed the session, leaving the badge visible
  on a clearly-active session. `live_state` now compares
  `last_seen_at` vs. `closed_at` and reports null close_status
  when activity follows the close.
- **GitHub Actions workflow least-privilege.** Added explicit
  `permissions: contents: read` at the top level. Previous default
  broad GITHUB_TOKEN write permissions were unnecessary for a
  test-only workflow.
- **`/p/{ph}/live/{session_id}/v2` redirect path-param validation.**
  FastAPI `Path()` pattern argument validates `ph` (12-char hex
  hash) and `session_id` (UUID-ish); defensive inline `re.fullmatch`
  re-check inside the function so CodeQL's data-flow analysis
  recognizes the sanitizer between user input and the redirect URL.
- **`tool_call` permission_mode tagging.** Initial implementation
  read `permission_mode` only off the immediate event; assistant
  events don't carry the field, so every tool_call published as
  null and the new Reflection-view matrix dumped everything into
  an "untagged" column. Carry-forward fallback fixes the chain.

## [v2.2.0] — 2026-04-28

Pre-publish polish + schema-fragility audit. UX refinements across all
three views; tooling hygiene for first-time GitHub publish; rebrand to
`tailward` as the public name.

### Added

- **Schema-pinning regression** (`tests/test_schema_pin.py`) — 9 cases
  pinning structural assumptions Warden makes about Claude Code's JSONL
  shape (assistant text/tool_use/thinking blocks, user typed /
  compact_summary / tool_result, system events, top-level field inventory).
  Catches schema drift before silent corruption.
- **Schema-audit helper** (`src/modmcp/schema/audit.py`) — `audit_jsonl()`
  + `VALIDATED_VERSIONS` frozenset (2.1.117, 2.1.119, 2.1.121) + 
  `is_validated_version()` check. Surfaces what fraction of events fall
  to "unknown" against the pinned schema.
- **Inline project picker** in the Platform view header
  (`ProjectQuickPicker.svelte`); reads `/v2/projects` and navigates to
  the chosen project's most-recent session at the Platform tab.
- **uPlot hover-value tag** on charts — floating top-right indicator
  showing the value at the cursor position with color-coded series dots.
- **Filter pills** on the live event feed (user / assistant / tool /
  rubric / audit / perf / session) with per-pill hover-tooltips.
- **Last-contact indicator** in the HeaderBar replacing the prior
  "live/polling/offline" status. Surfaces concrete time deltas
  ("just now" / "Ns ago" / "Nm ago" / "Nh ago") instead of an
  unfalsifiable liveness claim.
- **Closed-session badge** in the HeaderBar (slate tint) when viewing
  a closed/consolidated/done session.
- **Hover tooltips on Platform metric cards** — measurement caveats
  for prompt→response latency (first-block, includes thinking duration),
  output throughput (per-message wall-clock, not raw inference rate),
  cache hit ratio (input-side fraction). Designed to disclose what each
  number is and isn't.
- **Rubric in-flight indicator** — pulsing violet "calling local LLM"
  label visible during rubric scoring (fail-loudly campaign).
- **Rubric-done artifact pointer** — surfaces "scores landed in
  rubric_sample entries above" so users know where to find the result.
- **Self-rubric clarity** — distinguishes "quote" (verbatim italic from
  user) vs. "suggestion" (LLM-generated reflection prompt). Help line
  framed as reflection prompts not real-time corrections.
- **Latency-graph smoothing** — 3-point rolling average on ProbePanel
  Sparkline; raw latencies still drive the stats numbers.
- **GitHub Actions workflow** (`.github/workflows/test.yml`) — pytest
  + ruff + frontend build, parallels existing GitLab CI.
- **CONTRIBUTING.md** — project posture (in/out of scope), dev setup,
  test commands, PR shape guidance.
- **Status section** in README signaling actively-iterated work,
  current release, and which surfaces are supported vs. opt-in.

### Changed

- **Public name → `tailward`.** PyPI distribution name flipped from
  `modmcp` to `tailward`. CLI binary stays `warden`; internal Python
  package stays `modmcp`. Three-name layout — public, command, plumbing
  — chosen for the light-path rebrand.
- **`[project.urls]`** Repository points at
  `github.com/bifurcatelabs/tailward`; existing GitLab CE URL kept as
  `Mirror`.
- **README install instructions** updated for `pipx install tailward`
  (PyPI) and `pipx install git+https://github.com/bifurcatelabs/...`
  (latest commit) paths.
- **Tab subtitles** changed from trust-question framing to plain
  surface-description: session "live activity", reflection "your
  patterns", platform "inference path".
- **Last-event delta** floors fractional seconds (`3s ago`, not
  `3.2s ago`) below the one-minute threshold.
- **Closed-session badge tint** changed from muted-gray to slate to
  better signal "session is done" without reading as an error state.
- **Centering** on Reflection and Platform views (`margin: 0 auto`)
  to match Session view layout.
- **Genericized example user paths** in test fixtures
  (`/Users/glenn/...` → `/Users/example/...`) and illustrative
  comments. Pre-publish privacy pass; author attribution in
  `pyproject.toml` and `LICENSE` intentionally retained.
- **2.1.121 added to `VALIDATED_VERSIONS`** after audit confirmed no
  schema diffs vs. 2.1.119 in the fields Warden reads.

### Fixed

- **HeaderBar last-event delta** no longer leaks fractional seconds
  in the sub-minute display.

## [v2.1.0] — 2026-04-27

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
