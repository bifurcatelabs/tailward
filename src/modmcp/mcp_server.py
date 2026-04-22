"""Stdio MCP server exposing captured-intent tools to Claude Code.

Uses the official `mcp` Python SDK. The server reads/writes ``intent.md``
directly (the daemon is the source of truth; this process reuses the same
files). For ``query_intent`` we route through Qwen when available, else fall
back to keyword section-matching.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime

from mcp.server.fastmcp import FastMCP

from .paths import intent_path
from .schema.intent import SECTIONS, load_intent, save_intent

log = logging.getLogger(__name__)

mcp_app = FastMCP("modmcp")


def _current_project() -> str:
    return os.environ.get("MODMCP_PROJECT") or os.getcwd()


@mcp_app.tool()
def get_captured_intent(section: str | None = None) -> str:
    """Return the captured-intent document (or a single section)."""
    path = intent_path(_current_project())
    if not path.exists():
        return (
            "no captured intent for this project; "
            "run `modmcp handoff` in the project root first."
        )
    intent = load_intent(path)
    if section is None:
        return path.read_text(encoding="utf-8")
    if section not in SECTIONS:
        valid = ", ".join(SECTIONS)
        return f"unknown section {section!r}; valid: {valid}"
    return intent.sections.get(section, "").strip() or f"(no content in section {section!r})"


@mcp_app.tool()
def get_active_rules() -> str:
    """Convenience wrapper: just the Active Rules list."""
    return get_captured_intent("Active Rules")


@mcp_app.tool()
def query_intent(question: str) -> str:
    """Answer a question about the captured intent.

    Uses Qwen if reachable; otherwise falls back to keyword matching.
    """
    path = intent_path(_current_project())
    if not path.exists():
        return "no captured intent; run `modmcp handoff` first."
    intent = load_intent(path)

    try:
        from .daemon.qwen import QwenClient

        qwen = QwenClient()
        body = "\n\n".join(f"## {s}\n{intent.sections.get(s, '').strip()}" for s in SECTIONS)
        system = (
            "Answer the user's question using only the captured intent document provided. "
            "Be concise. If the document does not contain the answer, say so explicitly."
        )
        user = f"Captured intent:\n{body}\n\nQuestion: {question}"

        async def _run() -> str:
            return await qwen.complete(system, user, kind="query")

        try:
            return asyncio.run(_run()).strip()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(_run()).strip()
            finally:
                loop.close()
    except Exception as e:
        log.info("qwen unavailable for query_intent (%s); keyword fallback", e)

    # Keyword fallback: return sections whose words overlap the question.
    q_words = {w.lower() for w in question.split() if len(w) > 3}
    hits: list[str] = []
    for s in SECTIONS:
        body = intent.sections.get(s, "")
        if not body.strip():
            continue
        body_words = {w.lower() for w in body.split()}
        if q_words & body_words:
            hits.append(f"## {s}\n{body.strip()}")
    return "\n\n".join(hits) if hits else "no matching sections found."


@mcp_app.tool()
def record_decision(decision: str, rationale: str | None = None) -> str:
    """Append an agent-initiated decision to Open Threads or Active Rules.

    Heuristic: if the decision looks like a rule ("always", "never",
    "should"), append to Active Rules; otherwise append to Open Threads.
    """
    path = intent_path(_current_project())
    if not path.exists():
        return "no captured intent; run `modmcp handoff` first."
    intent = load_intent(path)

    low = f" {decision.lower().strip()} "
    rule_markers = (" always ", " never ", " must ", " must not ", " should ", " shouldn't ")
    section = "Active Rules" if any(m in low for m in rule_markers) else "Open Threads"
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"{decision.strip()} — recorded {ts}"
    if rationale:
        line += f" (rationale: {rationale.strip()})"
    intent.append_list_item(section, line)
    save_intent(intent, path)
    return f"recorded in {section}: {decision.strip()}"


def run_stdio() -> None:
    """Blocking entry point used by ``modmcp mcp``."""
    mcp_app.run()
