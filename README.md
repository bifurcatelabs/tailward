# modmcp

A local-first, passive-first session-audit layer for Claude Code. A long-running local daemon tails Claude Code transcripts, scores them against 8 trust-breaking failure modes using deterministic rule checks plus a **local LLM rubric**, and surfaces the signal in a localhost web UI. No transcripts, code, or scoring judgments leave your machine; the prompt is not modified by default.

See [`V1 Proposal.md`](V1%20Proposal.md) for the original design, [`failure modes.md`](failure%20modes.md) for the taxonomy that drives the v1.1 audit layer, and [`AUDIT_MAP.md`](AUDIT_MAP.md) for exactly which of those failure modes Warden currently detects and how.

## What it does

**v1 — session handoff and continuity (opt-in via `active` mode — _WIP, see note below_)**

- **Phase 1 (handoff):** `modmcp handoff` reads a Claude Code session's transcript and synthesizes a structured `intent.md` (active goal, open threads, active rules, known drift patterns, pending commitments, recent claims) for review in your editor.
- **Phase 2 (continuity):** on the next session's first turn, a `UserPromptSubmit` hook injects the intent as a preamble; for the first N turns, drift against the goal queues a corrective injection on the next turn and strong claims ("I removed all X") are grepped against the repo and logged verified / contradicted / unverifiable.
- **MCP pull:** `get_captured_intent`, `get_active_rules`, `query_intent`, `record_decision`.

**v1.1 — failure-mode audit layer (passive by default)**

- **LiveBus + SSE web UI.** Every assistant turn, tool call, violation, scope snapshot, and rubric score streams in real time to a localhost browser view. Polling fallback when SSE is unavailable.
- **Constraints worker.** Parses "Active Rules" from `intent.md` into path-glob / immutable-file / forbidden-bash policies and flags violations against every `tool_use` event. Ack / dismiss from the UI. Ships a baseline policy out of the box covering destructive commands (force-push, `rm -rf /`), mute-the-alarm moves (`--no-verify`, test/lint tools silenced with `|| true`, `pytest --deselect`), target-gaming moves (`pytest --override-ini`, `--cov-fail-under=0`, `coverage --omit`), and immutable measurement artifacts (`.github/workflows/**`, `.coveragerc`, `codecov.yml`, `tox.ini`, `.pre-commit-config.yaml`, `jest.config.*`). See [`AUDIT_MAP.md`](AUDIT_MAP.md) for the full mapping.
- **Scope worker.** Per-session counters (files touched, diff bytes, tool-kind breakdown) compared to a rolling baseline from the last N completed sessions. Emits `scope_creep` events when you blow past it.
- **Rubric worker.** Sampled Qwen JSON scoring across four dimensions (invariants, uncertainty, maintainability, provenance) — triggered on cadence, scope creep, and first-person completion claims. "Disagree" button writes feedback back to the ledger.
- **Session-close consolidator.** After configurable idle time, one Qwen call aggregates all collected signal into an 8-mode report card with a per-session permalink.
- **Cross-session trends.** Sparklines per failure mode per project.

## Operating modes

Warden has one top-level knob: `warden_mode` in `~/.modmcp/config.toml`.

| mode | hook preamble? | drift correctives? | auditing? | UI? | status |
|---|---|---|---|---|---|
| **`passive`** (default) | no | no | **yes** | yes | **supported** |
| `active` | yes | yes | yes | yes | **WIP** — experimental, see below |

> **`active` is work-in-progress.** The injection + drift-corrective path is the original v1 design; it functions but is deliberately de-emphasized pending the audit layer stabilizing. Expect rough edges: the corrective-queue UI surface is minimal, regression coverage is thinner than the passive-mode path, and the preamble contents are still being tuned against real handoffs. Treat `active` as "I know what I'm doing and I want to experiment with the continuity loop", not as a daily driver. The passive audit layer is the production surface.

### Why passive is the default

The whole point of this tool is to tell you whether your coding agent is behaving. That measurement is only trustworthy if the act of measuring doesn't shape the thing being measured. The original v1 design injected preambles and corrective turns into the prompt stream, which had three problems we only saw clearly once we started dogfooding:

