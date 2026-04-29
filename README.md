# warden

A local-first **trust layer for AI coding workflows**. A long-running local daemon tails Claude Code transcripts, scores them against trust-breaking failure modes using deterministic rule checks plus a **local LLM rubric**, derives in-band inference-path metrics, probes the local LLM endpoint, and surfaces everything in a multi-view localhost web UI (Session / Reflection / Platform). No transcripts, code, or scoring judgments leave your machine; the prompt is not modified by default.

See [`failure modes.md`](failure%20modes.md) for the taxonomy that drives the audit layer and [`AUDIT_MAP.md`](AUDIT_MAP.md) for exactly which failure modes Warden currently detects and how. [`V1 Proposal.md`](V1%20Proposal.md) is the original design and is preserved as a historical artifact — the project pivoted away from active prompt injection in v1.1 and reframed as a trust layer in v2.0.0.

## Status

Actively iterated. Current release: **v2.1.0**.

The **passive audit surface** (Session / Reflection / Platform views) is the supported daily-driver. **Active mode** (preamble injection + drift correctives, the original v1 design) is preserved as opt-in scaffolding but is not maintained beyond what the passive layer requires — see [Operating modes](#operating-modes).

Solo-dev work; expect rough edges. Issues and discussion welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).

## What it does

**Audit (passive by default)**

- **LiveBus + SSE web UI.** Every assistant turn, tool call, violation, scope snapshot, and rubric score streams in real time to a localhost browser view. Polling fallback when SSE is unavailable.
- **Constraints worker.** Parses "Active Rules" from `intent.md` into path-glob / immutable-file / forbidden-bash policies and flags violations against every `tool_use` event. Ack / dismiss from the UI. Ships a baseline policy out of the box covering destructive commands (force-push, `rm -rf /`), mute-the-alarm moves (`--no-verify`, test/lint tools silenced with `|| true`, `pytest --deselect`), target-gaming moves (`pytest --override-ini`, `--cov-fail-under=0`, `coverage --omit`), and immutable measurement artifacts (`.github/workflows/**`, `.coveragerc`, `codecov.yml`, `tox.ini`, `.pre-commit-config.yaml`, `jest.config.*`). See [`AUDIT_MAP.md`](AUDIT_MAP.md) for the full mapping.
- **Scope worker.** Per-session counters (files touched, diff bytes, tool-kind breakdown) compared to a rolling baseline from the last N completed sessions. Emits `scope_creep` events when you blow past it. Mode-aware — exploration / yolo modes don't fire creep events that don't apply to them.
- **Rubric worker.** Sampled Qwen JSON scoring across four dimensions (invariants awareness, uncertainty honesty, maintainability, provenance) — triggered on cadence, scope creep, and first-person completion claims. "Disagree" button writes feedback back to the ledger.
- **Session-close consolidator.** After configurable idle time, one Qwen call aggregates all collected signal into an 8-mode report card.
- **Synthesized-turn detection.** Claude Code's `/compact` persists its summary as a `type: "user"` JSONL row with `isCompactSummary: true`. The live feed surfaces these as a distinct `compact_summary` event so they don't blend in with typed user turns.

**Reflection — am I behaving?**

- **Derived signals (no LLM):** idle-gap distribution between typed user prompts, prompt-length distribution, destructive-action approval cadence, claim verification verdicts.
- **Self-rubric (LLM-scored):** four user-side dimensions — intent clarity, context coverage, verification engagement, mode coherence — sampled on a configurable cadence.
- **Past-sessions table.** Cross-project sessions list, click-to-expand for the 8-mode report card + per-dimension rubric trajectory.

**Platform — is the platform serving me consistently?**

- **In-band turn metrics.** TTFT, output TPS, cache hit ratio derived from JSONL timestamps + the `usage` block. No synthetic traffic — every metric is from a real prompt the user actually sent.
- **Local LLM probe worker.** Periodic probes of the OpenAI-compatible endpoint Warden talks to. Deliberately *not* probing `api.anthropic.com` — that mostly measures the user's ISP and CDN edge, not Anthropic's service.
- **LLM call budget panel.** Per-call-kind aggregates of Warden's own Qwen calls (max_tokens vs avg completion, finish_reason distribution) so you can spot truncation before it costs you.
- **Transparency panel.** Per-call-kind config (model, sampler params, max_tokens) plus the verbatim system + user prompt templates Warden sends. Read directly from the worker constants — drift between display and runtime is impossible.

