# Changelog

All notable changes to warden are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [v2.7.0] — 2026-05-01

The internal rename. ``tailward`` becomes the canonical name across
every surface — PyPI package, GitHub repo, CLI binary, internal
Python package, state directory, env var. ``warden`` survives as a
CLI deprecation alias for v2.x muscle memory (removed in v4).
Originally scoped as part of v3.0.0 but pulled forward so v3 can be
purely the Tauri/distribution story without conflating it with a
1000-line import diff.

### Changed
- **Python package directory** — ``src/modmcp/`` → ``src/tailward/``.
  All 65 ``from modmcp.X import ...`` lines rewritten across 24
  files. ``[tool.hatch.build.targets.wheel]`` aligned to
  ``src/tailward``. Logger names + FastAPI app title now use
  ``tailward.*``. The daemon-spawn module path in ``lifecycle.py``
  aligned to ``tailward.daemon``.
- **CLI binary** — ``tailward`` is canonical (``tailward daemon
  start``, ``tailward handoff``, etc.). ``warden`` preserved as a
  deprecation alias from v2.x — ``warden daemon start`` works
  identically. Slated for removal in v4.0.0. The original ``modmcp``
  CLI alias is retired.
- **State directory** — ``~/.modmcp/`` → ``~/.tailward/``. Existing
  v2.x state migrates forward automatically on first v2.7+ run via
  the new ``migrate_v2x_state_if_needed`` helper in ``paths.py``.
  Strategy is copy + breadcrumb: the legacy directory stays as a
  backup with a ``MIGRATED_TO_TAILWARD.txt`` file so a user
  navigating there sees where state went. Idempotent.
- **Env var** — ``TAILWARD_HOME`` is canonical. ``MODMCP_HOME``
  honored as deprecation alias for v2.x configs (removed in v4).
- **Per-repo symlink target** — ``tailward link`` writes to
  ``<repo>/.tailward/intent.md``; existing ``<repo>/.modmcp/`` from
  v2.x stays as orphan until the user re-runs the command.
- **README aligned** — install section documents the auto-
  migration; CLI examples promote ``tailward`` as canonical with
  ``warden`` noted as alias; storage paths show ``~/.tailward/``;
  source paths in references point at ``src/tailward/``; ASCII
  architecture diagram updated.

### Migration notes for v2.6.x users

- **Existing state preserved.** First v2.7+ daemon run sees
  ``~/.modmcp/`` exists, ``~/.tailward/`` doesn't, and copies
  forward. No manual migration needed. The legacy directory
  remains as a backup.
- **Existing scripts keep working.** ``warden daemon start`` and
  ``MODMCP_HOME=...`` both work for the entire v3.x line — just
  surfaces a deprecation framing in the README. Scripts and aliases
  pinned to those names continue to function unchanged.
- **Existing ``<repo>/.modmcp/intent.md`` symlinks become orphans.**
  Re-run ``tailward link`` in each project to update the symlink
  target to ``<repo>/.tailward/intent.md``. The old directory can
  be deleted at your convenience.

## [v2.6.1] — 2026-05-01

Polish + defensive infrastructure on top of v2.6.0. No runtime
behavior change for the synthesis stream; CI is now a more
load-bearing surface, and the public README aligns with the
project's actual architectural stance.

### Added
- **Schema-pinning inventory canary.** ``test_audit_inventory_canary``
  runs ``audit_jsonl`` against a comprehensive synthetic fixture
  covering every known event type and shape, asserts the resulting
  type / message-key / content-block-type inventories match pinned
  sets. When Claude Code ships a new shape, the test fails loudly
  with a diff — drift is no longer silent.
- **Trusted Publishing on tag push.** ``.github/workflows/publish-
  to-pypi.yml`` builds + publishes via PyPA's OIDC trusted-publishing
  flow when a ``v*`` tag lands. No API token stored; required-
  reviewer environment gate prevents auto-publish without explicit
  approval. Generates PEP 740 attestations for free.
- **PyPI project URLs.** Sidebar links for Homepage / Issues /
  Changelog now populate on the project page.
- **README "At a glance".** Feature inventory section surfacing
  v2.x UX additions (search, filter pills, full-session arc lens,
  snapshot panel, past-sessions table, Settings transparency,
  auto-handoff) — natural slots for screenshots in a follow-up.

### Changed
- **Claude Code 2.1.123 added to ``VALIDATED_VERSIONS``.** Re-audit
  of a 36MB session spanning four binary versions found schema
  stable; two new ``null``-placeholder message keys (``container``,
  ``context_management``) pinned in ``KNOWN_UNREAD_MESSAGE_FIELDS``
  with re-audit guidance if they become populated.