1. **Observer effect.** Any content Warden injects becomes part of the agent's context and changes the next turn. A "drift score" measured on a session Warden is actively steering is really measuring Warden's own intervention quality, not the agent's baseline behavior. You can't A/B your own tooling if the A and B arms can't be isolated.
2. **Model trust.** When the audit layer is invisible to the session, the agent has no incentive to perform for the audit. You get honest trajectories. The second a model can see it's being scored, the scoring task competes with the actual task.
3. **Blast radius.** Injected preambles and corrective turns are a live wire into every prompt. A bad rubric, a regex false positive, or a daemon bug can derail a real session. In passive mode the worst Warden can do is log a wrong row in SQLite or render an ugly widget in a browser tab.

Passive mode moves the human (you) into the loop at a decision boundary — the web UI — instead of hotwiring corrections into the model's context. You still get every signal; you just decide what to do with it.

Flip to `active` when you specifically want to experiment with the agent reacting to Warden's corrections in real time — typically at the start of a new session after a handoff, where the preamble is carrying context the agent genuinely needs. Flip back to `passive` after the first few turns. **Active mode is WIP** (see table above); the passive audit layer is the supported surface.

### Why local-first

An audit is only as trustworthy as its supply chain. If Warden shipped your prompts, tool calls, or code diffs to a SaaS scoring API, it would be asking you to trust a third party with the exact artifacts it's supposed to be auditing on your behalf. That undercuts the whole point of the tool and also makes it unusable on any codebase you can't legally egress.

So Warden is local-first, top to bottom:

- **Transcripts never leave the machine.** The watcher reads JSONL from `~/.claude/projects/`, the ledger writes to `~/.modmcp/ledger.db`, the HTTP server binds to `127.0.0.1`. No cloud writes, no telemetry, no opt-out required because there's nothing to opt out of.
- **The scoring LLM is yours too.** Warden talks to an OpenAI-compatible endpoint at `http://127.0.0.1:<port>/v1` — llama.cpp, Ollama, LM Studio, vLLM, whatever you prefer. There is deliberately no fallback to a hosted API: if the endpoint is unreachable, Warden skips the LLM-judged checks and keeps the deterministic ones running.
- **Deterministic first, LLM for depth.** The constraints worker (path / immutable-file / forbidden-bash), scope worker, and claim-grep path all run with zero LLM present — those are the load-bearing "is this session in bounds?" signals and they're regex-fast on CPU. The local model adds the softer trust dimensions (invariants awareness, uncertainty honesty, maintainability, provenance) and the end-of-session 8-mode consolidation. You can run Warden fully airgapped and still see live violations, scope creep, and claim verdicts; the rubric bar and report card just stay blank until a model comes online.

The cost of this posture is one extra piece of infra (a local model server, eventually). The payoff is that the audit lives inside the same trust boundary as the thing being audited — and nothing you care about ends up in someone else's log pipeline.

## Getting started

### Prerequisites