**Handoff (opt-in active mode — _unmaintained_)**

- **Phase 1 (handoff):** `warden handoff` reads a Claude Code session's transcript and synthesizes a structured `intent.md` (active goal, open threads, active rules, known drift patterns, pending commitments, recent claims) for review in your editor.
- **Phase 2 (continuity):** on the next session's first turn, a `UserPromptSubmit` hook injects the intent as a preamble; for the first N turns, drift against the goal queues a corrective injection on the next turn and strong claims are grepped against the repo and logged verified / contradicted / unverifiable.

## Operating modes

Warden has one top-level knob: `warden_mode` in `~/.modmcp/config.toml`.

| mode | hook preamble? | drift correctives? | auditing? | UI? | status |
|---|---|---|---|---|---|
| **`passive`** (default) | no | no | **yes** | yes | **supported** |
| `active` | yes | yes | yes | yes | **opt-in, unmaintained** — see below |

> **`active` is an opt-in, user-owned surface.** The injection + drift-corrective path is the original v1 design. It functions, but after the passive-first pivot we stopped shaping it into a paved path: the corrective-queue UI is minimal, regression coverage is thin, the preamble contents are not tuned against anyone's specific handoffs, and we don't ship fixes here unless they're blocking the passive layer. What's worth pushing into the preamble — and whether the drift loop fits how you work at all — is a call we leave to you. Treat `active` as scaffolding you own, not a daily driver. The passive audit layer is the supported surface.

### Why passive is the default

The whole point of this tool is to tell you whether your coding agent is behaving. That measurement is only trustworthy if the act of measuring doesn't shape the thing being measured. The original v1 design injected preambles and corrective turns into the prompt stream, which had three problems we only saw clearly once we started dogfooding:

1. **Observer effect.** Any content Warden injects becomes part of the agent's context and changes the next turn. A "drift score" measured on a session Warden is actively steering is really measuring Warden's own intervention quality, not the agent's baseline behavior. You can't A/B your own tooling if the A and B arms can't be isolated.
2. **Model trust.** When the audit layer is invisible to the session, the agent has no incentive to perform for the audit. You get honest trajectories. The second a model can see it's being scored, the scoring task competes with the actual task.
3. **Blast radius.** Injected preambles and corrective turns are a live wire into every prompt. A bad rubric, a regex false positive, or a daemon bug can derail a real session. In passive mode the worst Warden can do is log a wrong row in SQLite or render an ugly widget in a browser tab.

Passive mode moves the human (you) into the loop at a decision boundary — the web UI — instead of hotwiring corrections into the model's context. You still get every signal; you just decide what to do with it.

Flip to `active` when you specifically want the agent reacting to Warden's corrections in real time — typically at the start of a new session after a handoff, where the preamble is carrying context the agent genuinely needs — and flip back to `passive` after the first few turns. Active is unmaintained scaffolding (see table above): it works, but the preamble contents, drift-corrective shape, and rough ergonomics are yours to own. The passive audit layer is the supported surface.

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
pipx install tailward
# or, install from the latest commit on GitHub:
pipx install git+https://github.com/bifurcatelabs/tailward.git
# or, for development from a local clone — see CONTRIBUTING.md
```

First run creates `~/.modmcp/` for state (config, logs, ledger, per-project intent). Override with `MODMCP_HOME=/path/to/state`. The state-dir name is internal plumbing held over from the project's earlier name; user-facing surfaces use `tailward` (PyPI) and `warden` (CLI).

### Minimal passive setup (observe-only, no Claude Code changes)

This is the recommended starting point. You can run it against a live Claude Code session with zero config changes on the Claude Code side.

```bash
# 1. Start the daemon.
warden daemon start
# -> prints pid + http://127.0.0.1:7878

# 2. Seed a project so it shows up in the UI.
cd ~/code/your-project
warden handoff --no-edit         # creates ~/.modmcp/projects/<hash>/intent.md

# 3. Start using Claude Code in that same project as you normally would.
#    The transcript watcher will pick up the session automatically.

