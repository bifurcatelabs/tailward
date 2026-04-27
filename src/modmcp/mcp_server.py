"""Stdio MCP server exposing captured-intent tools to Claude Code.

Uses the official `mcp` Python SDK. The server reads/writes ``intent.md``
directly (the daemon is the source of truth; this process reuses the same
files). For ``query_intent`` we route through Qwen when available, else fall
back to keyword section-matching.
"""

from __future__ import annotations

import logging
import os
import re
from collections import Counter
from datetime import UTC, datetime

from mcp.server.fastmcp import FastMCP

from .paths import intent_path
from .schema.intent import SECTIONS, load_intent, save_intent

log = logging.getLogger(__name__)

mcp_app = FastMCP("modmcp")


# Single source of truth for the query_intent prompt; surfaced
# verbatim in /llm-profiles. Runtime user prompt built via
# ``PROMPT_USER_TEMPLATE.format(body=..., question=...)``.
PROMPT_SYSTEM: str = (
    "Answer the user's question using only the captured intent document provided. "
    "Be concise. If the document does not contain the answer, say so explicitly."
)
PROMPT_USER_TEMPLATE: str = "Captured intent:\n{body}\n\nQuestion: {question}"


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
async def query_intent(question: str) -> str:
    """Answer a question about the captured intent.

    Uses Qwen if reachable; otherwise falls back to keyword matching.

    Implementation note: this handler is ``async`` on purpose. FastMCP runs
    tool handlers inside its own asyncio loop, so the previous approach of
    wrapping the Qwen call in ``asyncio.run()`` raised "cannot be called
    from a running event loop" and silently fell back to keyword matching.
    Every live Claude Code session quietly bypassed the LLM path because of
    it. Making the handler async lets us await the coroutine directly on
    the existing loop.
    """
    path = intent_path(_current_project())
    if not path.exists():
        return "no captured intent; run `modmcp handoff` first."
    intent = load_intent(path)

    try:
        from .daemon.qwen import QwenClient

        qwen = QwenClient()
        body = "\n\n".join(f"## {s}\n{intent.sections.get(s, '').strip()}" for s in SECTIONS)
        user = PROMPT_USER_TEMPLATE.format(body=body, question=question)
        answer = await qwen.complete(PROMPT_SYSTEM, user, kind="query")
        return answer.strip()
    except Exception as e:
        log.info("qwen unavailable for query_intent (%s); keyword fallback", e)

    return _keyword_fallback(question, intent)


# --- keyword ranker ---------------------------------------------------------

# Split on anything that isn't an ASCII letter or digit. Critically, this
# splits identifier tokens like ``active_rules`` into ``active`` + ``rules``,
# so a body mention of the identifier contributes to queries about either
# concept. We lowercase everything and drop tokens shorter than 3 chars.
_TOKEN_RE = re.compile(r"[a-zA-Z0-9]+")

# Question words and determiners that should not contribute to scoring.
# Deliberately small: we'd rather keep a noisy content word (false-positive
# overlap that a heading match will dominate anyway) than drop a real one.
_QUERY_STOP = frozenset({
    "what", "when", "where", "which", "who", "why", "how",
    "the", "are", "and", "for", "not", "but", "this", "that",
    "these", "those", "there", "here", "now", "currently",
    "right", "any", "some", "our", "its",
})


def _tokens(text: str) -> list[str]:
    return [m.group(0).lower() for m in _TOKEN_RE.finditer(text or "") if len(m.group(0)) >= 3]


# Heading hits are worth far more than body hits. Section bodies frequently
# omit the words that name them — the *Active Rules* body lists gitignore /
# hook rules, none of which repeat "active" or "rules". Without heading
# weighting a ranker picks whichever section happens to mention the query
# tokens in passing, which is exactly the bug we're fixing.
_HEADING_WEIGHT = 5
_BODY_WEIGHT = 1
_TOP_K = 3


def _keyword_fallback(question: str, intent) -> str:  # noqa: ANN001
    q_tokens = {t for t in _tokens(question) if t not in _QUERY_STOP}
    if not q_tokens:
        return "no matching sections found."

    scored: list[tuple[float, int, str, str]] = []
    for idx, name in enumerate(SECTIONS):
        body = intent.sections.get(name, "") or ""
        if not body.strip():
            continue
        heading_tokens = set(_tokens(name))
        body_counts = Counter(_tokens(body))
        score = 0.0
        for q in q_tokens:
            if q in heading_tokens:
                score += _HEADING_WEIGHT
            score += body_counts.get(q, 0) * _BODY_WEIGHT
        if score > 0:
            # Tie-break by canonical section order so output is deterministic.
            scored.append((score, idx, name, body))

    if not scored:
        return "no matching sections found."

    scored.sort(key=lambda r: (-r[0], r[1]))
    top = scored[:_TOP_K]
    return "\n\n".join(f"## {name}\n{body.strip()}" for _, _, name, body in top)


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