- Python 3.12+ (Windows, macOS, Linux).
- A running Claude Code install that writes transcripts to `~/.claude/projects/` (the default).
- Optional but recommended: a local OpenAI-compatible LLM endpoint (llama.cpp, Ollama, LM Studio, vLLM) listening at `http://127.0.0.1:8080/v1`. See [Qwen / local LLM endpoint](#qwen--local-llm-endpoint) below for what degrades gracefully without one.

### Install

```bash
pip install -e .
# or
pipx install -e .
```

First run creates `~/.modmcp/` for state (config, logs, ledger, per-project intent). Override with `MODMCP_HOME=/path/to/state`.

### Minimal passive setup (observe-only, no Claude Code changes)

This is the recommended starting point. You can run it against a live Claude Code session with zero config changes on the Claude Code side.

```bash
# 1. Start the daemon.
modmcp daemon start
# -> prints pid + http://127.0.0.1:7878

# 2. Seed a project so it shows up in the UI.
cd ~/code/your-project
modmcp handoff --no-edit         # creates ~/.modmcp/projects/<hash>/intent.md

# 3. Start using Claude Code in that same project as you normally would.
#    The transcript watcher will pick up the session automatically.

# 4. Open the live view.
start http://127.0.0.1:7878/     # Windows
# open http://127.0.0.1:7878/    # macOS
# xdg-open http://127.0.0.1:7878 # Linux
```

Click into your project → **Live** → you'll see turns and tool calls stream in real time. Violations, scope snapshots, rubric scores, and the end-of-session report card fill in as they're produced.

Stop with `modmcp daemon stop`.

### Adding full Claude Code integration (only needed for `active` mode + MCP pull)

> Heads up: `active` mode is **WIP**. The audit layer (passive) does not need anything in this section. Skip unless you specifically want to experiment with the preamble + drift-corrective loop.

You only need this if you want the `UserPromptSubmit` preamble and the MCP tools (`get_captured_intent`, etc.). Neither is required for the audit layer.

**0. Find the absolute path to your `modmcp` executable.** Claude Code spawns hook and MCP commands with the `PATH` it inherited at launch. For a venv install (`pip install -e .` inside `.venv`) that `PATH` almost never includes `.venv/Scripts` / `.venv/bin`, so a bare `command: "modmcp"` will silently fail to resolve. The preferred shape is the absolute path to the launcher `pip` / `pipx` created:

```bash
# Windows (inside the activated venv, or from anywhere if on PATH)
where modmcp
# -> C:\path\to\.venv\Scripts\modmcp.exe

# macOS / Linux
which modmcp
# -> /path/to/.venv/bin/modmcp          (venv install)
# -> /home/you/.local/bin/modmcp        (pipx install)
```

Use that path verbatim in the snippets below. Bare `modmcp` works too **if** its install dir is on the user/system `PATH` that Claude Code inherits at launch (typical for `pipx install` after `pipx ensurepath`, plus a Claude Code restart). The absolute form survives PATH changes, venv activations, and ambiguous multi-install setups, so it's the recommended shape.

**1. Register the hook.** Edit `~/.claude/settings.json`:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "C:\\path\\to\\.venv\\Scripts\\modmcp.exe hook userpromptsubmit"
          }
        ]
      }
    ]
  }
}
```

On macOS / Linux the `command` becomes `"/path/to/.venv/bin/modmcp hook userpromptsubmit"`. Note the doubled backslashes in the Windows form — `settings.json` is JSON, so `\` must be escaped.

The hook has a hard ≤400 ms budget and silently passes your prompt through on any failure, so it can never block you. In `warden_mode = "passive"` the daemon returns an empty response — the hook fires but injects nothing.

**2. Register the MCP server per project.** In the project root, add `.claude/mcp.json`:

```json
{
  "mcpServers": {
    "modmcp": {
      "command": "C:\\path\\to\\.venv\\Scripts\\modmcp.exe",
      "args": ["mcp"],
      "env": { "MODMCP_PROJECT": "${workspaceFolder}" }
    }
  }
}
```

Same substitution on POSIX: `"command": "/path/to/.venv/bin/modmcp"`. If `MODMCP_PROJECT` isn't set, the MCP server falls back to its current working directory.

**3. Flip the mode.** Edit `~/.modmcp/config.toml`:

```toml
warden_mode = "active"
```

...and `modmcp daemon stop && modmcp daemon start` to pick up the change.

### Daily use

```bash
modmcp daemon status                # is it running?
modmcp daemon logs -n 200           # tail the daemon log
modmcp daemon run                   # foreground mode for debugging
modmcp handoff                      # re-capture intent (opens $EDITOR)
modmcp handoff --auto               # same, but Qwen-synthesized (needs LLM up)
modmcp handoff --no-edit            # skip $EDITOR
modmcp link                         # symlink ~/.modmcp/.../intent.md into <repo>/.modmcp/
modmcp version
```

## Web UI

All routes live under `http://127.0.0.1:7878/`. Every project gets a 12-char hash (first 12 chars of `sha256(canonical_project_path)`).

| route | what |
|---|---|
| `/` | Project index |
| `/p/<hash>` | Intent editor + mode pill + nav |
| `/p/<hash>/live` | Redirect to the latest session's live view |
| `/p/<hash>/live/<session_id>` | **Live session view** — turns, tool calls, violations, scope, rubric, report card |
| `/p/<hash>/live/<session_id>/stream` | SSE stream (consumed by `live.js`) |
| `/p/<hash>/live/<session_id>/events` | Polling fallback for `/stream` |
| `/p/<hash>/live/<session_id>/state` | Initial UI state JSON |
| `/p/<hash>/live/<session_id>/replay` | Recent events for reconnect |
| `/p/<hash>/violations` | Constraint-violation history |
| `/p/<hash>/violations/<id>/ack` \| `/dismiss` | POST: update status |
| `/p/<hash>/rubric/<score_id>/feedback` | POST: user disagreement |
| `/p/<hash>/sessions/<session_id>` | End-of-session report card permalink |
| `/p/<hash>/ledger` | Claim-verification ledger |
| `/p/<hash>/drift` | Drift events |
| `/p/<hash>/trends` | Cross-session sparklines per failure mode |

