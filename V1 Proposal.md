# modmcp v1 Implementation Proposal

> **Historical / superseded design document.** This was the original v1 spec
> in which Warden's primary mode of operation was injecting preambles and
> drift corrections into Claude Code's prompt stream. Dogfooding surfaced an
> observer-effect problem (any injected content shapes the next turn, which
> makes the same daemon both the judge and the judged), and the project
> pivoted to a **passive-first** posture in v1.1: observe, audit, and surface
> signal in a web UI without injecting into the prompt by default. Active
> mode from this proposal is still available as an opt-in (`warden_mode =
> "active"` in `~/.modmcp/config.toml`), but is no longer the headline
> experience.
>
> **For current behavior, modes, and setup, read [`README.md`](README.md).**
> **For the audit-layer spec that drove the pivot, read
> [`failure modes.md`](failure%20modes.md).**
>
> The rest of this file is preserved verbatim as design history. A few concrete
> implementation choices diverged from the spec below — most notably the
> UserPromptSubmit hook now talks to the daemon over HTTP on
> `127.0.0.1:<http_port>` rather than a Unix socket / named pipe, and
> `daemon.sock` does not exist in the shipped layout.

---

## 1. Overview

modmcp is a session-handoff and accountability layer for Claude Code. It runs as a long-running local daemon, mediates the transition between Claude sessions when context fills up, and stays active through the early turns of the new session — restating context, enforcing rules, and auditing the new agent's claims against transcript and filesystem evidence.

The local LLM (Qwen 3.6) plays the role of "warden" — a persistent gatekeeper that protects the new session both from the user's inconsistent openers (mood, energy, validation-seeking) and from the agent's own failure modes (confabulated completion, partial-commit-with-confidence, rule-establishment-without-adherence).

**North Star:** eliminate tokens wasted on manual re-iteration across sessions. Measurable as: re-pastes you stop typing, reminders you stop re-issuing, "did you actually do that?" follow-ups you stop asking.

## 2. Goals and Non-Goals

### Goals (v1 must deliver)
- **G1**: User-invoked Phase-1 handoff that produces a structured captured-intent file from the prior session's transcript.
- **G2**: UserPromptSubmit hook in the new session that injects relevant captured-intent context into the user's first N turns.
- **G3**: Phase-2 drift detection that runs async after each assistant turn and queues corrections for the next UserPromptSubmit.
- **G4**: Claim-verification pass on strong agent claims ("I did X") that cross-checks transcript tool calls and filesystem state.
- **G5**: 2–3 MCP tools the agent can call to pull captured-intent on demand.
- **G6**: Surfacing channel for high-stakes drifts/audit failures — MCP elicitation if supported, file+notification fallback otherwise.
- **G7**: Small local web UI for reviewing/editing captured intent and viewing the verification ledger.

### Non-goals (explicitly out of v1)
- Auto-trigger of handoff at token threshold (manual only)
- `--resume` based self-interrogation in Phase 1 (transcript-only)
- Human-written prompt template library (LLM-generated only)
- Multi-project / global cross-project memory
- Cross-tool support beyond Claude Code (no Cursor/Codex/Aider in v1)
- Cloud sync / multi-machine state
- Authentication, multi-user, sharing
- Plugin architecture for custom drift detectors

## 3. Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  WARDEN DAEMON (long-running local service)                 │
│  - process: Python, single binary entry point               │
│  - state: on-disk markdown + SQLite for ledger              │
│  - LLM: Qwen 3.6 (8B or 14B) via local llama.cpp endpoint   │
│  - workers: transcript watcher, drift queue, audit queue    │
└──┬───────────────┬─────────────────────────┬────────────────┘
   │               │                         │
   │ HTTP/local    │ MCP stdio               │ filesystem
   │ for web UI    │ (per-project server)    │ events
   │               │                         │
   ▼               ▼                         ▼
[Web UI]      [Claude Code session]    [.jsonl transcripts]
              - UserPromptSubmit hook
              - MCP client → warden tools