# 4. Open the live view.
start http://127.0.0.1:7878/     # Windows
# open http://127.0.0.1:7878/    # macOS
# xdg-open http://127.0.0.1:7878 # Linux
```

Click into your project → **Live** → you'll see turns and tool calls stream in real time. Violations, scope snapshots, rubric scores, and the end-of-session report card fill in as they're produced.

Stop with `warden daemon stop`.

### Adding the active-mode hook (optional, unmaintained)

> Heads up: `active` mode is **opt-in and unmaintained** (see the status note above). The audit layer (passive) does not need anything in this section. Skip unless you specifically want to wire up the preamble + drift-corrective loop yourself.

The MCP-server affordance was retired in v2.0.0; only the `UserPromptSubmit` hook remains for users running active mode.

**0. Find the absolute path to your `warden` executable.** Claude Code spawns hook commands with the `PATH` it inherited at launch. For a venv install (`pip install -e .` inside `.venv`) that `PATH` almost never includes `.venv/Scripts` / `.venv/bin`, so a bare `command: "warden"` will silently fail to resolve. The preferred shape is the absolute path to the launcher `pip` / `pipx` created:

```bash
# Windows (inside the activated venv, or from anywhere if on PATH)
where warden
# -> C:\path\to\.venv\Scripts\warden.exe

# macOS / Linux
which warden
# -> /path/to/.venv/bin/warden          (venv install)
# -> /home/you/.local/bin/warden        (pipx install)
```

Use that path verbatim in the snippets below. Bare `warden` works too **if** its install dir is on the user/system `PATH` that Claude Code inherits at launch (typical for `pipx install` after `pipx ensurepath`, plus a Claude Code restart). The absolute form survives PATH changes, venv activations, and ambiguous multi-install setups, so it's the recommended shape. The legacy `modmcp` binary is kept as a backward-compat alias and resolves to the same entry point.

**1. Register the hook.** Edit `~/.claude/settings.json`:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "C:\\path\\to\\.venv\\Scripts\\warden.exe hook userpromptsubmit"
          }
        ]
      }
    ]
  }
}
```

On macOS / Linux the `command` becomes `"/path/to/.venv/bin/warden hook userpromptsubmit"`. Note the doubled backslashes in the Windows form — `settings.json` is JSON, so `\` must be escaped.

The hook has a hard ≤400 ms budget and silently passes your prompt through on any failure, so it can never block you. In `warden_mode = "passive"` the daemon returns an empty response — the hook fires but injects nothing.

**2. Flip the mode.** Edit `~/.modmcp/config.toml`:

```toml
warden_mode = "active"
```

...and `warden daemon stop && warden daemon start` to pick up the change.

### Daily use

```bash
warden daemon status                # is it running?
warden daemon logs -n 200           # tail the daemon log
warden daemon run                   # foreground mode for debugging
warden handoff                      # re-capture intent (opens $EDITOR)
warden handoff --auto               # same, but Qwen-synthesized (needs LLM up)
warden handoff --no-edit            # skip $EDITOR
warden link                         # symlink ~/.modmcp/.../intent.md into <repo>/.modmcp/
warden version
```

## Web UI

All routes live under `http://127.0.0.1:7878/`. Every project gets a 12-char hash (first 12 chars of `sha256(canonical_project_path)`). The live audit surface is the Svelte SPA at `/p/<hash>/live/<session_id>`; cross-session navigation, the self-rubric, and platform telemetry are tab-views inside that SPA.

| route | what |
|---|---|
| `/` | Project index (Jinja landing page) |
| `/p/<hash>` | Intent editor (Jinja, paired with `warden handoff`) |
| `/p/<hash>/live/<session_id>` | **Live session view** (Svelte SPA — Session / Reflection / Platform tabs via `#hash` routing) |
| `/p/<hash>/live/<session_id>/stream` | SSE stream the SPA consumes |
| `/p/<hash>/live/<session_id>/events` | Polling fallback for `/stream` |
| `/p/<hash>/live/<session_id>/state` | Initial UI state JSON |
| `/p/<hash>/live/<session_id>/replay` | Recent events for reconnect / load-older |
| `/p/<hash>/violations/<id>/ack` \| `/dismiss` | POST: update status |
| `/p/<hash>/rubric/<score_id>/feedback` | POST: user disagreement |
| `/p/<hash>/turn-metrics` | Per-turn inference metrics (Platform view) |
| `/v2/sessions/recent` | Recent sessions across watched projects (HeaderBar picker) |
| `/v2/reflection/<hash>` | Derived signals for the Reflection view |
| `/v2/reflection/<hash>/self-rubric` | LLM-scored user-side rubric summary + recent samples |
| `/v2/reflection/sessions` | Cross-project past-sessions list |
| `/v2/reflection/sessions/<sid>` | Per-session deep view (8-mode card + rubric trajectory) |
| `/llm-profiles` | Per-call-kind config + verbatim prompts (Platform transparency panel) |
| `/llm-metrics/summary` | Per-call-kind aggregate of every Qwen call |
| `/probes/recent` | Recent local-LLM probe results |