## CLI

| command | what |
|---|---|
| `modmcp daemon start\|stop\|status\|logs\|run` | lifecycle (`run` = foreground) |
| `modmcp handoff [--session ID] [--no-edit] [--auto\|--manual] [--project PATH]` | run Phase 1 synthesis |
| `modmcp link [--project PATH]` | symlink `intent.md` into `<repo>/.modmcp/intent.md` |
| `modmcp hook userpromptsubmit` | bridge for the Claude Code hook (stdin JSON → daemon → stdout JSON) |
| `modmcp mcp` | run the stdio MCP server |
| `modmcp version` | print version |

## Configuration

First run writes `~/.modmcp/config.toml` with defaults. Restart the daemon after editing. The defaults live in [`src/modmcp/config.py`](src/modmcp/config.py) and that file is the source of truth; the highlights:

```toml
# Top-level posture.
warden_mode = "passive"              # "passive" | "active"

# Daemon HTTP.
http_host = "127.0.0.1"
http_port = 7878

# LLM endpoint (OpenAI-compatible).
qwen_endpoint = "http://127.0.0.1:8080/v1"
qwen_model = "qwen2.5-8b-instruct"
qwen_api_key = "not-needed"
qwen_context_tokens = 32768

# Per-call-kind routing (empty string = fall back to qwen_model).
qwen_model_synth = ""
qwen_model_drift = ""
qwen_model_query = ""
qwen_model_rubric = ""
qwen_model_consolidator = ""

# Per-call-kind output budgets (thinking models need generous headroom).
qwen_max_tokens_synth = 6000
qwen_max_tokens_drift = 1500
qwen_max_tokens_query = 1500
qwen_max_tokens_rubric = 2500
qwen_max_tokens_consolidator = 8000

# Per-call-kind Qwen3 thinking mode.
qwen_enable_thinking_synth = true
qwen_enable_thinking_drift = false
qwen_enable_thinking_query = false
qwen_enable_thinking_rubric = true
qwen_enable_thinking_consolidator = true

# Sampling (Qwen3 thinking-mode defaults).
qwen_temperature = 0.6
qwen_top_p = 0.95
qwen_top_k = 20

# Phase 2 (active mode).
phase2_turns_default = 8
drift_threshold = 0.35
per_turn_budget_seconds = 3.0
per_turn_hard_cap_seconds = 30.0
claim_grep_budget = 200
hook_timeout_ms = 400

# Rubric worker (v1.1): Qwen-judged score every N turns + on triggers.
rubric_turn_interval = 5
rubric_min_text_chars = 80

# Scope worker: rolling baseline from the last N sessions.
scope_baseline_window = 5
scope_creep_factor = 2.0
scope_creep_floor = 12

# Session close: idle threshold for end-of-session consolidator.
session_idle_seconds = 600.0
session_close_poll_seconds = 60.0

# Live UI transport.
live_sse_max_subscribers_per_session = 4
live_sse_replay_events = 200
live_sse_keepalive_seconds = 20.0

# Transcript watcher.
max_watch_projects = 32
```

Environment overrides:
- `MODMCP_HOME` — relocate the state directory (default `~/.modmcp`).
- `CLAUDE_PROJECTS_ROOT` — relocate the Claude Code transcript root (default `~/.claude/projects`).
- `MODMCP_PROJECT` — project path the MCP server binds to.

## Qwen / local LLM endpoint

