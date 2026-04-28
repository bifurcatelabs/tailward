# Contributing

Warden is a local-first audit overlay for AI coding sessions. It's solo-developed and intentionally opinionated — passive-only, no prompt injection, evidence-not-verdicts. Some categories of contribution are great; others fight the architectural posture.

## In scope

- **Audit signal expansion** — new event-type detection, new derived metrics from JSONL, new surface views.
- **Bug fixes + reliability** — anything making the daemon, watcher, or web UI more robust.
- **Schema-pinning + version validation** — Claude Code's JSONL schema shifts; tests + audit helpers catching the drift are valuable. See `src/modmcp/schema/audit.py`.
- **UI / UX clarity** — making the three views (Session / Reflection / Platform) communicate evidence more clearly.
- **Documentation** — anywhere README or in-code docs are unclear.

## Out of scope

- **Active prompt injection / drift correctives.** The passive-first pivot in v1.1 was deliberate (see [Why passive is the default](README.md#why-passive-is-the-default)). The opt-in `active` mode is preserved for users who want it but is not actively developed.
- **Cloud / SaaS integrations.** The local-first stance is load-bearing (see [Why local-first](README.md#why-local-first)). Telemetry, hosted scoring, or anything that ships transcripts off-machine is a hard no.
- **MCP server features.** Retired in v2.0.0.

## Development setup

```bash
git clone <repo-url>
cd warden
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows:     .venv\Scripts\activate
pip install -e ".[dev]"
cd frontend && npm ci && cd ..
```

Daemon (binds `127.0.0.1:7878`):

```bash
warden daemon start
```

Frontend dev server (hot reload, proxies to daemon):

```bash
cd frontend && npm run dev
```

## Tests + lint

```bash
python -m pytest tests/        # full suite
ruff check src/ tests/          # lint
cd frontend && npm run build   # frontend build
```

CI runs all three on PRs — see `.github/workflows/test.yml` and `.gitlab-ci.yml` (mirror).

## PR shape

- One concern per commit. Small, focused changes review quickly.
- [Conventional-commits](https://www.conventionalcommits.org/) prefixes: `feat:`, `fix:`, `chore:`, `docs:`, `test:`, `refactor:`.
- Tests when introducing new behavior. `tests/test_schema_pin.py` and `tests/test_schema_audit.py` are good models.
- Architectural changes (passive-only, local-first, no-injection) — open an issue first. These are settled positions, not implementation details.

## Filing issues

- **Bug reports:** OS, Python version, Claude Code version (`claude --version`), and the JSONL line if relevant. Sanitize personal paths first.
- **Feature requests:** state the audit signal you're trying to surface, not the implementation. Helps figure out whether it fits the posture.
- **Schema drift:** Claude Code emits an event type Warden doesn't handle? Attach a sample JSONL line and what you'd expect in the UI.