```

Four primitives, all running in/around the daemon:

1. **Daemon** — Python service, owns Qwen connection, holds in-memory state, persists to disk, exposes web UI on `localhost:<port>` and MCP server via stdio when invoked by Claude Code.
2. **UserPromptSubmit hook** — small script registered in user's Claude Code settings; on each user prompt, makes a local IPC call to the daemon and returns augmented prompt or additional context.
3. **MCP server** — exposed by the daemon; Claude Code connects via stdio with project-scoped configuration; provides pull tools.
4. **Transcript watcher** — daemon-internal worker that watches `~/.claude/projects/<project>/*.jsonl` and processes new events into the drift/audit queues.

## 4. Component Specs

### 4.1 Daemon

**Lifecycle:** `modmcp daemon start` launches it; runs as a user-level service (no root). On macOS/Linux: launchd/systemd unit (provide both); on Windows: Task Scheduler entry. PID file at `~/.modmcp/daemon.pid`. Logs at `~/.modmcp/logs/daemon.log` with rotation.

**State:**
- In-memory: active session-watch handles, drift/audit queues, Qwen client
- On disk: captured-intent markdown files (per project), SQLite for verification ledger and audit history
- Atomic writes for captured-intent files (write tmp, rename)

**Configuration:** `~/.modmcp/config.toml` for: Qwen endpoint URL, model name, web UI port, max-watch-projects, drift threshold, claim-verification grep budget.

**Concurrency:** drift and audit workers run as asyncio tasks; transcript watcher is a separate task; Qwen calls serialized through a single async queue to avoid contention with the user's other GPU workloads.

### 4.2 UserPromptSubmit hook

**Registration:** Claude Code settings.json `hooks.UserPromptSubmit` entry pointing at `modmcp hook userpromptsubmit`.

**Contract** *(per current Claude Code hook docs — verify exact schema at build time)*:
- Receives JSON on stdin: session ID, transcript path, raw user prompt, project root
- Returns JSON on stdout: either modified prompt text, or `additionalContext` to append, or empty (no-op)
- Hook must complete in under ~500ms to feel snappy; daemon call should be local Unix socket / named pipe with cached responses

**Behavior:**
- Look up session ID in daemon state
- If new session matches an active handoff → inject captured-intent preamble (first turn only) and any queued corrections (every turn while in Phase 2 window)
- After N turns or when drift signal stabilizes → exit Phase 2; hook becomes pass-through

**Failure mode:** if daemon unreachable, hook logs and passes prompt through unchanged. Must never block user.

### 4.3 MCP server

**Transport:** stdio per Claude Code MCP convention.

**Tools (v1):**
1. `get_captured_intent(section?: string)` — returns the captured-intent doc, optionally filtered to a section (active_goal, open_threads, active_rules, etc.)
2. `query_intent(question: string)` — semantic-ish lookup against captured intent; warden uses Qwen to answer "what did the previous session decide about X?"
3. `record_decision(decision: string, rationale?: string)` — agent-initiated write back into open_threads / active_rules; warden timestamps and persists

Optional 4th if signal warrants:
4. `get_active_rules()` — convenience wrapper, returns just the rule list with adherence counts

**No write-anything tool.** Agents can record decisions, but cannot modify captured-intent free-form. Keeps the schema honest.

### 4.4 Transcript watcher

**Path:** `~/.claude/projects/<sanitized-project-path>/<session-id>.jsonl`. New session = new file; tail is append-only.

**Event flow** on each new line:
- Parse JSON event (user message / assistant message / tool call / tool result)
- Update session-state in memory
- For assistant turns: enqueue drift-analysis job and (if claim-keywords matched) claim-verification job
- For tool calls: update commitment/claim ledger

**Robustness:** handle log rotation, partial writes, file truncation; recover from daemon restart by replaying from last-processed offset (stored in SQLite).

## 5. Captured-intent Schema

**On-disk format:** a single markdown file per project at `~/.modmcp/projects/<project-hash>/intent.md`, with structured frontmatter for machine-readable fields and markdown body for prose sections that humans want to edit.

```markdown
---
project_path: /Users/glenn/code/example
project_name: example
created: 2026-04-22T10:00:00Z
updated: 2026-04-22T10:00:00Z
session_mode: build  # build | meta | exploration
phase2_turns_remaining: 8
---

# Receiving Posture
[1-3 sentences setting how the next agent should receive the user regardless of opener]

# Active Goal
[What this session is trying to accomplish, concrete and bounded]

# Open Threads
- [thread 1, with priority high|med|low and status]
- [thread 2 ...]

# Active Rules
- [rule statement] — adherence: 4/5 (1 violation flagged 2026-04-22)

# Known User Drift Patterns
- validation-seeking — redirect template: "Want me to spin a meta session for this?"
- over-scoping when excited — redirect template: "...keeping it to the named scope"

# Known Agent Drift Patterns
- over-architecting in this codebase — corrective: "user prefers minimal change"
- adds tests/error-handling unprompted — corrective: "scope-strict, no scope creep"

# Commitments (pending)
- [agent statement] — captured 2026-04-22T10:30:00Z

# Recent Claims
- [claim] — status: verified | contradicted | unverifiable — checked 2026-04-22T10:35:00Z

# Notes
[free-form, human-editable]
```

**SQLite (separate from markdown):**
- `verification_ledger`: id, session_id, claim_text, status, evidence, ran_at
- `commitment_ledger`: id, session_id, commitment_text, fulfilled, fulfilled_at
- `drift_events`: id, session_id, event_type, severity, action_taken, ran_at
- `processed_offset`: session_id, jsonl_offset

Markdown is the source of truth for human-editable fields; SQLite is the append-only audit log.

## 6. Phase 1 Spec

**Trigger:** user runs `modmcp handoff` CLI or `/handoff` slash command (provide both). Argument: source session ID (defaults to most-recent for current project root).

**Pipeline:**
1. Load `.jsonl` transcript for source session
2. Slice last K tokens (or full transcript if under budget); de-noise tool-result chatter
3. Single Qwen pass with structured-output prompt → fills schema fields
4. Operator review step: open captured-intent file in `$EDITOR` (or skip with `--no-edit`); user can refine receiving_posture, drift_patterns, etc.
5. Persist; mark session ready for Phase 2 pickup

**Prompt skeleton for Qwen:**
- System: "You synthesize a structured handoff document from a Claude Code session transcript. Be specific, not generic. Capture commitments and rules verbatim where possible. Output strict JSON matching the schema."
- User: transcript + schema + 3-shot examples

**Output validation:** parse JSON → schema validate → on failure, retry once with "your previous output failed at field X" prompt → on second failure, fall back to writing partial intent + flagging incomplete.

## 7. Phase 2 Spec

**Activation:** when daemon sees new session start (transcript file appears) AND captured-intent exists for that project AND `phase2_turns_remaining > 0`.

**Per-turn loop** (after each assistant turn):

1. **Drift check** (always):
   - Compare last assistant turn against active_goal, open_threads, session_mode
   - Cheap pattern checks first (keyword overlap, mode-language detection)
   - LLM check only if pattern-check fires above threshold
   - Severity: low / med / high

2. **Claim-verification** (conditional):
   - Pattern-detect strong claims in assistant text ("I removed", "I refactored", "all", "every", "fully", "completely")
   - For each: pull recent tool-call history from transcript, run grep against repo, compare
   - Status → verified | contradicted | unverifiable
   - Append to ledger; on `contradicted`, queue surfacing

3. **Action selection:**
   - low severity + verified → noop
   - med severity → queue corrective text for next UserPromptSubmit injection
   - high severity OR contradicted-claim → surface to user (MCP elicitation or notification fallback)

4. **Decrement** `phase2_turns_remaining`; update intent file. When zero, daemon stops Phase 2 for this session (hook becomes pass-through).

**Async budget:** target full per-turn loop under 3 seconds on user's rig with 8B Qwen at Q4. Hard cap: 10 seconds; on overrun, drop the check and log. User must never feel lag from this.

## 8. Storage Layout

```
~/.modmcp/
├── config.toml                    # daemon config
├── daemon.pid
├── daemon.sock                    # Unix socket for hook IPC (Windows: named pipe)
├── logs/
│   └── daemon.log                 # rotating
├── ledger.db                      # SQLite
└── projects/
    └── <sha256(project_path)[:12]>/
        ├── project.toml           # canonical path, name, settings overrides
        ├── intent.md              # captured-intent (source of truth)
        └── archive/
            └── intent-<ts>.md     # snapshots before each Phase 1 overwrite
```

Per-project storage keyed by hash of canonical project path, so the same code in two checkout locations stays separate. Snapshots before Phase 1 overwrite enable rollback.

**Git-tracking:** by default, captured-intent lives outside the repo. Provide `modmcp link <project>` that symlinks `intent.md` to a `.modmcp/intent.md` file inside the repo for projects where the user wants it tracked.

## 9. Web UI Scope

**Stack:** FastAPI + a single HTML page with HTMX (or Alpine) for interactivity. No SPA framework, no build step. Single-file or near-it. Served on `localhost:<port>` with localhost-only bind.

**Pages (v1):**
- `/` — project list with last-handoff timestamp
- `/p/<hash>` — captured-intent viewer/editor for one project; markdown-edit in place; save writes file
- `/p/<hash>/ledger` — verification ledger table: recent claims, status, evidence
- `/p/<hash>/drift` — recent drift events with severity and action-taken

**Out of v1:** auth, sharing, search, history scrubbing UI, charts.

## 10. Tech Stack (recommended)

- **Language:** Python 3.12 (matches Claude Code ecosystem familiarity, mature async story, fits the daemon model)
- **MCP server:** official `mcp` Python SDK
- **Async:** asyncio + uvloop where supported
- **Web:** FastAPI + Uvicorn + HTMX
- **Persistence:** SQLite via `aiosqlite`; markdown files plain
- **LLM client:** OpenAI-compatible client pointed at user's local llama.cpp endpoint (Qwen 3.6, 8B or 14B at Q4/Q8)
- **Transcript parsing:** stdlib `json` + lightweight schema models (`pydantic`)
- **Packaging:** `pipx`-installable; provide `modmcp` CLI entry point

## 11. Build Order (sequenced milestones)

Each milestone is independently testable end-to-end.

**M1 — Daemon skeleton + transcript watcher (no LLM).** Daemon starts/stops, watches `.jsonl`, prints parsed events. Validates the transcript-watching primitive in isolation.

**M2 — Phase 1 manual.** `modmcp handoff` CLI reads transcript, opens an empty intent.md template in `$EDITOR` for user to fill manually. Validates the schema by hand-authoring it. *(This is the v0.1 spike, folded into v1 as its first useful checkpoint — gives early dogfood signal before Qwen integration.)*

**M3 — UserPromptSubmit hook + intent injection.** Hook reads intent.md and injects on first N turns. Validates the push-path plumbing without any drift logic.

**M4 — MCP server + pull tools.** `get_captured_intent` and `query_intent` (latter still Qwen-free, just keyword search). Validates pull-path.

**M5 — Qwen integration + Phase 1 auto-synth.** Wire local llama.cpp; replace M2 manual editing with auto-synthesis (with `$EDITOR` review still available).

**M6 — Phase 2 drift loop.** Per-turn drift check, queued corrections via UserPromptSubmit. Heuristic-only first, LLM-judged where threshold fires.

**M7 — Claim-verification pass.** Pattern-detect strong claims, grep + tool-call cross-check, append to ledger.

**M8 — Surfacing.** MCP elicitation attempt; on detect-fail, native notification fallback.

**M9 — Web UI.** Project list, intent viewer/editor, ledger view.

**M10 — Polish: packaging, README, demo GIF, install instructions for macOS / Linux / Windows.**

Each milestone should ship with a 1-paragraph README section and a "how to verify it works" command. M2 alone provides daily-use value, so dogfooding starts at M2.

## 12. Open Questions to Resolve During Build

- **MCP elicitation in current Claude Code:** verify before M8; if unsupported, M8 ships notification-only and elicitation moves to v2.
- **Hook contract specifics:** verify field names against current Claude Code docs at start of M3.
- **Qwen prompt iteration:** Phase 1 synthesis quality will need 5–10 rounds of prompt + few-shot tuning. Build a small fixture set of 5–10 representative transcripts before M5.
- **Drift threshold tuning:** start hardcoded; promote to config knobs once the false-positive shape is felt.
- **Project-scope detection:** how does the daemon know "this new session is for project X"? Likely from cwd at session start, exposed via hook payload. Confirm at M3.

## 13. Out of Scope (v2 candidates)

- Auto-trigger of Phase 1 at token threshold
- `--resume` self-interrogation as a Phase 1 augmentation
- Human-written prompt template library
- Cross-tool support (Cursor / Codex / Aider)
- Multi-project memory + cross-session retrieval
- Team/shared captured-intent
- Webhooks for CI integration ("did this PR violate any active rules?")
- Pluggable drift detectors (regex, LSP-based, custom)