Warden expects an OpenAI-compatible HTTP endpoint on `localhost` (see [Why local-first](#why-local-first) for the reasoning). Any of these work:

- [llama.cpp server](https://github.com/ggml-org/llama.cpp) with an OpenAI-compat flag
- [Ollama](https://ollama.com/) — set `qwen_endpoint = "http://127.0.0.1:11434/v1"`, `qwen_model = "qwen2.5:7b"`
- LM Studio's local server
- `vllm` with `--served-model-name`

Pointing `qwen_endpoint` at a remote host isn't explicitly blocked, but it defeats the audit-integrity argument; Warden will happily send your transcripts wherever you tell it to.

All LLM calls serialize through a single queue so Warden doesn't contend with other GPU workloads. Five distinct call kinds are routed with their own token budgets and thinking-mode settings:

| kind | used by | typical cost |
|---|---|---|
| `synth` | `modmcp handoff --auto` | heavy (one-shot, up to 6k out + thinking) |
| `drift` | Phase 2 drift worker | light (per-turn, in active mode) |
| `query` | `query_intent` MCP tool | light (on-demand) |
| `rubric` | rubric worker (v1.1) | medium (sampled, every N turns + triggers) |
| `consolidator` | session-close worker (v1.1) | heavy (once per session close, up to 8k out + thinking) |

### What requires an LLM vs what doesn't

If the LLM endpoint is unreachable, Warden degrades cleanly:

| works without LLM | needs LLM |
|---|---|
| Transcript watcher + live feed | `modmcp handoff --auto` (falls back to manual template) |
| LiveBus + SSE | Drift classifier (active mode) |
| Constraints worker + violations UI | Audit claim verification (uses LLM for claim extraction) |
| Scope worker + creep detection | Rubric worker (silently skips samples) |
| Ledger, trends, report rail (shell) | Session-close consolidator (skips, report stays "in progress") |
| `modmcp handoff` (manual template) | `query_intent` MCP tool (keyword fallback) |

In other words: the entire passive observation layer works fine with no model running at all. You just won't get rubric scores or the 8-mode report card until you bring one up.

## Storage layout

```
~/.modmcp/
├── config.toml
├── daemon.pid
├── logs/daemon.log
├── ledger.db                        # SQLite; tables below
└── projects/<hash>/
    ├── project.toml
    ├── intent.md                    # source of truth, human-editable
    ├── surfacings/                  # notification artifacts
    └── archive/
        └── intent-<timestamp>.md    # snapshot before each overwrite
```

`ledger.db` tables:
- v1: `claims`, `drift_events`, `corrections`, `processed_offset`, `sessions`, `surfacings`
- v1.1: `constraint_violations`, `scope_snapshots`, `rubric_scores`, `rubric_feedback`, `session_reports`, `session_close`, `live_events`

`<hash>` is the first 12 chars of `sha256(<canonical project path>)` — lowercase drive letter + forward slashes on Windows, fully resolved on POSIX.

## Architecture at a glance

```
Claude Code session
      │ writes transcript JSONL
      ▼
~/.claude/projects/<proj>/<session>.jsonl
      │ tailed by
      ▼
┌─────────────────── modmcp daemon ───────────────────┐
│                                                     │
│  TranscriptWatcher ──▶ on_event dispatch            │
│                        │                            │
│     ┌───────────┬──────┼──────┬────────────┐        │
│     ▼           ▼      ▼      ▼            ▼        │
│  constraints  scope  rubric  audit        drift     │
│  worker       worker worker  (claim-grep) worker    │
│     │           │      │      │            │        │
│     ▼           ▼      ▼      ▼            ▼        │
│              LiveBus ◀─── session_close             │
│                 │                  │                │
│                 ▼                  ▼                │
│          SQLite ledger     SSE ─▶ browser           │
│                                                     │
└─────────────────────────────────────────────────────┘
```

All workers run in both `passive` and `active` modes; the only mode-gated behavior is drift's corrective-enqueue step, which is suppressed in passive (the verdict is still recorded for the UI). Workers are wired lazily in [`src/modmcp/daemon/app.py`](src/modmcp/daemon/app.py) — each one is wrapped in a `try/except log.warning`, so a missing dependency or a config bug in one worker never brings down the others.

## Development

```bash
pip install -e ".[dev]"
pytest -q          # 95 tests, ~22s
```

Troubleshooting:

- `modmcp daemon logs -n 200` — everything interesting ends up here: hook failures, worker startup errors, LLM call failures with the call kind tagged.
- `modmcp daemon run` — runs the daemon in the foreground with uvicorn logs on stdout; useful when you want live-reload visibility into what the audit layer is doing.
- `MODMCP_HOME=/tmp/modmcp-dev modmcp daemon run` — isolated state dir for experimentation.

## Platform notes

- **Windows:** daemon uses `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`; `modmcp link` requires Developer Mode for symlinks and falls back to file copy otherwise.
- **macOS / Linux:** daemon uses `setsid` detachment; `modmcp link` uses `os.symlink`.

Service units (systemd user unit, launchd plist, Task Scheduler XML) are not required — the lightweight `modmcp daemon start` is sufficient. A future `modmcp daemon install-service` subcommand may ship them.