## CLI

| command | what |
|---|---|
| `warden daemon start\|stop\|status\|logs\|run` | lifecycle (`run` = foreground) |
| `warden handoff [--session ID] [--no-edit] [--auto\|--manual] [--project PATH]` | run Phase 1 synthesis |
| `warden link [--project PATH]` | symlink `intent.md` into `<repo>/.modmcp/intent.md` |
| `warden hook userpromptsubmit` | bridge for the Claude Code hook (stdin JSON → daemon → stdout JSON) |
| `warden version` | print version |

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
qwen_max_tokens_rubric = 6000
qwen_max_tokens_consolidator = 8000

# Per-call-kind Qwen3 thinking mode.
qwen_enable_thinking_synth = true
qwen_enable_thinking_drift = false
qwen_enable_thinking_query = false
qwen_enable_thinking_rubric = true
qwen_enable_thinking_consolidator = true

# Sampling. The globals below are fallbacks. Per-call-kind overrides
# below them follow the Qwen3 model-card profiles:
#   thinking + general:        temperature = 1.0, presence_penalty = 1.5
#   thinking + precise coding: temperature = 0.6, presence_penalty = 0.0
#   non-thinking:              temperature = 1.0, presence_penalty = 1.5
qwen_temperature = 0.6
qwen_top_p = 0.95
qwen_top_k = 20
qwen_presence_penalty = 0.0
qwen_repetition_penalty = 1.0

# Per-call-kind sampler overrides. Defaults (shown) match Qwen's published
# profiles per task shape — rubric stays on the precise-coding profile for
# stable JSON judging; everything else uses the general / non-thinking
# profile. Set to a value to override; comment out (or set to the global
# value above) to fall back to the global. Existing configs that only set
# the globals continue to work unchanged.
qwen_temperature_synth        = 1.0
qwen_temperature_drift        = 1.0
qwen_temperature_query        = 1.0
qwen_temperature_rubric       = 0.6
qwen_temperature_consolidator = 1.0
qwen_presence_penalty_synth        = 1.5
qwen_presence_penalty_drift        = 1.5
qwen_presence_penalty_query        = 1.5
qwen_presence_penalty_rubric       = 0.0
qwen_presence_penalty_consolidator = 1.5

# Phase 2 (active mode).
phase2_turns_default = 8
drift_threshold = 0.35
per_turn_budget_seconds = 3.0
per_turn_hard_cap_seconds = 30.0
claim_grep_budget = 200
hook_timeout_ms = 400

# Rubric worker: Qwen-judged score every N assistant turns + on triggers.
rubric_turn_interval = 5
rubric_min_text_chars = 80

# Self-rubric (Reflection view): scores user-side dimensions on a
# slower cadence; synthesized /compact turns are excluded.
user_rubric_turn_interval = 6
user_rubric_min_text_chars = 80

# Project scope filters (default empty = watch every project under
# ~/.claude/projects/). Either form accepted: sanitized folder name
# (C--myproject) or absolute path (C:/code/myproject).
watch_paths = []
exclude_paths = []

# Scope worker: rolling baseline from the last N sessions.
scope_baseline_window = 5
scope_creep_factor = 2.0
scope_creep_floor = 12

# Session close: idle threshold for end-of-session consolidator.
session_idle_seconds = 600.0
session_close_poll_seconds = 60.0

# Live UI transport.
live_sse_max_subscribers_per_session = 4
live_sse_replay_events = 100
live_sse_keepalive_seconds = 20.0