- **README aligned with v2.6 reality.** Status bumped through v2.6.1;
  Settings view documented; synthesis stream described; Web UI route
  table updated for v2.5+ endpoints (reflection/agent-behavior split,
  snapshots, arc, synthesize, search); test count 120 → 180;
  architecture diagram includes synthesis_worker.
- **README posture cleanup.** Original v1 active-mode design (MCP
  server + UserPromptSubmit hook + drift correctives) framed as
  historical artifact only — not maintained, not tested, may no
  longer work. Drops the install walkthrough and active-mode-only
  config keys; removes the binary "passive vs active" framing that
  implied parity. Aligns the public README with the no-injection
  architectural stance memo'd internally as ``rejected on principle``.
- **CI action versions bumped.** ``actions/checkout`` and
  ``actions/setup-python`` v4/v5 → v6; ``actions/setup-node`` v4 →
  v5. No behavior change.

### Fixed
- **Lint cleanup.** Three ``B904`` ``raise ... from None`` additions
  on the new synth endpoint exception handlers + import-sort fixes
  the ruff CI job had flagged on the v2.6.0 cut.

## [v2.6.0] — 2026-05-01

The original v1 compaction-handoff vision realized passively. A
single ``synthesis_worker`` writes compaction-resistant snapshots to
disk on three triggers — periodic (token-based threshold), on-demand
(button click), and comprehensive (when fullness crosses a fraction
of Claude's context window) — and the comprehensive trigger updates
the project's ``intent.md`` so the user has a coherent handoff
artifact ready before Claude Code's own auto-compact heuristic
typically fires. User in the middle, no prompt injection.

### Added
- **Session synthesis stream.** Three triggers, one writer.
  Periodic captures fire when Claude's ``input_tokens`` grows past
  ``synthesis_periodic_tokens`` since the last snapshot. On-demand
  captures fire when the user clicks the new "synthesize now" button
  in the Session-view ``SnapshotsPanel``. Comprehensive synth fires
  when fullness crosses ``synthesis_comprehensive_fullness_pct`` of
  ``synthesis_claude_context_tokens`` (default 75% of 200k = 150k);
  it produces a fresh ``intent.md`` for the project and archives
  the prior version under ``~/.modmcp/projects/<hash>/archive/``.
- **Session-view ``SnapshotsPanel``.** Lists captures with model +
  trigger + claude-side fullness + local-side input chars, expands
  rows to show the markdown body, fires on-demand synth from the
  panel header, dismisses stale errors, auto-clears on next success,
  caps vertical real estate via internal scroll.
- **Three new LiveBus event types.** ``synthesis_captured`` (any
  successful capture), ``synthesis_failed`` (loud failure with
  underlying error), ``intent_updated`` (comprehensive headline).
  All three integrate into the feed: filter pill (``synthesis``
  group), distinctive chip styles (violet for routine snapshots,
  red for failures, saturated copper for intent.md updates), and
  per-event expand bodies surfacing the relevant payload.
- **Backoff for repeated upstream failures.** After K consecutive
  failures (default 3), the worker pauses synthesis for N seconds
  (default 300). Periodic skips silently during suppression;
  on-demand raises ``SynthesisSuppressed`` → 503 with retry-after.
  Prevents the "broken upstream gets hammered every minute"
  failure mode.
- **Settings transparency.** ``LlmProfilesPanel`` now surfaces
  both ``synth (comprehensive)`` (the existing end-of-session synth
  via ``warden handoff``) and ``synth (incremental)`` (the new
  worker-driven prompt) verbatim, so users see exactly what's sent.
- **``docs/synthesis-recommendations.md``.** Living surface for
  A/B testing notes — model + sampler + prompt combinations as
  they're calibrated against real session data.

### Changed
- **Worker reads JSONL from disk on every capture.** Replaces the
  earlier in-memory rolling event window. JSONL is the source of
  truth; an in-memory parallel was both an unnecessary optimization
  and a bootstrap-correctness bug (cold-start calls saw an empty
  window). Falls back to ``claude_projects_root()`` lookup when the
  watcher hasn't re-attached the session yet (post-daemon-restart
  edge case).
- **Synthesis input widened.** Dispatcher now enqueues
  ``user_message + tool_use + tool_result`` alongside
  ``assistant_message``; worker mirrors phase1's denoise format
  (``USER:/ASSISTANT:/[tool_use:NAME]/[tool_result]``). Earlier the
  synth saw only assistant prose, which under-captured context.
- **Local-stack panels labeled "local"** for clarity now that the
  worker also calls the local LLM on its own cadence.

