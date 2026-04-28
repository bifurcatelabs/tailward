"""Schema-audit utilities for Claude Code JSONL transcripts.

Powers two use cases:

1. **Re-running the empirical audit** that produced
   ``memory/project_upstream_fragility.md``: walk a JSONL, return a
   structured report of types / fields / content-block types / Claude
   Code versions observed. Cheaper than the ad-hoc shell script and
   gives stable output we can diff across versions.

2. **Future ``warden doctor``-style sanity checks**: same function
   answers "is this transcript written by a Claude Code version we've
   validated against?" — far stronger signal than ``claude --version``
   from the shell, which reports the binary on disk rather than the
   process that wrote the file.

Read-only by construction. The audit never mutates the JSONL or the
ledger; it just reads and counts.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def claude_code_version(obj: dict[str, Any]) -> str | None:
    """Extract the Claude Code ``version`` field from a parsed JSONL
    event, if present. Each event Claude Code emits carries the
    version of the running process — that's the source of truth for
    "what wrote this line," not the binary on disk.
    """
    v = obj.get("version") if isinstance(obj, dict) else None
    return v if isinstance(v, str) and v else None


@dataclass
class AuditReport:
    """Structured audit of a JSONL transcript.

    Counters are population counts (``how many lines had this``), not
    densities — fields that appear on every event will report the
    line count.
    """

    path: str
    lines_total: int = 0
    lines_parsed: int = 0
    versions: Counter = field(default_factory=Counter)
    top_types: Counter = field(default_factory=Counter)
    top_keys: Counter = field(default_factory=Counter)
    message_keys: Counter = field(default_factory=Counter)
    content_block_types: Counter = field(default_factory=Counter)

    @property
    def latest_version(self) -> str | None:
        """The most-frequent version observed; ``None`` if no events
        carried a version field. For a session-spanning audit, ties
        prefer whichever version Counter saw last."""
        if not self.versions:
            return None
        return self.versions.most_common(1)[0][0]


def audit_jsonl(path: Path | str) -> AuditReport:
    """Walk a JSONL transcript and return a structured AuditReport.

    Parse failures are skipped silently — the goal is "what's actually
    in the file, given a permissive read?", not strict validation. A
    file that is 99% well-formed is still meaningful.
    """
    p = Path(path)
    report = AuditReport(path=str(p))
    if not p.exists():
        return report

    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            report.lines_total += 1
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if not isinstance(obj, dict):
                continue
            report.lines_parsed += 1

            for k in obj.keys():
                report.top_keys[k] += 1
            type_val = obj.get("type")
            report.top_types[type_val if isinstance(type_val, str) else "<missing>"] += 1

            v = claude_code_version(obj)
            if v:
                report.versions[v] += 1

            msg = obj.get("message")
            if isinstance(msg, dict):
                for k in msg.keys():
                    report.message_keys[k] += 1
                content = msg.get("content")
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict):
                            bt = block.get("type")
                            report.content_block_types[
                                bt if isinstance(bt, str) else "<missing>"
                            ] += 1

    return report


# Versions of Claude Code we've validated against. When this list
# evolves, update ``memory/project_upstream_fragility.md`` and re-run
# ``audit_jsonl`` against a representative recent transcript.
VALIDATED_VERSIONS: frozenset[str] = frozenset({
    # Audited 2026-04-28 (running session was 2.1.119 even though
    # binary on disk had auto-updated to 2.1.121).
    "2.1.117",
    "2.1.119",
})


def is_validated_version(v: str | None) -> bool:
    """``True`` if ``v`` is on the list of Claude Code versions warden
    has been audited against. ``None`` returns ``False`` — we treat
    "no version field" as untrusted rather than assuming compatibility.
    """
    return bool(v) and v in VALIDATED_VERSIONS