# Transcript watcher.
max_watch_projects = 32
```

Environment overrides:
- `MODMCP_HOME` — relocate the state directory (default `~/.modmcp`). The internal package and state directory keep the historical `modmcp` name; user-facing CLI and product surface are `warden`.
- `CLAUDE_PROJECTS_ROOT` — relocate the Claude Code transcript root (default `~/.claude/projects`).

## Qwen / local LLM endpoint

Warden expects an OpenAI-compatible HTTP endpoint on `localhost` (see [Why local-first](#why-local-first) for the reasoning). Any of these work:

- [llama.cpp server](https://github.com/ggml-org/llama.cpp) with an OpenAI-compat flag
- [Ollama](https://ollama.com/) — set `qwen_endpoint = "http://127.0.0.1:11434/v1"`, `qwen_model = "qwen2.5:7b"`
- LM Studio's local server
- `vllm` with `--served-model-name`

Pointing `qwen_endpoint` at a remote host isn't explicitly blocked, but it defeats the audit-integrity argument; Warden will happily send your transcripts wherever you tell it to.

All LLM calls serialize through a single queue so Warden doesn't contend with other GPU workloads. Distinct call kinds are routed with their own token budgets and thinking-mode settings:

| kind | used by | typical cost |
|---|---|---|
| `synth` | `warden handoff --auto` | heavy (one-shot, up to 6k out + thinking) |
| `drift` | Phase 2 drift worker | light (per-turn, in active mode) |
| `rubric` | rubric worker (assistant) + user-side rubric | medium (sampled, every N turns + triggers) |
| `consolidator` | session-close worker | heavy (once per session close, up to 8k out + thinking) |

### What requires an LLM vs what doesn't

If the LLM endpoint is unreachable, Warden degrades cleanly:

| works without LLM | needs LLM |
|---|---|
| Transcript watcher + live feed | `warden handoff --auto` (falls back to manual template) |
| LiveBus + SSE | Drift classifier (active mode) |
| Constraints worker + violations UI | Rubric worker (silently skips samples) |
| Scope worker + creep detection | Self-rubric (Reflection view; silently skips) |
| Reflection derived signals (idle gaps, prompt lengths, approvals, verification verdicts) | Session-close consolidator (skips, report stays "in progress") |
| `warden handoff` (manual template) | Audit claim verification (LLM for claim extraction) |
| Platform view in-band turn metrics + local LLM probes | |

In other words: the entire passive observation layer works fine with no model running at all. You just won't get rubric scores, self-rubric scores, or the 8-mode report card until you bring one up.

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

`ledger.db` tables (grouped by what wrote them):
- Audit: `claims`, `drift_events`, `corrections`, `processed_offset`, `sessions`, `surfacings`, `constraint_violations`, `scope_snapshots`, `rubric_scores`, `rubric_feedback`, `session_reports`, `session_close`, `live_events`, `session_state`
- Inference path: `turn_metrics`, `probe_results`, `llm_call_metrics`

`<hash>` is the first 12 chars of `sha256(<canonical project path>)` — lowercase drive letter + forward slashes on Windows, fully resolved on POSIX.

## Architecture at a glance

```
Claude Code session
      │ writes transcript JSONL
      ▼
~/.claude/projects/<proj>/<session>.jsonl
      │ tailed by
      ▼
┌─────────────────── warden daemon ───────────────────┐
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
pytest -q          # ~120 tests, ~22s
cd frontend && npm install && npm run build
```

Frontend lives in `frontend/` (Svelte 5 + Vite). The build emits to `src/modmcp/web/static/dist/` (gitignored); the daemon serves whichever bundle is on disk and falls back to a "run npm install && npm run build" hint if no manifest is present.

Troubleshooting:

- `warden daemon logs -n 200` — everything interesting ends up here: hook failures, worker startup errors, LLM call failures with the call kind tagged.
- `warden daemon run` — runs the daemon in the foreground with uvicorn logs on stdout; useful when you want live-reload visibility into what the audit layer is doing.
- `MODMCP_HOME=/tmp/modmcp-dev warden daemon run` — isolated state dir for experimentation.

## Platform notes

- **Windows:** daemon uses `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`; `warden link` requires Developer Mode for symlinks and falls back to file copy otherwise.
- **macOS / Linux:** daemon uses `setsid` detachment; `warden link` uses `os.symlink`.

Service units (systemd user unit, launchd plist, Task Scheduler XML) are not required — the lightweight `warden daemon start` is sufficient. A future `warden daemon install-service` subcommand may ship them.