### Fixed
- **``intent.md`` ``updated:`` frontmatter** now bumps when
  comprehensive synth writes a fresh document. Earlier, fresh
  bodies kept the stale timestamp from prior versions.
- **Copy-header timestamp in feed** rendered as 1970-01-21 because
  ``LiveEvent.created_at`` arrives as seconds-since-epoch but
  ``new Date()`` expects milliseconds. The inline clock was already
  correct; only the copy path missed the conversion.

## [v2.5.0] — 2026-04-30

View-identity discipline carried into the API + a fourth view for
local-stack transparency. The view split that landed in v2.4.0
established the principle (each view = one source of data); this
release pushes that discipline through the endpoint shape and the
overall view layout.

### Changed
- **API split along source-of-data axis.** ``/v2/reflection/{ph}`` was
  returning agent-side fields (claim verification, stop_reasons) after
  the v2.4.0 view reorg moved those panels to Platform — endpoint name
  no longer described the payload. Split into ``/v2/reflection/{ph}``
  (user-only: pacing, prompt lengths, permission-mode tool counts,
  approvals, memory edits) + new ``/v2/platform/{ph}/agent-behavior``
  (third-party-provider signals: claim verification + stop_reasons).
  Each panel fetches its narrow endpoint.

### Added
- **Settings view — fourth tab for local-stack transparency.**
  ``LlmProfilesPanel`` and ``LlmBudgetPanel`` describe what's running
  locally, not what the third-party model emitted. Carving them into
  a dedicated Settings view keeps Platform focused on third-party
  signals; users on cloud APIs can ignore the tab entirely; users
  running local inference get a single place to verify what's wired up.
  Tab nav updated to four entries: session / reflection / platform /
  settings.

## [v2.4.0] — 2026-04-30

A two-day push covering: secret-pattern detection across content
events, memory-edit signal differentiation, view-identity reorg
between Reflection and Platform, full-session arc with a meaningful
UX upgrade, and shared multi-toggle filter pills across both
event-bearing surfaces. Plus a verifier-scope fix and a livebus
regression test that closes the silent-publish gap behind several
prior bugs.

### Added

- **Project-scoped keyword search across content events.** New
  `/p/{ph}/search` endpoint + `SearchPanel.svelte`. Inline-expand
  result preview, deep-link via `#event-<id>` hash, copy-with-header,
  and per-event referenceable IDs across both feed and search results.
- **Exfiltration regex detection across content channels.** Pattern
  library in `modmcp.schema.exfiltration` (OpenAI / Anthropic /
  GitHub PAT / AWS access key / Stripe live+test / Slack token /
  private key block). Scanned across `user_turn`, `turn`,
  `compact_summary`, `tool_call`, and `away_summary` payloads —
  secrets in chat dialogue are caught and redacted, not just tool
  inputs. Fires `exfiltration_alert` with a redacted preview; the
  sanitized payload is what lands in `live_events`.
- **Source-event marking for redacted secrets.** Source events
  (`user_turn` / `turn` / `tool_call` / etc.) carry
  `secrets_redacted: ["pattern_name", ...]` when their text was
  scanned-and-redacted. FeedItem renders a small `SECRET` sub-badge
  next to the main chip with the matched pattern names on hover, so
  the alert chip and its source turn are visually linked.
- **Multi-toggle filter pills shared between Feed and SessionTimeline.**
  Extracted `feedFilter.svelte.js` as a singleton store. Multiple
  groups can be active at once (the common case: `user + assistant +
  secrets`); empty active set = "all" view. Toggling pills on either
  surface narrows both. New `secrets` filter pill spans the alert
  chips and source events with `secrets_redacted`.
- **Memory-edit signal type.** Edits to
  `~/.claude/projects/<ph>/memory/**` are by-design calibration
  artifacts, not policy events. `is_memory_edit_path` in
  `modmcp.schema.constraints` branches the path-policy check so these
  edits emit a neutral `memory_edit` event instead of a
  `constraint_violation`. Reflection panel surfaces a sub-row counting
  them alongside the violation cadence — signal stays visible without
  polluting violation counts.
- **Assistant `stop_reason` distribution panel** on Platform. Pivots
  turn events by `stop_reason`, deduped per `message_id` so the panel
  reflects how messages *ended* rather than how often the dispatcher
  emitted them. New `stop_reason_counts` ledger query; rendered as
  `StopReasonsPanel.svelte`.
- **Full-session arc endpoint.** New
  `/p/{ph}/live/{session_id}/arc` returns lightweight
  `(id, event_type, created_at)` triples for every event in the
  session — separate from the tail-windowed feed replay so the
  SessionTimeline strip can reflect the whole arc without bloating
  the feed bootstrap.
