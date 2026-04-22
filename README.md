# modmcp

A session-handoff and accountability layer for Claude Code.

modmcp runs as a long-running local daemon. It mediates the transition between Claude Code sessions when context fills up, and stays active through the early turns of the new session — restating context, enforcing rules, and auditing the agent's claims against transcript and filesystem evidence.

See [`V1 Proposal.md`](V1%20Proposal.md) for the full design.

## What it does (v1)

- **Phase 1 — handoff:** when you say "we're out of room", `modmcp handoff` reads the current session's transcript, synthesizes a structured `intent.md` (active goal, open threads, active rules, known drift patterns, pending commitments, recent claims), and opens it in your editor for a quick review.
- **Phase 2 — continuity:** on the next session's first turn, a `UserPromptSubmit` hook injects the intent as a preamble. For the first N turns, every assistant turn is audited — drift against the goal produces a queued corrective injection on the next turn; strong claims ("I removed all X", "fully refactored Y") are grepped against the repo and logged as verified / contradicted / unverifiable.
- **MCP pull:** the agent can call `get_captured_intent`, `query_intent`, `get_active_rules`, and `record_decision` at will.
- **Web UI:** a small localhost web page at `http://127.0.0.1:7878/` for reviewing and editing the intent and viewing the verification ledger / drift events.

## Install

Requires Python 3.12+. (Windows, macOS, or Linux.)

```bash
pip install -e .
# or
pipx install -e .
```

On first use, modmcp creates `~/.modmcp/` for state (config, logs, ledger, per-project intent files).

## Quick start

```bash
# 1. Start the daemon in the background.
modmcp daemon start

# 2. In the project you care about:
cd ~/code/your-project
modmcp handoff                # or: modmcp handoff --no-edit

# 3. Review status / logs as needed:
modmcp daemon status
modmcp daemon logs -n 200

# 4. Open the web UI:
open http://127.0.0.1:7878/   # Windows: start http://127.0.0.1:7878/
```

Stop the daemon with `modmcp daemon stop`.

## CLI

| command | what |
|---|---|
| `modmcp daemon start\|stop\|status\|logs\|run` | lifecycle |
| `modmcp handoff [--session ID] [--no-edit] [--auto\|--manual] [--project PATH]` | run Phase 1 synthesis |
| `modmcp link [--project PATH]` | symlink `intent.md` to `<repo>/.modmcp/intent.md` (so it can be git-tracked per project if you want) |
| `modmcp hook userpromptsubmit` | used by Claude Code hook config (reads JSON on stdin, POSTs to daemon, writes JSON on stdout) |
| `modmcp mcp` | run the stdio MCP server (used by Claude Code MCP config) |
| `modmcp version` | print version |

## Claude Code integration

### 1. Register the UserPromptSubmit hook

Edit your Claude Code `settings.json` (usually `~/.claude/settings.json`) and add:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "modmcp hook userpromptsubmit"
          }
        ]
      }
    ]
  }
}
```

The hook has a hard ≤400 ms budget; on any error or timeout it passes the prompt through unchanged so it can never block you.

### 2. Register the MCP server (per project)

In the project root you want warden to guard, add `.claude/mcp.json` (or the equivalent in your project-scoped Claude Code config):

```json
{
  "mcpServers": {
    "modmcp": {
      "command": "modmcp",
      "args": ["mcp"],
      "env": {
        "MODMCP_PROJECT": "${workspaceFolder}"
      }
    }
  }
}
```

If `MODMCP_PROJECT` isn't set, the MCP server falls back to the current working directory.

## Configuration

First run writes `~/.modmcp/config.toml` with defaults. Edit and restart the daemon:

```toml
qwen_endpoint = "http://127.0.0.1:8080/v1"
qwen_model = "qwen2.5-8b-instruct"
qwen_api_key = "not-needed"
http_host = "127.0.0.1"
http_port = 7878
phase2_turns_default = 8
drift_threshold = 0.35
per_turn_budget_seconds = 3.0
per_turn_hard_cap_seconds = 10.0
claim_grep_budget = 200
hook_timeout_ms = 400
```

## Qwen / local LLM endpoint

modmcp uses an OpenAI-compatible HTTP endpoint for synthesis, drift classification, and `query_intent`. Any of these work:

- [llama.cpp server](https://github.com/ggerganov/llama.cpp) started with `-cb` + an OpenAI-compat flag
- [Ollama](https://ollama.com/) with `OLLAMA_HOST=127.0.0.1:8080` (set `qwen_endpoint = "http://127.0.0.1:8080/v1"`, `qwen_model = "qwen2.5:7b"`)
- LM Studio's local server
- `vllm` with `--served-model-name`

modmcp serializes all LLM calls through a single queue so it won't contend with other GPU workloads you're running. If the endpoint is unreachable, drift / query fall back to heuristics and handoff falls back to a manual template.

## Storage layout

```
~/.modmcp/
├── config.toml
├── daemon.pid
├── logs/daemon.log
├── ledger.db                    # SQLite: claims, drift, corrections, offsets
└── projects/<hash>/
    ├── project.toml
    ├── intent.md                # source of truth, human-editable
    ├── surfacings/              # notification artifacts
    └── archive/
        └── intent-<timestamp>.md  # snapshot before each overwrite
```

`<hash>` is the first 12 chars of `sha256(<canonical project path>)`.

## Development

```bash
pip install -e ".[dev]"
pytest -q
```

Runtime troubleshooting: `modmcp daemon logs -n 200`. All errors (hook failures, drift/audit worker exceptions) are logged there.

## Platform notes

- **Windows:** daemon uses `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`; `modmcp link` requires Developer Mode for symlinks and falls back to file copy otherwise.
- **macOS / Linux:** daemon uses `setsid` detachment; `modmcp link` uses `os.symlink`.

Service units (systemd user unit / launchd plist / Task Scheduler XML) are not required for v1; the lightweight `modmcp daemon start` is sufficient. A future `modmcp daemon install-service` subcommand may ship them.