- **SessionTimeline UX overhaul.**
  - **Hover tooltips** with friendly type labels (matching the chip
    vocabulary), local-time timestamps, and time-deltas from the
    previous event.
  - **Tick clustering** — events within ~0.4% pct merge into one
    cluster with width that scales with count and a small top mark.
    Cluster hover shows the count + member list.
  - **Time-range lens** (30m / 1h / 8h / 24h / all) so the strip can
    show detail for active windows instead of compressing days of
    activity into one viewport. Window-bounds drive layout, so
    "8h" actually renders 8h even when activity is sparse.
  - **Idle banding** at leading + trailing edges of the window plus
    inter-event gaps, labeled "no activity, Xm Ys" — observational,
    not stateful.
- **Reflection panel auto-refresh on violation ack/dismiss.**
  LiveStore dispatches a `reflection:refresh` window event after
  each ack/dismiss POST resolves; ReflectionView listens and
  re-fires its loader. The destructive-action cadence card no longer
  drifts behind the ledger until manual reload.
- **Press state + inline confirmation on FeedItem violation buttons.**
  Buttons now have a 1px-translate `:active` style, go disabled with
  a `…` label during the in-flight POST, and get replaced in-place
  with a green `✓ acknowledged` / `✓ dismissed` confirmation once
  the action resolves. The click feels real instead of silently
  landing in the ledger.
- **Regression test pinning publish call sites to registered event
  types.** AST-walks `src/modmcp/daemon/**/*.py` looking for
  `*.publish(sid, ph, "type", payload)` calls and asserts every
  literal `event_type` is in `livebus.EVENT_TYPES`. Closes the gap
  behind `permission_mode_change` / `away_summary` /
  `tool_interrupted` / `exfiltration_alert` shipping silently broken
  in v2.3.0.

### Changed

- **View source-of-data split.** Reflection and Platform reorganized
  by where the data comes from:
  - **Reflection** = user-side signals (typed prompts, mode posture,
    response cadence, self-rubric).
  - **Platform** = third-party-provider-facing signals (turn metrics,
    assistant stop_reasons, claim verification, plus probes of the
    local LLM stack that audits them).
  Claim verification verdicts and stop_reasons cards moved out of
  Reflection into Platform as new autonomous panels
  (`ClaimVerificationPanel`, `StopReasonsPanel`). Subtitles
  re-aligned to match each surface's identity.
- **Filter pill behavior** — single-active → multi-toggle. The "all"
  pill is the inverse signal (active when no group is) and clears
  the set on click. Empty-state hint and click-handler updated
  accordingly.
- **Path-policy check** — branches on `is_memory_edit_path` before
  reporting a violation. Memory-file edits flow through the new
  `memory_edit` channel instead of being mis-classified.
- **Tool-calls-by-mode panel** — title renamed to "tool calls by
  user-selected permission mode" for accuracy; footnote tightened
  (drops the warden-history note about untagged events).
- **Voice tweaks across the dashboard.** "leak" → "secret" in
  user-facing strings (industry vocabulary; internal event type
  stays `exfiltration_alert`). "idle" → "no activity" on session-arc
  bands. Several footnotes shortened to drop "high-signal rows" /
  narrating-the-data framings — stays observational, not coaching.

### Fixed

- **livebus EVENT_TYPES gap.** `permission_mode_change`,
  `away_summary`, `tool_interrupted`, and `exfiltration_alert`
  weren't in the `EVENT_TYPES` frozenset; publish call sites caught
  the resulting `ValueError` silently and no chip ever rendered. All
  registered + a regression test (above) prevents recurrence.
- **Claim-verifier scope.** Test-file string literals (e.g.
  `text = "I removed FooBar"` inside `tests/test_audit.py`) were
  counting as evidence the symbol still existed — the verifier
  greppped the literal and flagged the removal claim as
  contradicted. New `_match_in_string_literal` heuristic skips
  matches inside single-line string literals when the file is
  detected as a test file (`tests/` directory or `test_*.py` /
  `*_test.py`). Real test-file imports / class defs / unquoted
  references still count as evidence.

### Removed

- **`/p/{ph}/live/{session_id}/v2` backward-compat redirect.** Held
  over from v2.0.0's URL-collapse cutover for bookmarks predating
  the `/v2` prefix elimination. Realistic bookmark exposure is zero,
  and CodeQL kept flagging the regex-rebind sanitizer pattern as an
  open-redirect sink despite multiple rounds of hardening. Deletion
  is cleaner than another suppression attempt; drops `re`,
  `PathParam`, `RedirectResponse` imports as a side effect.

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
